"""
Benchmark results -> the tables, figures and number macros the report
\\input{}s. Nothing in the write-up is typed by hand.

    python report_assets.py --bench benchmark --out ../report

Writes
    <out>/tables/*.tex        booktabs floats, ready to \\input
    <out>/figures/*.pdf       vector figures, sized for a 16 cm text block
    <out>/results_macros.tex  \\newcommand per headline number, so prose
                              like "an MAE of \\LoadCnnMAE{} kW" can never
                              drift from the tables

AGGREGATION RULES (stated in the methodology, applied here)
  * A model's score on a fold is the mean over its seeds; its headline
    score is the mean over folds. Folds have equal-length test blocks,
    so this equals the pooled score to within rounding.
  * The +/- figure is the standard deviation ACROSS SEEDS of the
    fold-averaged score - the run-to-run spread a reader would see on
    retraining, not the fold-to-fold spread, which is reported per fold.
  * Skill is quoted against the baseline with the lowest mean test MAE:
    the strongest available, which gives the most conservative skill.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# ------------------------------------------------------------------ style
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
SERIES = {"CNN-LSTM": "#2a78d6", "TCN": "#eb6834", "linear regression": "#1baf7a"}
BAND_OUTER, BAND_INNER = "#cde2fb", "#9ec5f4"
TEXT_WIDTH_IN = 6.3                     # 16 cm text block

plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
    "mathtext.fontset": "stixsans",
    "font.size": 9, "axes.titlesize": 9.5, "axes.labelsize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": INK2,
    "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
    "figure.dpi": 150, "savefig.bbox": "tight", "savefig.pad_inches": 0.03,
    "pdf.fonttype": 42,
})

UNIT = {"load": "kW", "solar": r"W/m$^2$"}
UNIT_TEX = {"load": "kW", "solar": r"W\,m$^{-2}$"}
NETWORKS = ["CNN-LSTM", "CNN-LSTM (quantile)", "TCN"]
PRETTY = {"CNN-LSTM (quantile)": "CNN-LSTM, quantile (median)",
          "linear regression": "Linear regression (same inputs)"}
TITLE = {"load": "Aggregate load", "solar": "Global horizontal irradiance"}
TARGET_SHORT = {"load": "Load", "solar": "Irradiance"}


# ================================================================ loading
def load_bench(path: Path):
    recs = [json.loads(l) for l in (path / "results.jsonl").read_text().splitlines() if l.strip()]
    meta = json.loads((path / "meta.json").read_text())
    sel = json.loads((path / "selection.json").read_text()) if (path / "selection.json").exists() else None
    return recs, meta, sel


def point_frame(recs, stage="compare") -> pd.DataFrame:
    rows = []
    for r in recs:
        if r["stage"] != stage or "test" not in r:
            continue
        rows.append({"variant": r["variant"], "fold": r["fold"],
                     "seed": -1 if r.get("seed") is None else r["seed"],
                     "kind": r.get("kind", "network"),
                     "val_mae": r["val"]["mae"], "test_mae": r["test"]["mae"],
                     "test_rmse": r["test"]["rmse"], "test_nmae": r["test"]["nmae"],
                     "test_r2": r["test"]["r2"], "epochs": r.get("epochs"),
                     "best_epoch": r.get("best_epoch"),
                     "train_seconds": r.get("train_seconds"), "params": r.get("params")})
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame, n_folds: int) -> pd.DataFrame:
    """One row per variant: per-fold means, headline mean, seed spread."""
    out = []
    for v, g in df.groupby("variant", sort=False):
        per_fold = g.groupby("fold")[["test_mae", "test_rmse", "val_mae"]].mean()
        if len(per_fold) < n_folds:
            continue                      # incomplete: never report a partial mean
        per_seed = g.groupby("seed")["test_mae"].mean()
        per_seed_rmse = g.groupby("seed")["test_rmse"].mean()
        row = {"variant": v, "kind": g["kind"].iloc[0],
               "mae": per_fold["test_mae"].mean(), "rmse": per_fold["test_rmse"].mean(),
               "val_mae": per_fold["val_mae"].mean(),
               "mae_sd": per_seed.std(ddof=1) if len(per_seed) > 1 else np.nan,
               "rmse_sd": per_seed_rmse.std(ddof=1) if len(per_seed_rmse) > 1 else np.nan,
               "n_seeds": len(per_seed)}
        for f, m in per_fold["test_mae"].items():
            row[f"fold{f}"] = m
        out.append(row)
    return pd.DataFrame(out)


def strongest_baseline(summary: pd.DataFrame) -> pd.Series:
    base = summary[summary["kind"] == "baseline"]
    return base.loc[base["mae"].idxmin()]


def skill(df: pd.DataFrame, variant: str, base_name: str):
    """Mean and seed-spread of 1 - MAE/MAE_base, on fold-averaged MAE."""
    base = df[df["variant"] == base_name].groupby("fold")["test_mae"].mean().mean()
    per_seed = df[df["variant"] == variant].groupby("seed")["test_mae"].mean()
    s = 1.0 - per_seed / base
    return float(s.mean()), (float(s.std(ddof=1)) if len(s) > 1 else np.nan)


# ================================================================= tables
def fmt(x, nd=1):
    return "--" if x is None or not np.isfinite(x) else f"{x:.{nd}f}"


def pm(m, sd, nd=1):
    if sd is None or not np.isfinite(sd):
        return f"${fmt(m, nd)}$"
    return f"${fmt(m, nd)} \\pm {fmt(sd, nd)}$"


def num(n) -> str:
    """Integer with a thin-space-safe thousands separator for math mode."""
    return f"{int(n):,}".replace(",", "{,}")


def tex_escape(s: str) -> str:
    return s.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"  wrote {path}")


def table_benchmark(target, df, summary, meta, out: Path):
    n_folds = meta["folds"]
    best = strongest_baseline(summary)
    order = (NETWORKS + ["linear regression"]
             + list(summary[summary["kind"] == "baseline"].sort_values("mae")["variant"]))
    rows = []
    for v in order:
        r = summary[summary["variant"] == v]
        if r.empty:
            continue
        r = r.iloc[0]
        folds = " & ".join(f"${fmt(r[f'fold{f}'])}$" for f in range(n_folds))
        if v == best["variant"]:
            sk = "$0$ (ref.)"
        elif r["kind"] == "network":
            m, sd = skill(df, v, best["variant"])
            sk = pm(m, sd, 3)
        else:
            sk = f"${fmt(1 - r['mae'] / best['mae'], 3)}$"
        name = tex_escape(PRETTY.get(v, v[0].upper() + v[1:]))
        rows.append(f"    {name} & {folds} & {pm(r['mae'], r['mae_sd'])} & "
                    f"{pm(r['rmse'], r['rmse_sd'])} & {sk} \\\\")
        if v == "linear regression":
            rows.append("    \\midrule")
    fold_heads = " & ".join(f"F{f + 1}" for f in range(n_folds))
    unit = UNIT_TEX[target]
    scope = (" Irradiance is scored on daylight steps only (clear-sky GHI "
             "$> 20$\\,W\\,m$^{-2}$)." if target == "solar" else "")
    text = f"""\\begin{{table}}[htbp]
  \\centering
  \\caption{{Day-ahead {TITLE[target].lower()} forecast error on the test block of each
  rolling-origin fold (F1--F{n_folds}), in {unit}. Networks: mean over {int(summary['n_seeds'].max())}
  seeds, $\\pm$ one standard deviation across seeds. Skill is
  $1-\\mathrm{{MAE}}/\\mathrm{{MAE}}_\\text{{ref}}$ against the strongest baseline,
  {tex_escape(best['variant'])}.{scope}}}
  \\label{{tab:bench-{target}}}
  \\small
  \\begin{{tabular}}{{l{'r' * n_folds}rrr}}
    \\toprule
    & \\multicolumn{{{n_folds}}}{{c}}{{MAE per fold}} & & & \\\\
    \\cmidrule(lr){{2-{n_folds + 1}}}
    Model & {fold_heads} & MAE & RMSE & Skill \\\\
    \\midrule
{chr(10).join(rows)}
    \\bottomrule
  \\end{{tabular}}
\\end{{table}}
"""
    write(out / "tables" / f"tab_bench_{target}.tex", text)


def table_selection(target, recs, sel, meta, out: Path):
    df = point_frame(recs, "select")
    if df.empty or sel is None:
        return
    n_folds = meta["folds"]
    rows = []
    for v, g in df.groupby("variant", sort=False):
        pf = g.groupby("fold")[["val_mae", "test_mae"]].mean()
        cells = " & ".join(f"${fmt(pf.loc[f, 'val_mae'])}$" for f in range(n_folds) if f in pf.index)
        mean_val = pf["val_mae"].mean()
        mv = f"$\\mathbf{{{fmt(mean_val)}}}$" if v == sel["winner"] else f"${fmt(mean_val)}$"
        feats = next(r["features"] for r in recs if r["stage"] == "select" and r["variant"] == v)
        feat_tex = ", ".join(f"\\texttt{{{tex_escape(f)}}}" for f in feats)
        rows.append(f"    {tex_escape(v.split()[0])} & {feat_tex} & {cells} & {mv} & "
                    f"\\textcolor{{gray}}{{${fmt(pf['test_mae'].mean())}$}} \\\\")
    fold_heads = " & ".join(f"F{f + 1}" for f in range(n_folds))
    text = f"""\\begin{{table}}[htbp]
  \\centering
  \\caption{{Decoder feature selection for {TITLE[target].lower()}: validation MAE
  ({UNIT_TEX[target]}) of the CNN-LSTM on each fold, seed 0. The set with the
  lowest mean validation MAE (bold) is selected. Test MAE is shown in grey for
  reference only and played no part in the choice.}}
  \\label{{tab:select-{target}}}
  \\small
  \\begin{{tabular}}{{ll{'r' * n_folds}rr}}
    \\toprule
    & & \\multicolumn{{{n_folds}}}{{c}}{{Validation MAE}} & Mean & Mean \\\\
    \\cmidrule(lr){{3-{n_folds + 2}}}
    Set & Decoder covariates & {fold_heads} & val. & test \\\\
    \\midrule
{chr(10).join(rows)}
    \\bottomrule
  \\end{{tabular}}
\\end{{table}}
"""
    write(out / "tables" / f"tab_select_{target}.tex", text)


def table_screening(target, bench: Path, out: Path):
    """Stage 2: the single-split ablation that shortlisted the candidates."""
    path = bench / "screening" / f"{target}.csv"
    if not path.exists():
        return
    df = pd.read_csv(path).sort_values("val_mae").reset_index(drop=True)
    shortlist = set(df["trial"].str.split().str[0].head(3)) if target == "load" else None
    rows = []
    for _, r in df.iterrows():
        code = r["trial"].split()[0]
        feats = ", ".join(f"\\texttt{{{tex_escape(f)}}}" for f in str(r["features"]).split())
        notes = []
        if r["use_doy"]:
            notes.append("+ day of year")
        if not r["encoder_calendar"]:
            notes.append("no encoder calendar")
        if r["residual"] != "none":
            notes.append(f"residual on {r['residual']} naive")
        desc = feats + (f" ({'; '.join(notes)})" if notes else "")
        mark = "$^\\dagger$" if shortlist and code in shortlist else ""
        rows.append(f"    {code}{mark} & {desc} & ${fmt(r['val_mae'])}$ & ${fmt(r['val_rmse'])}$ & "
                    f"\\textcolor{{gray}}{{${fmt(r['test_mae'])}$}} \\\\")
    extra = (" $^\\dagger$Shortlisted for rolling-origin selection." if target == "load" else
             " All sets without day of year went forward to rolling-origin selection.")
    solar_note = (" The clear-sky envelope and daylight flag are added to every irradiance set."
                  if target == "solar" else "")
    text = f"""\\begin{{table}}[htbp]
  \\centering
  \\caption{{Single-split screening of decoder feature sets for {TITLE[target].lower()}
  (CNN-LSTM, standard split, seed 0; early-stopping patience 5, at most 25
  epochs), ranked by validation MAE in {UNIT_TEX[target]}. Test MAE in grey is for
  reference and was not used.{solar_note}{extra}}}
  \\label{{tab:screen-{target}}}
  \\small
  \\begin{{tabular}}{{llrrr}}
    \\toprule
    Set & Decoder covariates & Val.\\ MAE & Val.\\ RMSE & Test MAE \\\\
    \\midrule
{chr(10).join(rows)}
    \\bottomrule
  \\end{{tabular}}
\\end{{table}}
"""
    write(out / "tables" / f"tab_screen_{target}.tex", text)


def prob_frame(recs) -> pd.DataFrame:
    rows = []
    for r in recs:
        if r["stage"] == "compare" and "prob" in r:
            rows.append({"variant": r["variant"], "fold": r["fold"],
                         "seed": -1 if r.get("seed") is None else r["seed"],
                         "wraps": r.get("wraps"), **r["prob"]})
    return pd.DataFrame(rows)


def table_probabilistic(results: dict, out: Path):
    cols = [("picp_50", "PICP$_{50}$", 3), ("picp_80", "PICP$_{80}$", 3),
            ("picp_90", "PICP$_{90}$", 3), ("width_80", "$\\bar{w}_{80}$", 1),
            ("interval_score_80", "IS$_{80}$", 1), ("pinball", "Pinball", 2)]
    rows, wraps = [], {}
    for target, (recs, meta, _) in results.items():
        pf = prob_frame(recs)
        if pf.empty:
            continue
        rows.append(f"    \\multicolumn{{{len(cols) + 1}}}{{l}}{{\\textit{{{TITLE[target]}}} "
                    f"({UNIT_TEX[target]})}} \\\\")
        for v, label in (("CNN-LSTM (quantile)", "CNN-LSTM, quantile heads"),
                         ("reference interval", "Reference interval")):
            g = pf[pf["variant"] == v]
            if g["fold"].nunique() < meta["folds"]:
                continue
            per_seed = g.groupby("seed")[[c for c, _, _ in cols]].mean()
            cells = []
            for c, _, nd in cols:
                m = per_seed[c].mean()
                sd = per_seed[c].std(ddof=1) if len(per_seed) > 1 else np.nan
                cells.append(pm(m, sd, nd))
            if v == "reference interval":
                wraps[target] = g["wraps"].mode().iloc[0]
            rows.append(f"    \\quad {label} & " + " & ".join(cells) + " \\\\")
    heads = " & ".join(h for _, h, _ in cols)
    wrapped = "; ".join(f"{TITLE[t].lower()}: {tex_escape(w)}" for t, w in wraps.items())
    text = f"""\\begin{{table}}[htbp]
  \\centering
  \\caption{{Probabilistic forecast quality, mean over folds (and seeds for the
  network). PICP$_{{x}}$: fraction of outcomes inside the central $x$\\,\\%
  interval (nominal $x/100$). $\\bar{{w}}_{{80}}$: mean width of the 80\\,\\%
  interval. IS$_{{80}}$: interval score of the 80\\,\\% interval; pinball: mean
  pinball loss over all seven quantile levels (both lower is better). The
  reference interval adds the empirical hour-of-day quantiles of the strongest
  baseline's validation errors to that baseline's forecast ({wrapped}).}}
  \\label{{tab:prob}}
  \\small
  \\begin{{tabular}}{{l{'r' * len(cols)}}}
    \\toprule
    & {heads} \\\\
    \\midrule
{chr(10).join(rows)}
    \\bottomrule
  \\end{{tabular}}
\\end{{table}}
"""
    write(out / "tables" / "tab_prob.tex", text)


def cost(target, variant, recs, timings) -> dict:
    """
    Seconds per epoch and latency from the isolated timing run when there
    is one (the benchmark trained both targets concurrently, so its own
    timings include contention); epochs from every benchmark run, which
    contention does not affect.
    """
    df = point_frame(recs)
    g = df[df["variant"] == variant]
    kept = next((r for r in recs if r["stage"] == "compare" and r["variant"] == variant
                 and "latency" in r), None)
    t = timings.get(target, {}).get(variant)
    per_ep = t["sec_per_epoch"] if t else float((g["train_seconds"] / g["epochs"]).mean())
    epochs = float(g["epochs"].mean())
    lat_src = t if t else (kept or {})
    return {"params": int(g["params"].iloc[0]), "epochs": epochs, "sec_per_epoch": per_ep,
            "train_seconds": per_ep * epochs,
            "latency_ms": lat_src.get("latency", {}).get("cpu_single_ms", np.nan),
            "kb": kept["checkpoint_bytes"] / 1024 if kept else np.nan, "clean": t is not None}


def table_compute(results: dict, out: Path, timings: dict):
    rows, clean = [], True
    for target, (recs, meta, _) in results.items():
        df = point_frame(recs)
        for v in NETWORKS:
            if df[df["variant"] == v].empty:
                continue
            c = cost(target, v, recs, timings)
            clean &= c["clean"]
            rows.append(f"    {TARGET_SHORT[target]} & {tex_escape(v)} & "
                        f"${num(c['params'])}$ & ${fmt(c['kb'], 0)}$ & "
                        f"${fmt(c['epochs'], 1)}$ & ${fmt(c['sec_per_epoch'], 1)}$ & "
                        f"${fmt(c['train_seconds'], 0)}$ & ${fmt(c['latency_ms'], 1)}$ \\\\")
    meta = next(iter(results.values()))[1]
    timing_note = ("Seconds per epoch and latency come from a separate run of fold F3 with "
                   "nothing else running; training time is that rate times the mean number "
                   "of epochs." if clean else
                   "Times are means over the benchmark runs, which shared the machine.")
    text = f"""\\begin{{table}}[htbp]
  \\centering
  \\caption{{Model size and computational cost on an Apple M1 (Metal back end,
  PyTorch {meta['torch']}). Epochs: mean over all folds and seeds (training stops
  once validation loss has not improved for {meta['common']['patience']} epochs).
  {timing_note} Latency: one 288-step forecast on the CPU, median of 50 runs.}}
  \\label{{tab:compute}}
  \\small
  \\begin{{tabular}}{{llrrrrrr}}
    \\toprule
    Target & Model & Params & KB & Epochs & s/epoch & Train s & Latency ms \\\\
    \\midrule
{chr(10).join(rows)}
    \\bottomrule
  \\end{{tabular}}
\\end{{table}}
"""
    write(out / "tables" / "tab_compute.tex", text)


def table_folds(results: dict, out: Path):
    """Exact calendar extent of every split of every fold."""
    from config import ForecastConfig
    from datasets import prepare
    rows = []
    for target, (recs, meta, sel) in results.items():
        feats = tuple(sel["features"]) if sel else ("tod",)
        for k in range(meta["folds"]):
            cfg = ForecastConfig(target=target, source=meta["source"], future_features=feats,
                                 cv_folds=meta["folds"], cv_fold=k,
                                 **{x: meta["common"][x] for x in ("horizon", "lookback", "stride")})
            spec = prepare(cfg)
            b = spec.splits.bounds()
            n = {s: len(spec.origins[s]) for s in ("train", "val", "test")}
            cell = lambda s: f"{b[s][0]:%d %b}--{b[s][1]:%d %b}"
            rows.append(f"    {TARGET_SHORT[target] if k == 0 else ''} & F{k + 1} & "
                        f"{cell('train')} & ${num(n['train'])}$ & {cell('val')} & "
                        f"${num(n['val'])}$ & {cell('test')} & ${num(n['test'])}$ \\\\")
        rows.append("    \\midrule")
    rows = rows[:-1]
    text = f"""\\begin{{table}}[htbp]
  \\centering
  \\caption{{Rolling-origin folds (2002 calendar; the irradiance record is re-dated
  from 2023, Section~\\ref{{sec:data}}). Each fold trains on everything before its
  validation block; F3 is the standard 70/15/15 split. $n$ is the number of
  forecast origins (one every 15 minutes) whose complete window lies inside the
  block and contains no missing value.}}
  \\label{{tab:folds}}
  \\small
  \\begin{{tabular}}{{llrrrrrr}}
    \\toprule
    & Fold & Train & $n$ & Validation & $n$ & Test & $n$ \\\\
    \\midrule
{chr(10).join(rows)}
    \\bottomrule
  \\end{{tabular}}
\\end{{table}}
"""
    write(out / "tables" / "tab_folds.tex", text)


# ================================================================ figures
def slug(text: str) -> str:
    """Must match benchmark.slug, which names the saved prediction files."""
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


def shared_legend(fig, ax) -> None:
    """One legend under all panels, so no panel's data sits behind it."""
    handles, labels = ax.get_legend_handles_labels()
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False,
               bbox_to_anchor=(0.5, 0.0))
def fig_lead(results: dict, out: Path):
    fig, axes = plt.subplots(1, len(results), figsize=(TEXT_WIDTH_IN, 2.5))
    axes = np.atleast_1d(axes)
    for ax, (target, (recs, meta, _)) in zip(axes, results.items()):
        df = point_frame(recs)
        summary = summarise(df, meta["folds"])
        best = strongest_baseline(summary)["variant"]
        curves = {}
        for r in recs:
            if r["stage"] == "compare" and "per_step_mae" in r:
                curves.setdefault(r["variant"], []).append(np.asarray(r["per_step_mae"], float))
        lead_h = (np.arange(meta["common"]["horizon"]) + 1) * 5 / 60
        for v, label, color, lw, ls in (
                ("CNN-LSTM", "CNN-LSTM", SERIES["CNN-LSTM"], 1.6, "-"),
                ("TCN", "TCN", SERIES["TCN"], 1.1, "-."),
                ("linear regression", "Linear regression", SERIES["linear regression"], 1.1, "--"),
                (best, "Strongest reference rule", INK2, 1.1, ":")):
            if v not in curves:
                continue
            stack = np.vstack(curves[v])
            ax.plot(lead_h, np.nanmean(stack, axis=0), color=color, lw=lw, ls=ls, label=label)
        ax.set_title(TITLE[target], loc="left", color=INK)
        ax.set_xlabel("Lead time (h)")
        ax.set_ylabel(f"MAE ({UNIT[target]})")
        ax.set_xticks([0, 6, 12, 18, 24])
        ax.set_xlim(0, 24)
    shared_legend(fig, axes[0])
    path = out / "figures" / "fig_lead.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {path}")


def fig_folds(results: dict, out: Path):
    fig, axes = plt.subplots(1, len(results), figsize=(TEXT_WIDTH_IN, 2.4))
    axes = np.atleast_1d(axes)
    for ax, (target, (recs, meta, _)) in zip(axes, results.items()):
        df = point_frame(recs)
        summary = summarise(df, meta["folds"])
        best = strongest_baseline(summary)["variant"]
        show = [("CNN-LSTM", "CNN-LSTM", SERIES["CNN-LSTM"], "o"),
                ("TCN", "TCN", SERIES["TCN"], "s"),
                ("linear regression", "Linear regression", SERIES["linear regression"], "D"),
                (best, "Strongest reference rule", INK2, "^")]
        width = 0.18
        for j, (v, label, color, marker) in enumerate(show):
            g = df[df["variant"] == v]
            if g.empty:
                continue
            x0 = np.arange(meta["folds"]) + (j - 1.5) * width
            means = g.groupby("fold")["test_mae"].mean()
            ax.scatter(x0[means.index], means.values, color=color, marker=marker, s=26,
                       zorder=3, label=label,
                       edgecolors="white", linewidths=0.6)
            if g["seed"].nunique() > 1:
                for f, gf in g.groupby("fold"):
                    ax.vlines(x0[f], gf["test_mae"].min(), gf["test_mae"].max(),
                              color=color, lw=1.0, zorder=2)
        periods = {}
        for r in recs:
            if r["stage"] == "compare" and "test_period" in r:
                periods[r["fold"]] = r["test_period"]
        labels = []
        for f in range(meta["folds"]):
            a, b = (pd.Timestamp(t) for t in periods.get(f, ("", "")))
            labels.append(f"F{f + 1}\n{a:%b}–{b:%b}")
        ax.set_xticks(np.arange(meta["folds"]), labels)
        ax.set_title(TITLE[target], loc="left", color=INK)
        ax.set_ylabel(f"Test MAE ({UNIT[target]})")
        ax.grid(axis="x", visible=False)
    shared_legend(fig, axes[0])
    path = out / "figures" / "fig_folds.pdf"
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {path}")


def _stitch_midnights(origin_ns, arr, start_day: int, days: int):
    """Concatenate the forecasts issued at consecutive midnights."""
    origins = pd.to_datetime(origin_ns)
    mid = np.flatnonzero((origins.hour == 0) & (origins.minute == 0))
    pick = mid[start_day:start_day + days]
    if pick.size == 0:
        return None, None
    times = np.concatenate([origins[i] + pd.to_timedelta(np.arange(arr.shape[1]) * 5, "m")
                            for i in pick])
    return times, np.concatenate([arr[i] for i in pick], axis=0)


def fig_sample(results: dict, bench: Path, out: Path):
    fig, axes = plt.subplots(len(results), 1, figsize=(TEXT_WIDTH_IN, 1.75 * len(results) + 0.35))
    axes = np.atleast_1d(axes)
    for ax, (target, (recs, meta, _)) in zip(axes, results.items()):
        pdir = bench / target / "predictions"
        qfile = pdir / f"{slug('compare_CNN-LSTM (quantile)')}.npz"
        if not qfile.exists():
            continue
        q = np.load(qfile)
        levels = list(meta["quantiles"])
        df = point_frame(recs)
        best = strongest_baseline(summarise(df, meta["folds"]))["variant"]
        bfile = pdir / f"{slug('compare_' + best)}.npz"
        start = 10 if target == "load" else 12
        t, truth = _stitch_midnights(q["origin_ns"], q["truth"], start, 3)
        if t is None:
            print(f"  ! {target}: no forecast issued at 00:00 - sample figure panel skipped")
            continue
        _, qq = _stitch_midnights(q["origin_ns"], q["quantiles"], start, 3)
        lo90, hi90 = qq[:, levels.index(0.05)], qq[:, levels.index(0.95)]
        lo50, hi50 = qq[:, levels.index(0.25)], qq[:, levels.index(0.75)]
        ax.fill_between(t, lo90, hi90, color=BAND_OUTER, lw=0, label="90% interval")
        ax.fill_between(t, lo50, hi50, color=BAND_INNER, lw=0, label="50% interval")
        if bfile.exists():
            b = np.load(bfile)
            _, bp = _stitch_midnights(b["origin_ns"], b["point"], start, 3)
            ax.plot(t, bp, color=INK2, lw=0.9, ls=":", label="Strongest reference rule")
        ax.plot(t, qq[:, levels.index(0.5)], color=SERIES["CNN-LSTM"], lw=1.4, label="Median forecast")
        ax.plot(t, truth, color=INK, lw=0.9, label="Measured")
        for d in range(1, 3):
            ax.axvline(t[0] + pd.Timedelta(days=d), color=AXIS, lw=0.8, ls="--")
        ax.set_title(f"{TITLE[target]}: forecasts issued at 00:00 on three consecutive days",
                     loc="left", color=INK)
        ax.set_ylabel(UNIT[target])
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%a %d %b\n%H:%M"))
        ax.xaxis.set_major_locator(matplotlib.dates.HourLocator(byhour=[0, 12]))
        ax.set_xlim(t[0], t[-1])
        ax.set_ylim(bottom=0)
    # One legend for both panels, below them, so it never sits on the data.
    shared_legend(fig, axes[0])
    path = out / "figures" / "fig_sample.pdf"
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {path}")


def fig_reliability(results: dict, out: Path):
    fig, axes = plt.subplots(1, len(results), figsize=(TEXT_WIDTH_IN * 0.75, 2.5))
    axes = np.atleast_1d(axes)
    for ax, (target, (recs, meta, _)) in zip(axes, results.items()):
        pf = prob_frame(recs)
        if pf.empty:
            continue
        levels = list(meta["quantiles"])
        ax.plot([0, 1], [0, 1], color=AXIS, lw=0.9, ls="--", label="Perfect calibration")
        for v, color, marker, label in (("CNN-LSTM (quantile)", SERIES["CNN-LSTM"], "o", "CNN-LSTM quantiles"),
                                        ("reference interval", INK2, "^", "Reference interval")):
            g = pf[pf["variant"] == v]
            if g.empty:
                continue
            obs = [g[f"below_{lv:g}"].mean() for lv in levels]
            ax.plot(levels, obs, color=color, marker=marker, ms=4.5, lw=1.1, label=label,
                    markeredgecolor="white", markeredgewidth=0.5)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_aspect("equal")
        ax.set_xlabel("Nominal quantile level")
        ax.set_ylabel("Observed frequency below")
        ax.set_title(TITLE[target], loc="left", color=INK)
    axes[0].legend(loc="lower right", fontsize=7)
    fig.tight_layout()
    path = out / "figures" / "fig_reliability.pdf"
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {path}")


def fig_training(results: dict, out: Path):
    fig, axes = plt.subplots(1, len(results), figsize=(TEXT_WIDTH_IN, 2.2))
    axes = np.atleast_1d(axes)
    for ax, (target, (recs, meta, _)) in zip(axes, results.items()):
        last = meta["folds"] - 1
        runs = [r for r in recs if r["stage"] == "compare" and r["variant"] == "CNN-LSTM"
                and r["fold"] == last]
        for i, r in enumerate(sorted(runs, key=lambda r: r["seed"])):
            ep = np.arange(1, len(r["history"]["train"]) + 1)
            ax.plot(ep, r["history"]["train"], color=SERIES["CNN-LSTM"], lw=1.0, alpha=0.85,
                    label="Training" if i == 0 else None)
            ax.plot(ep, r["history"]["val"], color=SERIES["TCN"], lw=1.0, ls="--", alpha=0.85,
                    label="Validation" if i == 0 else None)
            ax.scatter([r["best_epoch"]], [r["history"]["val"][r["best_epoch"] - 1]],
                       color=SERIES["TCN"], s=18, zorder=3, edgecolors="white", linewidths=0.6,
                       label="Checkpoint kept" if i == 0 else None)
        ax.set_title(f"{TITLE[target]} (fold F{last + 1}, all seeds)", loc="left", color=INK)
        ax.set_xlabel("Epoch")
        ax.set_ylabel("L1 loss (scaled units)")
    shared_legend(fig, axes[0])
    path = out / "figures" / "fig_training.pdf"
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {path}")


# ================================================================= macros
def macros(results: dict, out: Path, timings: dict):
    lines = ["% Generated by forecasting/report_assets.py - do not edit by hand."]

    def cmd(name, value):
        lines.append(f"\\newcommand{{\\{name}}}{{{value}}}")

    keys = {"CNN-LSTM": "Cnn", "CNN-LSTM (quantile)": "CnnQ", "TCN": "Tcn",
            "linear regression": "Linear"}
    for target, (recs, meta, sel) in results.items():
        T = target.capitalize()
        df = point_frame(recs)
        summary = summarise(df, meta["folds"])
        if summary.empty:
            continue
        best = strongest_baseline(summary)
        cmd(f"{T}BestBaseline", tex_escape(best["variant"]))
        cmd(f"{T}BestBaselineMAE", fmt(best["mae"]))
        for v, k in keys.items():
            r = summary[summary["variant"] == v]
            if r.empty:
                continue
            r = r.iloc[0]
            cmd(f"{T}{k}MAE", fmt(r["mae"]))
            cmd(f"{T}{k}MAEsd", fmt(r["mae_sd"]))
            cmd(f"{T}{k}RMSE", fmt(r["rmse"]))
            m, sd = (skill(df, v, best["variant"]) if r["kind"] == "network"
                     else (1 - r["mae"] / best["mae"], np.nan))
            cmd(f"{T}{k}Skill", fmt(m, 3))
            cmd(f"{T}{k}SkillPct", fmt(100 * m, 0))
        for _, r in summary[summary["kind"] == "baseline"].iterrows():
            nm = "".join(w.capitalize() for w in
                         "".join(c if c.isalpha() else " " for c in r["variant"]).split())
            cmd(f"{T}Base{nm}MAE", fmt(r["mae"]))
        if sel:
            cmd(f"{T}SelectedSet", tex_escape(sel["winner"].split()[0]))
            cmd(f"{T}SelectedFeatures",
                ", ".join(f"\\texttt{{{tex_escape(f)}}}" for f in sel["features"]))
        pf = prob_frame(recs)
        for v, k in (("CNN-LSTM (quantile)", "CnnQ"), ("reference interval", "Ref")):
            g = pf[pf["variant"] == v] if not pf.empty else pf
            if g.empty:
                continue
            for c, nm in (("picp_50", "PicpFifty"), ("picp_80", "PicpEighty"),
                          ("picp_90", "PicpNinety"), ("width_80", "WidthEighty"),
                          ("interval_score_80", "IsEighty")):
                nd = 1 if c.startswith(("width", "interval")) else 3
                cmd(f"{T}{k}{nm}", fmt(g[c].mean(), nd))
                if c.startswith("picp"):
                    cmd(f"{T}{k}{nm}Pct", fmt(100 * g[c].mean(), 0))
        net = df[df["kind"] == "network"]
        for v, k in keys.items():
            if net[net["variant"] == v].empty:
                continue
            c = cost(target, v, recs, timings)
            cmd(f"{T}{k}Params", num(c["params"]))
            cmd(f"{T}{k}TrainSeconds", fmt(c["train_seconds"], 0))
            cmd(f"{T}{k}SecPerEpoch", fmt(c["sec_per_epoch"], 1))
            cmd(f"{T}{k}Epochs", fmt(c["epochs"], 0))
            cmd(f"{T}{k}LatencyMs", fmt(c["latency_ms"], 1))
            cmd(f"{T}{k}CheckpointKB", fmt(c["kb"], 0))
    meta = next(iter(results.values()))[1]
    cmd("TorchVersion", meta["torch"])
    cmd("PythonVersion", meta["python"])
    cmd("NumSeeds", str(len(meta["seeds"])))
    cmd("NumFolds", str(meta["folds"]))
    write(out / "results_macros.tex", "\n".join(lines) + "\n")


# =================================================================== main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default="benchmark")
    ap.add_argument("--out", default="../report")
    ap.add_argument("--targets", nargs="+", default=["load", "solar"])
    a = ap.parse_args(argv)
    bench, out = Path(a.bench), Path(a.out)

    results = {t: load_bench(bench / t) for t in a.targets
               if (bench / t / "results.jsonl").exists()}
    if not results:
        raise SystemExit(f"no results under {bench}/")
    timings = {t: json.loads((bench / t / "timing.json").read_text()) for t in results
               if (bench / t / "timing.json").exists()}
    for target, (recs, meta, sel) in results.items():
        df = point_frame(recs)
        summary = summarise(df, meta["folds"])
        if not summary.empty:
            table_benchmark(target, df, summary, meta, out)
        table_selection(target, recs, sel, meta, out)
        table_screening(target, bench, out)
    table_probabilistic(results, out)
    table_compute(results, out, timings)
    table_folds(results, out)
    fig_lead(results, out)
    fig_folds(results, out)
    fig_sample(results, bench, out)
    fig_reliability(results, out)
    fig_training(results, out)
    macros(results, out, timings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
