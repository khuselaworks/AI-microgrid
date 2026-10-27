"""
Feature selection, step 2: train with each candidate set and compare.

The statistical pass in `feature_eda.py` can only score features one at a
time, and the most important effect in this series is an interaction -
day-of-week changes the SHAPE of the daily profile without moving its
mean, which no univariate score can see. So the decision is made here,
by training.

    python feature_ablation.py --source ../aggregate_total_load.csv

Selection is on the VALIDATION split. Test numbers are printed for the
shortlist only, and are not used to choose - picking a feature set on
test would turn the test score into a training score.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from baselines import default_baselines  # noqa: E402
from config import ForecastConfig  # noqa: E402
from datasets import prepare  # noqa: E402
from evaluate import evaluation_mask, horizon_matrix, inverse_scale, score  # noqa: E402
from train import predict, train_model  # noqa: E402

# Irradiance has no weekly cycle - the sun does not know it is Sunday -
# so dow, weekend and holiday are not candidates here. clear_sky and
# is_daylight are added automatically when target="solar".
SOLAR_TRIALS = [
    ("S1  tod only",           ("tod",), {}),
    ("S2  tod+lag_1d",         ("tod", "lag_1d"), {}),
    ("S3  tod+lag_1w",         ("tod", "lag_1w"), {}),
    ("S4  tod+lag_1d+lag_1w",  ("tod", "lag_1d", "lag_1w"), {}),
    ("S5  S2+doy",             ("tod", "lag_1d"), {"use_doy": True}),
]

# name -> (future_features, extra config overrides)
TRIALS = [
    ("A  tod only",                 ("tod",), {}),
    ("B  tod+dow",                  ("tod", "dow"), {}),
    ("C  tod+dow+weekend",          ("tod", "dow", "weekend"), {}),          # current default
    ("D  tod+dow+holiday",          ("tod", "dow", "holiday"), {}),
    ("E  D+lag_1w",                 ("tod", "dow", "holiday", "lag_1w"), {}),
    ("F  E+lag_1d",                 ("tod", "dow", "holiday", "lag_1w", "lag_1d"), {}),
    ("G  E+doy",                    ("tod", "dow", "holiday", "lag_1w"), {"use_doy": True}),
    ("H  E, no encoder calendar",   ("tod", "dow", "holiday", "lag_1w"),
                                    {"encoder_calendar": False}),
    ("I  D + residual weekly",      ("tod", "dow", "holiday"), {"residual": "weekly"}),
]


def evaluate_split(spec, model, cfg, split):
    """MAE and RMSE in real units on one split, using the standard mask."""
    origins = spec.origins[split]
    mask = evaluation_mask(spec, origins)
    truth = inverse_scale(horizon_matrix(spec, origins, spec.values), spec)
    pred = inverse_scale(predict(model, spec, split, cfg), spec)
    if cfg.target == "solar":
        pred = np.clip(pred, 0.0, None)
    m = score(truth, pred, cfg.model, cfg, mask)
    return m.mae, m.rmse


def baseline_scores(spec, cfg, split):
    origins = spec.origins[split]
    mask = evaluation_mask(spec, origins)
    truth = inverse_scale(horizon_matrix(spec, origins, spec.values), spec)
    out = {}
    for name, b in default_baselines(spec).items():
        if not b.available(spec, origins):
            continue
        pred = inverse_scale(b.predict(spec, origins), spec)
        if cfg.target == "solar":
            pred = np.clip(pred, 0.0, None)
        m = score(truth, pred, name, cfg, mask)
        out[name] = (m.mae, m.rmse)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", default="load")
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--patience", type=int, default=5)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="feature_ablation")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(exist_ok=True)
    rows = []

    trials = SOLAR_TRIALS if a.target == "solar" else TRIALS
    for label, feats, overrides in trials:
        cfg = ForecastConfig(
            target=a.target, source=a.source, future_features=feats,
            horizon=288, lookback=576, stride=3, hidden=a.hidden, dropout=0.0,
            lr=a.lr, epochs=a.epochs, patience=a.patience, seed=a.seed,
            device=a.device, **overrides)
        spec = prepare(cfg)
        t0 = time.time()
        model, hist = train_model(spec, cfg, verbose=False)
        secs = time.time() - t0

        val_mae, val_rmse = evaluate_split(spec, model, cfg, "val")
        test_mae, test_rmse = evaluate_split(spec, model, cfg, "test")
        rows.append({
            "trial": label, "features": " ".join(feats),
            "use_doy": cfg.use_doy, "encoder_calendar": cfg.encoder_calendar,
            "residual": cfg.residual,
            "val_mae": val_mae, "val_rmse": val_rmse,
            "test_mae": test_mae, "test_rmse": test_rmse,
            "epochs": len(hist.train_loss), "seconds": secs,
        })
        print(f"  {label:<28} val MAE {val_mae:7.3f}  val RMSE {val_rmse:7.3f}   "
              f"test MAE {test_mae:7.3f}  test RMSE {test_rmse:7.3f}   "
              f"[{len(hist.train_loss)} ep, {secs:.0f}s]", flush=True)

    frame = pd.DataFrame(rows).sort_values("val_mae").reset_index(drop=True)
    frame.to_csv(out / "results.csv", index=False)

    # Baselines, for context. Computed on the default feature spec since
    # they only read the target series.
    base_cfg = ForecastConfig(target=a.target, source=a.source,
                              horizon=288, lookback=576, stride=3)
    base_spec = prepare(base_cfg)
    print("\nBASELINES (test split)")
    for name, (mae, rmse) in baseline_scores(base_spec, base_cfg, "test").items():
        print(f"  {name:<26} MAE {mae:7.3f}   RMSE {rmse:7.3f}")

    print("\nRANKED BY VALIDATION MAE  (selection is on val; test shown for context)")
    print(frame[["trial", "val_mae", "val_rmse", "test_mae", "test_rmse",
                 "epochs"]].to_string(index=False))
    print(f"\n-> wrote {out}/results.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
