"""
Figures for the PV output profile.

    python plot_pv.py --export ../powerfactory_export --load ../aggregate_total_load.csv

Four panels, each answering one question:
  carpet         what does the whole 8-month resource look like at once
  monthly        how deep is the winter trough (this sizes the battery)
  net_load       when is there surplus, and how much
  forecast       how much does GHI forecast error cost in POWER
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

# Validated categorical slots 1 and 2, plus ink and recessive greys.
INK, SECOND, GRID, SURFACE = "#0b0b0b", "#52514e", "#d9d8d2", "#fcfcfb"
LOAD_C, PV_C, MUTED = "#2a78d6", "#eb6834", "#9a9992"
# Sequential ramp: ONE hue, light -> dark. Never a rainbow.
SOLAR_CMAP = LinearSegmentedColormap.from_list(
    "solar", ["#fdfbf5", "#fae3a8", "#f0b93f", "#e08700", "#8a4a00"])

P_AC0 = 600.0


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, lw=0.6, alpha=0.9)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=SECOND, labelsize=9, length=0)
    return ax


def title(ax, text, sub=None):
    ax.set_title(text, loc="left", fontweight="bold", fontsize=12, color=INK,
                 pad=24 if sub else 8)
    if sub:
        ax.text(0, 1.035, sub, transform=ax.transAxes, fontsize=9, color=SECOND,
                va="bottom")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    ap.add_argument("--load", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    ex = Path(a.export)
    out = Path(a.out) if a.out else ex / "figures"
    out.mkdir(parents=True, exist_ok=True)

    pv = pd.read_csv(ex / "pv_ac_profile_measured.csv",
                     parse_dates=["timestamp"]).set_index("timestamp")
    ld = pd.read_csv(a.load, parse_dates=["timestamp"]).set_index("timestamp")
    ld = ld.iloc[:, 0].reindex(pv.index)
    h = 5 / 60

    # ------------------------------------------------ 1. carpet plot
    grid = pv["p_ac_kw"].copy()
    days = grid.index.normalize().unique()
    mat = grid.to_numpy().reshape(len(days), 288).T      # (time-of-day, day)
    fig, ax = plt.subplots(figsize=(12, 4.6), dpi=140)
    ax.set_facecolor(SURFACE)
    im = ax.imshow(mat, aspect="auto", origin="lower", cmap=SOLAR_CMAP,
                   extent=[0, len(days), 0, 24], vmin=0, vmax=P_AC0,
                   interpolation="nearest")
    month_start = [i for i, d in enumerate(days) if d.day == 1]
    ax.set_xticks(month_start)
    ax.set_xticklabels([days[i].strftime("%b") for i in month_start])
    ax.set_yticks(range(0, 25, 4))
    ax.set_ylabel("hour of day", color=SECOND, fontsize=9)
    ax.tick_params(colors=SECOND, labelsize=9, length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, pad=0.012)
    cb.set_label("AC power (kW)", color=SECOND, fontsize=9)
    cb.ax.tick_params(colors=SECOND, labelsize=8, length=0)
    cb.outline.set_visible(False)
    title(ax, "PV AC output, every 5 minutes of the record",
          "726 kWp DC / 600 kW AC · each column is one day · the daylight "
          "envelope narrows into June and reopens by September")
    fig.tight_layout()
    fig.savefig(out / "pv_carpet.png", facecolor=SURFACE)
    plt.close(fig)

    # -------------------------------------------- 2. monthly generation
    m = pv["p_ac_kw"].resample("MS").sum() * h / 1000.0
    fig, ax = plt.subplots(figsize=(9, 3.8), dpi=140)
    style(ax)
    bars = ax.bar([t.strftime("%b") for t in m.index], m.to_numpy(),
                  color=PV_C, width=0.62)
    lo = int(np.argmin(m.to_numpy()))
    bars[lo].set_color(MUTED)
    for b, v in zip(bars, m.to_numpy()):
        ax.text(b.get_x() + b.get_width() / 2, v + m.max() * 0.02, f"{v:.0f}",
                ha="center", fontsize=9, color=INK, fontweight="bold")
    ax.set_ylim(0, m.max() * 1.16)
    ax.set_ylabel("generation (MWh)", color=SECOND, fontsize=9)
    title(ax, "Monthly generation — the winter trough sizes the battery",
          f"total {m.sum():.0f} MWh · June {m.min():.0f} MWh is "
          f"{m.max() / m.min():.1f}x below February's {m.max():.0f} MWh")
    fig.tight_layout()
    fig.savefig(out / "pv_monthly.png", facecolor=SURFACE)
    plt.close(fig)

    # --------------------------------------- 3. net load on a sample day
    daily = pv["p_ac_kw"].resample("D").sum() * h
    pick = daily.sort_values().index[int(len(daily) * 0.85)]   # a good, not freak, day
    day = slice(pick, pick + pd.Timedelta("23:55:00"))
    p, l = pv.loc[day, "p_ac_kw"], ld.loc[day]
    net = l - p
    t = p.index

    fig, ax = plt.subplots(figsize=(11, 4.4), dpi=140)
    style(ax)
    ax.fill_between(t, l, p, where=(p >= l), color=PV_C, alpha=0.18,
                    interpolate=True, label="surplus to battery / export")
    ax.fill_between(t, l, p, where=(p < l), color=LOAD_C, alpha=0.10,
                    interpolate=True, label="deficit — diesel or battery")
    ax.plot(t, l, color=LOAD_C, lw=2.0, label="load")
    ax.plot(t, p, color=PV_C, lw=2.0, label="PV AC output")
    ax.plot(t, net, color=INK, lw=1.4, ls="--", label="net load")
    ax.axhline(0, color=SECOND, lw=0.8)
    ax.set_ylabel("power (kW)", color=SECOND, fontsize=9)
    ax.set_xlabel("time", color=SECOND, fontsize=9)
    surplus = np.maximum(p - l, 0).sum() * h
    title(ax, f"Load, PV and net load — {pick:%A %d %B %Y}",
          f"surplus {surplus:.0f} kWh this day · peak surplus "
          f"{float(np.maximum(p - l, 0).max()):.0f} kW · net load goes negative "
          f"whenever PV exceeds demand")
    leg = ax.legend(fontsize=8.5, frameon=True, framealpha=0.95, loc="upper left",
                    edgecolor=GRID, ncols=2)
    for x in leg.get_texts():
        x.set_color(SECOND)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out / "pv_net_load_day.png", facecolor=SURFACE)
    plt.close(fig)

    # ------------------------------ 4. forecast error, in power terms
    fc_path = ex / "pv_ac_profile_forecast.csv"
    if fc_path.exists():
        fc = pd.read_csv(fc_path, parse_dates=["timestamp"]).set_index("timestamp")
        dsum = fc["p_ac_kw_actual"].resample("D").sum()
        # A median day AND the sunniest one. Showing only the sunniest
        # would be unfair to the model (it is where any regression-to-the-
        # mean predictor must undershoot); showing only the median would
        # hide a failure mode that matters operationally.
        picks = [(dsum.sort_values().index[len(dsum) // 2], "typical day"),
                 (dsum.idxmax(), "sunniest day — the model's worst case")]
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.2), dpi=140,
                                 gridspec_kw={"width_ratios": [1.2, 1.2, 1]})
        for ax_i, (d0, lab) in zip(axes[:2], picks):
            fday = fc.loc[d0:d0 + pd.Timedelta("23:55:00")]
            ax = style(ax_i)
            ax.plot(fday.index, fday["p_ac_kw_actual"], color=INK, lw=2.2,
                    label="actual")
            ax.plot(fday.index, fday["p_ac_kw_forecast"], color=LOAD_C, lw=2.0,
                    label="forecast")
            ax.fill_between(fday.index, fday["p_ac_kw_actual"],
                            fday["p_ac_kw_forecast"], color=LOAD_C, alpha=0.14)
            ax.set_ylim(0, P_AC0 * 1.05)
            ax.set_ylabel("AC power (kW)", color=SECOND, fontsize=9)
            title(ax, lab, f"{d0:%d %B %Y}")
            leg = ax.legend(fontsize=9, frameon=True, edgecolor=GRID,
                            framealpha=0.95, loc="upper left")
            for x in leg.get_texts():
                x.set_color(SECOND)
            for t_ in ax.get_xticklabels():
                t_.set_rotation(30)
                t_.set_ha("right")

        gen = fc["p_ac_kw_actual"] > 1.0
        err = (fc.loc[gen, "p_ac_kw_forecast"] - fc.loc[gen, "p_ac_kw_actual"])
        ax = style(axes[2])
        ax.hist(err, bins=60, color=LOAD_C, alpha=0.85)
        ax.axvline(0, color=INK, lw=1.2)
        ax.set_xlabel("forecast − actual (kW)", color=SECOND, fontsize=9)
        ax.set_ylabel("5-min intervals", color=SECOND, fontsize=9)
        title(ax, "Error distribution",
              f"generating hours · MAE {err.abs().mean():.0f} kW · RMSE "
              f"{np.sqrt((err ** 2).mean()):.0f} kW · bias {err.mean():+.0f} kW")
        fig.tight_layout()
        fig.savefig(out / "pv_forecast_error.png", facecolor=SURFACE)
        plt.close(fig)

    print(f"-> wrote {out}/")
    for f in sorted(out.glob("*.png")):
        print(f"   {f.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
