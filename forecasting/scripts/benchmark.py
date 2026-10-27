"""
The final benchmark: every model variant, on every rolling-origin fold,
over several seeds, against every baseline - one resumable command.

    python benchmark.py --target load  --source ../aggregate_total_load.csv
    python benchmark.py --target solar --source ../stellies_mod.csv

STAGES
------
1. select   Each candidate feature set, CNN-LSTM, seed 0, every fold. The
            winner is the lowest MEAN VALIDATION MAE across folds. Test is
            recorded for the write-up but never consulted - on the single
            split, validation and test ranked the solar candidates almost
            in reverse, which is exactly why one split cannot choose.
2. compare  With the winning features: CNN-LSTM (point), CNN-LSTM
            (quantile) and TCN, on every fold x every seed. The baselines,
            the linear model and the reference interval run once per fold;
            they are deterministic.

WHY FOLDS AND SEEDS
-------------------
One split answers "how good was this model on Aug 27 - Sep 30", not "how
good is this model". Three folds put test in winter, late winter and
spring; three seeds separate what the architecture does from what one
lucky initialisation did. A difference smaller than the seed spread is
not a difference.

Results append to <out>/results.jsonl one unit at a time, so an
interrupted run resumes where it stopped. The last fold with the first
seed also keeps its predictions and checkpoint, for the figures and the
PowerFactory export.
"""
from __future__ import annotations

import argparse
import copy
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from baselines import default_baselines  # noqa: E402
from config import ForecastConfig  # noqa: E402
from datasets import prepare  # noqa: E402
from evaluate import evaluation_mask, horizon_matrix, inverse_scale, score  # noqa: E402
from linear_model import LinearDirect  # noqa: E402
from models import count_parameters  # noqa: E402
from probabilistic import (QUANTILE_LEVELS, EmpiricalResidualIntervals,  # noqa: E402
                           quantile_metrics)
from train import (WindowBatcher, predict, predict_quantiles,  # noqa: E402
                   save_checkpoint, train_model)

# The final training recipe - the one that produced the reported results.
COMMON = dict(horizon=288, lookback=576, stride=3, hidden=128, decoder_hidden=64,
              conv_channels=(32, 32), kernel_size=5, dropout=0.0, lr=1e-3,
              weight_decay=1e-5, epochs=30, patience=8, batch_size=64)

# Stage 1 candidates. Load: the three leaders of the single-split ablation
# (A-C, G-I were dominated there by margins far beyond seed noise). Solar:
# every set without day-of-year, which is harmful on < 1 year of data.
CANDIDATES = {
    "load": [("D  tod+dow+holiday", ("tod", "dow", "holiday")),
             ("E  D+lag_1w", ("tod", "dow", "holiday", "lag_1w")),
             ("F  E+lag_1d", ("tod", "dow", "holiday", "lag_1w", "lag_1d"))],
    "solar": [("S1  tod", ("tod",)),
              ("S2  tod+lag_1d", ("tod", "lag_1d")),
              ("S3  tod+lag_1w", ("tod", "lag_1w")),
              ("S4  tod+lag_1d+lag_1w", ("tod", "lag_1d", "lag_1w"))],
}

# Seven blocks of kernel 5 with dilations 1, 2, ..., 64 give a receptive
# field of 1 + 2(5 - 1)(1 + 2 + ... + 64) = 1,017 steps, which covers the
# 576-step lookback. The two-block TCN used before saw 25 steps (about two
# hours) of a 48-hour history - a comparison it could not win.
TCN_CHANNELS = (32,) * 7

VARIANTS = {
    "CNN-LSTM": dict(model="cnn_lstm"),
    "CNN-LSTM (quantile)": dict(model="cnn_lstm", quantiles=QUANTILE_LEVELS),
    "TCN": dict(model="tcn", conv_channels=TCN_CHANNELS),
}


# ===================================================================== store
class Store:
    """Append-only JSONL of finished units; the key makes a rerun resume."""

    def __init__(self, path: Path):
        self.path = path
        self.records = []
        if path.exists():
            self.records = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        self.done = {self.key(r["stage"], r["variant"], r["fold"], r.get("seed"))
                     for r in self.records}

    @staticmethod
    def key(stage, variant, fold, seed) -> str:
        return f"{stage}|{variant}|{fold}|{seed}"

    def has(self, *k) -> bool:
        return self.key(*k) in self.done

    def append(self, rec: dict) -> None:
        with self.path.open("a") as fh:
            fh.write(json.dumps(rec, default=_jsonable) + "\n")
        self.records.append(rec)
        self.done.add(self.key(rec["stage"], rec["variant"], rec["fold"], rec.get("seed")))


def _jsonable(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, tuple):
        return list(o)
    return str(o)


def slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


# ================================================================ evaluation
def truth_mask(spec, split):
    origins = spec.origins[split]
    return (inverse_scale(horizon_matrix(spec, origins, spec.values), spec),
            evaluation_mask(spec, origins))


def floor(pred, cfg):
    """Irradiance cannot be negative; nothing here is told that."""
    return np.clip(pred, 0.0, None) if cfg.target == "solar" else pred


def point_metrics(truth, pred, cfg, mask) -> dict:
    m = score(truth, pred, "x", cfg, mask)
    return {"mae": m.mae, "rmse": m.rmse, "mape": m.mape, "nmae": m.nmae,
            "r2": m.r2, "n": m.n, "per_step_mae": m.per_step_mae}


def split_block(rec: dict, split: str, metrics: dict) -> None:
    per_step = metrics.pop("per_step_mae")
    rec[split] = metrics
    if split == "test":
        rec["per_step_mae"] = per_step


def latency(model, spec, cfg) -> dict:
    """One forecast on the CPU (what a deployment box has) and the whole
    test split on the training device."""
    cpu = copy.deepcopy(model).to("cpu").eval()
    past, future, _ = next(WindowBatcher(spec, "test", cfg, torch.device("cpu"))
                           .batches(shuffle=False))
    past, future = past[:1], future[:1]
    with torch.no_grad():
        for _ in range(5):
            cpu(past, future)
        times = []
        for _ in range(50):
            t0 = time.perf_counter()
            cpu(past, future)
            times.append(time.perf_counter() - t0)
    t0 = time.perf_counter()
    predict(model, spec, "test", cfg)
    batch_s = time.perf_counter() - t0
    return {"cpu_single_ms": 1000 * float(np.median(times)),
            "device_test_split_s": batch_s,
            "n_test_forecasts": int(len(spec.origins["test"]))}


# ===================================================================== units
def make_cfg(a, features, fold, seed, **over) -> ForecastConfig:
    kw = {**COMMON, **a.common_override, **over}
    return ForecastConfig(target=a.target, source=a.source, future_features=features,
                          cv_folds=a.folds, cv_fold=fold, seed=seed, device=a.device,
                          run_name="benchmark", **kw)


def run_network(a, store, stage, variant, features, fold, seed, over, keep: bool):
    if store.has(stage, variant, fold, seed):
        return
    cfg = make_cfg(a, features, fold, seed, **over)
    spec = prepare(cfg)
    t0 = time.time()
    model, hist = train_model(spec, cfg, verbose=False)
    wall = time.time() - t0

    rec = {"stage": stage, "variant": variant, "fold": fold, "seed": seed,
           "features": list(features), "model": cfg.model,
           "params": count_parameters(model), "epochs": len(hist.train_loss),
           "best_epoch": hist.best_epoch + 1, "train_seconds": float(sum(hist.epoch_seconds)),
           "wall_seconds": wall, "device": hist.device,
           "history": {"train": hist.train_loss, "val": hist.val_loss},
           "test_period": [str(spec.index[spec.origins["test"][0]]),
                           str(spec.index[spec.origins["test"][-1]])],
           "n_train_windows": int(len(spec.origins["train"]))}

    preds = {}
    for split in ("val", "test"):
        truth, mask = truth_mask(spec, split)
        pred = floor(inverse_scale(predict(model, spec, split, cfg), spec), cfg)
        split_block(rec, split, point_metrics(truth, pred, cfg, mask))
        preds[split] = (truth, pred, mask)

    q = None
    if cfg.n_quantiles > 1:
        truth, mask = truth_mask(spec, "test")
        q = floor(inverse_scale(predict_quantiles(model, spec, "test", cfg), spec), cfg)
        rec["prob"] = quantile_metrics(truth, q, cfg.quantiles, mask)
        rec["quantiles"] = list(cfg.quantiles)

    if keep:
        rec["latency"] = latency(model, spec, cfg)
        name = slug(f"{stage}_{variant}")
        (a.out / "models").mkdir(exist_ok=True)
        save_checkpoint(model, cfg, spec, a.out / "models" / f"{name}.pt")
        rec["checkpoint_bytes"] = (a.out / "models" / f"{name}.pt").stat().st_size
        save_predictions(a, spec, name, preds["test"][1], q)

    store.append(rec)
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    tm = rec["test"]
    print(f"  {stage:<7} {variant:<22} fold {fold} seed {seed}  "
          f"val MAE {rec['val']['mae']:8.3f}  test MAE {tm['mae']:8.3f}  "
          f"[{rec['epochs']} ep, {wall:.0f}s]", flush=True)


def run_reference(a, store, features, fold, keep: bool):
    """Baselines, the linear model and the reference interval, once per fold."""
    cfg = make_cfg(a, features, fold, 0)     # seed only fixes any subsampling
    spec = prepare(cfg)
    val_truth, val_mask = truth_mask(spec, "val")
    test_truth, test_mask = truth_mask(spec, "test")

    # Point rules, plus the linear model fitted on train.
    candidates = list(default_baselines(spec).values())
    linear = LinearDirect().fit(spec, "train")
    candidates.append(linear)

    val_mae = {}
    for b in candidates:
        if not (b.available(spec, spec.origins["val"]) and b.available(spec, spec.origins["test"])):
            continue
        val_pred = floor(inverse_scale(b.predict(spec, spec.origins["val"]), spec), cfg)
        test_pred = floor(inverse_scale(b.predict(spec, spec.origins["test"]), spec), cfg)
        val_m = point_metrics(val_truth, val_pred, cfg, val_mask)
        val_mae[b.name] = (val_m["mae"], b, val_pred, test_pred)
        if store.has("compare", b.name, fold, None):
            continue
        rec = {"stage": "compare", "variant": b.name, "fold": fold, "seed": None,
               "features": list(features),
               "kind": "linear" if b is linear else "baseline",
               "test_period": [str(spec.index[spec.origins["test"][0]]),
                               str(spec.index[spec.origins["test"][-1]])]}
        if b is linear:
            rec["params"] = int(linear.coef.size)
        split_block(rec, "val", val_m)
        split_block(rec, "test", point_metrics(test_truth, test_pred, cfg, test_mask))
        store.append(rec)
        if keep:
            save_predictions(a, spec, slug(f"compare_{b.name}"), test_pred, None)
        print(f"  compare {b.name:<30} fold {fold}            "
              f"val MAE {rec['val']['mae']:8.3f}  test MAE {rec['test']['mae']:8.3f}",
              flush=True)

    # The reference interval wraps the strongest RULE on validation (the
    # linear model is a competitor, not a baseline) in its own error
    # quantiles, estimated on validation and scored on test.
    ref_name = "reference interval"
    if store.has("compare", ref_name, fold, None):
        return
    rules = {k: v for k, v in val_mae.items() if k != linear.name}
    best = min(rules, key=lambda k: rules[k][0])
    _, _, val_pred, test_pred = rules[best]
    hours = spec.index.hour.to_numpy()
    eri = EmpiricalResidualIntervals(QUANTILE_LEVELS,
                                     floor=0.0 if cfg.target == "solar" else None)
    eri.fit(val_truth, val_pred, horizon_matrix(spec, spec.origins["val"], hours), val_mask)
    q = eri.predict(test_pred, horizon_matrix(spec, spec.origins["test"], hours))
    rec = {"stage": "compare", "variant": ref_name, "fold": fold, "seed": None,
           "kind": "reference", "wraps": best, "quantiles": list(QUANTILE_LEVELS),
           "prob": quantile_metrics(test_truth, q, QUANTILE_LEVELS, test_mask)}
    store.append(rec)
    if keep:
        save_predictions(a, spec, slug(f"compare_{ref_name}"), test_pred, q)
    print(f"  compare {ref_name:<30} fold {fold}   wraps {best}; 80% PICP "
          f"{rec['prob'].get('picp_80', float('nan')):.3f}", flush=True)


def save_predictions(a, spec, name, point, q) -> None:
    """Test-split forecasts of the kept fold, for the report figures."""
    origins = spec.origins["test"]
    d = a.out / "predictions"
    d.mkdir(exist_ok=True)
    truth, mask = truth_mask(spec, "test")
    payload = {"origin_ns": spec.index[origins].to_numpy().astype("datetime64[ns]").astype(np.int64), "truth": truth.astype(np.float32),
               "point": point.astype(np.float32)}
    if mask is not None:
        payload["mask"] = mask
    if q is not None:
        payload["quantiles"] = q.astype(np.float32)
    np.savez_compressed(d / f"{name}.npz", **payload)


# ===================================================================== stages
def select_features(a, store):
    print(f"\nSTAGE 1 - feature selection ({a.target}), seed {a.seeds[0]}, "
          f"{a.folds} folds", flush=True)
    for fold in range(a.folds):
        for label, feats in CANDIDATES[a.target]:
            run_network(a, store, "select", label, feats, fold, a.seeds[0],
                        VARIANTS["CNN-LSTM"], keep=False)

    rows = {}
    for label, feats in CANDIDATES[a.target]:
        recs = [r for r in store.records if r["stage"] == "select" and r["variant"] == label]
        if len(recs) == a.folds:
            rows[label] = (float(np.mean([r["val"]["mae"] for r in recs])), feats)
    winner = min(rows, key=lambda k: rows[k][0])
    out = {"winner": winner, "features": list(rows[winner][1]),
           "mean_val_mae": {k: v[0] for k, v in rows.items()},
           "rule": "lowest mean validation MAE across folds; test not consulted"}
    (a.out / "selection.json").write_text(json.dumps(out, indent=2))
    print(f"  -> selected {winner}  {rows[winner][1]}", flush=True)
    return tuple(rows[winner][1])


def compare(a, store, features):
    print(f"\nSTAGE 2 - comparison ({a.target}), features {features}", flush=True)
    last = a.folds - 1
    for fold in range(a.folds):
        run_reference(a, store, features, fold, keep=(fold == last))
    for seed in a.seeds:
        for fold in range(a.folds):
            for variant in a.variants:
                run_network(a, store, "compare", variant, features, fold, seed,
                            VARIANTS[variant], keep=(fold == last and seed == a.seeds[0]))


def timing(a, features):
    """
    Clean cost figures for the report: one run per variant on the last
    fold with the first seed, with nothing else running on the machine.

    The benchmark's own per-run times are not used for this: the two
    targets were trained concurrently to halve the wall-clock time, so
    each run shared the GPU with another. Epoch counts and accuracy are
    unaffected by that (training is deterministic); seconds are not.
    """
    print(f"\nTIMING ({a.target}) - run this with nothing else on the machine", flush=True)
    out = {}
    for variant in a.variants:
        cfg = make_cfg(a, features, a.folds - 1, a.seeds[0], **VARIANTS[variant])
        spec = prepare(cfg)
        model, hist = train_model(spec, cfg, verbose=False)
        out[variant] = {"epochs": len(hist.train_loss),
                        "train_seconds": float(sum(hist.epoch_seconds)),
                        "sec_per_epoch": float(np.mean(hist.epoch_seconds)),
                        "params": count_parameters(model), "device": hist.device,
                        "latency": latency(model, spec, cfg)}
        print(f"  {variant:<22} {out[variant]['sec_per_epoch']:.2f} s/epoch, "
              f"{out[variant]['epochs']} epochs, "
              f"{out[variant]['latency']['cpu_single_ms']:.1f} ms CPU latency", flush=True)
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    (a.out / "timing.json").write_text(json.dumps(out, indent=2, default=_jsonable))
    return out


# ======================================================================= main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=("load", "solar"), required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", default=None, help="default: benchmark/<target>")
    ap.add_argument("--stage", choices=("all", "select", "compare", "timing"), default="all")
    ap.add_argument("--features", nargs="+", default=None,
                    help="skip selection and use these decoder covariates")
    ap.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--device", default="auto")
    ap.add_argument("--quick", action="store_true",
                    help="tiny settings to exercise the harness end to end")
    a = ap.parse_args(argv)

    a.common_override = {}
    if a.quick:
        a.common_override = dict(epochs=1, stride=48, max_train_windows=200, hidden=16,
                                 decoder_hidden=16)
        a.seeds = a.seeds[:1]
    a.out = Path(a.out or f"benchmark/{a.target}{'-quick' if a.quick else ''}")
    a.out.mkdir(parents=True, exist_ok=True)

    meta_path = a.out / ("meta_timing.json" if a.stage == "timing" else "meta.json")
    meta_path.write_text(json.dumps({
        "target": a.target, "source": a.source, "folds": a.folds, "seeds": a.seeds,
        "common": {**COMMON, **a.common_override}, "tcn_channels": TCN_CHANNELS,
        "quantiles": QUANTILE_LEVELS, "torch": torch.__version__,
        "python": platform.python_version(), "machine": platform.machine(),
        "platform": platform.platform(), "quick": a.quick,
    }, indent=2, default=_jsonable))

    store = Store(a.out / "results.jsonl")
    if a.features:
        features = tuple(a.features)
    elif a.stage in ("compare", "timing") and (a.out / "selection.json").exists():
        features = tuple(json.loads((a.out / "selection.json").read_text())["features"])
    else:
        features = select_features(a, store)
        if a.stage == "select":
            return 0
    if a.stage == "timing":
        timing(a, features)
        return 0
    compare(a, store, features)
    print(f"\n-> {a.out}/results.jsonl  ({len(store.records)} units)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
