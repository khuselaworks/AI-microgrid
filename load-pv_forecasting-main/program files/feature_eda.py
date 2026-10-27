"""
Feature selection, step 1: what actually carries signal in the load series.

Runs on the TRAINING SPLIT ONLY - the same 70% chronological slice the
model will train on - so nothing here can leak validation or test
information into a feature decision.

    python feature_eda.py --source ../aggregate_total_load.csv

Writes feature_eda/ : autocorrelation.png, periodogram.png,
day_profiles.png, and importance.csv (mutual information between each
candidate future covariate and the target, at three lead times).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from config import ForecastConfig  # noqa: E402
from datasets import calendar_features, chronological_splits, load_target  # noqa: E402
from holidays import is_holiday  # noqa: E402

STEPS_PER_DAY = 288
STEPS_PER_WEEK = STEPS_PER_DAY * 7


def mutual_information(x: np.ndarray, y: np.ndarray, bins: int = 24) -> float:
    """
    Nonparametric MI in nats, via 2-D histogram. `x` may be continuous or
    discrete (few unique values are used as-is); `y` is binned.
    """
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if x.size < 100:
        return np.nan
    ux = np.unique(x)
    x_edges = ux.size if ux.size <= 12 else bins
    c = np.histogram2d(x, y, bins=[x_edges, bins])[0]
    p = c / c.sum()
    px = p.sum(axis=1, keepdims=True)
    py = p.sum(axis=0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        term = p * np.log(p / (px * py))
    return float(np.nansum(term[p > 0]))


def acf(series: np.ndarray, max_lag: int) -> np.ndarray:
    s = series - np.nanmean(series)
    s = np.nan_to_num(s)
    full = np.correlate(s, s, mode="full")
    mid = full.size // 2
    out = full[mid:mid + max_lag + 1]
    return out / out[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", default="load")
    ap.add_argument("--out", default="feature_eda")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(exist_ok=True)

    cfg = ForecastConfig(target=a.target, source=a.source)
    series = load_target(cfg)
    splits = chronological_splits(series.index, cfg)
    train = series.iloc[splits.train]
    idx = train.index
    y = train.to_numpy(dtype=float)

    print(f"Train split: {idx[0]:%Y-%m-%d} -> {idx[-1]:%Y-%m-%d}  "
          f"({len(train):,} steps, {np.isfinite(y).mean():.1%} finite)")

    # ---- 1. autocorrelation ------------------------------------------
    max_lag = STEPS_PER_WEEK * 2
    r = acf(y, max_lag)
    lags_h = np.arange(max_lag + 1) * 5 / 60
    for label, lag in [("5 min", 1), ("1 hour", 12), ("1 day", STEPS_PER_DAY),
                       ("1 week", STEPS_PER_WEEK), ("2 weeks", STEPS_PER_WEEK * 2)]:
        print(f"  ACF at {label:>7}: {r[lag]:+.3f}")

    fig, ax = plt.subplots(figsize=(11, 4), dpi=110)
    ax.plot(lags_h, r, lw=0.9, color="#2563eb")
    ax.axhline(0, color="#888", lw=0.6)
    for d in range(1, 15):
        ax.axvline(d * 24, color="#d1495b", lw=0.5, alpha=0.4)
    ax.set_xlabel("lag (hours)")
    ax.set_ylabel("autocorrelation")
    ax.set_title("Load autocorrelation — red lines mark 24 h multiples",
                 loc="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "autocorrelation.png")
    plt.close(fig)

    # ---- 2. periodogram --------------------------------------------
    from scipy.signal import periodogram
    yy = np.nan_to_num(y - np.nanmean(y))
    freq, power = periodogram(yy, fs=1.0)  # cycles per 5-min step
    period_h = np.where(freq > 0, 1.0 / freq * 5 / 60, np.inf)
    keep = (period_h > 1) & (period_h < 24 * 20)
    top = np.argsort(power[keep])[::-1][:8]
    print("\n  Dominant periods (periodogram):")
    for t in top:
        print(f"    {period_h[keep][t]:8.2f} h   power {power[keep][t]:.3e}")

    fig, ax = plt.subplots(figsize=(11, 4), dpi=110)
    ax.semilogy(period_h[keep], power[keep], lw=0.8, color="#2563eb")
    for h in (12, 24, 24 * 7):
        ax.axvline(h, color="#d1495b", lw=0.6)
    ax.set_xlabel("period (hours)")
    ax.set_ylabel("power")
    ax.set_title("Load periodogram — lines at 12 h, 24 h, 168 h",
                 loc="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "periodogram.png")
    plt.close(fig)

    # ---- 3. day-type profiles ------------------------------------
    frame = pd.DataFrame({"y": y}, index=idx)
    frame["tod"] = idx.hour + idx.minute / 60
    frame["dow"] = idx.dayofweek
    frame["weekend"] = frame["dow"] >= 5
    frame["holiday"] = is_holiday(idx).astype(bool)

    hol_dates = sorted({d.date() for d in idx[frame["holiday"].to_numpy()]})
    print(f"\n  Public holidays in train split: "
          f"{', '.join(str(d) for d in hol_dates) or 'none'}")

    prof = lambda mask: frame.loc[mask].groupby("tod")["y"].mean()
    fig, ax = plt.subplots(figsize=(11, 4.5), dpi=110)
    ax.plot(prof(~frame.weekend & ~frame.holiday), label="weekday", lw=1.8,
            color="#2563eb")
    ax.plot(prof(frame.weekend & ~frame.holiday), label="weekend", lw=1.8,
            color="#8b5cf6")
    if frame.holiday.any():
        ax.plot(prof(frame.holiday), label="public holiday", lw=1.8,
                color="#d1495b", ls="--")
    ax.set_xlabel("hour of day")
    ax.set_ylabel("mean load (kW)")
    ax.set_title("Mean daily load profile by day type (train split)",
                 loc="left", fontweight="bold", fontsize=11)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "day_profiles.png")
    plt.close(fig)

    wk = frame.loc[~frame.weekend & ~frame.holiday, "y"].mean()
    we = frame.loc[frame.weekend & ~frame.holiday, "y"].mean()
    print(f"  mean weekday load {wk:8.2f} kW")
    print(f"  mean weekend load {we:8.2f} kW   ({we / wk - 1:+.1%} vs weekday)")
    if frame.holiday.any():
        ho = frame.loc[frame.holiday, "y"].mean()
        print(f"  mean holiday load {ho:8.2f} kW   ({ho / wk - 1:+.1%} vs weekday)")

    # ---- 4. mutual information vs the target, by lead time ---------
    minute_of_day = idx.hour.to_numpy() * 60 + idx.minute.to_numpy()
    dow = idx.dayofweek.to_numpy()
    doy = idx.dayofyear.to_numpy()
    hol = is_holiday(idx)

    candidates = {
        "tod_sin": np.sin(2 * np.pi * minute_of_day / 1440.0),
        "tod_cos": np.cos(2 * np.pi * minute_of_day / 1440.0),
        "dow_sin": np.sin(2 * np.pi * dow / 7.0),
        "dow_cos": np.cos(2 * np.pi * dow / 7.0),
        "is_weekend": (dow >= 5).astype(float),
        "doy_sin": np.sin(2 * np.pi * doy / 365.25),
        "doy_cos": np.cos(2 * np.pi * doy / 365.25),
        "is_holiday": hol,
        "lag_1d": np.concatenate([np.full(STEPS_PER_DAY, np.nan), y[:-STEPS_PER_DAY]]),
        "lag_1w": np.concatenate([np.full(STEPS_PER_WEEK, np.nan), y[:-STEPS_PER_WEEK]]),
    }

    leads = {"+30min": 6, "+6h": 72, "+24h": 288}
    rows = []
    base_mi = mutual_information(np.arange(len(y)) % 1, y)  # ~0 reference
    for name, feat in candidates.items():
        row = {"feature": name}
        for label, lead in leads.items():
            f = feat[:-lead] if lead else feat
            t = y[lead:] if lead else y
            row[label] = mutual_information(np.asarray(f, dtype=float), t)
        rows.append(row)
    imp = pd.DataFrame(rows).set_index("feature")
    imp.to_csv(out / "importance.csv")
    print("\n  Mutual information with the target (nats), by forecast lead:")
    print(imp.round(4).to_string())

    print(f"\n-> wrote {out}/  (autocorrelation.png, periodogram.png, "
          f"day_profiles.png, importance.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
