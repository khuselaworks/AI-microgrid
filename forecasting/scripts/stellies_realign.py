"""
Re-date the Stellenbosch 2023 irradiance record onto the 2002 calendar so
it can be used alongside the Westridge 2002 load record.

    python stellies_realign.py --source Stellies_2023.xlsx --out stellies_mod.csv

WHAT THIS DOES, AND WHAT IT DOES NOT CLAIM
------------------------------------------
The two records cover the same 8-month seasonal window (1 Feb - 30 Sep)
but are 21 years apart and share no real timestamps. This script maps
each solar timestamp onto the same month, day and time-of-day in 2002:

    2023-03-17 08:45  ->  2002-03-17 08:45

so the two series sit on one time axis and can be combined into a
net-load scenario.

Why month-day and not a weekday-preserving shift: irradiance has no
weekly cycle - the sun does not know it is Sunday - so nothing in the
solar record is misaligned by taking the load record's weekday labels.
Seasonal position, which IS what drives irradiance, is preserved exactly.
(A weekday-preserving shift would instead perturb the seasonal position,
which is the one thing that matters here.)

What this pairing does NOT give you: any load-PV correlation in the
merged series is an artefact of this mapping, not physics. That matters
most for the net-load tail, which is what reserve sizing depends on. Use
--offset-days to build an ensemble of pairings (0, +/-7, +/-14 keep the
weekday labels consistent) and report results ACROSS it rather than
treating one pairing as truth.

Neither 2002 nor 2023 is a leap year, so there is no 29 February to
reconcile. South Africa does not observe daylight saving, so no
timestamp is ambiguous or skipped. Both are asserted below, not assumed.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

TARGET_YEAR = 2002
SOLAR_VALID = (0.0, 1400.0)   # W/m^2; matches ForecastConfig.screen_defaults


def read_record(path: Path) -> pd.DataFrame:
    """First column is the timestamp; everything else is kept."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path, engine="openpyxl")
    else:
        raw = pd.read_csv(path)
    time_col = raw.columns[0]
    idx = pd.DatetimeIndex(pd.to_datetime(raw[time_col]))
    body = raw.drop(columns=[time_col])
    body.index = idx
    body.index.name = "timestamp"
    return body.sort_index()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="Stellies_2023.xlsx")
    ap.add_argument("--out", default="stellies_mod.csv")
    ap.add_argument("--load", default=None,
                    help="load CSV, to cross-check the resulting overlap")
    ap.add_argument("--resample", default="5min",
                    help="target grid ('5min' to match the load record; "
                         "'none' keeps the native 1-minute resolution)")
    ap.add_argument("--offset-days", type=int, default=0,
                    help="extra whole-day shift, for a pairing ensemble; "
                         "multiples of 7 keep weekday labels consistent")
    ap.add_argument("--also-xlsx", action="store_true")
    a = ap.parse_args(argv)

    src = Path(a.source)
    body = read_record(src)
    idx = body.index

    print(f"SOURCE  {src}")
    print(f"  {len(body):,} rows, {body.shape[1]} data column(s): "
          f"{', '.join(map(str, body.columns))}")
    print(f"  {idx[0]:%Y-%m-%d %H:%M} -> {idx[-1]:%Y-%m-%d %H:%M}")
    step = pd.Series(idx).diff().mode()
    if len(step):
        print(f"  modal step: {step.iloc[0]}")

    # -- guards, asserted rather than assumed --------------------------
    if ((idx.month == 2) & (idx.day == 29)).any():
        raise SystemExit("source contains 29 February; cannot map onto "
                         f"{TARGET_YEAR}, which is not a leap year.")
    dupes = int(idx.duplicated().sum())
    if dupes:
        print(f"  ! {dupes:,} duplicate timestamps — keeping the first of each")
        body = body[~idx.duplicated(keep="first")]
        idx = body.index

    # -- physical screening BEFORE resampling --------------------------
    # One impossible reading must not contaminate the 5-minute mean it
    # falls in. The raw export reaches ~2,977 W/m^2, about twice what can
    # arrive at the surface.
    ghi_cols = [c for c in body.columns if "ghi" in str(c).lower()]
    screened = 0
    for c in ghi_cols:
        bad = (body[c] < SOLAR_VALID[0]) | (body[c] > SOLAR_VALID[1])
        screened += int(bad.sum())
        body.loc[bad, c] = np.nan
    if ghi_cols:
        print(f"  screened {screened:,} reading(s) outside "
              f"{SOLAR_VALID} W/m^2 in {ghi_cols}")

    # -- resample -------------------------------------------------------
    if a.resample.lower() != "none":
        before = len(body)
        body = body.resample(a.resample).mean()
        print(f"  resampled {before:,} -> {len(body):,} rows on a "
              f"{a.resample} grid (mean over each bin)")
        idx = body.index

    # -- re-date ---------------------------------------------------------
    shifted = idx + pd.Timedelta(days=a.offset_days) if a.offset_days else idx
    try:
        new_idx = pd.DatetimeIndex(
            [ts.replace(year=TARGET_YEAR) for ts in shifted])
    except ValueError as e:                      # 29 Feb after an offset
        raise SystemExit(f"cannot re-date: {e}")

    out = body.copy()
    out.index = new_idx
    out.index.name = "timestamp"
    out = out.sort_index()

    if not out.index.is_monotonic_increasing:
        raise SystemExit("re-dated index is not monotonic — check --offset-days")
    if out.index.duplicated().any():
        raise SystemExit("re-dating produced duplicate timestamps")

    print(f"\nRE-DATED  {TARGET_YEAR} calendar"
          + (f", offset {a.offset_days:+d} day(s)" if a.offset_days else ""))
    print(f"  {out.index[0]:%Y-%m-%d %H:%M} -> {out.index[-1]:%Y-%m-%d %H:%M}"
          f"   ({out.index.normalize().nunique()} distinct days)")
    print(f"  example: {idx[0]:%Y-%m-%d %H:%M} ({idx[0]:%A})"
          f"  ->  {out.index[0]:%Y-%m-%d %H:%M} ({out.index[0]:%A})")

    # -- cross-check against the load record -----------------------------
    if a.load:
        load = read_record(Path(a.load))
        lo = max(out.index[0], load.index[0])
        hi = min(out.index[-1], load.index[-1])
        days = (hi.normalize() - lo.normalize()).days + 1
        print(f"\nOVERLAP WITH LOAD  {Path(a.load).name}")
        print(f"  load  {load.index[0]:%Y-%m-%d} -> {load.index[-1]:%Y-%m-%d}")
        print(f"  solar {out.index[0]:%Y-%m-%d} -> {out.index[-1]:%Y-%m-%d}")
        print(f"  common window {lo:%Y-%m-%d} -> {hi:%Y-%m-%d}  ({days} days)")
        if days <= 0:
            print("  ! no overlap — check --offset-days")

    # -- write -----------------------------------------------------------
    dst = Path(a.out)
    out.to_csv(dst)
    print(f"\n-> wrote {dst}  ({len(out):,} rows)")
    if a.also_xlsx:
        xl = dst.with_suffix(".xlsx")
        out.to_excel(xl, engine="openpyxl")
        print(f"-> wrote {xl}")

    # -- provenance sidecar, for the write-up ----------------------------
    note = dst.with_suffix(".README.txt")
    shift_txt = f", after a {a.offset_days:+d} day shift" if a.offset_days else ""
    grid_txt = ("  - native resolution preserved\n"
                if a.resample.lower() == "none"
                else f"  - resampled to {a.resample} by bin mean\n")
    note.write_text(
        f"{dst.name}\n"
        f"{'=' * len(dst.name)}\n\n"
        f"Derived from {src.name} by stellies_realign.py.\n\n"
        f"Transformation\n"
        f"  - every timestamp mapped to the same month, day and time-of-day\n"
        f"    in {TARGET_YEAR}{shift_txt}\n"
        f"  - {screened:,} reading(s) outside {SOLAR_VALID} W/m^2 screened to NaN\n"
        f"    BEFORE resampling, so they cannot contaminate a bin mean\n"
        + grid_txt +
        f"\nSource range   {idx[0]:%Y-%m-%d} to {idx[-1]:%Y-%m-%d}\n"
        f"Output range   {out.index[0]:%Y-%m-%d} to {out.index[-1]:%Y-%m-%d}\n"
        f"Rows           {len(out):,}\n\n"
        f"LIMITATION\n"
        f"  The load and irradiance records are 21 years apart and share no\n"
        f"  real timestamps. This file places them on one time axis so a\n"
        f"  net-load scenario can be built; it does NOT make them contemporaneous.\n"
        f"  Any load-PV correlation observed in the merged series is an artefact\n"
        f"  of this pairing, not physics. This matters most for the net-load tail,\n"
        f"  which is what reserve sizing depends on. Build an ensemble with\n"
        f"  --offset-days 0 +/-7 +/-14 and report results across it.\n"
        f"\n  Solar geometry downstream will be computed from the {TARGET_YEAR}\n"
        f"  dates, while the irradiance was physically measured under the 2023\n"
        f"  sun. Same month-day, so declination differs by well under a degree;\n"
        f"  negligible next to the effects this pipeline screens for, but stated\n"
        f"  here rather than left implicit.\n")
    print(f"-> wrote {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
