"""
Forecast inputs for the MPC study: one 24-hour forecast of load and PV
power issued at EVERY 5-minute step of the held-out test block.

    python mpc_forecasts.py --load-source  ../aggregate_total_load.csv \
                            --solar-source ../stellies_mod.csv \
                            --out mpc_inputs
    python mpc_forecast_plots.py --study mpc_inputs

The MPC itself is built and run elsewhere; this produces its inputs.

THREE FORECAST TIERS
--------------------
  perfect     the measured future itself. The MPC lower bound: whatever
              cost remains is the cost of the controller, not of the
              forecast.
  best        the forecasts with the lowest test MAE on this block:
              CNN-LSTM (point) for load and the median of the CNN-LSTM
              quantile model for irradiance. The saved benchmark
              checkpoints are applied as they are - nothing is retrained.
  reference   the standard rules the networks were benchmarked against:
              similar-day average (4 weeks) for load and clear-sky
              persistence (24 h) for irradiance. Both are about 17-18 %
              worse than `best` in MAE, so the step from best to
              reference raises the error of the two signals by a
              similar amount.

WHY STRIDE 1
------------
The MPC re-solves every 5 minutes, so it needs a fresh forecast at every
step. The benchmark stored forecasts every 15 minutes (stride 3); here
the same checkpoints are run at stride 1. At the origins the two share,
the new forecasts are checked against the stored ones before anything
is written.

The block, the scaler and the window rules are exactly those of the F3
fold of the benchmark: the scaler comes from the training rows, every
input is known at the origin, and the test block was used for no model
decision.

IRRADIANCE TO PV POWER
----------------------
Every irradiance forecast, and the measured irradiance, passes through
the same PVWatts chain (pv_power.py) with the MEASURED ambient
temperature, so the three tiers differ only in the irradiance they
start from. There is no temperature forecast; that is stated in the
thesis as well.
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

from baselines import ClearSkyPersistence, SimilarDayAverage  # noqa: E402
from config import STEPS_PER_DAY, ForecastConfig  # noqa: E402
from datasets import prepare  # noqa: E402
from evaluate import evaluation_mask, horizon_matrix, inverse_scale  # noqa: E402
from models import build_model  # noqa: E402
from pv_power import pv_ac_power  # noqa: E402
from train import predict_quantiles, resolve_device  # noqa: E402

TIERS = ("perfect", "best", "reference")

# Which saved benchmark model is "best" for each target, and which rule
# is the reference. Chosen on the F3 test MAE of the stored forecasts
# (load 27.2 kW vs TCN 28.3; irradiance 155.1 W/m^2 vs the point model's
# 161.2), because the study asks for the lowest-error forecast on the
# period the MPC is run over.
BEST_CHECKPOINT = {"load": "compare_cnn_lstm.pt", "solar": "compare_cnn_lstm__quantile.pt"}
REFERENCE_RULE = {"load": lambda: SimilarDayAverage(weeks=4),
                  "solar": lambda: ClearSkyPersistence(window=STEPS_PER_DAY,
                                                       name="clear-sky persistence (24 h)")}
REFERENCE_NAME = {"load": "similar-day average (4 wk)",
                  "solar": "clear-sky persistence (24 h)"}
BEST_NAME = {"load": "CNN-LSTM", "solar": "CNN-LSTM (quantile), median"}


# =========================================================== checkpoints
def load_checkpoint(path: Path, source: str, device: str):
    """Rebuild the config, the prepared data and the model of a checkpoint."""
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    raw = dict(ckpt["config"])
    fields = ForecastConfig.__dataclass_fields__
    kw = {k: (tuple(v) if isinstance(v, list) else v) for k, v in raw.items() if k in fields}
    kw.update(source=source, stride=1, device=device)
    cfg = ForecastConfig(**kw)
    spec = prepare(cfg)

    # The scaler the model was trained under must be the one prepare()
    # rebuilds from the training rows, or the outputs would be mis-scaled.
    stored = next(iter(ckpt["scaler"]["channels"].values()))
    fresh = spec.scaler.channels[next(iter(spec.scaler.channels))]
    if not (np.isclose(stored["data_min"], fresh.data_min)
            and np.isclose(stored["data_max"], fresh.data_max)):
        raise SystemExit(f"{path.name}: checkpoint scaler [{stored['data_min']}, "
                         f"{stored['data_max']}] differs from the rebuilt one "
                         f"[{fresh.data_min}, {fresh.data_max}] - wrong source file?")
    if (spec.n_past_features, spec.n_future_features) != (ckpt["past_dim"], ckpt["future_dim"]):
        raise SystemExit(f"{path.name}: input dimensions do not match the checkpoint")

    model = build_model(cfg, ckpt["past_dim"], ckpt["future_dim"])
    model.load_state_dict(ckpt["state_dict"])
    model.to(resolve_device(cfg)).eval()
    return cfg, spec, model


def network_forecast(model, spec, cfg) -> np.ndarray:
    """Point forecast in real units: the model's median output."""
    q = predict_quantiles(model, spec, "test", cfg)            # (N, H, Q)
    pred = inverse_scale(q[:, :, cfg.median_index], spec)
    return np.clip(pred, 0.0, None) if cfg.target == "solar" else pred


def rule_forecast(rule, spec, cfg) -> np.ndarray:
    pred = inverse_scale(rule.predict(spec, spec.origins["test"]), spec)
    return np.clip(pred, 0.0, None) if cfg.target == "solar" else pred


def check_against_stored(target, origins_ns, pred, bench: Path, name: str) -> dict:
    """Stride-1 forecasts must reproduce the stored stride-3 ones."""
    stored = np.load(bench / target / "predictions" / name)
    pos = {t: i for i, t in enumerate(origins_ns)}
    rows = np.array([pos[t] for t in stored["origin_ns"]])
    # A quantile model's stored "point" is its median head BEFORE the
    # levels were sorted; the median used here is the sorted one, which
    # is the stored quantiles' middle column (and 0.04 W/m^2 better).
    ref = (stored["quantiles"][:, :, stored["quantiles"].shape[2] // 2]
           if "quantiles" in stored.files and "cnn_lstm" in name else stored["point"])
    diff = np.abs(pred[rows] - ref)
    mask = stored["mask"] if "mask" in stored.files else np.ones_like(diff, bool)
    out = {"stored_file": name, "n_shared_origins": int(len(rows)),
           "max_abs_diff": float(diff.max()),
           "stored_mae": float(np.abs(ref - stored["truth"])[mask].mean()),
           "rebuilt_mae": float(np.abs(pred[rows] - stored["truth"])[mask].mean())}
    # MPS and CPU kernels differ in the last bits; a real mismatch (wrong
    # features, wrong scaler, shifted origins) shows up as whole units.
    tol = 0.5 if target == "load" else 2.0
    if out["max_abs_diff"] > tol:
        raise SystemExit(f"{target}/{name}: rebuilt forecasts differ from the stored "
                         f"ones by up to {out['max_abs_diff']:.3f} - not the same model")
    return out


# ============================================================ assembling
def target_forecasts(target, source, bench, device):
    """Origins, truth and the best/reference forecasts, in real units."""
    cfg, spec, model = load_checkpoint(bench / target / "models" / BEST_CHECKPOINT[target],
                                       source, device)
    origins = spec.origins["test"]
    origins_ns = spec.index[origins].to_numpy().astype("datetime64[ns]").astype(np.int64)
    best = network_forecast(model, spec, cfg)
    ref = rule_forecast(REFERENCE_RULE[target](), spec, cfg)

    checks = {
        "best": check_against_stored(target, origins_ns, best, bench,
                                     BEST_CHECKPOINT[target].replace(".pt", ".npz")),
        "reference": check_against_stored(target, origins_ns, ref, bench,
                                          "compare_" + "".join(
                                              c if c.isalnum() else "_"
                                              for c in REFERENCE_NAME[target]).strip("_")
                                          + ".npz"),
    }
    truth = horizon_matrix(spec, origins, spec.raw)
    mask = evaluation_mask(spec, origins)
    return {"cfg": cfg, "spec": spec, "origins": origins, "origins_ns": origins_ns,
            "truth": truth, "best": best, "reference": ref, "mask": mask,
            "checks": checks}


def fill_missing_origins(origin_ns: np.ndarray, pred: np.ndarray,
                         wanted: pd.DatetimeIndex):
    """
    A forecast for every wanted origin.

    An origin is missing when its window held a missing reading (only
    irradiance has any). The controller then uses the latest forecast it
    has, shifted to the current step; the k steps that shifting leaves
    uncovered at the far end are filled from the same clock time one day
    earlier in that forecast, which is its own day-ahead shape.
    """
    have = pd.DatetimeIndex(pd.to_datetime(origin_ns))
    pos = pd.Series(np.arange(len(have)), index=have)
    out = np.empty((len(wanted), pred.shape[1]), dtype=np.float64)
    age = np.zeros(len(wanted), dtype=int)
    last = None
    for i, t in enumerate(wanted):
        if t in pos.index:
            last = int(pos[t])
            out[i] = pred[last]
            continue
        if last is None:
            raise SystemExit(f"no forecast available at or before {t}")
        k = int((t - have[last]) / pd.Timedelta("5min"))
        if k >= pred.shape[1]:
            raise SystemExit(f"forecast at {t} would be more than a day old")
        out[i, :-k] = pred[last, k:]
        out[i, -k:] = pred[last, :k]
        age[i] = k
    return out, age


def pv_matrix(ghi: np.ndarray, wanted: pd.DatetimeIndex, temp: pd.Series) -> np.ndarray:
    """PVWatts chain applied to every (origin, step) of a GHI matrix."""
    n, h = ghi.shape
    stamps = (wanted.to_numpy()[:, None]
              + (np.arange(h) * np.timedelta64(5, "m"))[None, :]).ravel()
    index = pd.DatetimeIndex(stamps)
    t_amb = temp.reindex(index).to_numpy()
    p = pv_ac_power(ghi.ravel(), t_amb, index)["p_ac_kw"].to_numpy()
    return p.reshape(n, h)


# =========================================================== CSV export
def wide_csv(path: Path, wanted: pd.DatetimeIndex, mat: np.ndarray, decimals: int = 2) -> None:
    """One row per forecast origin, one column per lead step (h001 = origin)."""
    cols = [f"h{j + 1:03d}" for j in range(mat.shape[1])]
    df = pd.DataFrame(np.round(mat, decimals), columns=cols)
    df.insert(0, "origin", wanted.strftime("%Y-%m-%d %H:%M"))
    df.to_csv(path, index=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--load-source", required=True)
    ap.add_argument("--solar-source", required=True,
                    help="re-dated irradiance record with GHI and temperature (stellies_mod.csv)")
    ap.add_argument("--bench", default="benchmark")
    ap.add_argument("--out", default="mpc_inputs")
    ap.add_argument("--start", default="2002-08-28 00:00",
                    help="first MPC step (default: first midnight with a full test window)")
    ap.add_argument("--end", default="2002-09-29 23:55", help="last MPC step")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--no-wide-csv", action="store_true",
                    help="skip the per-origin CSV matrices (about 20 MB each)")
    a = ap.parse_args(argv)

    bench, out = Path(a.bench), Path(a.out)
    for sub in ("csv", "metrics"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    wanted = pd.date_range(a.start, a.end, freq="5min")

    load = target_forecasts("load", a.load_source, bench, a.device)
    solar = target_forecasts("solar", a.solar_source, bench, a.device)
    print(f"load:  {len(load['origins']):,} test origins at stride 1, "
          f"best max |diff| vs stored {load['checks']['best']['max_abs_diff']:.4f} kW")
    print(f"solar: {len(solar['origins']):,} test origins at stride 1, "
          f"best max |diff| vs stored {solar['checks']['best']['max_abs_diff']:.4f} W/m2")

    # Measured series on the full grid: the "perfect" forecasts and the plant.
    load_series = pd.Series(load["spec"].raw, index=load["spec"].index)
    src = pd.read_csv(a.solar_source, parse_dates=["timestamp"]).set_index("timestamp")
    gcol = next(c for c in src.columns if "ghi" in str(c).lower())
    tcol = next(c for c in src.columns if "temp" in str(c).lower())
    ghi_series = pd.Series(solar["spec"].raw, index=solar["spec"].index)
    temp = src[tcol].astype(float).reindex(ghi_series.index).ffill().bfill()
    pv_series = pd.Series(pv_ac_power(ghi_series.to_numpy(), temp.to_numpy(),
                                      ghi_series.index)["p_ac_kw"].to_numpy(),
                          index=ghi_series.index)

    last_needed = wanted[-1] + pd.Timedelta(minutes=5 * (STEPS_PER_DAY - 1))
    if last_needed > load_series.index[-1]:
        raise SystemExit(f"--end {a.end}: the last horizon would run past the record")

    def perfect(series):
        h = np.arange(STEPS_PER_DAY)
        rows = series.index.get_indexer(wanted)
        return series.to_numpy()[rows[:, None] + h[None, :]]

    mats = {"perfect": {"load_kw": perfect(load_series), "pv_kw": perfect(pv_series),
                        "ghi_wm2": perfect(ghi_series.fillna(0.0))}}
    age = {}
    for tier in ("best", "reference"):
        lk, la = fill_missing_origins(load["origins_ns"], load[tier], wanted)
        gk, ga = fill_missing_origins(solar["origins_ns"], solar[tier], wanted)
        age[tier] = {"load_filled_origins": int((la > 0).sum()),
                     "solar_filled_origins": int((ga > 0).sum()),
                     "solar_max_age_steps": int(ga.max())}
        mats[tier] = {"load_kw": lk, "ghi_wm2": gk, "pv_kw": pv_matrix(gk, wanted, temp)}
    # Net load is what the dispatchable units (generator and battery) have
    # to cover, so its error is the one the controller actually feels.
    for tier in TIERS:
        mats[tier]["net_kw"] = mats[tier]["load_kw"] - mats[tier]["pv_kw"]

    # ------------------------------------------------------ forecast skill
    rows = []
    for tier in TIERS:
        for sig in ("load_kw", "pv_kw", "net_kw", "ghi_wm2"):
            err = mats[tier][sig] - mats["perfect"][sig]
            day = (mats["perfect"]["ghi_wm2"] > 0 if sig in ("pv_kw", "ghi_wm2")
                   else np.ones_like(err, bool))
            rows.append({"tier": tier, "signal": sig,
                         "mae": float(np.abs(err).mean()),
                         "rmse": float(np.sqrt((err ** 2).mean())),
                         "bias": float(err.mean()),
                         "mae_daylight": float(np.abs(err[day]).mean()),
                         "mae_first_hour": float(np.abs(err[:, :12]).mean())})
    skill = pd.DataFrame(rows)
    skill.to_csv(out / "metrics" / "forecast_error_summary.csv", index=False,
                 float_format="%.4f")
    lead = {"lead_step": np.arange(1, STEPS_PER_DAY + 1),
            "lead_minutes": np.arange(STEPS_PER_DAY) * 5}
    for tier in ("best", "reference"):
        for sig in ("load_kw", "pv_kw", "net_kw"):
            lead[f"{tier}_{sig}_mae"] = np.abs(mats[tier][sig] - mats["perfect"][sig]).mean(axis=0)
    pd.DataFrame(lead).to_csv(out / "metrics" / "forecast_mae_by_lead.csv", index=False,
                              float_format="%.4f")

    # --------------------------------------------------------- write
    np.savez_compressed(out / "forecasts.npz",
                        origin_ns=wanted.to_numpy().astype("datetime64[ns]").astype(np.int64),
                        **{f"{t}__{s}": m.astype(np.float32)
                           for t, d in mats.items() for s, m in d.items()})
    actual = pd.DataFrame({"timestamp": load_series.index, "load_kw": load_series.to_numpy(),
                           "ghi_wm2": ghi_series.to_numpy(), "temp_amb_c": temp.to_numpy(),
                           "pv_kw": pv_series.to_numpy()})
    actual.to_csv(out / "csv" / "measured_series.csv", index=False, float_format="%.4f")
    if not a.no_wide_csv:
        for tier in TIERS:
            for sig in ("load_kw", "pv_kw"):
                wide_csv(out / "csv" / f"{tier}_{sig}.csv", wanted, mats[tier][sig])

    meta = {
        "period": [str(wanted[0]), str(wanted[-1])], "n_origins": len(wanted),
        "horizon_steps": STEPS_PER_DAY, "step_minutes": 5,
        "tiers": {"perfect": "measured future",
                  "best": {"load": BEST_NAME["load"], "solar": BEST_NAME["solar"]},
                  "reference": {"load": REFERENCE_NAME["load"],
                                "solar": REFERENCE_NAME["solar"]}},
        "checkpoints": {t: str(bench / t / "models" / BEST_CHECKPOINT[t]) for t in ("load", "solar")},
        "sources": {"load": a.load_source, "solar": a.solar_source},
        "consistency_with_benchmark": {"load": load["checks"], "solar": solar["checks"]},
        "missing_origins_filled": age,
        "pv_chain": "pv_power.pv_ac_power with measured ambient temperature",
        "torch": torch.__version__,
    }
    (out / "forecast_meta.json").write_text(json.dumps(meta, indent=2))
    print(skill.to_string(index=False, float_format=lambda v: f"{v:9.3f}"))
    print(json.dumps(age, indent=2))
    print(f"-> {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
