"""
The heuristic MPC over the 2002 profiles, and the same yardstick for any dispatch log
=====================================================================================

Runs mpc_model.HeuristicMPC in closed loop over the load and PV profiles: at each 5-min step
it decides over the horizon, applies the first move, and the battery state moves on. The
model is the MPC's own (losses as a share, DG_1 as the slack), not PowerFactory's load flow.

By default it runs up to each operating point of ctrl.py (POINTS), over the same period as
the rule-based log of that point when there is one, else QDS_WARMUP_DAYS before it. Each
run writes MPC_<point>.csv with the columns of ctrl.py's dispatch logs (QDS_COLUMNS) and a
few of its own after them. Put those files in ctrl.py's DISPATCH_DIR (or pass --dispatch-dir)
and its stability study reads the MPC's state at each point like the rule-based one's.

Every run, and every log passed with --evaluate, is scored with the same cost (fuel, starts,
curtailment, battery wear) and the same stability margins (S1-S7), so the controllers can be
compared on one yardstick.

  python mpc_run.py                                   OP1-OP3
  python mpc_run.py --points OP3 --days 2             one point, two days before it
  python mpc_run.py --start "2002-07-01 00:00" --end "2002-07-07 23:55"
  python mpc_run.py --evaluate stability_rms_rule_based_complete/dispatch/Rule-based_OP1.csv
  python mpc_run.py --set m_star=0.3 --set "constraint_modes={'S1':'hard','S2':'report'}"
"""

import argparse
import csv
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta

import mpc_model as mm

HERE = os.path.dirname(os.path.abspath(__file__))
LOAD_CSV = os.path.join(HERE, "aggregate_total_load.csv")
PV_CSV = os.path.join(HERE, "pv_ac_profile_measured.csv")
OUT_DIR = os.path.join(HERE, "mpc_results")
RULE_LOG_DIRS = [os.path.join(HERE, "stability_rms_rule_based_complete", "dispatch")]
CONTROLLER = "MPC"
QDS_COLUMNS = ["time", "load_kW", "pv_avail_kW", "pv_kW", "pv_curtailed_kW",
               "bess_kW", "bess_soc_pct", "other_kW", "dispatch_signal_kW",
               "DG1_kW", "DG1_loading_pct", "DG2_on", "DG2_kW",
               "DG2_on_level_kW", "DG2_off_level_kW"]
EXTRA_COLUMNS = ["margin", "binding", "fuel_L", "cost_R", "rule_p1_target_kW", "rule_soc_floor",
                 "horizon_min_margin", "feasible_candidates"]
# ctrl.py's operating points and lead-in, if ctrl.py cannot be read
POINTS = {
    "OP1": dict(text="Peak demand", time="2002-07-14 11:20", load=792.01, pv_avail=190.21),
    "OP2": dict(text="Largest excess PV", time="2002-02-25 12:05", load=114.5, pv_avail=599.29),
    "OP3": dict(text="Worst PV week, evening (DG_2 on)", time="2002-07-30 20:30", load=436.32, pv_avail=0.0),
}
WARMUP_DAYS = 7


def ctrl_settings():
    """POINTS, QDS_WARMUP_DAYS and DISPATCH_DIR from ctrl.py (imported without running its study)."""
    global POINTS, WARMUP_DAYS
    before = os.environ.get("STABILITY_RMS_IMPORT_ONLY")
    os.environ["STABILITY_RMS_IMPORT_ONLY"] = "1"
    try:
        sys.path.insert(0, HERE)
        import ctrl
        POINTS, WARMUP_DAYS = dict(ctrl.POINTS), ctrl.QDS_WARMUP_DAYS
        RULE_LOG_DIRS.append(ctrl.DISPATCH_DIR)
        return ctrl.DISPATCH_DIR
    except Exception as e:
        print(f"!! ctrl.py could not be read ({type(e).__name__}: {e}): its POINTS as of 2026-10-16 are used")
        return None
    finally:
        if before is None:
            os.environ.pop("STABILITY_RMS_IMPORT_ONLY", None)
        else:
            os.environ["STABILITY_RMS_IMPORT_ONLY"] = before


# ============================================================================
# PROFILES
# ============================================================================
def read_profiles():
    """(times 'YYYY-MM-DD HH:MM', load kW, PV kW), one per step."""
    with open(LOAD_CSV, newline="", encoding="utf-8-sig") as fh:
        load = [(r["timestamp"][:16], float(r["total_active_power_kw"])) for r in csv.DictReader(fh)]
    with open(PV_CSV, newline="", encoding="utf-8-sig") as fh:
        pv = {r["timestamp"][:16]: float(r["p_ac_kw"]) for r in csv.DictReader(fh)}
    times = [t for t, _ in load]
    missing = [t for t in times if t not in pv]
    if missing:
        raise SystemExit(f"PV profile has no value at {len(missing)} load times (first {missing[0]})")
    return times, [v for _, v in load], [pv[t] for t in times]


def forecast(kind, series, i, n, day):
    """The forecast of a series over n steps from step i. 'perfect' = what happens; 'yesterday' =
    the value 24 h earlier (now is measured)."""
    if kind == "perfect":
        return series[i:i + n]
    return [series[i]] + [series[j - day] if j >= day else series[j] for j in range(i + 1, i + n)]


# ============================================================================
# THE CLOSED LOOP
# ============================================================================
def run(p, times, load, pv, i0, i1, kind="perfect", label="", quiet=False):
    mpc = mm.HeuristicMPC(p)
    st = mm.State(soc=p.soc_init, locked=p.soc_init <= p.soc_min)
    day = round(24 * 60 / p.dt_min)
    rows, wall = [], time.time()
    for i in range(i0, i1 + 1):
        n = min(p.horizon, len(load) - i)
        d = mpc.decide(st, forecast(kind, load, i, n, day), forecast(kind, pv, i, n, day))
        op = mm.operate(p, load[i], pv[i], d.u2, st.soc, st.locked, d.rule)
        cost, _ = mm.step_cost(p, op, op.u2 > st.u2)
        signal = (1 + p.losses) * load[i] - pv[i] - mm.discharge_max(p, st.soc, st.locked)
        st = mm.advance(p, st, op)
        rows.append({
            "time": times[i], "load_kW": load[i], "pv_avail_kW": pv[i], "pv_kW": op.pv,
            "pv_curtailed_kW": op.curtail, "bess_kW": op.bess, "bess_soc_pct": 100.0 * st.soc,
            "other_kW": 0.0, "dispatch_signal_kW": signal, "DG1_kW": op.p1,
            "DG1_loading_pct": 100.0 * op.p1 / p.dg1_rating_kw, "DG2_on": op.u2, "DG2_kW": op.p2,
            "DG2_on_level_kW": 0.0, "DG2_off_level_kW": 0.0,
            "margin": op.m, "binding": op.binding, "fuel_L": mm.fuel_l(p, op), "cost_R": cost,
            "rule_p1_target_kW": d.rule.p1_target, "rule_soc_floor": d.rule.soc_floor,
            "horizon_min_margin": d.min_margin, "feasible_candidates": d.feasible})
        done = i - i0 + 1
        if not quiet and (done % day == 0 or i == i1):
            el = time.time() - wall
            print(f"  {label} {times[i][:10]} ({done / (i1 - i0 + 1):4.0%})  SOC {st.soc:4.0%}  "
                  f"{el / 60:4.1f} min, about {el / done * (i1 - i) / 60:4.1f} min left", flush=True)
    return rows


def write_log(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(QDS_COLUMNS + EXTRA_COLUMNS)
        for r in rows:
            w.writerow([f"{r[c]:.3f}" if isinstance(r[c], float) else r[c] for c in QDS_COLUMNS + EXTRA_COLUMNS])


# ============================================================================
# THE YARDSTICK
# ============================================================================
def read_log(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for c in QDS_COLUMNS[1:]:
            r[c] = float(r[c])
    return rows


def score(p, rows):
    """Cost and stability of a dispatch log (MPC or not), on the MPC's model."""
    soc0 = mm.soc_before(p, rows[0]["bess_soc_pct"] / 100.0, rows[0]["bess_kW"])
    s = dict(steps=len(rows), first=rows[0]["time"], last=rows[-1]["time"], fuel_dg1=0.0, fuel_dg2=0.0,
             starts=0, dg2_h=0.0, curtail_kwh=0.0, pv_avail_kwh=0.0, load_kwh=0.0, bess_in=0.0, bess_out=0.0,
             below_min=0, above_prime=0, m_min=mm.INF, m_neg=0, m_low=0, binding={}, s5_neg=0)
    prev = 0
    for r in rows:
        u2 = int(r["DG2_on"] > 0.5)
        op = mm.Op(r["load_kW"], r["pv_avail_kW"], max(r["pv_avail_kW"] - r["pv_kW"], 0.0), r["bess_kW"], u2,
                   r["DG1_kW"], r["DG2_kW"] if u2 else 0.0)
        op.m, op.binding = mm.margins(p, op)
        f1 = p.dg1_fuel_a + p.dg1_fuel_b * max(op.p1, 0.0)
        s["fuel_dg1"] += f1 * p.dt_h
        if u2:
            s["fuel_dg2"] += (p.dg2_fuel_a + p.dg2_fuel_b * max(op.p2, 0.0)) * p.dt_h
            s["dg2_h"] += p.dt_h
        s["starts"] += u2 > prev
        prev = u2
        s["curtail_kwh"] += op.curtail * p.dt_h
        s["pv_avail_kwh"] += op.pv_avail * p.dt_h
        s["load_kwh"] += op.load * p.dt_h
        s["bess_out" if op.bess > 0 else "bess_in"] += abs(op.bess) * p.dt_h
        # 2 %: the load flow's own losses put the rule-based DG_1 a few kW off its set level
        s["below_min"] += op.p1 < 0.98 * p.dg1_p_min
        s["above_prime"] += op.p1 > 1.02 * p.dg1_p_max
        s["m_min"] = min(s["m_min"], op.m)
        if op.m < 0:
            s["m_neg"] += 1
        elif op.m < p.m_star:
            s["m_low"] += 1
        if op.m < p.m_star:
            s["binding"][op.binding] = s["binding"].get(op.binding, 0) + 1
        if p.constraint_modes.get("S5") == "report":
            s["s5_neg"] += any(c == "S5" and m < 0 for c, _, _, _, m, _ in mm.margin_table(p, op))
    s["soc0"], s["soc_end"] = soc0, rows[-1]["bess_soc_pct"] / 100.0
    s["fuel_l"] = s["fuel_dg1"] + s["fuel_dg2"]
    s["cost_fuel"] = p.diesel_price * s["fuel_l"]
    s["cost_start"] = p.c_start * s["starts"]
    s["cost_curtail"] = p.c_curtail * s["curtail_kwh"]
    s["cost_wear"] = p.c_wear * (s["bess_in"] + s["bess_out"])
    s["cost"] = s["cost_fuel"] + s["cost_start"] + s["cost_curtail"] + s["cost_wear"]
    # the energy the battery ends with, valued as the MPC's terminal cost values it
    s["soc_value"] = p.c_terminal_value * (s["soc_end"] - soc0) * p.bess_e_kwh
    s["cost_adjusted"] = s["cost"] - s["soc_value"]
    return s


def score_lines(name, s, p):
    days = s["steps"] * p.dt_h / 24
    top = ", ".join(f"{k} {v}" for k, v in sorted(s["binding"].items(), key=lambda x: -x[1])[:4]) or "-"
    return [
        f"{name}: {s['first']} -> {s['last']} ({days:.1f} days, {s['steps']} steps)",
        f"   fuel {s['fuel_l']:8.0f} L (DG_1 {s['fuel_dg1']:.0f}, DG_2 {s['fuel_dg2']:.0f})   "
        f"DG_2 {s['starts']} starts, {s['dg2_h']:.1f} h on",
        f"   PV curtailed {s['curtail_kwh']:7.0f} of {s['pv_avail_kwh']:.0f} kWh   battery in {s['bess_in']:.0f} / "
        f"out {s['bess_out']:.0f} kWh   SOC {s['soc0']:.0%} -> {s['soc_end']:.0%}",
        f"   cost R {s['cost']:9.0f} (fuel {s['cost_fuel']:.0f}, starts {s['cost_start']:.0f}, curtailment "
        f"{s['cost_curtail']:.0f}, wear {s['cost_wear']:.0f});  with the SOC change valued: R {s['cost_adjusted']:.0f}",
        f"   margin m_k: min {s['m_min']:.2f}; steps below 0: {s['m_neg']}, between 0 and m* = {p.m_star}: "
        f"{s['m_low']}   DG_1 below its minimum: {s['below_min']}, above prime: {s['above_prime']}",
        f"   where m_k < m*: {top}" + (f"   (S5 ROCOF, reported only, < 0 at {s['s5_neg']} steps)"
                                        if s["s5_neg"] else ""),
    ]


# ============================================================================
# MAIN
# ============================================================================
def rule_log(pid):
    for folder in RULE_LOG_DIRS:
        path = os.path.join(folder, f"Rule-based_{pid}.csv")
        if os.path.isfile(path):
            return path
    return None


def periods(args, times, p):
    """[(label, i0, i1, rule-based log or None, SOC at the start or None)]. A run that starts
    inside a rule-based log starts from that log's SOC there."""
    index = {t: i for i, t in enumerate(times)}

    def at(text):
        if text not in index:
            raise SystemExit(f"{text} is not a step of the profiles ({times[0]} -> {times[-1]})")
        return index[text]

    if args.start or args.end:
        if not (args.start and args.end):
            raise SystemExit("--start and --end go together")
        return [(f"{args.start[:10]}_{args.end[:10]}", at(args.start), at(args.end), None, None)]
    out = []
    for pid in args.points or list(POINTS):
        if pid not in POINTS:
            raise SystemExit(f"no operating point {pid} (ctrl.py POINTS: {', '.join(POINTS)})")
        i1 = at(POINTS[pid]["time"])
        log = rule_log(pid)
        rows = read_log(log) if log else []
        if args.days is not None:
            first = datetime.strptime(times[i1], "%Y-%m-%d %H:%M") - timedelta(days=args.days)
            i0 = at(first.strftime("%Y-%m-%d %H:%M"))
        elif rows:
            i0 = at(rows[0]["time"])
        else:
            i0 = max(0, i1 - round(WARMUP_DAYS * 24 * 60 / p.dt_min))
        row = next((r for r in rows if r["time"] == times[i0]), None)
        soc0 = mm.soc_before(p, row["bess_soc_pct"] / 100.0, row["bess_kW"]) if row else None
        out.append((pid, i0, i1, log, soc0))
    return out


def one(job):
    """One closed-loop run (a separate process when --jobs > 1)."""
    label, i0, i1, overrides, kind, out_dir, quiet = job
    p = mm.load_params(overrides=overrides)
    times, load, pv = read_profiles()
    started = time.time()
    rows = run(p, times, load, pv, i0, i1, kind, label, quiet)
    path = os.path.join(out_dir, f"{CONTROLLER}_{label}.csv")
    write_log(rows, path)
    return label, path, time.time() - started


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--points", nargs="*", help="operating points of ctrl.py (default: all)")
    ap.add_argument("--days", type=float, help="days before the point (default: as the rule-based log)")
    ap.add_argument("--start", help='"YYYY-MM-DD HH:MM": a period instead of the points')
    ap.add_argument("--end")
    ap.add_argument("--forecast", choices=("perfect", "yesterday"), default="perfect")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a parameter")
    ap.add_argument("--out", default=OUT_DIR, help="folder for the logs and the report")
    ap.add_argument("--dispatch-dir", action="store_true", help="write the logs to ctrl.py's DISPATCH_DIR")
    ap.add_argument("--evaluate", nargs="*", metavar="CSV", help="only score these dispatch logs")
    ap.add_argument("--jobs", type=int, default=min(3, os.cpu_count() or 1))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    dispatch_dir = ctrl_settings()
    overrides = {}
    for item in args.set:
        key, _, value = item.partition("=")
        overrides[key.strip()] = mm.parse_value(value.strip())
    p = mm.load_params(overrides=overrides)
    report = [f"Heuristic MPC, {datetime.now():%Y-%m-%d %H:%M}"] + ["!! " + n for n in p.notes]
    report += ["Parameters that are open, assumed, calibrated or overridden:"]
    report += [f"   {k} = {getattr(p, k)}  ({p.source[k]})" for k in p.source
               if p.source[k].startswith(("OPEN", "ASSUMED", "calibrated", "override"))]

    if args.evaluate is not None:
        for path in args.evaluate:
            report += [""] + score_lines(os.path.basename(path), score(p, read_log(path)), p)
        print("\n".join(report))
        return

    times, load, pv = read_profiles()
    jobs = periods(args, times, p)
    out_dir = dispatch_dir if args.dispatch_dir and dispatch_dir else args.out
    print(f"Running {len(jobs)} period(s), horizon {p.horizon} steps, {len(mm.HeuristicMPC(p).rules)} rules, "
          f"forecast {args.forecast}; logs to {out_dir}")
    work = [(label, i0, i1, dict(overrides, **({"soc_init": soc0} if soc0 is not None else {})),
             args.forecast, out_dir, args.quiet) for label, i0, i1, _, soc0 in jobs]
    if args.jobs > 1 and len(work) > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            results = list(ex.map(one, work))
    else:
        results = [one(w) for w in work]

    for (label, path, secs), (_, i0, i1, log, _) in zip(results, jobs):
        report += ["", f"=== {label} ({POINTS.get(label, {}).get('text', 'period')}), "
                       f"{secs / 60:.1f} min, log {path}"]
        report += score_lines("MPC", score(p, read_log(path)), p)
        if log:
            rule = [r for r in read_log(log) if times[i0] <= r["time"] <= times[i1]]
            report += score_lines("Rule-based (its log, scored the same way)", score(p, rule), p)
    text = "\n".join(report)
    print("\n" + text)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "mpc_run_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n\nParameters\n" + "\n".join(p.report()) + "\n")


if __name__ == "__main__":
    main()
