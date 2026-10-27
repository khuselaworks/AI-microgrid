"""
Tube plot of the MPC: the plan, the closed loop and the forecast-error tube
===========================================================================

For one decision instant: the MPC's plan over its horizon (the battery rule and DG_2
schedule it chose, replayed step by step), the closed loop that follows (the MPC deciding
again at every step on what is measured), and where the plan would take the island if load
and PV came out otherwise. For that, the plan's rule and schedule are replayed over
forecast-error scenarios: the errors of the 'yesterday' forecast (the value 24 h earlier)
on days of the same season, at the same time of day, added to the plan's forecast.

Every --every steps the 95 % region of the scenarios is drawn in the plane of SOC and DG_1
power (--z dg1) or of SOC and the stability margin m_k (--z margin), coloured by what the
scenarios do to m_k there, read as a chance constraint at 95 %:
  green    m_k >= m* in 95 % of the scenarios
  orange   m_k >= 0 in 95 %, but below m* in more than 5 %
  red      m_k < 0 (a hard stability constraint broken) in more than 5 %
The tube is open loop: inside a scenario the plan is not made again, as the closed loop
does. That it is wider than what the closed loop meets is the case for deciding every step.

The MPC is ctrl.py's (imported without running its study) on its own model of the island,
not PowerFactory's load flows; mpc_model.py's if ctrl.py cannot be read. The state at the
instant comes from a dispatch log (--log, or MPC_<point>.csv in ctrl.py's DISPATCH_DIR or in
mpc_results), else from the MPC run on its own model from SOC_INIT, --warmup days before.

For each instant, in --out:
  tube3d_<label>.png   the tube in 3-D (time, SOC, DG_1 or m_k), with its shadows: SOC on the
                       floor, DG_1 or m_k on the wall (read the values there: a point in front
                       is drawn lower than one behind it)
  tube2d_<label>.png   the same against time in three panels: SOC, DG_1 power, m_k
  tube_<label>.csv     every step: forecast, plan, closed loop, scenario quantiles, risk

Needs numpy and matplotlib (Anaconda's Python has them).

  python mpc_tube_plot.py                                  OP1-OP3, decided at the point
  python mpc_tube_plot.py --points OP3 --lead 2.5          decided 2.5 h before OP3
  python mpc_tube_plot.py --time "2002-07-30 18:00" --z margin
  python mpc_tube_plot.py --forecast perfect --scenarios 1000 --region hull --pdf
  python mpc_tube_plot.py --points OP3 --log MPC_OP3.csv --set m_star=0.3
"""

import argparse
import ast
import csv
import math
import os
import random
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

try:
    import numpy as np
except ImportError:
    raise SystemExit("mpc_tube_plot.py needs numpy and matplotlib: run it with a Python that has them "
                     "(Anaconda's, for one)")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOAD_CSV = os.path.join(HERE, "aggregate_total_load.csv")
PV_CSV = os.path.join(HERE, "pv_ac_profile_measured.csv")
OUT_DIR = os.path.join(HERE, "mpc_tube")
LOG_DIRS = [os.path.join(HERE, "mpc_results")]      # mpc_run.py's logs; ctrl.py's DISPATCH_DIR goes first
# ctrl.py's operating points and lead-in, if ctrl.py cannot be read
POINTS = {
    "OP1": dict(text="Peak demand", time="2002-07-14 11:20", load=792.01, pv_avail=190.21),
    "OP2": dict(text="Largest excess PV", time="2002-02-25 12:05", load=114.5, pv_avail=599.29),
    "OP3": dict(text="Worst PV week, evening (DG_2 on)", time="2002-07-30 20:30", load=436.32, pv_avail=0.0),
}
WARMUP_DAYS = 7
SCENARIOS = 400
SEASON_DAYS = 30            # the error days lie within this many days of the instant
EVERY = 3                   # a region every 3 steps (15 min)
CHI2_95 = 5.991             # chi-square with 2 degrees of freedom at 95 %: the ellipse
M_SHOWN = (-1.0, 1.0)       # m_k is drawn within these (a step with no allowance at all has m = -9.99)

# the colours of ctrl.py's plots
INK, INK2, GRID, AXIS = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"
GOOD, SERIOUS, CRITICAL = "#0ca30c", "#ec835a", "#d03b3b"
DOTS, DASHES = (0, (1, 2)), (0, (4, 2))
LOOP = "#2a78d6"            # the closed loop: the first categorical slot
WALL = "#86b6ef"            # the constraint the plan heads for
SEVERITY = ("good", "serious", "critical")
STATUS_COLOUR = {"good": GOOD, "serious": SERIOUS, "critical": CRITICAL}
STATUS_TEXT = {"good": "m$_k$ ≥ m* in 95 % of the scenarios",
               "serious": "m$_k$ ≥ 0 in 95 %, below m* in more than 5 %",
               "critical": "m$_k$ < 0 in more than 5 % of the scenarios"}


# ============================================================================
# THE MPC AND THE PROFILES
# ============================================================================
def engine(name):
    """The MPC's functions: ctrl.py's (with S8; imported without running its study) or
    mpc_model.py's. Both have the same calls under these names."""
    global POINTS, WARMUP_DAYS
    if name in ("auto", "ctrl"):
        before = os.environ.get("STABILITY_RMS_IMPORT_ONLY")
        os.environ["STABILITY_RMS_IMPORT_ONLY"] = "1"
        try:
            import ctrl
            POINTS, WARMUP_DAYS = dict(ctrl.POINTS), ctrl.QDS_WARMUP_DAYS
            LOG_DIRS.insert(0, ctrl.DISPATCH_DIR)
            return SimpleNamespace(
                name="ctrl.py", forecast=ctrl.MPC_FORECAST,
                params=lambda o: ctrl.mpc_params(overrides=dict(ctrl.MPC_PARAMS, **o)),
                MPC=ctrl.HeuristicMPC, State=ctrl.MpcState, operate=ctrl.mpc_operate,
                next_soc=ctrl.mpc_next_soc, advance=ctrl.mpc_advance, margins=ctrl.mpc_margins,
                soc_before=ctrl.mpc_soc_before)
        except Exception as e:
            if name == "ctrl":
                raise SystemExit(f"ctrl.py could not be read ({type(e).__name__}: {e})")
            print(f"!! ctrl.py could not be read ({type(e).__name__}: {e}): mpc_model.py's MPC is used (no S8)")
        finally:
            if before is None:
                os.environ.pop("STABILITY_RMS_IMPORT_ONLY", None)
            else:
                os.environ["STABILITY_RMS_IMPORT_ONLY"] = before
    import mpc_model as mm
    return SimpleNamespace(
        name="mpc_model.py", forecast=None, params=lambda o: mm.load_params(overrides=o),
        MPC=mm.HeuristicMPC, State=mm.State, operate=mm.operate, next_soc=mm.next_soc,
        advance=mm.advance, margins=mm.margins, soc_before=mm.soc_before)


def parse_value(text):
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def read_profiles():
    """(times 'YYYY-MM-DD HH:MM', load kW, PV kW), one per step (as mpc_run.py)."""
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


def scenarios(p, series, i, n, base, count, season_days, rng, low=0.0, high=math.inf):
    """count forecast-error scenarios of a series over n steps from i: the errors of the
    'yesterday' forecast on other days within season_days of i, at the same time of day, added
    to the forecast base. Drawn with rng: call it for load and PV with generators seeded alike,
    so a scenario takes its load and PV errors from the same day."""
    day = round(24 * 60 / p.dt_min)
    pool = [j for j in range(i % day, len(series) - n, day)
            if j >= day and j != i and abs(j - i) <= season_days * day]
    if not pool:
        raise SystemExit(f"no days within {season_days} days of the instant to take forecast errors from")
    out = []
    for _ in range(count):
        j = rng.choice(pool)
        out.append([min(max(b + (series[j + k] - series[j + k - day] if k else 0.0), low), high)
                    for k, b in enumerate(base)])
    return out


# ============================================================================
# THE STATE AT THE INSTANT
# ============================================================================
def find_log(label):
    for folder in LOG_DIRS:
        path = os.path.join(folder, f"MPC_{label}.csv")
        if os.path.isfile(path):
            return path
    return None


def state_from_log(e, p, path, when):
    """The state at the start of the step `when` from a dispatch log (any controller; the SOC
    of a row is the one after its step): SOC, the SOC_MIN lockout, DG_2 and for how many steps
    it has been so. None if the log does not hold that step."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    at = next((n for n, r in enumerate(rows) if r["time"][:16] == when), None)
    if at is None:
        return None
    soc = e.soc_before(p, float(rows[0]["bess_soc_pct"]) / 100.0, float(rows[0]["bess_kW"]))
    locked = soc <= p.soc_min + 1e-6
    u2, t2 = 0, 10 ** 6
    for r in rows[:at]:
        soc = float(r["bess_soc_pct"]) / 100.0
        if soc <= p.soc_min + 1e-6:
            locked = True
        elif locked and soc >= p.soc_resume:
            locked = False
        on = int(float(r["DG2_on"]) > 0.5)
        t2 = t2 + 1 if on == u2 else 1
        u2 = on
    return e.State(soc, locked, u2, t2)


def step(e, p, mpc, prof, i, st, kind):
    """One step of the closed loop at i: (state after it, the step as dispatched)."""
    times, load, pv = prof
    day = round(24 * 60 / p.dt_min)
    n = min(p.horizon, len(load) - i)
    d = mpc.decide(st, forecast(kind, load, i, n, day), forecast(kind, pv, i, n, day))
    op = e.operate(p, load[i], pv[i], d.u2, st.soc, st.locked, d.rule)
    return e.advance(p, st, op), op


def warm_up(e, p, prof, i0, i, kind):
    """The state at step i: the MPC on its own model from SOC_INIT at step i0."""
    st = e.State(p.soc_init, p.soc_init <= p.soc_min)
    mpc = e.MPC(p)
    for j in range(i0, i):
        st, _ = step(e, p, mpc, prof, j, st, kind)
    return st


# ============================================================================
# THE PLAN, THE CLOSED LOOP AND THE TUBE
# ============================================================================
def replay(e, p, st, d, loads, pvs, binding=False):
    """The decision d (its rule and DG_2 schedule) over a load and PV series:
    [(SOC at the start of the step, the step as dispatched)]."""
    soc, locked, out = st.soc, st.locked, []
    for k, u in enumerate(d.schedule):
        op = e.operate(p, loads[k], pvs[k], u, soc, locked, d.rule)
        if binding:
            op.binding = e.margins(p, op)[1]
        out.append((soc, op))
        soc, locked = e.next_soc(p, soc, locked, op.bess)
    return out


def tube(e, p, prof, i, st, kind, count, season_days, seed):
    """Everything the plots and the table show of the decision at step i."""
    times, load, pv = prof
    day = round(24 * 60 / p.dt_min)
    n = min(p.horizon, len(load) - i)
    mpc = e.MPC(p)
    loads, pvs = forecast(kind, load, i, n, day), forecast(kind, pv, i, n, day)
    d = mpc.decide(st, loads, pvs)
    plan = replay(e, p, st, d, loads, pvs, binding=True)
    loop, s = [], st
    for j in range(i, i + n):
        soc = s.soc
        s, op = step(e, p, mpc, prof, j, s, kind)
        op.binding = e.margins(p, op)[1]
        loop.append((soc, op))
    sc_load = scenarios(p, load, i, n, loads, count, season_days, random.Random(seed))
    sc_pv = scenarios(p, pv, i, n, pvs, count, season_days, random.Random(seed), high=p.pv_p_max)
    runs = [replay(e, p, st, d, l, v) for l, v in zip(sc_load, sc_pv)]
    S = np.array([[100.0 * soc for soc, _ in r] for r in runs])
    P1 = np.array([[op.p1 for _, op in r] for r in runs])
    M = np.array([[op.m for _, op in r] for r in runs])
    below_target, below_zero = (M < p.m_star).mean(axis=0), (M < 0.0).mean(axis=0)
    status = ["critical" if z > 0.05 else "serious" if b > 0.05 else "good"
              for b, z in zip(below_target, below_zero)]
    t0 = datetime.strptime(times[i], "%Y-%m-%d %H:%M")
    return SimpleNamespace(
        p=p, d=d, st=st, n=n, t0=t0, kind=kind, count=count, loads=loads, pvs=pvs, plan=plan, loop=loop,
        clock=[t0 + timedelta(minutes=k * p.dt_min) for k in range(n)],
        hours=np.arange(n) * p.dt_min / 60.0,
        plan_soc=np.array([100.0 * s for s, _ in plan]), plan_p1=np.array([op.p1 for _, op in plan]),
        plan_m=np.array([op.m for _, op in plan]),
        loop_soc=np.array([100.0 * s for s, _ in loop]), loop_p1=np.array([op.p1 for _, op in loop]),
        loop_m=np.array([op.m for _, op in loop]),
        S=S, P1=P1, M=M, below_target=below_target, below_zero=below_zero, status=status)


def write_table(r, path):
    """tube_<label>.csv: one row per step of the horizon."""
    q = {name: np.percentile(a, [2.5, 50.0, 97.5], axis=0) for name, a in (("soc", r.S), ("DG1", r.P1), ("m", r.M))}
    head = ["step", "time", "load_forecast_kW", "pv_forecast_kW", "load_kW", "pv_avail_kW",
            "plan_soc_pct", "plan_DG1_kW", "plan_DG2_on", "plan_bess_kW", "plan_curtailed_kW", "plan_margin",
            "plan_binding", "loop_soc_pct", "loop_DG1_kW", "loop_DG2_on", "loop_bess_kW", "loop_curtailed_kW",
            "loop_margin", "loop_binding"]
    head += [f"{name}_p{x}" for name in q for x in ("2.5", "50", "97.5")]
    head += ["share_m_below_mstar", "share_m_below_0", "status"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(head)
        for k in range(r.n):
            (ps, po), (ls, lo) = r.plan[k], r.loop[k]
            row = [k, f"{r.clock[k]:%Y-%m-%d %H:%M}", r.loads[k], r.pvs[k], lo.load, lo.pv_avail,
                   100 * ps, po.p1, po.u2, po.bess, po.curtail, po.m, po.binding,
                   100 * ls, lo.p1, lo.u2, lo.bess, lo.curtail, lo.m, lo.binding]
            row += [q[name][x][k] for name in q for x in range(3)]
            row += [r.below_target[k], r.below_zero[k], r.status[k]]
            w.writerow([f"{v:.3f}" if isinstance(v, (float, np.floating)) else v for v in row])


# ============================================================================
# THE PLOTS
# ============================================================================
def span(arrays, limits=(), always=False, least=1.0, pad=0.06, near=0.3):
    """(low, high, limits shown): axis limits around the data, out to the limits that lie
    within `near` of its range (all of them if always), with a margin."""
    lo = min(float(np.min(a)) for a in arrays)
    hi = max(float(np.max(a)) for a in arrays)
    width = max(hi - lo, least)
    shown = [x for x in limits if always or lo - near * width <= x <= hi + near * width]
    lo, hi = min([lo] + shown), max([hi] + shown)
    room = pad * max(hi - lo, least)
    return lo - room, hi + room, shown


def hull(points):
    """Convex hull of [(x, y)], counter-clockwise (Andrew's monotone chain)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for q in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def region(x, z, how):
    """The 95 % region of the points (x, z) as a closed outline (xs, zs): the ellipse of a
    Gaussian with their mean and covariance, or the convex hull of the 95 % of them nearest
    the mean (Mahalanobis distance). None if they do not spread."""
    sx, sz = float(np.std(x)), float(np.std(z))
    if sx < 1e-9 and sz < 1e-9:
        return None
    xy = np.vstack([x, z])
    mu = xy.mean(axis=1)
    cov = np.cov(xy) + np.diag([(1e-6 * max(sx, sz)) ** 2 + 1e-12] * 2)
    if how == "hull":
        dev = xy - mu[:, None]
        d2 = np.einsum("ij,ij->j", dev, np.linalg.solve(cov, dev))
        keep = d2 <= np.quantile(d2, 0.95)
        pts = hull(list(zip(x[keep].tolist(), z[keep].tolist())))
        if len(pts) >= 3:
            xs, zs = zip(*(pts + pts[:1]))
            return np.array(xs), np.array(zs)
    w, v = np.linalg.eigh(cov)
    th = np.linspace(0.0, 2.0 * np.pi, 73)
    out = mu[:, None] + math.sqrt(CHI2_95) * v @ (np.sqrt(np.maximum(w, 0.0))[:, None] *
                                                 np.vstack([np.cos(th), np.sin(th)]))
    return out[0], out[1]


def soc_limits(r):
    """[(SOC %, text, line style)] that bound the plan: first the one it heads for."""
    p, rule = r.p, r.d.rule
    floor = 100.0 * max(rule.soc_floor, p.soc_min)
    floor_text = (f"SOC floor of the chosen rule, {floor:.0f} %" if rule.soc_floor > p.soc_min
                  else f"SOC$_{{min}}$ {floor:.0f} %")
    lower = (floor, floor_text, DASHES)
    upper = (100.0 * p.soc_max, f"SOC$_{{max}}$ {100 * p.soc_max:.0f} %", DASHES)
    target = (100.0 * p.soc_ref, f"SOC$_{{ref}}$ {100 * p.soc_ref:.0f} % (terminal target)", DOTS)
    change = r.plan_soc[-1] - r.plan_soc[0]
    if abs(change) < 0.05:                          # flat: the limit it stays nearer to
        change = (r.plan_soc[-1] - lower[0]) - (upper[0] - r.plan_soc[-1])
    heads = (lower, upper) if change <= 0 else (upper, lower)
    return [heads[0], heads[1], target]


def z_series(r, z_kind):
    """(scenarios, plan, closed loop, axis name, [(value, text, line style)], limits always shown)."""
    p = r.p
    if z_kind == "dg1":
        return (r.P1, r.plan_p1, r.loop_p1, "DG_1 power [kW]",
                [(p.dg1_p_min, f"DG_1 minimum {p.dg1_p_min:.0f} kW", DASHES),
                 (p.dg1_p_max, f"DG_1 prime {p.dg1_p_max:.0f} kW", DASHES)], False)
    clip = lambda a: np.clip(a, *M_SHOWN)
    return (clip(r.M), clip(r.plan_m), clip(r.loop_m), "Stability margin m$_k$",
            [(0.0, "m$_k$ = 0, hard limit", "-"), (p.m_star, f"m* = {p.m_star:g}, target", DASHES)], True)


def switches(series, before):
    """[(k, 1 on / 0 off)] where DG_2 is switched."""
    out, prev = [], before
    for k, u in enumerate(series):
        if u != prev:
            out.append((k, u))
        prev = u
    return out


def legend_handles(plan_text, wall=None):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    handles = [Line2D([], [], color=INK, ls=DOTS, marker="o", ms=5, mfc="white", mec=INK, label=plan_text),
               Line2D([], [], color=LOOP, marker="o", ms=3.5, label="Closed loop (decided again every step)")]
    handles.append(Patch(fc=WALL, alpha=0.5, label=wall) if wall else Line2D([], [], lw=0, label=" "))
    handles += [Patch(fc=STATUS_COLOUR[s], alpha=0.45, label=("95 % region: " if s == "good" else "... ") + STATUS_TEXT[s])
                for s in SEVERITY]
    return handles


def titles(r, label, text):
    return (f"{label}{': ' + text if text else ''}",
            f"Plan made at {r.t0:%Y-%m-%d %H:%M} on the '{r.kind}' forecast, closed loop, "
            f"{r.count} forecast-error scenarios")


def plot_3d(plt, r, path, label, text, z_kind, region_kind, every, formats):
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    n, t = r.n, r.hours
    T = float(t[-1])
    Z, plan_z, loop_z, z_name, z_limits, z_always = z_series(r, z_kind)
    zlo, zhi, z_shown = span([Z, plan_z, loop_z], [v for v, _, _ in z_limits], z_always,
                             least=50.0 if z_kind == "dg1" else 0.2)
    dz = zhi - zlo
    lims = soc_limits(r)
    ylo, yhi, y_shown = span([r.S, r.plan_soc, r.loop_soc], [v for v, _, _ in lims], least=4.0)
    falling = lims[0][0] <= lims[1][0]
    back = ylo if falling else yhi                  # the SOC axis runs into the picture the way the plan goes

    fig = plt.figure(figsize=(16 / 2.54, 12 / 2.54))
    ax = fig.add_subplot(projection="3d", computed_zorder=False)
    for a in (ax.xaxis, ax.yaxis, ax.zaxis):
        a.set_pane_color((1, 1, 1, 0))
        a._axinfo["grid"].update(color=GRID, linewidth=0.5)
        a.line.set_color(AXIS)

    # the limit the plan heads for as a wall; the others as lines on the floor
    wall_y, wall_text = back, None
    if lims[0][0] in y_shown:
        wall_y, wall_text = lims[0][0], lims[0][1]
        xx, zz = np.meshgrid([0.0, T], [zlo, zhi])
        ax.plot_surface(xx, np.full_like(xx, wall_y), zz, color=WALL, alpha=0.30, shade=False, zorder=0)
        ax.text(T * 0.02, wall_y, zhi - 0.06 * dz, wall_text, fontsize=7, color=INK2, zorder=8)
    for v, txt, ls in lims[1:]:
        if v in y_shown:
            ax.plot([0.0, T], [v, v], [zlo, zlo], color=INK2, lw=0.9, ls=ls, zorder=1)
            ax.text(0.0, v, zlo - 0.06 * dz, txt, fontsize=7, color=INK2, zorder=8)
    # the limits of the vertical axis on the wall
    for (v, txt, ls), where in zip(z_limits, (0.36, 0.68)):
        if v in z_shown:
            ax.plot([0.0, T], [wall_y] * 2, [v, v], color=INK2, lw=0.9, ls=ls, zorder=1)
            ax.text(T * where, wall_y, v + 0.02 * dz, txt, fontsize=7, color=INK2, zorder=8)

    # the tube, and its shadows: the 95 % intervals of the scenarios, of SOC on the floor and of
    # the vertical quantity on the wall (a point in front is drawn lower: read the values there).
    # A region stays within what the scenarios reach (SOC piles up at its limits, DG_1 at the
    # rule's target), where a Gaussian ellipse would not
    for k in range(every, n, every):
        outline = region(r.S[:, k], Z[:, k], region_kind)
        if outline is None:
            continue
        xs = np.clip(outline[0], r.S[:, k].min(), r.S[:, k].max())
        zs = np.clip(outline[1], Z[:, k].min(), Z[:, k].max())
        colour = STATUS_COLOUR[r.status[k]]
        ax.add_collection3d(Poly3DCollection([list(zip(np.full_like(xs, t[k]), xs, zs))],
                                             facecolor=colour, edgecolor=colour, linewidths=0.6, alpha=0.28,
                                             zorder=2))
        lo, hi = np.percentile(r.S[:, k], [2.5, 97.5])
        ax.plot([t[k]] * 2, [lo, hi], [zlo] * 2, color=colour, lw=1.8, zorder=3)
        lo, hi = np.percentile(Z[:, k], [2.5, 97.5])
        ax.plot([t[k]] * 2, [wall_y] * 2, [lo, hi], color=colour, lw=1.4, alpha=0.7, zorder=1)

    # the plan, the closed loop, and their shadows
    ax.plot(t, np.full(n, wall_y), plan_z, color=INK, lw=0.8, ls=DOTS, alpha=0.6, zorder=1)
    ax.plot(t, np.full(n, wall_y), loop_z, color=LOOP, lw=0.9, alpha=0.6, zorder=1)
    ax.plot(t, r.plan_soc, plan_z, color=INK, lw=1.2, ls=DOTS, zorder=4)
    ax.plot(t[::every], r.plan_soc[::every], plan_z[::every], "o", ms=5, mfc="white", mec=INK, mew=1.2, zorder=6)
    ax.plot(t, r.loop_soc, loop_z, color=LOOP, lw=1.5, zorder=5)
    ax.plot(t[::every], r.loop_soc[::every], loop_z[::every], "o", ms=3.5, color=LOOP, zorder=7)
    ax.plot(t, r.plan_soc, np.full(n, zlo), color=INK, lw=1.0, ls=DOTS, zorder=4)
    ax.plot(t, r.loop_soc, np.full(n, zlo), color=LOOP, lw=1.2, zorder=5)
    for k, u in switches(r.d.schedule, r.st.u2):
        ax.text(t[k], r.plan_soc[k], plan_z[k] - 0.14 * dz, f"plan: DG_2 {'on' if u else 'off'}",
                fontsize=7, color=INK, ha="center", zorder=9)
    for k, u in switches([op.u2 for _, op in r.loop], r.st.u2):
        ax.text(t[k], r.loop_soc[k], loop_z[k] + 0.16 * dz, f"closed loop: DG_2 {'on' if u else 'off'}",
                fontsize=7, color=LOOP, ha="center", zorder=9)

    ax.set_xlim(0.0, T)
    ax.set_ylim((yhi, ylo) if falling else (ylo, yhi))
    ax.set_zlim(zlo, zhi)
    ticks = np.arange(0.0, T + 1e-9, 0.5 if T <= 3.01 else 1.0)
    ax.set_xticks(ticks, [f"{r.t0 + timedelta(hours=float(h)):%H:%M}" for h in ticks])
    ax.set_xlabel(f"Time on {r.t0:%Y-%m-%d}", labelpad=2)
    ax.set_ylabel("Battery SOC [%]", labelpad=2)
    ax.set_zlabel(z_name, labelpad=4)
    ax.view_init(elev=20, azim=-62)
    ax.set_box_aspect((1.6, 0.9, 0.85), zoom=1.0)

    fig.legend(handles=legend_handles(f"Plan made at {r.t0:%H:%M} (on the forecast)", wall_text and
                                      "SOC limit the plan heads for"),
               loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(0.5, 0.0), columnspacing=1.2)
    head, sub = titles(r, label, text)
    fig.text(0.02, 0.985, head, fontsize=9.5, color=INK, va="top")
    fig.text(0.02, 0.945, sub, fontsize=8, color=INK2, va="top")
    fig.subplots_adjust(left=-0.06, right=1.0, top=1.02, bottom=0.17)
    return save(plt, fig, path, formats)


def plot_2d(plt, r, path, label, text, every, formats):
    import matplotlib.dates as mdates
    from matplotlib.collections import PolyCollection
    x = mdates.date2num(r.clock)
    tint = {s: tuple(1.0 - 0.30 * (1.0 - int(STATUS_COLOUR[s][j:j + 2], 16) / 255.0) for j in (1, 3, 5))
            for s in SEVERITY}
    Zm, plan_m, loop_m, m_name, m_limits, _ = z_series(r, "margin")
    P1, plan_p1, loop_p1, p_name, p_limits, _ = z_series(r, "dg1")
    rows = (("Battery SOC [%]", r.S, r.plan_soc, r.loop_soc, soc_limits(r), False, 4.0),
            (p_name, P1, plan_p1, loop_p1, p_limits, False, 50.0),
            (m_name, Zm, plan_m, loop_m, m_limits, True, 0.2))
    fig, axes = plt.subplots(3, 1, figsize=(16 / 2.54, 16 / 2.54), sharex=True, layout="constrained")
    for ax, (name, A, plan, loop, limits, always, least) in zip(axes, rows):
        lo, hi = np.percentile(A, [2.5, 97.5], axis=0)
        quads = [[(x[k], lo[k]), (x[k + 1], lo[k + 1]), (x[k + 1], hi[k + 1]), (x[k], hi[k])] for k in range(r.n - 1)]
        colours = [tint[max(r.status[k], r.status[k + 1], key=SEVERITY.index)] for k in range(r.n - 1)]
        ax.add_collection(PolyCollection(quads, facecolors=colours, edgecolors=colours, linewidths=0.3, zorder=1))
        ax.plot(x, plan, color=INK, lw=1.2, ls=DOTS, zorder=3)
        ax.plot(x[::every], plan[::every], "o", ms=4.5, mfc="white", mec=INK, mew=1.1, zorder=4)
        ax.plot(x, loop, color=LOOP, lw=1.5, zorder=5)
        ax.plot(x[::every], loop[::every], "o", ms=3, color=LOOP, zorder=6)
        y0, y1, shown = span([lo, hi, plan, loop], [v for v, _, _ in limits], always, least)
        tail = max(2, r.n // 4)
        for v, txt, ls in limits:
            if v in shown:
                ax.axhline(v, color=INK2, lw=0.8, ls=ls, zorder=2)
                clear = [min(abs(np.concatenate([a[part] for a in (lo, hi, plan, loop)]) - v).min(), 1e9)
                         for part in (slice(0, tail), slice(r.n - tail, r.n))]
                right = clear[1] >= clear[0]
                ax.annotate(txt, (1.0 if right else 0.0, v), xycoords=("axes fraction", "data"),
                            xytext=(-2 if right else 2, 2), textcoords="offset points",
                            ha="right" if right else "left", va="bottom", fontsize=7, color=INK2)
        ax.set_ylim(y0, y1)
        ax.set_ylabel(name)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(color=GRID, lw=0.6)
        ax.set_axisbelow(True)
    axes[-1].set_xlim(x[0], x[-1])
    hours = (r.n - 1) * r.p.dt_min / 60.0
    axes[-1].xaxis.set_major_locator(mdates.MinuteLocator(byminute=(0, 30)) if hours <= 3.01
                                     else mdates.HourLocator(interval=max(1, round(hours / 6))))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[-1].set_xlabel(f"Time on {r.t0:%Y-%m-%d}")
    fig.legend(handles=legend_handles(f"Plan made at {r.t0:%H:%M} (on the forecast)"),
               loc="outside lower center", ncol=2, frameon=False, columnspacing=1.2)
    head, sub = titles(r, label, text)
    fig.suptitle(f"{head}\n{sub}", x=0.01, ha="left", fontsize=8.5, color=INK)
    return save(plt, fig, path, formats)


def save(plt, fig, path, formats):
    out = []
    for ext in formats:
        fig.savefig(f"{path}.{ext}", facecolor="white")
        out.append(f"{os.path.basename(path)}.{ext}")
    plt.close(fig)
    return out


# ============================================================================
# MAIN
# ============================================================================
def instants(args, times, p):
    """[(label, text, decision time, lead-in from, dispatch log or None)]."""
    def on_grid(when):
        when -= timedelta(minutes=when.minute % int(p.dt_min), seconds=when.second)
        text = when.strftime("%Y-%m-%d %H:%M")
        if text not in index:
            raise SystemExit(f"{text} is not a step of the profiles ({times[0]} -> {times[-1]})")
        return text

    index = set(times)
    first = datetime.strptime(times[0], "%Y-%m-%d %H:%M")
    warm = timedelta(days=args.warmup if args.warmup is not None else WARMUP_DAYS)
    log = None if args.log in (None, "none") else args.log
    if args.time:
        when = datetime.strptime(args.time, "%Y-%m-%d %H:%M")
        return [(when.strftime("%Y%m%d_%H%M"), "", on_grid(when), on_grid(max(when - warm, first)), log)]
    out = []
    for pid in args.points or list(POINTS):
        if pid not in POINTS:
            raise SystemExit(f"no operating point {pid} (POINTS: {', '.join(POINTS)})")
        point = datetime.strptime(POINTS[pid]["time"], "%Y-%m-%d %H:%M")
        when = point - timedelta(hours=args.lead)
        label = pid if not args.lead else f"{pid}-{args.lead:g}h"
        start = point - warm if when > point - warm else when - warm      # where ctrl.py's logs start
        out.append((label, POINTS[pid]["text"], on_grid(when), on_grid(max(start, first)),
                    log or (None if args.log == "none" else find_log(pid))))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--points", nargs="*", help="operating points of ctrl.py (default: all)")
    ap.add_argument("--lead", type=float, default=0.0, help="decide this many hours before the point")
    ap.add_argument("--time", help='"YYYY-MM-DD HH:MM": an instant of your own instead of the points')
    ap.add_argument("--forecast", choices=("yesterday", "perfect"), default="yesterday",
                    help="the forecast the MPC decides on (default: yesterday)")
    ap.add_argument("--scenarios", type=int, default=SCENARIOS)
    ap.add_argument("--season-days", type=int, default=SEASON_DAYS)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--every", type=int, default=EVERY, help="a region every this many steps")
    ap.add_argument("--z", choices=("dg1", "margin"), default="dg1", help="vertical axis of the 3-D plot")
    ap.add_argument("--region", choices=("ellipse", "hull"), default="ellipse",
                    help="95 %% region: Gaussian ellipse, or the hull of the 95 %% nearest scenarios")
    ap.add_argument("--log", help="dispatch log to take the state at the instant from ('none': run up to it)")
    ap.add_argument("--warmup", type=float, help="days the MPC runs up to the instant without a log "
                                                 "(default: ctrl.py's QDS_WARMUP_DAYS)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override an MPC parameter")
    ap.add_argument("--engine", choices=("auto", "ctrl", "model"), default="auto",
                    help="ctrl.py's MPC (auto: if it can be read) or mpc_model.py's")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--pdf", action="store_true", help="also save the plots as PDF")
    args = ap.parse_args()

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise SystemExit("mpc_tube_plot.py needs matplotlib: run it with a Python that has it (Anaconda's, for one)")
    plt.rcParams.update({
        "font.size": 9, "axes.titlesize": 9.5, "axes.labelsize": 9, "xtick.labelsize": 8, "ytick.labelsize": 8,
        "legend.fontsize": 8, "axes.edgecolor": AXIS, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
        "axes.labelcolor": INK, "savefig.dpi": 300})

    e = engine(args.engine)
    overrides = {}
    for item in args.set:
        key, _, value = item.partition("=")
        overrides[key.strip()] = parse_value(value.strip())
    p = e.params(overrides)
    for note in p.notes:
        print("!! " + note)
    if e.forecast and e.forecast != args.forecast:
        print(f"!! ctrl.py's MPC decides on the '{e.forecast}' forecast (MPC_FORECAST); this plot on "
              f"'{args.forecast}'")
    times, load, pv = prof = read_profiles()
    index = {t: i for i, t in enumerate(times)}
    os.makedirs(args.out, exist_ok=True)
    formats = ("png", "pdf") if args.pdf else ("png",)
    print(f"MPC of {e.name}, horizon {p.horizon} steps, m* = {p.m_star}; plots in {args.out}")

    for label, text, when, lead_in, log in instants(args, times, p):
        i = index[when]
        st = state_from_log(e, p, log, when) if log else None
        if st is not None:
            source = f"from {log}"
        else:
            if log:
                print(f"!! {log} has no step {when}: the MPC runs up to it")
            st = warm_up(e, p, prof, index[lead_in], i, args.forecast)
            source = f"the MPC on its own model from SOC_INIT {p.soc_init:.0%} at {lead_in}"
        r = tube(e, p, prof, i, st, args.forecast, args.scenarios, args.season_days, args.seed)
        files = plot_3d(plt, r, os.path.join(args.out, f"tube3d_{label}"), label, text, args.z, args.region,
                        args.every, formats)
        files += plot_2d(plt, r, os.path.join(args.out, f"tube2d_{label}"), label, text, args.every, formats)
        write_table(r, os.path.join(args.out, f"tube_{label}.csv"))
        drawn = [r.status[k] for k in range(args.every, r.n, args.every)]
        worst = int(np.argmax(r.below_zero))
        print(f"\n{label}{' (' + text + ')' if text else ''}: plan made at {when}, '{args.forecast}' forecast")
        print(f"   state: SOC {st.soc:.1%}, DG_2 {'on' if st.u2 else 'off'}"
              f"{', discharge locked out' if st.locked else ''} ({source})")
        print(f"   plan: battery holds DG_1 at {r.d.rule.p1_target:.0f} kW, SOC floor {r.d.rule.soc_floor:.0%}; "
              f"DG_2 {''.join(map(str, r.d.schedule))}; smallest m_k {r.plan_m.min():.2f}")
        print(f"   closed loop: smallest m_k {r.loop_m.min():.2f}, DG_2 switched "
              f"{len(switches([op.u2 for _, op in r.loop], st.u2))} times")
        print(f"   {r.count} scenarios: of {len(drawn)} regions drawn {drawn.count('good')} green, "
              f"{drawn.count('serious')} orange, {drawn.count('critical')} red; m_k < 0 in up to "
              f"{r.below_zero[worst]:.0%} of them (at {r.clock[worst]:%H:%M}), below m* in up to "
              f"{r.below_target.max():.0%}")
        print(f"   {', '.join(files)}, tube_{label}.csv")


if __name__ == "__main__":
    main()
