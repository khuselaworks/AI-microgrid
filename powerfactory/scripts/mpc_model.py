"""
Stability-constrained heuristic MPC for the isolated PV-BESS-diesel microgrid: the model
=======================================================================================

The model of the handoff page (section 5) as code. Its data come from the PowerFactory
model where the model holds them (mpc_system_model.json, written by mpc_model_poll.py),
from the fits of mpc_calibrate.py (mpc_calibration.json), and otherwise from the data
sheets and ctrl.py's settings. Every value keeps a note of where it came from, so the
gaps stay plain (Params.report()).

  5.1 gensets      limits, fuel (affine fit of the data-sheet points), starts, min up/down
  5.2 battery      SOC with charge and discharge efficiency, and the SOC_MIN lockout
  5.3 PV and load  curtailment, balance with losses, DG_1 as the slack
  5.4 events       the credible sudden changes of power, down (under-frequency) and up
  5.5 stability    S1-S7 as allowances for each event, and the margin index m_k
  5.6 objective    fuel + starts + curtailment + battery wear + w phi(m_k) + terminal SOC
  5.7 heuristic    DG_2 schedules x battery/curtailment rules, rolled out, repaired, the
                   cheapest kept; only its first move is applied

Powers in kW (battery > 0 discharging), energies in kWh, kinetic energy in kWs, cost in rand.
  [P] Parisio, Rikos, Glielmo, IEEE TCST 22(5), 2014
  [S] Sachs, Sawodny, IEEE TSTE 7(3), 2016

USE
  import mpc_model as mm
  p = mm.load_params()                        # print("\\n".join(p.report())) shows the sources
  mpc = mm.HeuristicMPC(p)
  state = mm.State(soc=p.soc_init)
  d = mpc.decide(state, load_forecast, pv_forecast)        # lists of kW, from now on
  op = mm.operate(p, load_now, pv_now, d.u2, state.soc, state.locked, d.rule)
  state = mm.advance(p, state, op)

  python mpc_model.py     the parameters with their sources, and the check of the polled point
"""

import ast
import json
import math
import os
from dataclasses import dataclass
from typing import NamedTuple

HERE = os.path.dirname(os.path.abspath(__file__))
SYSTEM_JSON = (os.path.join(HERE, "mpc_system_model.json"),
               os.path.join(os.path.expanduser("~"), "mpc_model", "mpc_system_model.json"))
CALIBRATION_JSON = os.path.join(HERE, "mpc_calibration.json")
INF = float("inf")
M_NONE = -9.99      # the margin where nothing is allowed and something would still have to be met

# ============================================================================
# PARAMETERS: (key, default, unit, meaning, where the default comes from)
# ============================================================================
# Defaults are the handoff values (data sheets and ctrl.py). The system JSON, the calibration
# and explicit overrides replace them, in that order. Sources that start with "OPEN" are
# decisions still to be made; "ASSUMED" values have no data behind them yet.
PARAMETERS = [
    ("dt_min", 5.0, "min", "time step", "ctrl.py QDS_STEP_MIN"),
    ("f0", 50.0, "Hz", "nominal frequency", "ctrl.py F_NOM"),
    # DG_1: grid former, isochronous, must run
    ("dg1_name", "DG_1", "", "grid-forming genset (isochronous, must run)", "ctrl.py DG1_NAME"),
    ("dg1_s_kva", 810.0, "kVA", "alternator rating", "S6L1D-C4 data sheet"),
    ("dg1_rating_kw", 648.0, "kW", "rating as ctrl.py counts it (S x cos phi_n)", "810 kVA x 0.8"),
    ("dg1_p_max", 600.0, "kW", "upper dispatch limit (prime rating)", "C825 D5 data sheet"),
    ("dg1_p_standby", 660.0, "kW", "standby rating (base of the reverse-power relay)", "C825 D5 data sheet"),
    ("dg1_p_min", 194.4, "kW", "minimum loading", "ctrl.py DG1_MIN_LEVEL 0.30 x 648 kW"),
    ("dg1_e_kws", 0.427 * 810.0, "kWs", "stored kinetic energy H x S", "H 0.427 s (ctrl.py GENSET_DATA) x 810 kVA"),
    ("dg1_fuel_a", 16.00, "L/h", "fuel while running, fixed part", "affine fit of the C825 D5 prime points"),
    ("dg1_fuel_b", 0.2225, "L/kWh", "fuel per kWh", "affine fit of the C825 D5 prime points"),
    ("dg1_xdss", 0.15, "pu", "sub-transient reactance X''d", "ASSUMED typical value (poll: TypSym.xdss)"),
    # DG_2: droop 4 %, started and stopped by the MPC
    ("dg2_name", "DG_2", "", "second genset (droop 4 %)", "ctrl.py DG2_NAME"),
    ("dg2_s_kva", 250.0, "kVA", "alternator rating", "UCDI274K data sheet"),
    ("dg2_rating_kw", 200.0, "kW", "rating as ctrl.py counts it (S x cos phi_n)", "250 kVA x 0.8"),
    ("dg2_p_max", 200.0, "kW", "upper dispatch limit (prime rating)", "C275 D5 data sheet"),
    ("dg2_p_standby", 220.0, "kW", "standby rating", "C275 D5 data sheet"),
    ("dg2_p_min", 60.0, "kW", "minimum loading", "ASSUMED 30 % of 200 kW"),
    ("dg2_e_kws", 0.213 * 250.0, "kWs", "stored kinetic energy H x S", "H 0.213 s (ctrl.py GENSET_DATA) x 250 kVA"),
    ("dg2_fuel_a", 0.85, "L/h", "fuel while running, fixed part", "affine fit of the C275 D5 prime points"),
    ("dg2_fuel_b", 0.2548, "L/kWh", "fuel per kWh", "affine fit of the C275 D5 prime points"),
    ("dg2_xdss", 0.15, "pu", "sub-transient reactance X''d", "ASSUMED typical value (poll: TypSym.xdss)"),
    ("dg2_min_up_min", 60.0, "min", "minimum run time", "ctrl.py DG2_MIN_RUN_MIN"),
    ("dg2_min_down_min", 60.0, "min", "minimum stop time", "ctrl.py DG2_MIN_STOP_MIN"),
    # battery
    ("bess_p_max", 400.0, "kW", "charge and discharge limit", "400 kVA, cos phi 1"),
    ("bess_e_kwh", 3200.0, "kWh", "usable energy at 100 % SOC", "ctrl.py BESS_E_KWH"),
    ("soc_min", 0.20, "", "lowest SOC", "ctrl.py BESS_SOC_MIN"),
    ("soc_max", 0.95, "", "highest SOC", "ctrl.py BESS_SOC_MAX"),
    ("soc_resume", 0.40, "", "after SOC_MIN, no discharge until charged back to this", "ctrl.py BESS_SOC_RESUME"),
    ("soc_init", 0.50, "", "SOC at the start of a run", "ctrl.py BESS_SOC_INIT"),
    ("bess_eta_rt", 0.90, "", "round-trip efficiency, split evenly", "ctrl.py BESS_EFFICIENCY"),
    # PV, load, network
    ("pv_p_max", 800.0, "kW", "PV rating", "PV_Plant 1000 kVA x 0.8"),
    ("losses", 0.0066, "", "network losses, share of the load", "load flow at 2002-07-01 06:20"),
    ("load_tan_phi", 0.0, "", "Q/P of the load", "Ld_1: Q about 0 kvar in the RMS runs"),
    # protection and standards
    ("f_uf", 47.0, "Hz", "inverter under-frequency trip (0.2 s)", "NRS 097-2-1, ctrl.py F_UNDER"),
    ("f_over_limit", 58.3, "Hz", "the over-frequency peak must stay below this",
     "genset over-speed, ctrl.py OVERSPEED_HZ (the 51.5 Hz inverter trip waits 4 s)"),
    ("reverse_fraction", 0.10, "", "reverse-power relay, share of DG_1's standby rating", "ctrl.py REVERSE_FRACTION"),
    ("rocof_limit", 1.5, "Hz/s", "ROCOF the plant must ride through", "ctrl.py ROCOF_LIMIT"),
    ("rocof_window", 0.5, "s", "measuring window of the ROCOF relay", "ASSUMED (ctrl.py reports 100 and 500 ms)"),
    ("iso_class", "G2", "", "ISO 8528-5 performance class (voltage dip limit)", "OPEN decision"),
    # calibration (mpc_calibrate.py replaces these)
    ("tau_eff", 0.21, "s", "energy-deficit time of a step down (nadir model)", "handoff estimate"),
    ("tau_eff_over", 0.21, "s", "energy-deficit time of a step up (peak model)", "handoff estimate"),
    ("rho", 0.37, "", "PV fast-drop fraction", "handoff estimate"),
    ("lambda_up", 0.24, "", "credible load step up, share of the load", "handoff estimate"),
    ("lambda_down", 0.19, "", "credible load loss, share of the load", "handoff estimate"),
    ("kappa", 0.73, "", "largest inverter share of the running genset kVA", "handoff estimate"),
    ("pv_surge", "delivered", "", "PV surge after a cloud: rho x delivered | curtailed | none",
     "ctrl.py curtails by scale0, so the output follows the sun (handoff: rho x curtailed)"),
    ("delta_f", 0.2, "Hz", "frequency margin kept from every limit", "handoff"),
    ("delta_p", 10.0, "kW", "power margin kept from the reverse-power limit", "ASSUMED"),
    # which stability constraints count: "hard" = in m_k, "report" = worked out and reported only, "off"
    ("constraint_modes", {"S1": "hard", "S2": "hard", "S3": "hard", "S4": "hard", "S5": "report",
                          "S6": "hard", "S7": "hard"}, "", "use of S1-S7",
     "S5 report only, as in ctrl.py (1.5 Hz/s over 0.5 s allows ~50 kW)"),
    # objective
    ("diesel_price", 29.11, "R/L", "diesel price", "mpc_model_poll.py DIESEL_PRICE"),
    ("c_start", 250.0, "R", "DG_2 start-up cost", "OPEN decision (placeholder)"),
    ("c_curtail", 0.15, "R/kWh", "cost of curtailed PV", "PowerFactory costCurtailment 150 R/MWh"),
    ("c_wear", 0.50, "R/kWh", "battery wear per kWh in or out", "OPEN decision (placeholder)"),
    ("m_star", 0.20, "", "target stability margin m*", "OPEN decision"),
    ("w_margin", 1000.0, "R", "weight w of phi(m) per step", "ASSUMED"),
    ("soc_ref", 0.50, "", "SOC the horizon should end at or above", "ctrl.py BESS_SOC_INIT"),
    ("c_terminal", None, "R/kWh", "value of each kWh the horizon ends below soc_ref",
     "None = DG_1's marginal fuel cost x eta_d"),
    # heuristic
    ("horizon", 36, "steps", "prediction horizon N", "3 h"),
    ("switch_steps", (0, 1, 2, 3, 6, 12, 24), "steps", "where a DG_2 schedule may switch", "ASSUMED"),
    ("rule_p1_offsets", (0.0, 100.0, 250.0), "kW", "battery rules: DG_1 target above its minimum", "ASSUMED"),
    ("rule_soc_floors", (0.20, 0.40), "", "battery rules: SOC kept back from discharge", "ASSUMED"),
    ("tie_tolerance", 0.005, "", "costs this close count as equal; the larger margin wins", "ASSUMED"),
]
CALIBRATED = ("tau_eff", "tau_eff_over", "rho", "lambda_up", "lambda_down", "kappa")

# ISO 8528-5 guide for a load step on a four-stroke engine of ~24 bar BMEP (ctrl.py
# GUIDE_STAGES 0/35/63/85/100 %): the step allowed from each loading, % of prime.
ACCEPT = ((0.0, 35.0), (35.0, 28.0), (63.0, 22.0), (85.0, 15.0))
# ISO 8528-5 transient voltage dip, % (ctrl.py ISO_VOLT)
ISO_DIP = {"G1": 25.0, "G2": 20.0, "G3": 15.0}

CONSTRAINTS = {
    "S1": "spinning reserve",
    "S2": "ISO 8528-5 load acceptance",
    "S3": "frequency nadir above the inverter UF trip",
    "S4r": "reverse power on DG_1",
    "S4f": "over-frequency peak",
    "S5": "ROCOF over the relay window",
    "S6": "inverter share",
    "S7q": "reactive power",
    "S7u": "voltage dip",
}


class Params:
    """The model's data. Attributes are the keys of PARAMETERS; .source[key] says where each
    value came from."""

    def __init__(self):
        self.source, self.meta = {}, {}
        for key, value, unit, meaning, source in PARAMETERS:
            setattr(self, key, value)
            self.source[key] = source
        self.notes = []

    def set(self, key, value, source):
        if key not in self.source:
            raise KeyError(f"no parameter '{key}'")
        setattr(self, key, value)
        self.source[key] = source

    def finish(self):
        """Quantities worked out from the parameters, used in the inner loops."""
        self.dt_h = self.dt_min / 60.0
        self.eta = self.bess_eta_rt ** 0.5
        self.dg2_min_up = max(1, round(self.dg2_min_up_min / self.dt_min))
        self.dg2_min_down = max(1, round(self.dg2_min_down_min / self.dt_min))
        if self.c_terminal is None:
            self.c_terminal_value = self.dg1_fuel_b * self.diesel_price * self.eta
        else:
            self.c_terminal_value = float(self.c_terminal)
        # allowances per kWs of stored energy (S3, S4f, S5)
        self.k_uf = 2.0 * (self.f0 - self.f_uf - self.delta_f) / (self.f0 * self.tau_eff)
        self.k_of = 2.0 * (self.f_over_limit - self.f0 - self.delta_f) / (self.f0 * self.tau_eff_over)
        self.k_rocof = 2.0 * self.rocof_limit * self.rocof_window / (self.f0 * min(self.rocof_window, self.tau_eff))
        self.dip = ISO_DIP[self.iso_class] / 100.0
        if self.pv_surge not in ("delivered", "curtailed", "none"):
            raise ValueError(f"pv_surge is '{self.pv_surge}': 'delivered', 'curtailed' or 'none'")
        self.reverse_kw = self.reverse_fraction * self.dg1_p_standby - self.delta_p
        modes = self.constraint_modes
        self.hard = {cid: modes.get(cid[:2], "hard") == "hard" for cid in CONSTRAINTS}
        self.shown = {cid: modes.get(cid[:2], "hard") != "off" for cid in CONSTRAINTS}
        return self

    def report(self):
        lines = [f"{'parameter':<18} {'value':>14} {'unit':<6} {'meaning':<52} source"]
        for key, _, unit, meaning, _ in PARAMETERS:
            v = getattr(self, key)
            text = f"{v:.6g}" if isinstance(v, float) else str(v)
            if len(text) > 14:
                text = text[:11] + "..."
            lines.append(f"{key:<18} {text:>14} {unit:<6} {meaning[:52]:<52} {self.source[key]}")
        return lines


def _is_default(value):
    """PowerFactory's 'no limit' (9999 MW and the like) or an empty value (as the poll counts it)."""
    return value in (None, "", 0, 0.0, [], False) or (isinstance(value, (int, float)) and abs(value) >= 9999)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _uc(entry, kw=True):
    """A unit-commitment entry of the poll ({value, unit, kw}): the number, None if not set."""
    if not isinstance(entry, dict) or _is_default(entry.get("value")):
        return None
    v = entry.get("kw") if kw else entry.get("value")
    return float(v) if _number(v) else None


def _minutes(entry):
    v = _uc(entry, kw=False)
    if v is None:
        return None
    unit = str(entry.get("unit") or "").strip("[] ").lower()
    return v * {"h": 60.0, "s": 1 / 60.0}.get(unit, 1.0)


def _from_system(p, path):
    """The values the PowerFactory model holds (mpc_system_model.json)."""
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    meta = m.get("meta") or {}
    tag = f"JSON ({meta.get('study_time', '?')})"
    p.meta.update(system_json=path, project=meta.get("project"), read_at=meta.get("read_at"))

    def put(key, value, where, scale=1.0):
        if _number(value):
            p.set(key, float(value) * scale, f"{tag} {where}")

    for d in m.get("gensets") or []:
        c = d.get("ctrl_py") or {}
        role = ("dg1" if d.get("name") == p.dg1_name or "min_level" in c else
                "dg2" if d.get("name") == p.dg2_name or "on_level" in c else None)
        if role is None:
            continue
        uc = d.get("unit_commitment") or {}
        put(f"{role}_s_kva", d.get("rating_kva"), "rating_kva")
        put(f"{role}_rating_kw", d.get("rating_kw"), "rating_kw")
        put(f"{role}_e_kws", d.get("kinetic_energy_kws"), "H x S")
        put(f"{role}_xdss", (d.get("machine") or {}).get("xdss"), "TypSym.xdss")
        put(f"{role}_p_standby", c.get("standby_kw"), "ctrl_py.standby_kw")
        if _uc(uc.get("p_max_operational")) is not None:
            put(f"{role}_p_max", _uc(uc["p_max_operational"]), "Pmax_uc")
        else:
            put(f"{role}_p_max", c.get("prime_kw"), "ctrl_py.prime_kw")
        if _uc(uc.get("p_min_operational")) is not None:
            put(f"{role}_p_min", _uc(uc["p_min_operational"]), "Pmin_uc")
        elif role == "dg1" and _number(c.get("min_level")) and _number(d.get("rating_kw")):
            put("dg1_p_min", c["min_level"] * d["rating_kw"], "ctrl_py.min_level x rating_kw")
        if role == "dg2":
            for key, field, ctrl_key in (("dg2_min_up_min", "min_up_time", "min_run_min"),
                                         ("dg2_min_down_min", "min_down_time", "min_stop_min")):
                v = _minutes(uc.get(field))
                if v is not None:
                    put(key, v, field)
                else:
                    put(key, c.get(ctrl_key), f"ctrl_py.{ctrl_key}")
            put("c_start", _uc(uc.get("cost_start_up"), kw=False), "cost_up")
        fit = ((d.get("fuel") or {}).get("fit_prime")) or {}
        put(f"{role}_fuel_a", fit.get("a_l_h"), "fuel.fit_prime")
        put(f"{role}_fuel_b", fit.get("b_l_kwh"), "fuel.fit_prime")
        put("diesel_price", (d.get("fuel") or {}).get("diesel_price_r_per_l"), "fuel.diesel_price")

    b = m.get("battery")
    if b:
        lim, st, c = b.get("limits") or {}, b.get("storage_model") or {}, b.get("ctrl_py") or {}
        if _uc(lim.get("p_max_operational")) is not None:
            put("bess_p_max", _uc(lim["p_max_operational"]), "Pmax_uc")
        elif _number(c.get("p_kw")):
            put("bess_p_max", c["p_kw"], "ctrl_py.p_kw")
        elif _number(b.get("rating_kva")):
            put("bess_p_max", b["rating_kva"] * (b.get("cos_phi") or 1.0), "rating_kva x cos_phi")
        if _uc(st.get("capacity")) is not None:
            put("bess_e_kwh", _uc(st["capacity"]), "ElmStorage.capacity")
        else:
            put("bess_e_kwh", c.get("e_kwh"), "ctrl_py.e_kwh")
        for key, ck in (("soc_min", "soc_min"), ("soc_max", "soc_max"), ("soc_resume", "soc_resume"),
                        ("soc_init", "soc_init"), ("bess_eta_rt", "round_trip_efficiency")):
            put(key, c.get(ck), f"ctrl_py.{ck}")
        put("c_curtail", _uc(lim.get("cost_curtailment"), kw=False), "costCurtailment (per MWh)", 1e-3)

    pvs = m.get("pv") or []
    if pvs and all(_number(x.get("rating_kva")) for x in pvs):
        put("pv_p_max", sum(x["rating_kva"] * (x.get("cos_phi") or 1.0) for x in pvs), "rating_kva x cos_phi")
    for lod in m.get("loads") or []:
        lf = lod.get("load_flow") or {}
        if _number(lf.get("p_kw")) and _number(lf.get("q_kvar")) and lf["p_kw"] > 1:
            put("load_tan_phi", lf["q_kvar"] / lf["p_kw"], "load flow Q/P")
        break
    put("losses", m.get("losses_share"), "losses_share")


def _from_calibration(p, path):
    with open(path, encoding="utf-8") as fh:
        cal = json.load(fh)
    p.meta["calibration_json"] = path
    for key in CALIBRATED:
        entry = cal.get(key)
        if isinstance(entry, dict) and _number(entry.get("value")):
            p.set(key, float(entry["value"]), f"calibrated: {entry.get('source', '')}")


def parse_value(text):
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def load_params(system_json="auto", calibration="auto", overrides=None):
    """Params from the defaults, then the system JSON, then the calibration, then overrides
    ({key: value}). "auto" looks in the usual places; None skips that source."""
    p = Params()
    places = SYSTEM_JSON if system_json == "auto" else ([system_json] if system_json else [])
    path = next((x for x in places if os.path.isfile(x)), None)
    if path:
        _from_system(p, path)
    elif system_json != "auto" and system_json:
        raise FileNotFoundError(system_json)
    else:
        p.notes.append("mpc_system_model.json not found (looked in " + "; ".join(places) +
                       "): data-sheet and ctrl.py values used throughout")
    cal = CALIBRATION_JSON if calibration == "auto" else calibration
    if cal and os.path.isfile(cal):
        _from_calibration(p, cal)
    elif calibration == "auto":
        p.notes.append("mpc_calibration.json not found: run mpc_calibrate.py; handoff estimates used")
    for key, value in (overrides or {}).items():
        p.set(key, value, "override")
    return p.finish()


# ============================================================================
# 5.1-5.3 THE PLANT
# ============================================================================
class Rule(NamedTuple):
    """A battery and curtailment rule: the battery holds DG_1 at p1_target (DG_2 at its
    minimum) while it can, and does not discharge below soc_floor."""
    p1_target: float
    soc_floor: float


@dataclass(slots=True)
class State:
    soc: float
    locked: bool = False    # lambda_k: discharge locked out after SOC_MIN until SOC_RESUME
    u2: int = 0             # DG_2 running
    t2: int = 10 ** 6       # steps DG_2 has been in its present state (on or off)


@dataclass(slots=True)
class Op:
    """One step as dispatched, with its margin."""
    load: float
    pv_avail: float
    curtail: float
    bess: float
    u2: int
    p1: float
    p2: float
    m: float = INF
    binding: str = ""

    @property
    def pv(self):
        return self.pv_avail - self.curtail


def discharge_max(p, soc, locked, floor=0.0):
    """Most the battery can deliver this step (power limit, or the energy above the floor)."""
    if locked:
        return 0.0
    kwh = (soc - max(p.soc_min, floor)) * p.bess_e_kwh * p.eta
    return max(0.0, min(p.bess_p_max, kwh / p.dt_h))


def charge_max(p, soc):
    kwh = (p.soc_max - soc) * p.bess_e_kwh / p.eta
    return max(0.0, min(p.bess_p_max, kwh / p.dt_h))


def next_soc(p, soc, locked, bess):
    """5.2: SOC and lockout after one step at terminal power bess (as ctrl.py's Battery)."""
    if bess >= 0:
        soc -= bess * p.dt_h / (p.eta * p.bess_e_kwh)
    else:
        soc -= bess * p.dt_h * p.eta / p.bess_e_kwh
    soc = min(max(soc, 0.0), 1.0)
    if soc <= p.soc_min + 1e-6:
        locked = True
    elif locked and soc >= p.soc_resume:
        locked = False
    return soc, locked


def soc_before(p, soc_after, bess):
    """The SOC a step started from (next_soc backwards, without its limits)."""
    if bess >= 0:
        return soc_after + bess * p.dt_h / (p.eta * p.bess_e_kwh)
    return soc_after + bess * p.dt_h * p.eta / p.bess_e_kwh


def advance(p, st, op):
    soc, locked = next_soc(p, st.soc, st.locked, op.bess)
    return State(soc, locked, op.u2, st.t2 + 1 if op.u2 == st.u2 else 1)


def build(p, load, pv_avail, curtail, bess, u2, p2=None):
    """5.3: the balance with DG_1 as the slack. DG_2, when running, stays at its minimum and
    takes only what DG_1 cannot (its fuel per kWh is higher, and a smaller DG_2 is a smaller
    event when it trips), unless p2 sets it."""
    diesel = (1.0 + p.losses) * load - (pv_avail - curtail) - bess
    if not u2:
        p2 = 0.0
    elif p2 is None:
        p2 = min(max(diesel - p.dg1_p_max, p.dg2_p_min), p.dg2_p_max)
    else:
        p2 = min(max(p2, p.dg2_p_min), p.dg2_p_max)
    op = Op(load, pv_avail, curtail, bess, u2, diesel - p2, p2)
    op.m, op.binding = margins(p, op)
    return op


def operate(p, load, pv_avail, u2, soc, locked, rule=None, repair=True):
    """The step a rule gives (battery, then curtailment so the diesels keep their minimum),
    repaired where its margin is below m*."""
    if rule is None:
        rule = Rule(p.dg1_p_min, p.soc_min)
    demand = (1.0 + p.losses) * load
    floor = p.dg1_p_min + (p.dg2_p_min if u2 else 0.0)
    want = demand - pv_avail - max(rule.p1_target, p.dg1_p_min) - (p.dg2_p_min if u2 else 0.0)
    if want > 0:
        bess = min(want, discharge_max(p, soc, locked, rule.soc_floor))
    else:
        bess = -min(-want, charge_max(p, soc))
    curtail = min(max(floor - (demand - pv_avail - bess), 0.0), pv_avail)
    op = build(p, load, pv_avail, curtail, bess, u2)
    if repair and op.m < p.m_star:
        op = repair_op(p, op)
    return op


REPAIR_STEPS = (0.125, 0.25, 0.5, 0.75, 1.0)    # shares of the most a repair move can do
REPAIR_BISECT = 3                               # halvings to find the least share that is enough


REPAIR_MOVES = (
    # curtail PV (the diesels take up what PV no longer gives)
    (lambda p, o, x: build(p, o.load, o.pv_avail, o.curtail + x, o.bess, o.u2),
     lambda p, o: o.pv),
    # discharge less (the diesels take it up)
    (lambda p, o, x: build(p, o.load, o.pv_avail, o.curtail, o.bess - x, o.u2),
     lambda p, o: max(o.bess, 0.0)),
    # charge less, with as much PV curtailed, so the diesels keep their load
    (lambda p, o, x: build(p, o.load, o.pv_avail, min(o.curtail + x, o.pv_avail), o.bess + x, o.u2),
     lambda p, o: max(-o.bess, 0.0)),
    # move load from DG_1 to a running DG_2: DG_1's load acceptance a(l) grows as its loading falls
    (lambda p, o, x: build(p, o.load, o.pv_avail, o.curtail, o.bess, o.u2, o.p2 + x),
     lambda p, o: min(p.dg2_p_max - o.p2, o.p1 - p.dg1_p_min) if o.u2 else 0.0),
)


def repair_value(p, op):
    """What a repaired step costs: the step cost less the value of the energy it puts in the
    battery (so curtailing PV that would have charged it, or charging it from diesel, both show)."""
    stored = -op.bess * (p.eta if op.bess < 0 else 1.0 / p.eta) * p.dt_h
    return step_cost(p, op, False)[0] - p.c_terminal_value * stored


def repair_op(p, op):
    """5.7 step 3: curtail PV, limit the battery, or move load onto a running DG_2. Each move
    takes the smallest of REPAIR_STEPS that reaches m*; of the moves that do, the cheapest
    (repair_value) is kept. If none does, the one that helps most is kept and the moves are
    tried once more from there. Starting DG_2 is left to the schedules."""
    for _ in range(2):
        enough, best = [], op
        for move, room in REPAIR_MOVES:
            x_max = room(p, op)
            if x_max <= 1e-6:
                continue
            lo = 0.0
            for frac in REPAIR_STEPS:
                cand = move(p, op, frac * x_max)
                if cand.m >= p.m_star:
                    hi = frac
                    for _ in range(REPAIR_BISECT):      # the least of this move that is enough
                        mid = 0.5 * (lo + hi)
                        trial = move(p, op, mid * x_max)
                        if trial.m >= p.m_star:
                            hi, cand = mid, trial
                        else:
                            lo = mid
                    enough.append(cand)
                    break
                lo = frac
                if cand.m > best.m + 1e-9:
                    best = cand
        if enough:
            return min(enough, key=lambda o: repair_value(p, o))
        if best is op:
            return op
        op = best
    return op


# ============================================================================
# 5.4-5.5 EVENTS, STABILITY CONSTRAINTS AND THE MARGIN INDEX
# ============================================================================
def acceptance_pct(loading_pct):
    """a(l): the load step DG_1 may take from loading l, % of prime (ISO 8528-5 guide)."""
    if loading_pct <= ACCEPT[0][0]:
        return ACCEPT[0][1]
    for (x0, y0), (x1, y1) in zip(ACCEPT, ACCEPT[1:]):
        if loading_pct <= x1:
            return y0 + (y1 - y0) * (loading_pct - x0) / (x1 - x0)
    return ACCEPT[-1][1]


def events(p, op):
    """5.4: (name, dP kW, DG_2 lost) of the credible sudden changes. Down = generation lost or
    load gained (under-frequency), up = generation gained or load lost (over-frequency)."""
    down = (("PV drop", p.rho * op.pv, False),
            ("BESS trip", max(op.bess, 0.0), False),
            ("load step", p.lambda_up * op.load, False),
            ("DG_2 trip", op.p2 if op.u2 else 0.0, True))
    surge = op.pv if p.pv_surge == "delivered" else op.curtail if p.pv_surge == "curtailed" else 0.0
    up = (("BESS trip charging", max(-op.bess, 0.0)),
          ("load loss", p.lambda_down * op.load),
          ("PV surge", p.rho * surge))
    return down, up


def checks(p, op):
    """5.5: every (constraint, event, dP, allowed dP) of a step."""
    u2 = op.u2
    e_all = p.dg1_e_kws + (p.dg2_e_kws if u2 else 0.0)
    s_all = p.dg1_s_kva + (p.dg2_s_kva if u2 else 0.0)
    xd = max(p.dg1_xdss, p.dg2_xdss) if u2 else p.dg1_xdss
    reserve1 = p.dg1_p_max - op.p1
    reserve2 = (p.dg2_p_max - op.p2) if u2 else 0.0
    accept = acceptance_pct(100.0 * op.p1 / p.dg1_p_max) / 100.0 * p.dg1_p_max
    down, up = events(p, op)
    out = []
    for name, dp, lost2 in down:
        if dp <= 0.0:
            continue
        e = e_all - (p.dg2_e_kws if lost2 else 0.0)
        s = s_all - (p.dg2_s_kva if lost2 else 0.0)
        out += (("S1", name, dp, reserve1 + (0.0 if lost2 else reserve2)),   # what the rest can pick up
                ("S2", name, dp, accept),                                    # DG_1 (isochronous) takes it
                ("S3", name, dp, p.k_uf * e),                                # nadir above f_uf + delta_f
                ("S5", name, dp, p.k_rocof * e),
                ("S7u", name, dp, p.dip * s / xd))
    for name, dp in up:
        if dp <= 0.0:
            continue
        # DG_1 is isochronous: once the frequency is back it has absorbed the whole step
        out += (("S4r", name, dp, op.p1 + p.reverse_kw),
                ("S4f", name, dp, p.k_of * e_all),
                ("S5", name, dp, p.k_rocof * e_all),
                ("S7u", name, dp, p.dip * s_all / xd))
    out.append(("S6", "inverters", op.pv + abs(op.bess), p.kappa * s_all))
    q_cap = math.sqrt(max(p.dg1_s_kva ** 2 - op.p1 ** 2, 0.0))
    if u2:
        q_cap += math.sqrt(max(p.dg2_s_kva ** 2 - op.p2 ** 2, 0.0))
    out.append(("S7q", "load Q", p.load_tan_phi * op.load, q_cap))
    return out


def margin_of(dp, allow):
    """m = 1 - dP / dP_allow."""
    if allow > 0.0:
        return 1.0 - dp / allow
    return 1.0 if dp <= 0.0 else M_NONE


def margins(p, op):
    """(m_k, 'constraint: event' that sets it) over the hard constraints."""
    best, why = INF, ""
    hard = p.hard
    for cid, name, dp, allow in checks(p, op):
        if hard[cid]:
            m = margin_of(dp, allow)
            if m < best:
                best, why = m, f"{cid}: {name}"
    return best, why


def margin_table(p, op):
    """[(constraint, event, dP, allowed, m, mode)] of every constraint not switched off."""
    return [(cid, name, dp, allow, margin_of(dp, allow), "hard" if p.hard[cid] else "report")
            for cid, name, dp, allow in checks(p, op) if p.shown[cid]]


# ============================================================================
# 5.6 THE OBJECTIVE
# ============================================================================
def fuel_l(p, op):
    """Litres burnt in one step ([P] (14), [S] (23): affine in P while running)."""
    f = p.dg1_fuel_a + p.dg1_fuel_b * max(op.p1, 0.0)
    if op.u2:
        f += p.dg2_fuel_a + p.dg2_fuel_b * max(op.p2, 0.0)
    return f * p.dt_h


def phi(p, m):
    if m >= p.m_star:
        return 0.0
    return (p.m_star - max(m, 0.0)) ** 2


def step_cost(p, op, started):
    """(cost of the step in rand, violation = how far m_k is below zero)."""
    c = p.diesel_price * fuel_l(p, op)
    if started:
        c += p.c_start
    c += (p.c_curtail * op.curtail + p.c_wear * abs(op.bess)) * p.dt_h
    c += p.w_margin * phi(p, op.m)
    return c, (-op.m if op.m < 0.0 else 0.0)


def terminal_cost(p, soc):
    return p.c_terminal_value * max(0.0, p.soc_ref - soc) * p.bess_e_kwh


# ============================================================================
# 5.7 THE HEURISTIC MPC
# ============================================================================
@dataclass
class Decision:
    u2: int
    rule: Rule
    cost: float
    min_margin: float
    violation: float
    schedule: tuple
    candidates: int
    feasible: int


class HeuristicMPC:
    def __init__(self, p):
        self.p = p
        self.rules = [Rule(p.dg1_p_min + off, floor) for off in p.rule_p1_offsets for floor in p.rule_soc_floors]

    def schedules(self, st, n):
        """DG_2 on/off sequences over n steps that keep the minimum run and stop times: stay as
        it is, or switch at one of switch_steps; a start may also stop again after the minimum run."""
        p, out = self.p, set()
        grid = [s for s in p.switch_steps if s < n]
        out.add((st.u2,) * n)
        if st.u2:
            earliest = max(0, p.dg2_min_up - st.t2)
            for s in grid:
                s = max(s, earliest)
                if s < n:
                    out.add((1,) * s + (0,) * (n - s))
        else:
            earliest = max(0, p.dg2_min_down - st.t2)
            for s in grid:
                s = max(s, earliest)
                if s < n:
                    out.add((0,) * s + (1,) * (n - s))
                    if s + p.dg2_min_up < n:
                        out.add((0,) * s + (1,) * p.dg2_min_up + (0,) * (n - s - p.dg2_min_up))
        return sorted(out)

    def rollout(self, ri, rule, sched, st, load, pv, cache):
        """(cost J, violation, min m_k) of one candidate. Candidates that share the start of
        their schedule share those steps through the cache."""
        p = self.p
        soc, locked, prev = st.soc, st.locked, st.u2
        cost = viol = 0.0
        mmin = INF
        for k, u in enumerate(sched):
            key = (ri, k, u, prev, soc, locked)
            hit = cache.get(key)
            if hit is None:
                op = operate(p, load[k], pv[k], u, soc, locked, rule)
                c, v = step_cost(p, op, u > prev)
                hit = cache[key] = (c, v, op.m) + next_soc(p, soc, locked, op.bess)
            c, v, m, soc, locked = hit
            cost += c
            viol += v
            mmin = min(mmin, m)
            prev = u
        return cost + terminal_cost(p, soc), viol, mmin

    def decide(self, st, load, pv):
        """The best candidate over the forecasts (kW, from this step on). Feasible ones (m_k >= 0
        throughout) first; among those the cheapest, and within tie_tolerance of it the one
        with the larger smallest margin. If none is feasible, the least violation."""
        p = self.p
        n = min(p.horizon, len(load), len(pv))
        cache, runs = {}, []
        scheds = self.schedules(st, n)
        for ri, rule in enumerate(self.rules):
            for sched in scheds:
                cost, viol, mmin = self.rollout(ri, rule, sched, st, load, pv, cache)
                runs.append((viol, cost, mmin, rule, sched))
        feasible = [r for r in runs if r[0] <= 0.0]
        if feasible:
            j_best = min(r[1] for r in feasible)
            close = [r for r in feasible if r[1] <= j_best + p.tie_tolerance * abs(j_best)]
            viol, cost, mmin, rule, sched = max(close, key=lambda r: (r[2], -r[1]))
        else:
            viol, cost, mmin, rule, sched = min(runs, key=lambda r: (r[0], r[1]))
        return Decision(sched[0], rule, cost, mmin, viol, sched, len(runs), len(feasible))


# ============================================================================
# CHECK: the polled point (handoff section 6)
# ============================================================================
def check_polled_point(p):
    """Load ~279 kW, PV ~0, battery discharging ~87 kW, DG_1 ~193.7 kW, DG_2 off."""
    op = Op(load=279.0, pv_avail=0.0, curtail=0.0, bess=87.0, u2=0, p1=193.7, p2=0.0)
    op.m, op.binding = margins(p, op)
    lines = [f"Polled point 2002-07-01 06:20: load {op.load:.0f} kW, battery {op.bess:+.0f} kW, "
             f"DG_1 {op.p1:.1f} kW, DG_2 off",
             f"{'':3}{'constraint':<44} {'event':<20} {'dP kW':>8} {'allowed':>8} {'m':>7}"]
    for cid, name, dp, allow, m, mode in margin_table(p, op):
        lines.append(f"{'':3}{cid + ' ' + CONSTRAINTS[cid]:<44} {name:<20} {dp:8.1f} {allow:8.1f} {m:7.2f}"
                     + ("" if mode == "hard" else "  (reported only)"))
    lines.append(f"   m_k = {op.m:.3f} ({op.binding}); m* = {p.m_star}")
    return lines


if __name__ == "__main__":
    params = load_params()
    for note in params.notes:
        print("!! " + note)
    print("\n".join(params.report()))
    print()
    print("\n".join(check_polled_point(params)))
