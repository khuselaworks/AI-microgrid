"""
Compare two completed runs: headline error, error against lead time, and
a sample day.

    python compare_runs.py runs/load-baseline runs/load-improved

Reads each run's `predictions.csv` (truth and every prediction, in real
units, one row per origin x lead step) so nothing is recomputed and the
figures cannot disagree with the reports they came from.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Validated categorical slots 1 and 2, plus ink and a recessive gray.
# Colour follows the ENTITY across every figure here: the improved model
# is always blue, the previous feature set always orange.
INK = "#0b0b0b"
SECONDARY = "#52514e"
IMPROVED = "#2a78d6"
PREVIOUS = "#eb6834"
MUTED = "#9a9992"
GRID = "#d9d8d2"
SURFACE = "#fcfcfb"


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, lw=0.6, alpha=0.9)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=SECONDARY, labelsize=9, length=0)
    return ax


def load_run(path: Path):
    pred = pd.read_csv(path / "predictions.csv", parse_dates=["origin"])
    cfg = json.loads((path / "config.json").read_text())
    if "scored" in pred.columns:
        pred = pred[pred["scored"].astype(bool)]
    return pred, cfg


def rmse(a, b):
    d = np.asarray(a, float) - np.asarray(b, float)
    d = d[np.isfinite(d)]
    return float(np.sqrt(np.mean(d ** 2)))


def mae(a, b):
    d = np.abs(np.asarray(a, float) - np.asarray(b, float))
    d = d[np.isfinite(d)]
    return float(np.mean(d))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("previous")
    ap.add_argument("improved")
    ap.add_argument("--label-previous", default="previous features")
    ap.add_argument("--label-improved", default="improved features")
    ap.add_argument("--out", default="comparison")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(exist_ok=True)

    prev, prev_cfg = load_run(Path(a.previous))
    impr, impr_cfg = load_run(Path(a.improved))
    model_col = prev_cfg["model"]
    unit = "kW" if prev_cfg["target"] == "load" else "W/m²"

    baseline_cols = [c for c in prev.columns
                     if c not in ("origin", "lead_step", "actual", "scored", model_col)]
    # The strongest baseline is the honest yardstick, so find it rather
    # than quoting whichever is most flattering.
    best_base = min(baseline_cols, key=lambda c: rmse(prev["actual"], prev[c]))

    rows = []
    for name, frame, col in (
            (best_base, prev, best_base),
            (a.label_previous, prev, model_col),
            (a.label_improved, impr, model_col)):
        rows.append({"model": name,
                     "MAE": mae(frame["actual"], frame[col]),
                     "RMSE": rmse(frame["actual"], frame[col])})
    table = pd.DataFrame(rows)
    base_rmse = table.loc[table["model"] == best_base, "RMSE"].iloc[0]
    table["skill_vs_baseline"] = 1 - table["RMSE"] / base_rmse
    table.to_csv(out / "summary.csv", index=False)

    prev_rmse = table["RMSE"].iloc[1]
    impr_rmse = table["RMSE"].iloc[2]
    delta = prev_rmse - impr_rmse

    print(f"\nTEST-SPLIT ERROR ({unit})")
    print(table.to_string(index=False, float_format=lambda v: f"{v:8.3f}"))
    verb = "lower" if delta > 0 else "higher"
    print(f"\nRMSE {prev_rmse:.3f} -> {impr_rmse:.3f} {unit}   "
          f"({abs(delta):.3f} {unit} {verb}, {-delta / prev_rmse:+.1%})")

    # ---------------------------------------------- 1. RMSE summary bars
    fig, ax = plt.subplots(figsize=(9, 3.2), dpi=140)
    style(ax)
    names = list(table["model"])
    vals = list(table["RMSE"])
    colours = [MUTED, PREVIOUS, IMPROVED]
    bars = ax.barh(names[::-1], vals[::-1], color=colours[::-1], height=0.55)
    for bar, v in zip(bars, vals[::-1]):
        ax.text(v + max(vals) * 0.015, bar.get_y() + bar.get_height() / 2,
                f"{v:.2f}", va="center", ha="left", fontsize=10,
                color=INK, fontweight="bold")
    ax.set_xlim(0, max(vals) * 1.16)
    ax.set_xlabel(f"RMSE ({unit})", color=SECONDARY, fontsize=9)
    ax.set_title("Test-split RMSE — lower is better",
                 loc="left", fontweight="bold", fontsize=12, color=INK)
    fig.text(0.01, 0.02,
             f"{abs(delta):.2f} {unit} ({-delta / prev_rmse:+.1%}) "
             f"vs the previous feature set",
             fontsize=9, color=SECONDARY)
    fig.tight_layout()
    fig.savefig(out / "rmse_summary.png", facecolor=SURFACE)
    plt.close(fig)

    # ------------------------------------------- 2. error vs lead time
    def by_lead(frame, col):
        g = frame.groupby("lead_step").apply(
            lambda d: rmse(d["actual"], d[col]), include_groups=False)
        return g.index.to_numpy() * 5 / 60.0, g.to_numpy()

    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=140)
    style(ax)
    for frame, col, colour, label in (
            (prev, best_base, MUTED, best_base),
            (prev, model_col, PREVIOUS, a.label_previous),
            (impr, model_col, IMPROVED, a.label_improved)):
        x, y = by_lead(frame, col)
        ax.plot(x, y, lw=2.0, color=colour, label=label,
                ls="--" if colour is MUTED else "-")
        ax.text(x[-1] + 0.15, y[-1], label, color=colour, fontsize=9,
                va="center", fontweight="bold")
    ax.set_xlim(0, 24 * 1.30)
    ax.set_xticks(range(0, 25, 4))
    ax.set_xlabel("forecast lead time (hours ahead)", color=SECONDARY, fontsize=9)
    ax.set_ylabel(f"RMSE ({unit})", color=SECONDARY, fontsize=9)
    ax.set_title("Error grows with lead time — the gap is what the features buy",
                 loc="left", fontweight="bold", fontsize=12, color=INK)
    fig.tight_layout()
    fig.savefig(out / "rmse_by_lead.png", facecolor=SURFACE)
    plt.close(fig)

    # ------------------------------------------------ 3. a sample day
    origins = sorted(set(prev["origin"]) & set(impr["origin"]))
    midnights = [o for o in origins if o.hour == 0]
    pick = (midnights or origins)[len(midnights or origins) // 2]
    p = prev[prev["origin"] == pick].sort_values("lead_step")
    i = impr[impr["origin"] == pick].sort_values("lead_step")
    when = pd.to_datetime(pick) + pd.to_timedelta(p["lead_step"] * 5, unit="m")

    fig, ax = plt.subplots(figsize=(11, 4.4), dpi=140)
    style(ax)
    ax.plot(when, p["actual"], lw=2.4, color=INK, label="actual", zorder=5)
    ax.plot(when, p[best_base], lw=1.4, color=MUTED, ls="--", label=best_base)
    ax.plot(when, p[model_col], lw=2.0, color=PREVIOUS, label=a.label_previous)
    ax.plot(when, i[model_col], lw=2.0, color=IMPROVED, label=a.label_improved)
    ax.set_ylabel(f"{'active power' if unit == 'kW' else 'GHI'} ({unit})",
                  color=SECONDARY, fontsize=9)
    ax.set_xlabel("time", color=SECONDARY, fontsize=9)
    ax.set_title(f"24-hour forecast from {pick:%Y-%m-%d %H:%M} "
                 f"({pick:%A})", loc="left", fontweight="bold",
                 fontsize=12, color=INK)
    leg = ax.legend(fontsize=9, frameon=True, framealpha=0.95, loc="upper left",
                    edgecolor=GRID, ncols=2)
    for t in leg.get_texts():
        t.set_color(SECONDARY)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out / "sample_day.png", facecolor=SURFACE)
    plt.close(fig)

    print(f"\n-> wrote {out}/ (rmse_summary.png, rmse_by_lead.png, "
          f"sample_day.png, summary.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
