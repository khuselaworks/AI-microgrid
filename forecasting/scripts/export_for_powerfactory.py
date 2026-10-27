"""
Stitch finished runs into continuous day-ahead series for DIgSILENT.

    python export_for_powerfactory.py --load-run runs/load-improved \
                                      --solar-run runs/solar-final \
                                      --solar-source ../stellies_mod.csv \
                                      --out ../powerfactory_export

WHAT "STITCHED DAY-AHEAD" MEANS
-------------------------------
A run's predictions.csv holds one 288-step forecast per ORIGIN, and with
stride 3 those origins overlap heavily - 3,197 forecasts covering ~34
days. That is a set of forecasts, not a time series, and PowerFactory
needs a time series.

So this takes only the origins that fall at 00:00 and concatenates each
one's 288 steps end to end. The result is continuous, non-overlapping,
and every point is a genuine day-ahead forecast made before midnight of
the day it covers - which is what a once-daily operational forecast
actually looks like, and what a unit-commitment study consumes.

NORMALISATION
-------------
Normalised columns use the scaler that the MODEL WAS TRAINED UNDER,
recovered from model.pt. That scaler was fitted on training rows only,
so nothing here is contaminated by validation or test data. Re-fitting a
fresh scaler on the exported window would be leakage-free too, but it
would not be the transform the model actually used, and the two would
silently disagree. The parameters are written beside the data so the
transform is invertible outside this codebase.

TEMPERATURE
-----------
The solar model forecasts GHI only. There is no temperature model. The
temperature column here is the MEASURED value, carried through so PV
cell-temperature derating can be computed downstream. It is labelled
_actual for that reason and must not be presented as a forecast.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

STEPS_PER_DAY = 288


def load_run(run_dir: Path):
    """predictions.csv + config + the scaler the model was trained under."""
    cfg = json.loads((run_dir / "config.json").read_text())
    pred = pd.read_csv(run_dir / "predictions.csv", parse_dates=["origin"])
    ckpt = torch.load(run_dir / "model.pt", map_location="cpu", weights_only=False)
    return pred, cfg, ckpt.get("scaler")


def stitch(pred: pd.DataFrame, model_col: str, value_name: str) -> pd.DataFrame:
    """
    Concatenate the 288 steps of every midnight origin into one series.

    Each row's timestamp is origin + lead_step * 5 min, so consecutive
    days abut exactly and nothing overlaps.
    """
    midnight = pred[(pred["origin"].dt.hour == 0) & (pred["origin"].dt.minute == 0)]
    if midnight.empty:
        raise SystemExit("no origin falls at 00:00 — cannot build a daily trace; "
                         "re-run with a stride that divides 288")
    days = midnight["origin"].nunique()
    full = midnight.groupby("origin")["lead_step"].count().eq(STEPS_PER_DAY)
    keep = full[full].index
    dropped = days - len(keep)
    if dropped:
        print(f"  ! {dropped} midnight origin(s) lacked a full 288-step horizon "
              f"— dropped")
    m = midnight[midnight["origin"].isin(keep)].copy()

    m["timestamp"] = m["origin"] + pd.to_timedelta(m["lead_step"] * 5, unit="m")
    m = m.sort_values("timestamp")
    out = pd.DataFrame({
        "timestamp": m["timestamp"].to_numpy(),
        f"{value_name}_forecast": m[model_col].to_numpy(),
        f"{value_name}_actual": m["actual"].to_numpy(),
        "lead_step": m["lead_step"].to_numpy(),
    })
    # Quantile columns from a model trained with --quantiles: the interval
    # a reserve study needs, e.g. load_kw_q0.1 and load_kw_q0.9 for 80%.
    prefix = f"{model_col}_q"
    for c in (c for c in m.columns if c.startswith(prefix)):
        out[f"{value_name}_q{c[len(prefix):]}"] = m[c].to_numpy()
    if out["timestamp"].duplicated().any():
        raise SystemExit("stitched series has duplicate timestamps")
    print(f"  {len(keep)} whole days -> {len(out):,} rows, "
          f"{out['timestamp'].iloc[0]:%Y-%m-%d %H:%M} to "
          f"{out['timestamp'].iloc[-1]:%Y-%m-%d %H:%M}")
    return out


def scaler_bounds(scaler_dict) -> tuple:
    """(data_min, data_max, feature_min, feature_max) of the single channel."""
    ch = scaler_dict["channels"]
    first = ch[list(ch)[0]]
    fr = scaler_dict.get("feature_range", [0.0, 1.0])
    return first["data_min"], first["data_max"], fr[0], fr[1]


def normalise(series: pd.Series, bounds) -> pd.Series:
    lo, hi, fmin, fmax = bounds
    if hi == lo:
        return pd.Series(np.full(len(series), fmin), index=series.index)
    return (series - lo) / (hi - lo) * (fmax - fmin) + fmin


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--load-run", required=True)
    ap.add_argument("--solar-run", required=True)
    ap.add_argument("--solar-source", required=True,
                    help="re-dated solar file, for the measured temperature column")
    ap.add_argument("--out", default="../powerfactory_export")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- load
    print("LOAD")
    lpred, lcfg, lscaler = load_run(Path(a.load_run))
    load = stitch(lpred, lcfg["model"], "load_kw")

    # --------------------------------------------------------------- solar
    print("SOLAR")
    spred, scfg, sscaler = load_run(Path(a.solar_run))
    solar = stitch(spred, scfg["model"], "ghi_wm2")
    # Irradiance cannot be negative; the network has no reason to know that.
    for c in ["ghi_wm2_forecast", "ghi_wm2_actual"] + [c for c in solar.columns
                                                       if c.startswith("ghi_wm2_q")]:
        solar[c] = solar[c].clip(lower=0.0)

    # measured temperature, carried through (NOT forecast)
    src = pd.read_csv(a.solar_source, parse_dates=["timestamp"]).set_index("timestamp")
    tcol = next((c for c in src.columns if "temp" in str(c).lower()), None)
    if tcol is None:
        raise SystemExit(f"no temperature column found in {a.solar_source}")
    solar["temp_c_actual"] = src[tcol].reindex(solar["timestamp"]).to_numpy()
    print(f"  temperature column '{tcol}' carried through as measured "
          f"({solar['temp_c_actual'].isna().sum()} NaN)")

    # ------------------------------------------------- temporal alignment
    common = np.intersect1d(load["timestamp"].to_numpy(),
                            solar["timestamp"].to_numpy())
    print(f"\nALIGNMENT\n  load {len(load):,} rows, solar {len(solar):,} rows, "
          f"common {len(common):,}")
    load = load[load["timestamp"].isin(common)].reset_index(drop=True)
    solar = solar[solar["timestamp"].isin(common)].reset_index(drop=True)
    if not load["timestamp"].equals(solar["timestamp"]):
        raise SystemExit("timestamps still disagree after intersection")
    print(f"  aligned on {len(load):,} timestamps, "
          f"{load['timestamp'].iloc[0]:%Y-%m-%d} to {load['timestamp'].iloc[-1]:%Y-%m-%d}")

    # ------------------------------------------------------------ write
    lb = scaler_bounds(lscaler)
    sb = scaler_bounds(sscaler)

    load_q = [c for c in load.columns if c.startswith("load_kw_q")]
    load_real = load[["timestamp", "load_kw_forecast", "load_kw_actual", "lead_step"] + load_q]
    load_real = load_real.assign(load_mw_forecast=load["load_kw_forecast"] / 1000.0,
                                 load_mw_actual=load["load_kw_actual"] / 1000.0)
    load_norm = pd.DataFrame({
        "timestamp": load["timestamp"],
        "load_forecast_norm": normalise(load["load_kw_forecast"], lb),
        "load_actual_norm": normalise(load["load_kw_actual"], lb),
        "lead_step": load["lead_step"],
    })

    solar_q = [c for c in solar.columns if c.startswith("ghi_wm2_q")]
    solar_real = solar[["timestamp", "ghi_wm2_forecast", "ghi_wm2_actual",
                        "temp_c_actual", "lead_step"] + solar_q]
    solar_norm = pd.DataFrame({
        "timestamp": solar["timestamp"],
        "ghi_forecast_norm": normalise(solar["ghi_wm2_forecast"], sb),
        "ghi_actual_norm": normalise(solar["ghi_wm2_actual"], sb),
        "lead_step": solar["lead_step"],
    })

    files = {
        "load_forecast_real_units.csv": load_real,
        "load_forecast_normalised.csv": load_norm,
        "solar_forecast_real_units.csv": solar_real,
        "solar_forecast_normalised.csv": solar_norm,
    }
    print("\nWROTE")
    for name, frame in files.items():
        frame.to_csv(out / name, index=False)
        print(f"  {name:<38} {len(frame):,} rows x {frame.shape[1]} cols")

    params = {
        "load": {"run": str(a.load_run), "scaler": lscaler,
                 "features": lcfg.get("future_features"),
                 "units": "kW (and MW)", },
        "solar": {"run": str(a.solar_run), "scaler": sscaler,
                  "features": scfg.get("future_features"),
                  "units": "W/m^2", },
        "note": ("Normalised columns use the scaler each model was TRAINED "
                 "under (fitted on training rows only). Invert with: "
                 "x = (x_norm - feature_min)/(feature_max - feature_min) "
                 "* (data_max - data_min) + data_min"),
    }
    (out / "scaler_params.json").write_text(json.dumps(params, indent=2, default=str))
    print(f"  {'scaler_params.json':<38} min-max parameters, both channels")
    print(f"\n-> {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
