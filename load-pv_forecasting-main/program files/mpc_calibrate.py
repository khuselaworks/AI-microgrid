"""
Calibration of the MPC's stability model
========================================

Fits, from the data in this folder, the quantities the handoff leaves open (section 7), and
writes them to mpc_calibration.json, which mpc_model.load_params() reads:

  rho          PV fast-drop fraction: the q-quantile of the drop of PV output over one step,
               as a share of the output before it (steps with PV above PV_MIN_SHARE of rating)
                                                      <- pv_ac_profile_measured.csv
  lambda_up    credible load step up / loss, share of the load: q-quantile of the change over
  lambda_down  one step                               <- aggregate_total_load.csv
  tau_eff      energy-deficit time of the nadir model df = f0 dP tau / (2 E), from every RMS
  tau_eff_over run of ctrl.py with a single step of power (loss of a source, load pick-up, loss
               of load) that stayed stable: tau = 2 E df / (f0 dP). dP is what the surviving
               diesels took up by the end (so the voltage dependence of the load is in it), E
               the kinetic energy of the surviving diesels. The largest value is kept.
                                                      <- stability_rms_results.csv
  kappa        inverter share: the largest (PV + |BESS|) / running genset kVA at which ctrl.py's
               RMS runs stayed stable. A lower bound: no run has failed on it yet.

It also checks the windowed-ROCOF model of S5 against the 500 ms ROCOF the RMS runs measured,
and how long the frequency stayed above 51.5 Hz after each loss of load (the inverter trip
there waits 4 s), from the curve files.

  python mpc_calibrate.py [--rms DIR] [--quantile 0.95]
"""

import argparse
import csv
import json
import os
from datetime import datetime

import mpc_model as mm

HERE = os.path.dirname(os.path.abspath(__file__))
PV_CSV = os.path.join(HERE, "pv_ac_profile_measured.csv")
LOAD_CSV = os.path.join(HERE, "aggregate_total_load.csv")
RMS_DIR = os.path.join(HERE, "stability_rms_rule_based_complete")
QUANTILE = 0.95
PV_MIN_SHARE = 0.10         # steps with PV below this share of its rating are left out of rho
F_INVERTER_OVER = 51.5      # Hz (NRS 097-2-1, 4 s)


def quantile(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))] if v else None


def column(path, name):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [float(r[name]) for r in csv.DictReader(fh)]


def fit_profiles(p, q):
    pv = column(PV_CSV, "p_ac_kw")
    load = column(LOAD_CSV, "total_active_power_kw")
    floor = PV_MIN_SHARE * p.pv_p_max
    drops = [max(0.0, (a - b) / a) for a, b in zip(pv, pv[1:]) if a > floor]
    rel = [(b - a) / a for a, b in zip(load, load[1:]) if a > 0]
    ups, downs = [max(0.0, x) for x in rel], [max(0.0, -x) for x in rel]
    out = {
        "rho": dict(value=quantile(drops, q),
                    source=f"{q:.0%} quantile of the PV drop per step, PV > {floor:.0f} kW ({len(drops)} steps)",
                    quantiles={f"{x:g}": quantile(drops, x) for x in (0.9, 0.95, 0.99, 0.999)}),
        "lambda_up": dict(value=quantile(ups, q), source=f"{q:.0%} quantile of the load rise per step",
                          quantiles={f"{x:g}": quantile(ups, x) for x in (0.9, 0.95, 0.99, 0.999)}),
        "lambda_down": dict(value=quantile(downs, q), source=f"{q:.0%} quantile of the load fall per step",
                            quantiles={f"{x:g}": quantile(downs, x) for x in (0.9, 0.95, 0.99, 0.999)}),
    }
    return out


def f(row, key):
    try:
        return float(row[key])
    except (KeyError, ValueError, TypeError):
        return None


def time_above(path, limit):
    """Seconds the frequency at any bus was above `limit` in a curve file."""
    if not os.path.isfile(path):
        return None
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    cols = [c for c in rows[0] if c.startswith("f_") and c.endswith("_Hz")]
    t = [float(r["t_s"]) for r in rows]
    total = 0.0
    for i in range(1, len(rows)):
        if max(float(rows[i][c]) for c in cols) > limit:
            total += t[i] - t[i - 1]
    return total


def fit_rms(p, rms_dir):
    path = os.path.join(rms_dir, "stability_rms_results.csv")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    runs, shares = [], []
    for r in rows:
        if not r.get("verdict") or r["verdict"] == "not applicable":
            continue
        case = r["case"].split("_")[0]
        dg2_on = (f(r, "p_dg2_0") or 0.0) > 1.0
        s = p.dg1_s_kva + (p.dg2_s_kva if dg2_on else 0.0)
        share = ((f(r, "p_pv_0") or 0.0) + abs(f(r, "p_bess_0") or 0.0)) / s
        if r["verdict"] == "stable":
            shares.append((share, r["case"]))
        if r["verdict"] != "stable" or case[0] not in "GPR":
            continue
        lost2 = case == "G2"
        e = p.dg1_e_kws + (p.dg2_e_kws if dg2_on and not lost2 else 0.0)
        dp = (f(r, "p_dg1_end") or 0.0) - (f(r, "p_dg1_0") or 0.0)
        if dg2_on and not lost2:
            dp += (f(r, "p_dg2_end") or 0.0) - (f(r, "p_dg2_0") or 0.0)
        over = case[0] == "R"
        df = (f(r, "f_max") - p.f0) if over else (p.f0 - f(r, "f_min"))
        if abs(dp) < 1.0 or df <= 0:
            continue
        tau = 2.0 * e * df / (p.f0 * abs(dp))
        # S5 model: ROCOF over the window = f0 dP / (2E) x min(T, tau) / T
        model_r500 = p.f0 * abs(dp) / (2 * e) * min(0.5, tau) / 0.5
        above = time_above(os.path.join(rms_dir, f"{r['case']}_curves.csv"), F_INVERTER_OVER) if over else None
        runs.append(dict(case=r["case"], text=r["text"], direction="up" if over else "down", dp_kw=abs(dp),
                         e_kws=e, df_hz=df, tau_s=tau, rocof_500_measured=f(r, "rocof_500"),
                         rocof_500_model=model_r500, above_51_5_s=above))
    down = [x["tau_s"] for x in runs if x["direction"] == "down"]
    up = [x["tau_s"] for x in runs if x["direction"] == "up"]
    best_share = max(shares) if shares else (None, None)
    return {
        "tau_eff": dict(value=max(down), source=f"largest of {len(down)} stable RMS steps down "
                                                f"(range {min(down):.3f}-{max(down):.3f} s)"),
        "tau_eff_over": dict(value=max(up), source=f"largest of {len(up)} stable RMS steps up "
                                                   f"(range {min(up):.3f}-{max(up):.3f} s)"),
        "kappa": dict(value=best_share[0], source=f"largest inverter share in a stable RMS run ({best_share[1]}); "
                                                  "a lower bound"),
    }, runs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rms", default=RMS_DIR, help="folder with ctrl.py's stability_rms_results.csv")
    ap.add_argument("--quantile", type=float, default=QUANTILE)
    ap.add_argument("--out", default=mm.CALIBRATION_JSON)
    args = ap.parse_args()

    p = mm.load_params(calibration=None)
    cal = dict(made=f"{datetime.now():%Y-%m-%d %H:%M}", quantile=args.quantile)
    cal.update(fit_profiles(p, args.quantile))
    rms, runs = fit_rms(p, args.rms)
    cal.update(rms)
    cal["rms_runs"] = runs
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(cal, fh, indent=2)

    print(f"Calibration written to {args.out}\n")
    for key in mm.CALIBRATED:
        print(f"  {key:<13} {cal[key]['value']:8.3f}   {cal[key]['source']}")
    print(f"\n  {'RMS run':<20} {'dP kW':>7} {'E kWs':>6} {'df Hz':>6} {'tau s':>6}  "
          f"{'ROCOF500 meas':>13} {'model':>6}  {'>51.5 Hz s':>10}")
    for x in runs:
        above = f"{x['above_51_5_s']:10.2f}" if x["above_51_5_s"] is not None else ""
        print(f"  {x['case']:<20} {x['dp_kw']:7.1f} {x['e_kws']:6.0f} {x['df_hz']:6.3f} {x['tau_s']:6.3f}  "
              f"{x['rocof_500_measured']:13.2f} {x['rocof_500_model']:6.2f}  {above}")


if __name__ == "__main__":
    main()
