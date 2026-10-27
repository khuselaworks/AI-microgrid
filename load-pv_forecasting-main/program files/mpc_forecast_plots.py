"""
Figures and tables for the forecast inputs of the MPC study.

    python mpc_forecast_plots.py --study mpc_inputs

Reads <study>/forecasts.npz written by mpc_forecasts.py and writes

    <study>/figures/*.pdf, *.png   vector for the thesis, raster for slides
    <study>/csv/day_ahead_stitched.csv
                                   forecasts issued at 00:00, concatenated
                                   into one continuous series per tier
    <study>/powerfactory/{tier}_{load,pv}.csv
                                   the same series, one file per tier and
                                   signal, for a DIgSILENT time characteristic
    <study>/metrics/daily_mae.csv  MAE of every forecast issued on each day
    <study>/metrics/mae_by_hour_of_day.csv
    <study>/metrics/sample_days.csv
                                   which days the sample-day figure shows,
                                   and why

PV error is scored over all hours here, not daylight only: what the
controller has to cover is energy, and a night-time zero is a correct
forecast it can rely on. The daylight-only figure, comparable with the
irradiance results of the forecasting chapter, is in
metrics/forecast_error_summary.csv.
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
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from pv_power import P_AC0_KW  # noqa: E402
from config import SITE_LAT, SITE_LON, SITE_TZ_OFFSET, STEPS_PER_DAY  # noqa: E402
from report_assets import AXIS, INK, INK2, MUTED, TEXT_WIDTH_IN  # noqa: E402  (also sets rcParams)
from solar_geometry import clear_sky_ghi  # noqa: E402

TIERS = ("perfect", "best", "reference")
SIGNALS = ("load_kw", "pv_kw", "net_kw")
# Categorical slots 1 and 2 of the report palette; measured is ink.
COLOR = {"perfect": INK, "best": "#2a78d6", "reference": "#eb6834"}
WIDTH = {"perfect": 1.0, "best": 1.3, "reference": 1.1}
SIGNAL_TITLE = {"load_kw": "Load", "pv_kw": "PV power (AC)", "net_kw": "Net load (load - PV)"}


def short_labels() -> dict:
    return {"perfect": "Measured", "best": "Best forecast", "reference": "Reference forecast"}


def save(fig, out: Path, name: str) -> None:
    fig.savefig(out / f"{name}.pdf")
    fig.savefig(out / f"{name}.png", dpi=300)
    plt.close(fig)


def legend_top(fig, handles, labels_, ncol=3) -> None:
    fig.legend(handles, labels_, loc="upper center", ncol=ncol, bbox_to_anchor=(0.5, 1.0),
               handlelength=1.8, columnspacing=1.4)


def day_axis(ax, fmt="%H:%M", hours=6) -> None:
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, hours)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt))


# =================================================================== loading
def load_study(study: Path):
    fc = np.load(study / "forecasts.npz")
    meta = json.loads((study / "forecast_meta.json").read_text())
    origins = pd.DatetimeIndex(pd.to_datetime(fc["origin_ns"]))
    mats = {t: {s: fc[f"{t}__{s}"].astype(np.float64) for s in SIGNALS + ("ghi_wm2",)}
            for t in TIERS}
    return origins, mats, meta


def midnight_rows(origins: pd.DatetimeIndex) -> np.ndarray:
    return np.flatnonzero((origins.hour == 0) & (origins.minute == 0))


def stitched(origins, mats) -> pd.DataFrame:
    """Each day's 00:00 forecast, end to end: a once-daily day-ahead series."""
    rows = midnight_rows(origins)
    h = np.arange(STEPS_PER_DAY)
    stamps = (origins[rows].to_numpy()[:, None]
              + (h * np.timedelta64(5, "m"))[None, :]).ravel()
    df = pd.DataFrame({"timestamp": pd.DatetimeIndex(stamps),
                       "lead_step": np.tile(h + 1, len(rows))})
    for sig in SIGNALS:
        for tier, name in (("perfect", "measured"), ("best", "best"), ("reference", "reference")):
            df[f"{sig.replace('_kw', '')}_{name}_kw"] = mats[tier][sig][rows].ravel()
    return df


def powerfactory_series(st: pd.DataFrame, out: Path) -> None:
    """One file per tier and signal: a single value per timestamp.

    time_h counts hours from the first timestamp, for a characteristic
    defined on relative time. PV also gets factor = p / 600 kW AC rating,
    for a characteristic used as a scaling factor.
    """
    out.mkdir(parents=True, exist_ok=True)
    t_h = (st["timestamp"] - st["timestamp"].iloc[0]).dt.total_seconds() / 3600.0
    for tier, name in (("perfect", "measured"), ("best", "best"), ("reference", "reference")):
        for sig in ("load", "pv"):
            kw = st[f"{sig}_{name}_kw"]
            df = pd.DataFrame({"timestamp": st["timestamp"], "time_h": t_h,
                               "p_kw": kw, "p_mw": kw / 1000.0})
            if sig == "pv":
                df["factor"] = kw / P_AC0_KW
            df.to_csv(out / f"{tier}_{sig}.csv", index=False, float_format="%.6f",
                      date_format="%Y-%m-%d %H:%M:%S")


# =================================================================== figures
def fig_sample_days(origins, mats, meta, out: Path, metrics: Path) -> None:
    """Clearest, median and cloudiest day, forecast issued at 00:00."""
    rows = midnight_rows(origins)
    days = origins[rows]
    cs = np.stack([clear_sky_ghi(pd.date_range(d, periods=STEPS_PER_DAY, freq="5min"),
                                 SITE_LAT, SITE_LON, SITE_TZ_OFFSET).to_numpy() for d in days])
    kt = mats["perfect"]["ghi_wm2"][rows].sum(axis=1) / cs.sum(axis=1)
    order = np.argsort(kt)
    pick = {"cloudiest": order[0], "median": order[len(order) // 2], "clearest": order[-1]}
    show = [("clearest", pick["clearest"]), ("median", pick["median"]),
            ("cloudiest", pick["cloudiest"])]

    info = []
    for kind, j in show:
        r = rows[j]
        info.append({"kind": kind, "date": f"{days[j]:%Y-%m-%d}", "weekday": f"{days[j]:%A}",
                     "daily_clear_sky_index": round(float(kt[j]), 3),
                     **{f"{t}_{s}_mae": round(float(np.abs(mats[t][s][r]
                                                           - mats["perfect"][s][r]).mean()), 2)
                        for t in ("best", "reference") for s in ("load_kw", "pv_kw")}})
    pd.DataFrame(info).to_csv(metrics / "sample_days.csv", index=False)

    fig, axes = plt.subplots(2, 3, figsize=(TEXT_WIDTH_IN, 4.1), sharex="col", sharey="row")
    lab = short_labels()
    for c, (kind, j) in enumerate(show):
        r = rows[j]
        t = pd.date_range(days[j], periods=STEPS_PER_DAY, freq="5min")
        for row, sig in enumerate(("load_kw", "pv_kw")):
            ax = axes[row, c]
            for tier in ("perfect", "reference", "best"):
                ax.plot(t, mats[tier][sig][r], color=COLOR[tier], lw=WIDTH[tier],
                        label=lab[tier], zorder={"perfect": 3, "best": 2, "reference": 1}[tier])
            day_axis(ax)
            if row == 0:
                ax.set_title(f"{days[j]:%a %d %b}\n{kind} day, $k$ = {kt[j]:.2f}",
                             loc="left", color=INK)
        axes[0, 0].set_ylabel("Load (kW)")
        axes[1, 0].set_ylabel("PV power (kW)")
    for ax in axes[1]:
        ax.set_xlabel("Time of day")
    h, l = axes[0, 0].get_legend_handles_labels()
    order_ = [l.index(lab[t]) for t in TIERS]
    legend_top(fig, [h[i] for i in order_], [l[i] for i in order_])
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    save(fig, out, "fig_sample_days")


def fig_mae_by_lead(study: Path, meta, out: Path) -> None:
    lead = pd.read_csv(study / "metrics" / "forecast_mae_by_lead.csv")
    x = lead["lead_minutes"] / 60.0
    lab = short_labels()
    fig, axes = plt.subplots(1, 3, figsize=(TEXT_WIDTH_IN, 2.3))
    for ax, sig in zip(axes, SIGNALS):
        for tier in ("reference", "best"):
            ax.plot(x, lead[f"{tier}_{sig}_mae"], color=COLOR[tier], lw=WIDTH[tier],
                    label=lab[tier])
        ax.set_title(SIGNAL_TITLE[sig], loc="left", color=INK)
        ax.set_xlabel("Lead time (h)")
        ax.set_xlim(0, 24)
        ax.set_xticks([0, 6, 12, 18, 24])
        ax.set_ylim(bottom=0)
    axes[0].set_ylabel("MAE (kW)")
    h, l = axes[0].get_legend_handles_labels()
    legend_top(fig, h[::-1], l[::-1], ncol=2)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    save(fig, out, "fig_mae_by_lead")


def daily_mae(origins, mats) -> pd.DataFrame:
    day = origins.normalize()
    rows = []
    for d in day.unique():
        sel = day == d
        rec = {"date": f"{d:%Y-%m-%d}", "weekday": f"{d:%a}", "n_forecasts": int(sel.sum())}
        for tier in ("best", "reference"):
            for sig in SIGNALS:
                rec[f"{tier}_{sig}_mae"] = float(np.abs(mats[tier][sig][sel]
                                                        - mats["perfect"][sig][sel]).mean())
        rows.append(rec)
    return pd.DataFrame(rows)


def fig_daily_mae(daily: pd.DataFrame, out: Path) -> None:
    d = pd.to_datetime(daily["date"])
    lab = short_labels()
    fig, axes = plt.subplots(3, 1, figsize=(TEXT_WIDTH_IN, 4.6), sharex=True)
    for ax, sig in zip(axes, SIGNALS):
        for tier in ("reference", "best"):
            ax.plot(d, daily[f"{tier}_{sig}_mae"], color=COLOR[tier], lw=WIDTH[tier],
                    marker="o", ms=3, markeredgecolor="white", markeredgewidth=0.4,
                    label=lab[tier])
        ax.set_title(SIGNAL_TITLE[sig], loc="left", color=INK)
        ax.set_ylabel("MAE (kW)")
        ax.set_ylim(bottom=0)
    axes[-1].xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    axes[-1].set_xlabel("Day the forecasts were issued (ticks on Mondays)")
    h, l = axes[0].get_legend_handles_labels()
    legend_top(fig, h[::-1], l[::-1], ncol=2)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    save(fig, out, "fig_daily_mae")


def fig_error_distribution(mats, out: Path) -> None:
    """Forecast minus measured, every (origin, step) cell; PV in daylight only."""
    lab = short_labels()
    day = mats["perfect"]["ghi_wm2"] > 0
    fig, axes = plt.subplots(1, 3, figsize=(TEXT_WIDTH_IN, 2.4))
    for ax, sig in zip(axes, SIGNALS):
        mask = day if sig == "pv_kw" else np.ones_like(day)
        errs = {t: (mats[t][sig] - mats["perfect"][sig])[mask] for t in ("best", "reference")}
        lim = np.percentile(np.abs(np.concatenate(list(errs.values()))), 99.5)
        bins = np.linspace(-lim, lim, 81)
        for tier in ("reference", "best"):
            e = errs[tier]
            ax.hist(e, bins=bins, density=True, histtype="step", color=COLOR[tier],
                    lw=WIDTH[tier], label=lab[tier])
            ax.axvline(e.mean(), ymax=0.76, color=COLOR[tier], lw=0.9, ls="--")
        ax.axvline(0, ymax=0.76, color=AXIS, lw=0.8)
        ax.set_ylim(0, ax.get_ylim()[1] * 1.3)          # headroom for the bias note
        ax.set_title(SIGNAL_TITLE[sig].replace(" (AC)", ", daylight") if sig == "pv_kw"
                     else SIGNAL_TITLE[sig], loc="left", color=INK)
        ax.set_xlabel("Forecast - measured (kW)")
        ax.set_yticks([])
        ax.text(0.98, 0.95, "\n".join(f"{name} bias {errs[t].mean():+.1f} kW" for t, name in
                                      (("best", "best"), ("reference", "ref."))),
                transform=ax.transAxes, ha="right", va="top", fontsize=7, color=INK2)
    axes[0].set_ylabel("Density")
    h, l = axes[0].get_legend_handles_labels()
    legend_top(fig, h[::-1], l[::-1], ncol=2)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    save(fig, out, "fig_error_distribution")


def fig_week(st: pd.DataFrame, out: Path, days: int = 7) -> None:
    w = st.iloc[: days * STEPS_PER_DAY]
    lab = short_labels()
    fig, axes = plt.subplots(2, 1, figsize=(TEXT_WIDTH_IN, 3.8), sharex=True)
    for ax, sig, ylabel in ((axes[0], "load", "Load (kW)"), (axes[1], "pv", "PV power (kW)")):
        for tier, name in (("reference", "reference"), ("best", "best"), ("perfect", "measured")):
            ax.plot(w["timestamp"], w[f"{sig}_{name}_kw"], color=COLOR[tier], lw=WIDTH[tier] * 0.8,
                    label=lab[tier])
        ax.set_ylabel(ylabel)
        for d in pd.date_range(w["timestamp"].iloc[0], periods=days + 1, freq="D"):
            ax.axvline(d, color=AXIS, lw=0.6, ls="--", zorder=0)
    axes[0].set_title("Day-ahead forecasts issued at 00:00, stitched end to end",
                      loc="left", color=INK)
    axes[-1].xaxis.set_major_locator(mdates.DayLocator())
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
    h, l = axes[0].get_legend_handles_labels()
    legend_top(fig, h[::-1], l[::-1])
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, out, "fig_week_day_ahead")


def by_hour(origins, mats) -> pd.DataFrame:
    """MAE by the clock hour of the TARGET step, pooled over every lead."""
    n, H = mats["perfect"]["load_kw"].shape
    tgt = (origins.to_numpy()[:, None] + (np.arange(H) * np.timedelta64(5, "m"))[None, :])
    hour = pd.DatetimeIndex(tgt.ravel()).hour.to_numpy().reshape(n, H)
    rows = []
    for hr in range(24):
        m = hour == hr
        rec = {"hour": hr}
        for tier in ("best", "reference"):
            for sig in SIGNALS:
                rec[f"{tier}_{sig}_mae"] = float(np.abs(mats[tier][sig][m]
                                                        - mats["perfect"][sig][m]).mean())
        rows.append(rec)
    return pd.DataFrame(rows)


def fig_by_hour(bh: pd.DataFrame, out: Path) -> None:
    lab = short_labels()
    fig, axes = plt.subplots(1, 3, figsize=(TEXT_WIDTH_IN, 2.3), sharey=True)
    for ax, sig in zip(axes, SIGNALS):
        for tier in ("reference", "best"):
            ax.step(np.append(bh["hour"], 24), np.append(bh[f"{tier}_{sig}_mae"],
                                                        bh[f"{tier}_{sig}_mae"].iloc[-1]),
                    where="post", color=COLOR[tier], lw=WIDTH[tier], label=lab[tier])
        ax.set_title(SIGNAL_TITLE[sig], loc="left", color=INK)
        ax.set_xlim(0, 24)
        ax.set_xticks([0, 6, 12, 18, 24])
        ax.set_xlabel("Hour of day")
    axes[0].set_ylabel("MAE (kW)")
    axes[0].set_ylim(bottom=0)
    h, l = axes[0].get_legend_handles_labels()
    legend_top(fig, h[::-1], l[::-1], ncol=2)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    save(fig, out, "fig_mae_by_hour")


# ====================================================================== main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--study", default="mpc_inputs")
    a = ap.parse_args(argv)

    study = Path(a.study)
    figs, metrics, csv = study / "figures", study / "metrics", study / "csv"
    for d in (figs, metrics, csv):
        d.mkdir(parents=True, exist_ok=True)
    origins, mats, meta = load_study(study)

    st = stitched(origins, mats)
    st.to_csv(csv / "day_ahead_stitched.csv", index=False, float_format="%.3f",
              date_format="%Y-%m-%d %H:%M")
    powerfactory_series(st, study / "powerfactory")
    daily = daily_mae(origins, mats)
    daily.to_csv(metrics / "daily_mae.csv", index=False, float_format="%.3f")
    bh = by_hour(origins, mats)
    bh.to_csv(metrics / "mae_by_hour_of_day.csv", index=False, float_format="%.3f")

    fig_sample_days(origins, mats, meta, figs, metrics)
    fig_mae_by_lead(study, meta, figs)
    fig_daily_mae(daily, figs)
    fig_error_distribution(mats, figs)
    fig_week(st, figs)
    fig_by_hour(bh, figs)
    for p in sorted(figs.glob("*.pdf")):
        print(f"  {p}")
    print(f"-> {study}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
