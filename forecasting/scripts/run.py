"""
Entry point: one command trains a model, scores it against the
baselines, and writes everything needed to defend the result.

    python run.py --target load  --source ../aggregate_total_load.csv
    python run.py --target solar --source ../Stellies_2023.xlsx --model tcn

Every run writes a self-contained directory:

    runs/<name>/
        config.json        exact settings, enough to reproduce the run
        data_summary.txt   splits, window counts, scaling, what was dropped
        report.txt         the evaluation table and skill scores
        metrics.csv        one row per model, machine-readable
        per_step_mae.csv   error against lead time, for plotting
        predictions.csv    truth and every prediction, in real units
        history.csv        train/val loss per epoch
        model.pt           weights + scaler, usable on new data
        forecast.png       a sample day, truth vs model vs best baseline
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

from baselines import default_baselines  # noqa: E402
from config import ForecastConfig  # noqa: E402
from datasets import prepare, summarise  # noqa: E402
from evaluate import (add_skill, evaluation_mask, horizon_matrix,  # noqa: E402
                      inverse_scale, per_step_frame, report, score)
from probabilistic import quantile_metrics  # noqa: E402
from train import predict, predict_quantiles, save_checkpoint, train_model  # noqa: E402

ACCENT = "#2563eb"
TRUTH = "#111827"
BASELINE = "#a8481a"


def parse_args(argv=None) -> ForecastConfig:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", choices=("load", "solar"), default="load")
    p.add_argument("--source", required=True)
    p.add_argument("--column", default=None)
    p.add_argument("--model", choices=("cnn_lstm", "tcn"), default="cnn_lstm")
    p.add_argument("--residual", choices=("none", "daily", "weekly"), default="none",
                   help="learn the correction to a seasonal rule instead of the level")
    p.add_argument("--horizon", type=int, default=288)
    p.add_argument("--lookback", type=int, default=576)
    p.add_argument("--gap", type=int, default=0)
    p.add_argument("--stride", type=int, default=3)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--batch-size", type=int, default=64)
    # 1e-3 is the rate every reported result was trained at (benchmark.py).
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--hidden", type=int, default=128)
    p.add_argument("--conv-channels", type=int, nargs="+", default=None, metavar="C",
                   help="channels per conv block; default 32 32 for cnn_lstm and seven "
                        "blocks of 32 for tcn, whose receptive field then covers the "
                        "576-step lookback")
    p.add_argument("--patience", type=int, default=8)
    p.add_argument("--dropout", type=float, default=0.0)
    p.add_argument("--quantiles", type=float, nargs="+", default=[0.5],
                   metavar="Q",
                   help="quantile levels to predict with the pinball loss "
                        "(must include 0.5); e.g. --quantiles 0.1 0.5 0.9")
    p.add_argument("--features", nargs="+", default=["tod", "dow", "weekend"],
                   metavar="NAME",
                   help="decoder covariates: tod dow weekend holiday lag_1d lag_1w")
    p.add_argument("--no-encoder-calendar", action="store_true",
                   help="stop feeding calendar columns to the encoder history")
    p.add_argument("--use-doy", action="store_true",
                   help="add day-of-year features (needs 2+ years; harmful below that)")
    p.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--max-train-windows", type=int, default=None)
    p.add_argument("--run-name", default=None)
    p.add_argument("--out-dir", default="runs")
    a = p.parse_args(argv)

    return ForecastConfig(
        target=a.target, source=a.source, column=a.column, model=a.model,
        residual=a.residual,
        horizon=a.horizon, lookback=a.lookback, gap=a.gap, stride=a.stride,
        epochs=a.epochs, batch_size=a.batch_size, lr=a.lr, hidden=a.hidden,
        conv_channels=tuple(a.conv_channels) if a.conv_channels
        else ((32,) * 7 if a.model == "tcn" else (32, 32)),
        dropout=a.dropout, use_doy=a.use_doy, quantiles=tuple(a.quantiles),
        future_features=tuple(a.features),
        encoder_calendar=not a.no_encoder_calendar,
        patience=a.patience, seed=a.seed, device=a.device,
        max_train_windows=a.max_train_windows,
        run_name=a.run_name or f"{a.target}-{a.model}"
                 + ("" if a.residual == "none" else f"-res{a.residual}"),
        out_dir=a.out_dir)


def evaluate_all(spec, model, cfg, split: str = "test"):
    """Score the model and every applicable baseline on the same windows."""
    origins = spec.origins[split]
    mask = evaluation_mask(spec, origins)

    truth = inverse_scale(horizon_matrix(spec, origins, spec.values), spec)
    results, predictions = [], {}

    for name, baseline in default_baselines(spec).items():
        if not baseline.available(spec, origins):
            continue
        pred = inverse_scale(baseline.predict(spec, origins), spec)
        if cfg.target == "solar":
            pred = np.clip(pred, 0.0, None)
        predictions[name] = pred
        results.append(score(truth, pred, name, cfg, mask))

    model_pred = inverse_scale(predict(model, spec, split, cfg), spec)
    if cfg.target == "solar":
        # A physical floor the network has no reason to know about.
        model_pred = np.clip(model_pred, 0.0, None)
    predictions[cfg.model] = model_pred
    model_metrics = score(truth, model_pred, cfg.model, cfg, mask)

    results.insert(0, model_metrics)
    add_skill(results, [m.name for m in results if m.name != cfg.model])

    # Every quantile level, kept beside the median, for intervals downstream.
    quantiles = None
    if cfg.n_quantiles > 1:
        quantiles = inverse_scale(predict_quantiles(model, spec, split, cfg), spec)
        if cfg.target == "solar":
            quantiles = np.clip(quantiles, 0.0, None)
    return results, truth, predictions, mask, quantiles


def plot_sample_day(spec, truth, predictions, cfg, out: Path, split: str = "test") -> Path:
    """One full horizon, truth against the model and the best baseline."""
    origins = spec.origins[split]
    # Pick an origin at local midnight so the panel is a natural day.
    hours = spec.index[origins].hour.to_numpy()
    midnights = np.flatnonzero(hours == 0)
    pick = int(midnights[len(midnights) // 2]) if midnights.size else len(origins) // 2

    start = origins[pick] + cfg.gap
    when = spec.index[start:start + cfg.horizon]

    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=110)
    ax.plot(when, truth[pick], color=TRUTH, lw=1.6, label="actual")
    for name, pred in predictions.items():
        is_model = name == cfg.model
        ax.plot(when, pred[pick], lw=1.6 if is_model else 1.0,
                color=ACCENT if is_model else BASELINE,
                alpha=1.0 if is_model else 0.55,
                ls="-" if is_model else "--", label=name)

    unit = "kW" if cfg.target == "load" else "W/m²"
    ax.set_title(f"Day-ahead forecast — {cfg.target}  ({when[0]:%Y-%m-%d})",
                 fontsize=11, fontweight="bold", loc="left")
    ax.set_ylabel(f"{'Active power' if cfg.target == 'load' else 'GHI'} ({unit})")
    ax.set_xlabel("Time")
    ax.grid(alpha=0.25, lw=0.6)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.legend(fontsize=8, framealpha=0.9)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def main(argv=None) -> int:
    cfg = parse_args(argv)
    out_dir = Path(cfg.out_dir) / cfg.run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"→ {cfg.describe()}")
    spec = prepare(cfg)
    summary = summarise(spec)
    print()
    print(summary)
    (out_dir / "data_summary.txt").write_text(summary)
    cfg.save(out_dir / "config.json")

    print("\nTRAINING")
    model, history = train_model(spec, cfg)
    print()
    print(history.describe())
    pd.DataFrame({"epoch": np.arange(1, len(history.train_loss) + 1),
                  "train_loss": history.train_loss,
                  "val_loss": history.val_loss,
                  "seconds": history.epoch_seconds}).to_csv(
        out_dir / "history.csv", index=False)

    print("\nEVALUATING on the test split (touched once, now)")
    results, truth, predictions, mask, quantiles = evaluate_all(spec, model, cfg)
    text = report(results, spec, "test")
    if quantiles is not None:
        qm = quantile_metrics(truth, quantiles, cfg.quantiles, mask)
        unit = "kW" if cfg.target == "load" else "W/m²"
        text += ("\n\nPREDICTIVE INTERVALS  (test split; nominal coverage in brackets)\n\n"
                 + "".join(f"  {k[5:]:>3}% interval   coverage {qm[k]:.3f} ({int(k[5:]) / 100:.2f})"
                           f"   mean width {qm['width_' + k[5:]]:8.2f} {unit}"
                           f"   interval score {qm['interval_score_' + k[5:]]:8.2f}\n"
                           for k in sorted(x for x in qm if x.startswith("picp_")))
                 + f"  mean pinball loss {qm['pinball']:.3f} {unit}")
    print()
    print(text)
    (out_dir / "report.txt").write_text(text + "\n\n" + history.describe())

    pd.DataFrame([{
        "model": m.name, "mae": m.mae, "rmse": m.rmse, "mape": m.mape,
        "nmae": m.nmae, "r2": m.r2, "n": m.n,
        **{f"skill_vs_{k}": v for k, v in m.skill.items()},
    } for m in results]).to_csv(out_dir / "metrics.csv", index=False)
    per_step_frame(results, cfg).to_csv(out_dir / "per_step_mae.csv", index=False)

    origins = spec.origins["test"]
    flat = {"origin": np.repeat(spec.index[origins], cfg.horizon),
            "lead_step": np.tile(np.arange(1, cfg.horizon + 1), len(origins)),
            "actual": truth.ravel()}
    for name, pred in predictions.items():
        flat[name] = pred.ravel()
    if quantiles is not None:
        for j, q in enumerate(cfg.quantiles):
            flat[f"{cfg.model}_q{q:g}"] = quantiles[:, :, j].ravel()
    if mask is not None:
        flat["scored"] = mask.ravel()
    pd.DataFrame(flat).to_csv(out_dir / "predictions.csv", index=False)

    save_checkpoint(model, cfg, spec, out_dir / "model.pt")
    plot_sample_day(spec, truth, predictions, cfg, out_dir / "forecast.png")

    print(f"\n→ wrote {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
