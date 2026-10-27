"""
System data for the MPC dispatch of the isolated PV-BESS-diesel microgrid
PowerFactory 2024 SP5A  -  Python script
=====================================================================

Reads everything the network model holds about the gensets, the battery, the PV
plant, the load and the network, and writes it out in the form an MPC needs to
build its models. It changes nothing in the model: it only reads, and (with
RUN_LOAD_FLOW) runs one load flow at the study time already set, so that the
operating point and the network losses are known.

WHAT THE MPC NEEDS, AND WHERE IT COMES FROM
  The two papers in this folder set the models:
    [P] Parisio, Rikos, Glielmo, "A model predictive control approach to
        microgrid operation optimization", IEEE TCST 22(5), 2014
    [S] Sachs, Sawodny, "A two-stage model predictive control strategy for
        economic diesel-PV-battery island microgrid operation in rural areas",
        IEEE TSTE 7(3), 2016
  Gensets ([P] (11), (13), (14), (16b-c); [S] (10)-(12), (23), (25b-c), (26d-e))
    rated and operational power limits, minimum loading, ramp limits, minimum up
    and down times, start-up and shut-down cost, fuel cost curve, and, for a
    spinning reserve or stability constraint, inertia and governor data.
  Battery ([P] (1)-(5), (16a); [S] (5)-(9), (26a-c))
    power limits, usable energy, energy (SOC) limits, charge and discharge
    efficiencies, self-discharge.
  PV ([P] Pres; [S] (1)-(4))
    rated power, inverter limits, and the available power over time (the time
    characteristic of the model, i.e. the measured profile).
  Load ([P] D; [S] Pload)
    the demand over time (its time characteristic) and its voltage dependence.
  PowerFactory 2024 keeps most of the genset and battery data in its unit
  commitment and storage fields (Pmin_uc, limRampUp, minUpTime, costColdStart,
  pOpCostCurve, pStorage -> ElmStorage ...). Many of these stay at their defaults
  unless someone has filled them in. The report says, for every quantity the MPC
  needs, whether the model holds a value, whether it is still at its default, and
  what ctrl.py assumes for it today (its settings and GENSET_DATA), so that the
  gaps are plain before the MPC is built.

HOW IT FINDS EVERY ATTRIBUTE
  PowerFactory's Python interface cannot list the attributes of an object. The
  installation holds them per class in datascheme.db; the script asks each object
  for every name listed there for its class (and a few result variables), and
  keeps the ones that exist, with value, unit and description.

HOW TO RUN
  Inside PowerFactory: a Python Script object (ComPython) that points to this file,
  then Execute. The project and study case of ctrl.py must be active. Set the
  study time first if the operating point matters (e.g. an instant of POINTS).

OUTPUT (OUT_DIR)
  mpc_system_model.json        the data in a structure the MPC can load
  mpc_system_model_report.txt  the same, readable, with the gaps listed
  attributes/<class>_<name>.csv  every attribute of every object read
"""

import csv
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

# ============================================================================
# SETTINGS
# ============================================================================
OUT_DIR = os.path.join(os.path.expanduser("~"), "mpc_model")
RUN_LOAD_FLOW = True        # one load flow at the study time already set (results only, no data changed)
DG_NAMES = None             # None = every synchronous machine (ElmSym), in or out of service
PV_NAMES = None             # None = static generators / PV systems named "PV..." or driven by a PV characteristic
BESS_NAME = None            # None = the static generator whose name contains "BESS" or "batt"
CTRL_PY = None              # ctrl.py, for what the dispatch assumes today; None = next to this file
VECTOR_PREVIEW = 8          # values of a long vector shown in the report and the attribute files
F_NOM = 50.0
PF_PYTHON_PATH = r"C:\Program Files\DIgSILENT\PowerFactory 2024 SP5A\Python\3.12"   # only outside PowerFactory

# Fuel (Cummins generator set data sheets, 50 Hz, diesel): litres per hour at 1/4, 1/2, 3/4
# and full load of the prime and the standby rating. The MPC's fuel cost ([P] (14), [S] (23))
# is the affine least-squares fit of the prime points, F = a*on + b*P, times the diesel price.
DIESEL_PRICE = 29.11        # R per litre (the price used in this study)
LOAD_STEPS = (0.25, 0.5, 0.75, 1.0)
FUEL_DATA = {
    "dg1": dict(model="C825 D5", prime_kw=600.0, prime_l_h=(49.2, 83.4, 115.4, 149.8),
                standby_kw=660.0, standby_l_h=(53.0, 89.9, 125.3, 165.3)),
    "dg2": dict(model="C275 D5", prime_kw=200.0, prime_l_h=(14.2, 25.8, 38.3, 52.5),
                standby_kw=220.0, standby_l_h=(16.7, 30.0, 45.8, 62.5)),
}

# Result variables read after the load flow (they are not in the data scheme)
RESULTS = {
    "ElmSym": ["m:P:bus1", "m:Q:bus1", "m:i1:bus1", "m:u1", "m:loading", "c:loading"],
    "ElmGenstat": ["m:P:bus1", "m:Q:bus1", "m:i1:bus1", "m:u1", "c:loading"],
    "ElmPvsys": ["m:P:bus1", "m:Q:bus1", "m:i1:bus1", "m:u1", "c:loading"],
    "ElmLod": ["m:P:bus1", "m:Q:bus1", "m:u1"],
    "ElmTerm": ["m:u", "m:u1", "m:phiu", "m:fehz"],
    "ElmLne": ["m:P:bus1", "m:P:bus2", "m:Q:bus1", "c:loading", "c:Losses"],
    "ElmTr2": ["m:P:bus1", "m:P:bus2", "c:loading", "c:Losses"],
}
# Asked of every object as well: names the data scheme of an older build may not list
ALWAYS = ["loc_name", "outserv", "typ_id", "sgn", "cosn", "ngnum", "pgini", "qgini", "plini", "qlini", "scale0",
          "h", "rstr", "xd", "xq", "xds", "xdss", "uknom", "dline", "nlnum", "sline", "rline", "xline", "c_pmod",
          "pelm", "pblk", "params", "av_mode", "ip_ctrl", "aCategory", "cCategory", "pStorage", "pOpCostCurve",
          "Pmax_uc", "Pmin_uc", "PmaxInv", "PminInv", "limRampUp", "limRampDown", "minUpTime", "minDownTime",
          "cost_up", "costColdStart", "costShutDown", "capacity", "initEnergy", "minEnergyLim", "maxEnergyLim",
          "selfDischarge", "efficiencyCurveGen", "efficiencyCurveConsum"]
# Object references that are followed and read as well (types, storage, cost curves ...)
FOLLOW = ("typ_id", "pStorage", "pOpCostCurve", "efficiencyCurveGen", "efficiencyCurveConsum", "c_pmod",
          "pCharYrMW", "pCharYrWS")

# ============================================================================
# CONNECTION AND LOGGING
# ============================================================================
pf = None
app = None
_lines = []


def log(msg=""):
    _lines.append(msg)
    if app is not None:
        try:
            app.PrintPlain(msg)
            return
        except Exception:
            pass
    print(msg)


def warn(msg):
    log("!! " + msg)


def connect():
    global pf, app
    try:
        import powerfactory  # type: ignore
    except ImportError:
        sys.path.append(PF_PYTHON_PATH)
        import powerfactory  # type: ignore
    pf = powerfactory
    try:
        app = pf.GetApplication()
    except Exception:
        app = None
    if app is None:
        app = pf.GetApplicationExt()


# ============================================================================
# READING OBJECTS
# ============================================================================
def attr(obj, name, default=None):
    if obj is None:
        return default
    try:
        value = obj.GetAttribute(name)
    except Exception:
        return default
    return default if value is None else value


def full_name(obj):
    try:
        return obj.GetFullName()
    except Exception:
        return f"{obj.GetClassName()}:{obj.loc_name}"


def is_object(value):
    return hasattr(value, "GetClassName") and hasattr(value, "loc_name")


def short(obj):
    return f"{obj.loc_name}.{obj.GetClassName()}" if is_object(obj) else ""


_KILO = {"": 1e-3, "k": 1.0, "M": 1e3, "G": 1e6}


def kilo(value, unit):
    """value in kW / kVA / kvar / kWh, from a value in `unit`; None where the unit is not a power or energy."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    u = str(unit or "").strip().strip("[]").strip()
    for base in ("Wh", "var", "VA", "W"):
        if u.endswith(base) and u[: -len(base)] in _KILO:
            return value * _KILO[u[: -len(base)]]
    return None


class Schema:
    """The attribute names of each class, from the data scheme of the installation."""

    def __init__(self):
        self.names, self.source = {}, None
        try:
            path = os.path.join(app.GetInstallationDirectory(), "datascheme.db")
        except Exception:
            path = ""
        if not os.path.isfile(path):
            warn(f"datascheme.db not found ({path or 'no installation directory'}): only the attributes "
                 "the script names itself are read")
            return
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            build, name = db.execute("select Build, Name from Scheme order by Build desc limit 1").fetchone()
            for cls, att in db.execute("select c.Name, a.Name from Attribute a join Class c "
                                       "on a.ClassId = c.Id and a.Build = c.Build where a.Build = ? "
                                       "order by c.Name, a.Id", (build,)):
                self.names.setdefault(cls, []).append(att)
            db.close()
            self.source = f"{path} (scheme {name})"
        except Exception as e:
            warn(f"datascheme.db could not be read ({type(e).__name__}: {e})")

    def of(self, cls):
        return list(self.names.get(cls, []))


def describe(obj, name):
    for get in (lambda: obj.GetAttributeDescription(name), lambda: app.GetAttributeDescription(obj.GetClassName(), name)):
        try:
            text = get()
            if text:
                return str(text)
        except Exception:
            pass
    return ""


def unit_of(obj, name):
    for get in (lambda: obj.GetAttributeUnit(name), lambda: app.GetAttributeUnit(obj.GetClassName(), name)):
        try:
            text = get()
            if text:
                return str(text).strip()
        except Exception:
            pass
    return ""


def plain(value):
    """A value as JSON can hold it: objects by name, vectors as lists."""
    if is_object(value):
        return full_name(value)
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, float) and value != value:
        return None
    return value


def preview(value):
    """A value for the report and the attribute files: long vectors shortened."""
    if isinstance(value, (list, tuple)):
        flat = [v for row in value for v in (row if isinstance(row, (list, tuple)) else [row])]
        nums = [v for v in flat if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(flat) <= VECTOR_PREVIEW:
            return "[" + ", ".join(str(plain(v)) for v in flat) + "]"
        head = ", ".join(str(plain(v)) for v in flat[:VECTOR_PREVIEW])
        span = f"; min {min(nums):g}, max {max(nums):g}" if nums else ""
        return f"[{head}, ...] ({len(flat)} values{span})"
    return str(plain(value))


class Reader:
    """Reads every attribute of an object, once, and the objects it refers to."""

    def __init__(self, schema):
        self.schema, self.dumps = schema, {}

    def read(self, obj, follow=True):
        key = full_name(obj)
        if key in self.dumps:
            return self.dumps[key]
        cls = obj.GetClassName()
        names = self.schema.of(cls) + ALWAYS + [n for n in RESULTS.get(cls, []) if RUN_LOAD_FLOW]
        rows = {}
        for name in dict.fromkeys(names):
            try:
                if not obj.HasAttribute(name):
                    continue
                value = obj.GetAttribute(name)
            except Exception:
                continue
            rows[name] = dict(value=value, unit=unit_of(obj, name), text=describe(obj, name))
        self.dumps[key] = dict(obj=obj, cls=cls, name=obj.loc_name, rows=rows)
        if follow:
            for ref in FOLLOW:
                target = rows.get(ref, {}).get("value")
                for t in (target if isinstance(target, (list, tuple)) else [target]):
                    if is_object(t):
                        self.read(t, follow=False)
        return self.dumps[key]

    def value(self, obj, name, default=None):
        d = self.read(obj)
        return d["rows"][name]["value"] if name in d["rows"] else default

    def entry(self, obj, name):
        """{value, unit, text, kw} of one attribute, or None where the object has no such attribute."""
        if obj is None:
            return None
        d = self.read(obj)
        if name not in d["rows"]:
            return None
        row = d["rows"][name]
        return dict(attribute=f"{d['cls']}.{name}", value=plain(row["value"]), unit=row["unit"], text=row["text"],
                    kw=kilo(row["value"], row["unit"]))

    def write_all(self, folder):
        os.makedirs(folder, exist_ok=True)
        for d in self.dumps.values():
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{d['cls']}_{d['name']}")
            with open(os.path.join(folder, safe + ".csv"), "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["attribute", "value", "unit", "description"])
                for name, row in d["rows"].items():
                    w.writerow([name, preview(row["value"]), row["unit"], row["text"]])
        return len(self.dumps)


# ============================================================================
# THE NETWORK
# ============================================================================
def calc_objects(cls):
    try:
        return list(app.GetCalcRelevantObjects(f"*.{cls}", 1) or [])
    except Exception:
        return []


def characteristics(obj):
    """{parameter: characteristic} from the ChaRef objects inside an element."""
    out = {}
    for get in (lambda: obj.GetContents("*.ChaRef"), lambda: obj.GetChildren(1, "*.ChaRef", 0)):
        try:
            refs = list(get() or [])
        except Exception:
            refs = []
        for ref in refs:
            cha = attr(ref, "typ_id")
            if cha is not None:
                out[ref.loc_name] = cha
        if out:
            break
    return out


def terminal(obj, side="bus1"):
    cub = attr(obj, side)
    return attr(cub, "cterm") if cub is not None else None


def composite_of(gen):
    """The composite model (ElmComp) that holds a generator in one of its slots."""
    linked = attr(gen, "c_pmod")
    if linked is not None:
        return linked
    for comp in calc_objects("ElmComp"):
        if any(e is not None and full_name(e) == full_name(gen) for e in list(attr(comp, "pelm", []) or [])):
            return comp
    return None


def dsl_parameters(dsl):
    """{name: value} of a DSL model: from its own parameter list, else from its definition."""
    names = []
    for key in ("parameterNames", "parnam"):
        raw = attr(dsl, key)
        if raw:
            names = [n for n in re.split(r"[,;\s]+", " ".join(raw if isinstance(raw, (list, tuple)) else [raw])) if n]
            break
    if not names:
        typ = attr(dsl, "typ_id")
        for key in ("sParams", "sLowLimPar", "sUpLimPar"):
            raw = attr(typ, key)
            if raw:
                names += [n for n in re.split(r"[,;\s]+", " ".join(raw if isinstance(raw, (list, tuple)) else [raw]))
                          if n and n not in names]
    try:
        values = [float(v) for v in attr(dsl, "params")]
    except Exception:
        values = []
    if names and len(names) == len(values):
        return dict(zip(names, values))
    return {n: float(attr(dsl, n)) for n in names if isinstance(attr(dsl, n), (int, float))}


def plant_models(reader, gen):
    """{slot: {model, definition, parameters}} of the composite model of a generator. The models,
    their definitions (with the equations) and the composite model are read in full as well."""
    comp = composite_of(gen)
    if comp is None:
        return None, {}
    reader.read(comp)
    slots = list(attr(comp, "pblk", []) or [])
    elements = list(attr(comp, "pelm", []) or [])
    out = {}
    for i, elm in enumerate(elements):
        if elm is None or full_name(elm) == full_name(gen) or elm.GetClassName() != "ElmDsl":
            continue
        slot = slots[i].loc_name if i < len(slots) and slots[i] is not None else f"slot {i}"
        reader.read(elm)                            # follows typ_id to the definition (BlkDef)
        typ = attr(elm, "typ_id")
        out[slot] = dict(model=elm.loc_name, definition=typ.loc_name if typ is not None else None,
                         in_service=not attr(elm, "outserv", 0), parameters=dsl_parameters(elm))
    return comp, out


def classify_genstats():
    """(PV elements, battery element) among the static generators and PV systems."""
    pvs, bess = [], None
    for obj in calc_objects("ElmGenstat") + calc_objects("ElmPvsys"):
        name = obj.loc_name.lower()
        chars = " ".join(c.loc_name.lower() for c in characteristics(obj).values())
        if BESS_NAME and obj.loc_name == BESS_NAME or not BESS_NAME and ("bess" in name or "batt" in name):
            bess = bess or obj
        elif (PV_NAMES and obj.loc_name in PV_NAMES or not PV_NAMES and
              (obj.GetClassName() == "ElmPvsys" or name.startswith("pv") or "pv" in chars or "solar" in chars)):
            pvs.append(obj)
    return pvs, bess


# ============================================================================
# WHAT ctrl.py ASSUMES TODAY
# ============================================================================
def load_ctrl():
    places = [CTRL_PY] if CTRL_PY else []
    if "__file__" in globals():
        places.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ctrl.py"))
    places.append(os.path.join(os.path.expanduser("~"), "Documents", "DigSILENT Outputs", "ctrl.py"))
    path = next((p for p in places if os.path.isfile(p)), None)
    if path is None:
        warn(f"ctrl.py not found (looked in {'; '.join(places)}; set CTRL_PY): its assumptions are left out")
        return None
    # ctrl.py runs its study on import unless told otherwise. PowerFactory keeps one Python
    # process for the session, so the flag is put back, or ctrl.py would not run next time.
    before = os.environ.get("STABILITY_RMS_IMPORT_ONLY")
    os.environ["STABILITY_RMS_IMPORT_ONLY"] = "1"
    try:
        spec = importlib.util.spec_from_file_location("ctrl_settings", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        log(f"Assumptions of the dispatch read from {path}")
        return mod
    except Exception as e:
        warn(f"ctrl.py could not be read ({type(e).__name__}: {e})")
        return None
    finally:
        if before is None:
            os.environ.pop("STABILITY_RMS_IMPORT_ONLY", None)
        else:
            os.environ["STABILITY_RMS_IMPORT_ONLY"] = before


def ctrl_value(ctrl, name):
    return getattr(ctrl, name, None) if ctrl is not None else None


# ============================================================================
# THE MODEL FOR THE MPC
# ============================================================================
# What the MPC needs from each unit: (key, attribute(s) in PowerFactory, what it is, paper reference).
# The first attribute the object has is used.
DG_FIELDS = [
    ("p_max_operational", ["Pmax_uc"], "maximum operational active power", "[P] (16b), [S] (26d)"),
    ("p_min_operational", ["Pmin_uc"], "minimum operational active power (minimum loading)", "[P] (16b)"),
    ("p_min_rating", ["P_min"], "minimum active power (rating)", "[P] (16b)"),
    ("rating_factor", ["pmaxratf"], "active power rating factor", ""),
    ("limit_p_max", ["isLimPmax"], "the maximum is a constraint (unit commitment / OPF)", ""),
    ("limit_p_min", ["isLimPmin"], "the minimum is a constraint (unit commitment / OPF)", ""),
    ("ramp_up", ["limRampUp"], "ramp-up limit", "[P] (16c)"),
    ("ramp_down", ["limRampDown"], "ramp-down limit", "[P] (16c)"),
    ("ramp_start_up", ["limRampStartUp"], "ramp limit at start-up", ""),
    ("ramp_shut_down", ["limRampShutDown"], "ramp limit at shut-down", ""),
    ("ramps_constrained", ["isConstrRamp"], "ramp limits are constraints", ""),
    ("min_up_time", ["minUpTime"], "minimum up time", "[P] (11)"),
    ("min_down_time", ["minDownTime"], "minimum down time", "[P] (11), [S] (26e)"),
    ("up_down_constrained", ["isConstrUpDown"], "minimum up/down times are constraints", ""),
    ("cost_start_up", ["cost_up"], "start-up cost", "[P] (13), [S] (22)"),
    ("cost_cold_start", ["costColdStart"], "cold start cost", "[P] (13)"),
    ("cold_start_time", ["coldStartTime"], "time after which a start counts as cold", ""),
    ("cost_shut_down", ["costShutDown"], "shut-down cost", "[P] (13), [S] (22)"),
    ("start_up_times", ["vecStartUpTimes"], "start-up cost steps: times", ""),
    ("start_up_costs", ["vecStartUpCosts"], "start-up cost steps: costs", ""),
    ("cost_fixed", ["fixedCosts"], "fixed operating cost", "[P] OM, [S] c_O&M"),
    ("cost_operation_on", ["isConsCostOp"], "operating cost is considered", ""),
    ("cost_curve", ["pOpCostCurve"], "operating (fuel) cost curve", "[P] (14), [S] (23)"),
    ("cost_curve_breakpoints", ["numBreakpointsCostOp"], "breakpoints of the cost curve", "[P] (14)"),
    ("must_run", ["isMustRunUC"], "must run (unit commitment)", ""),
    ("spinning_reserve", ["is4SpinReserve"], "contributes to the spinning reserve", "[S] (25b)"),
    ("primary_bias", ["Kpf"], "primary frequency bias", ""),
    ("droop", ["ddroop"], "droop (load flow)", ""),
    ("secondary_share", ["dsecres"], "secondary control participation", ""),
]
STORAGE_FIELDS = [
    ("capacity", ["capacity"], "usable energy capacity", "[P] (16a), [S] C_N"),
    ("initial_energy", ["initEnergy"], "initial energy", "[P] x_b(0)"),
    ("min_energy", ["minEnergyLim"], "minimum energy (SOC min)", "[P] (16a), [S] (26a)"),
    ("max_energy", ["maxEnergyLim"], "maximum energy (SOC max)", "[P] (16a), [S] (26a)"),
    ("limits_relative", ["isEnergyLimsRel"], "energy limits are in % of the capacity", ""),
    ("self_discharge", ["selfDischarge"], "self-discharge", "[P] x_sb"),
    ("max_decrease", ["maxDecreaseLim"], "largest decrease per step", ""),
    ("max_increase", ["maxIncreaseLim"], "largest increase per step", ""),
    ("target_energy", ["targetEnergy"], "target energy at the end", "[S] (21)"),
    ("storage_type", ["storageType"], "type of storage", ""),
]
GENSTAT_FIELDS = [
    ("p_max_operational", ["Pmax_uc"], "maximum operational active power", "[P] C_b, [S] (26b)"),
    ("p_min_operational", ["Pmin_uc"], "minimum operational active power (charging limit)", "[P] -C_b, [S] (26b)"),
    ("p_max_inverter", ["PmaxInv"], "inverter maximum active power", ""),
    ("p_min_inverter", ["PminInv"], "inverter minimum active power", ""),
    ("rating_factor", ["pmaxratf"], "active power rating factor", ""),
    ("ramp_up", ["limRampUp"], "ramp-up limit", ""),
    ("ramp_down", ["limRampDown"], "ramp-down limit", ""),
    ("cost_curtailment", ["costCurtailment"], "curtailment cost", "[P] rho_c"),
    ("efficiency_charging", ["efficiencyCurveConsum"], "efficiency curve, charging", "[P] eta_c, [S] (13)"),
    ("efficiency_discharging", ["efficiencyCurveGen"], "efficiency curve, discharging", "[P] eta_d, [S] (13)"),
    ("storage", ["pStorage"], "storage model (ElmStorage)", "[P] (1)"),
    ("inverter_efficiency", ["inveff"], "inverter efficiency", "[S] (13)"),
    ("category", ["aCategory", "cCategory"], "category", ""),
]


def fields(reader, obj, spec):
    out = {}
    for key, names, text, ref in spec:
        found = next((reader.entry(obj, n) for n in names if reader.entry(obj, n) is not None), None)
        out[key] = dict(found or dict(attribute=None, value=None, unit="", text="not in this model"),
                        meaning=text, paper=ref)
    return out


def is_default(value):
    """PowerFactory's 'no limit' (9999 MW, also shown as 9999000 kW) or an empty value."""
    return value in (None, "", 0, 0.0, [], False) or (isinstance(value, (int, float)) and abs(value) >= 9999)


def number(entry, kw=False):
    """The value of an entry, None where it is missing or still at PowerFactory's default."""
    if not entry or is_default(entry.get("value")):
        return None
    value = entry.get("kw") if kw else entry.get("value")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def num(v):
    """A number for the gaps table: no float noise (648.0000115633011 -> 648)."""
    return f"{v:.6g}" if isinstance(v, float) else preview(v)


def operating(reader, obj):
    """Load flow results of an element, in kW / kvar / pu."""
    out = {}
    for key, name in (("p_kw", "m:P:bus1"), ("q_kvar", "m:Q:bus1"), ("u_pu", "m:u1"), ("loading_pct", "c:loading")):
        e = reader.entry(obj, name)
        if e is not None:
            out[key] = e["kw"] if key in ("p_kw", "q_kvar") and e["kw"] is not None else e["value"]
    return out


def setpoint(reader, obj, name):
    e = reader.entry(obj, name)
    return dict(value=e["value"], unit=e["unit"], kw=e["kw"]) if e else None


def characteristic_info(reader, obj):
    """The time characteristics of an element: which parameter, which characteristic, and its data."""
    out = {}
    for par, cha in characteristics(obj).items():
        d = reader.read(cha)
        info = dict(characteristic=cha.loc_name, cls=cha.GetClassName())
        for name, row in d["rows"].items():
            if any(k in name.lower() for k in ("file", "f_name", "source", "usage", "unit", "scale", "timeformat",
                                               "startyear", "datacol", "timecol", "approx", "recurr", "period")):
                info[name] = plain(row["value"])
        vec = next((row["value"] for name, row in d["rows"].items()
                    if name in ("vector", "values", "y", "dat") and isinstance(row["value"], (list, tuple))), None)
        if vec and not info.get("f_name"):         # a file-driven characteristic keeps an unused internal table
            flat = [v for r in vec for v in (r if isinstance(r, (list, tuple)) else [r])
                    if isinstance(v, (int, float))]
            if flat:
                info["values"] = dict(count=len(flat), min=min(flat), max=max(flat), mean=sum(flat) / len(flat))
        out[par] = info
    return out


def rating(reader, obj):
    """(rated kVA of all parallel units, rated power factor, parallel units)."""
    holder = attr(obj, "typ_id") if obj.GetClassName() == "ElmSym" else obj
    units = attr(obj, "ngnum", 1) or 1
    s = reader.entry(holder, "sgn") if holder is not None else None
    cos = attr(holder, "cosn", 1.0) or 1.0
    kva = s["kw"] * units if s and s["kw"] is not None else None
    return kva, cos, units


def fuel_curve(role):
    """The data-sheet fuel curve of one genset: the points, the affine fit of the prime points
    (L/h = a + b*P while running) and the same in rand, or None for an unknown genset."""
    d = FUEL_DATA.get(role)
    if not d:
        return None
    p = [s * d["prime_kw"] for s in LOAD_STEPS]
    f = list(d["prime_l_h"])
    mp, mf = sum(p) / len(p), sum(f) / len(f)
    b = sum((x - mp) * (y - mf) for x, y in zip(p, f)) / sum((x - mp) ** 2 for x in p)
    a = mf - b * mp
    slopes = [(f[i + 1] - f[i]) / (p[i + 1] - p[i]) for i in range(len(p) - 1)]
    return dict(
        model=d["model"], diesel_price_r_per_l=DIESEL_PRICE,
        prime=dict(p_kw=p, fuel_l_h=f), standby=dict(p_kw=[s * d["standby_kw"] for s in LOAD_STEPS],
                                                     fuel_l_h=list(d["standby_l_h"])),
        fit_prime=dict(a_l_h=a, b_l_kwh=b, max_residual_l_h=max(abs(y - a - b * x) for x, y in zip(p, f)),
                       a_r_h=a * DIESEL_PRICE, b_r_kwh=b * DIESEL_PRICE),
        # the incremental fuel use between the points: rising = convex, so the points can be
        # the breakpoints of a piecewise-linear cost ([P] (14)) as they are
        slopes_l_kwh=slopes, convex=all(s2 >= s1 for s1, s2 in zip(slopes, slopes[1:])),
        cost_r_kwh_at_points=[y * DIESEL_PRICE / x for x, y in zip(p, f)])


def genset(reader, gen, ctrl):
    typ = attr(gen, "typ_id")
    kva, cos, units = rating(reader, gen)
    h = attr(typ, "h")
    comp, models = plant_models(reader, gen)
    role = "dg1" if gen.loc_name == ctrl_value(ctrl, "DG1_NAME") else "dg2" if gen.loc_name == ctrl_value(ctrl, "DG2_NAME") else None
    data = (ctrl_value(ctrl, "GENSET_DATA") or {}).get(role, {}) if role else {}
    ctrl_part = {}
    if role == "dg1":
        ctrl_part = dict(min_level=ctrl_value(ctrl, "DG1_MIN_LEVEL"), rating_kw=ctrl_value(ctrl, "DG1_RATING_KW"))
    elif role == "dg2":
        ctrl_part = dict(on_level=ctrl_value(ctrl, "DG2_ON_LEVEL"), off_level=ctrl_value(ctrl, "DG2_OFF_LEVEL"),
                         p_kw=ctrl_value(ctrl, "DG2_P_KW"), min_run_min=ctrl_value(ctrl, "DG2_MIN_RUN_MIN"),
                         min_stop_min=ctrl_value(ctrl, "DG2_MIN_STOP_MIN"), rating_kw=ctrl_value(ctrl, "DG2_RATING_KW"))
    if data:
        ctrl_part.update(prime_kw=data.get("prime_kw"), standby_kw=data.get("standby_kw"), j_kgm2=data.get("j_kgm2"),
                         j_note=data.get("j_note"), governor_datasheet=data.get("gov"))
    return dict(
        name=gen.loc_name, cls=gen.GetClassName(), in_service=not attr(gen, "outserv", 0),
        terminal=short(terminal(gen)), type=typ.loc_name if typ is not None else None,
        rating_kva=kva, cos_phi=cos, parallel_units=units, rating_kw=kva * cos if kva else None,
        inertia_h_s=h, kinetic_energy_kws=h * kva if h and kva else None,
        machine=dict(rstr=attr(typ, "rstr"), xd=attr(typ, "xd"), xq=attr(typ, "xq"), xds=attr(typ, "xds"),
                     xdss=attr(typ, "xdss"), tds=attr(typ, "tds"), tdss=attr(typ, "tdss")),
        dispatch=dict(pgini=setpoint(reader, gen, "pgini"), qgini=setpoint(reader, gen, "qgini"),
                      control=attr(gen, "av_mode"), reference_machine=attr(gen, "ip_ctrl")),
        unit_commitment=fields(reader, gen, DG_FIELDS),
        composite_model=comp.loc_name if comp is not None else None, controllers=models,
        load_flow=operating(reader, gen), ctrl_py=ctrl_part, characteristics=characteristic_info(reader, gen),
        fuel=fuel_curve(role))


def battery(reader, bess, ctrl):
    kva, cos, units = rating(reader, bess)
    f = fields(reader, bess, GENSTAT_FIELDS)
    storage = attr(bess, "pStorage")
    for obj in calc_objects("ElmStorage"):          # a storage model that points the other way
        if storage is None and any(full_name(x) == full_name(bess) for x in
                                   [attr(obj, n) for n in ("pGen", "pElm", "pgen", "elm")] if is_object(x)):
            storage = obj
    return dict(
        name=bess.loc_name, cls=bess.GetClassName(), in_service=not attr(bess, "outserv", 0),
        terminal=short(terminal(bess)), rating_kva=kva, cos_phi=cos, parallel_units=units,
        dispatch=dict(pgini=setpoint(reader, bess, "pgini"), qgini=setpoint(reader, bess, "qgini")),
        limits=f, storage_model=dict(name=storage.loc_name, **fields(reader, storage, STORAGE_FIELDS))
        if storage is not None else None,
        load_flow=operating(reader, bess), characteristics=characteristic_info(reader, bess),
        ctrl_py=dict(p_kw=ctrl_value(ctrl, "BESS_P_KW"), e_kwh=ctrl_value(ctrl, "BESS_E_KWH"),
                     soc_init=ctrl_value(ctrl, "BESS_SOC_INIT"), soc_min=ctrl_value(ctrl, "BESS_SOC_MIN"),
                     soc_max=ctrl_value(ctrl, "BESS_SOC_MAX"), soc_resume=ctrl_value(ctrl, "BESS_SOC_RESUME"),
                     round_trip_efficiency=ctrl_value(ctrl, "BESS_EFFICIENCY")))


def pv_plant(reader, pv, ctrl):
    kva, cos, units = rating(reader, pv)
    return dict(
        name=pv.loc_name, cls=pv.GetClassName(), in_service=not attr(pv, "outserv", 0),
        terminal=short(terminal(pv)), rating_kva=kva, cos_phi=cos, parallel_units=units,
        dispatch=dict(pgini=setpoint(reader, pv, "pgini"), scale0=attr(pv, "scale0")),
        limits=fields(reader, pv, GENSTAT_FIELDS), characteristics=characteristic_info(reader, pv),
        load_flow=operating(reader, pv),
        ctrl_py=dict(curtail_pv=ctrl_value(ctrl, "CURTAIL_PV")))


def load_model(reader, lod):
    typ = attr(lod, "typ_id")
    dep = {}
    if typ is not None:
        for k in ("aP", "bP", "cP", "kpu0", "kpu1", "kpu", "aQ", "bQ", "cQ", "kqu0", "kqu1", "kqu", "loddy"):
            v = attr(typ, k)
            if v is not None:
                dep[k] = v
    return dict(name=lod.loc_name, cls=lod.GetClassName(), in_service=not attr(lod, "outserv", 0),
                terminal=short(terminal(lod)), type=typ.loc_name if typ is not None else None,
                plini=setpoint(reader, lod, "plini"), qlini=setpoint(reader, lod, "qlini"),
                scale0=attr(lod, "scale0"), voltage_dependence=dep,
                characteristics=characteristic_info(reader, lod), load_flow=operating(reader, lod))


def network(reader):
    out = dict(terminals=[], lines=[], transformers=[], external_grids=[])
    for t in calc_objects("ElmTerm"):
        reader.read(t)
        out["terminals"].append(dict(name=t.loc_name, u_kv=attr(t, "uknom"), in_service=not attr(t, "outserv", 0),
                                     u_pu=attr(t, "m:u") if RUN_LOAD_FLOW else None))
    for ln in calc_objects("ElmLne"):
        typ = attr(ln, "typ_id")
        reader.read(ln)
        out["lines"].append(dict(name=ln.loc_name, from_=short(terminal(ln, "bus1")), to=short(terminal(ln, "bus2")),
                                 length_km=attr(ln, "dline"), parallel=attr(ln, "nlnum"),
                                 type=typ.loc_name if typ is not None else None,
                                 i_rated_ka=attr(typ, "sline"), r1_ohm_km=attr(typ, "rline"),
                                 x1_ohm_km=attr(typ, "xline"),
                                 loading_pct=attr(ln, "c:loading") if RUN_LOAD_FLOW else None))
    for tr in calc_objects("ElmTr2"):
        reader.read(tr)
        out["transformers"].append(dict(name=tr.loc_name, loading_pct=attr(tr, "c:loading") if RUN_LOAD_FLOW else None))
    for x in calc_objects("ElmXnet"):
        out["external_grids"].append(dict(name=x.loc_name, in_service=not attr(x, "outserv", 0)))
    return out


def derived(model):
    """Quantities the script works out from the data above (not read from PowerFactory)."""
    dgs = [d for d in model["gensets"] if d["kinetic_energy_kws"]]
    out = dict(note="Worked out by this script from the data above, by the swing equation without load "
                    "damping or governor action: an initial-ROCOF estimate, not a simulation result.",
               commitments=[])
    if not dgs:
        return out
    first = dgs[0]
    combos = [[first]] + ([[first] + dgs[1:]] if len(dgs) > 1 else [])
    losses = []
    b = model.get("battery")
    if b:
        p = (number(b["limits"]["p_max_operational"], kw=True) or b["ctrl_py"].get("p_kw")
             or (b["rating_kva"] or 0) * (b["cos_phi"] or 1))
        losses.append(("battery at full discharge", p))
    for pv in model.get("pv", []):
        losses.append((f"{pv['name']} at its rating", (pv["rating_kva"] or 0) * (pv["cos_phi"] or 1)))
    for combo in combos:
        e = sum(d["kinetic_energy_kws"] for d in combo)
        s = sum(d["rating_kva"] for d in combo)
        cap = sum(d["rating_kw"] or 0 for d in combo)
        row = dict(running=[d["name"] for d in combo], kinetic_energy_kws=e, h_system_s=e / s if s else None,
                   rated_kw=cap, rocof_hz_s_per_100kw=100.0 * F_NOM / (2 * e), loss_of=[])
        for d in combo[1:]:
            rest = e - d["kinetic_energy_kws"]
            row["loss_of"].append(dict(what=f"{d['name']} at its rating", p_kw=d["rating_kw"],
                                       initial_rocof_hz_s=(d["rating_kw"] or 0) * F_NOM / (2 * rest)))
        for what, p in losses:
            row["loss_of"].append(dict(what=what, p_kw=p, initial_rocof_hz_s=p * F_NOM / (2 * e)))
        out["commitments"].append(row)
    return out


def gaps(model):
    """For every quantity the MPC needs: where the value comes from."""
    rows = []

    def add(unit, what, entry, ctrl_text, paper):
        v = entry.get("value") if entry else None
        attribute = entry.get("attribute") if entry else None
        if attribute is None:
            state = "not in the model"
        elif is_default(v):
            state = f"{attribute} = {num(v)} (default / not set)"
        else:
            state = f"{attribute} = {num(v)} {entry.get('unit', '')}".rstrip()
        rows.append(dict(unit=unit, quantity=what, powerfactory=state, ctrl_py=ctrl_text or "-", paper=paper))

    for d in model["gensets"]:
        uc, c = d["unit_commitment"], d["ctrl_py"]
        add(d["name"], "rated power", dict(attribute="TypSym.sgn x cosn", value=d["rating_kw"], unit="kW"),
            f"prime {c.get('prime_kw')} / standby {c.get('standby_kw')} kW (data sheet)", "[P] (16b)")
        add(d["name"], "minimum loading", uc["p_min_operational"],
            f"{c['min_level']:.0%} of rating" if c.get("min_level") is not None else "", "[P] (16b), [S] C<30%")
        add(d["name"], "maximum operational power", uc["p_max_operational"], "", "[P] (16b), [S] (26d)")
        add(d["name"], "ramp limits", uc["ramp_up"], "", "[P] (16c)")
        add(d["name"], "minimum up time", uc["min_up_time"],
            f"{c['min_run_min']} min" if c.get("min_run_min") else "", "[P] (11)")
        add(d["name"], "minimum down time", uc["min_down_time"],
            f"{c['min_stop_min']} min" if c.get("min_stop_min") else "", "[P] (11), [S] (26e)")
        add(d["name"], "start-up cost", uc["cost_start_up"], "", "[P] (13), [S] (22)")
        fit = (d["fuel"] or {}).get("fit_prime")
        add(d["name"], "fuel cost curve", uc["cost_curve"],
            f"{fit['a_r_h']:.1f} R/h + {fit['b_r_kwh']:.3f} R/kWh" if fit else "", "[P] (14), [S] (23)")
        add(d["name"], "inertia H", dict(attribute="TypSym.h", value=d["inertia_h_s"], unit="s"),
            f"J = {c['j_kgm2']:.4g} kg m2" if c.get("j_kgm2") else "", "stability constraint")
    b = model.get("battery")
    if b:
        lim, st, c = b["limits"], b["storage_model"] or {}, b["ctrl_py"]
        add(b["name"], "discharge limit", lim["p_max_operational"],
            f"{c['p_kw']} kW" if c.get("p_kw") else "rating from PowerFactory", "[P] C_b, [S] (26b)")
        add(b["name"], "charge limit", lim["p_min_operational"], "minus the discharge limit", "[P] -C_b, [S] (26b)")
        add(b["name"], "usable energy", st.get("capacity"), f"{c.get('e_kwh')} kWh", "[P] (16a), [S] C_N")
        add(b["name"], "SOC limits", st.get("min_energy"),
            f"{c.get('soc_min')}-{c.get('soc_max')} (resume at {c.get('soc_resume')})", "[P] (16a), [S] (26a)")
        add(b["name"], "charge/discharge efficiency", lim["efficiency_charging"],
            f"round trip {c.get('round_trip_efficiency')}, split evenly", "[P] (2), [S] (13)")
        add(b["name"], "self-discharge", st.get("self_discharge"), "", "[P] x_sb")
    for pv in model.get("pv", []):
        chars = ", ".join(f"{k} -> {v['characteristic']}" for k, v in pv["characteristics"].items())
        add(pv["name"], "rated power", dict(attribute="sgn x cosn", value=(pv["rating_kva"] or 0) * (pv["cos_phi"] or 1),
                                           unit="kW"), "", "[P] Pres")
        add(pv["name"], "available power over time", dict(attribute="characteristic", value=chars or None),
            "pv_ac_profile_measured.csv", "[P] Pres, [S] (1)-(4)")
    for lod in model.get("loads", []):
        chars = ", ".join(f"{k} -> {v['characteristic']}" for k, v in lod["characteristics"].items())
        add(lod["name"], "demand over time", dict(attribute="characteristic", value=chars or None),
            "aggregate_total_load.csv", "[P] D, [S] Pload")
    return rows


# ============================================================================
# REPORT
# ============================================================================
def fmt(v, pattern="{:.1f}"):
    return pattern.format(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else ("-" if v is None else str(v))


def report(model):
    log("\n" + "=" * 100)
    log("SYSTEM DATA FOR THE MPC (read from PowerFactory)")
    log("=" * 100)
    m = model["meta"]
    log(f"Project {m['project']}, study case {m['study_case']}, study time {m['study_time']}"
        f"{', load flow solved' if m['load_flow'] == 'ok' else ', load flow: ' + str(m['load_flow'])}")
    for d in model["gensets"]:
        log(f"\n{d['name']} ({d['type']}), {'in service' if d['in_service'] else 'OUT OF SERVICE'}, at {d['terminal']}")
        log(f"   rating {fmt(d['rating_kva'])} kVA x cos {fmt(d['cos_phi'], '{:.2f}')} = {fmt(d['rating_kw'])} kW "
            f"({d['parallel_units']} unit(s));  H = {fmt(d['inertia_h_s'], '{:.3f}')} s, "
            f"stored energy {fmt(d['kinetic_energy_kws'], '{:.0f}')} kWs")
        if d["load_flow"]:
            log(f"   load flow: P {fmt(d['load_flow'].get('p_kw'))} kW, Q {fmt(d['load_flow'].get('q_kvar'))} kvar")
        for key, e in d["unit_commitment"].items():
            if e["attribute"]:
                log(f"   {e['meaning']:<52} {e['attribute']:<26} {preview(e['value'])} {e['unit']}".rstrip())
        for slot, mdl in d["controllers"].items():
            pars = ", ".join(f"{k} = {v:g}" for k, v in mdl["parameters"].items())
            log(f"   {slot}: {mdl['model']} ({mdl['definition']}){'' if mdl['in_service'] else ' OUT OF SERVICE'}: {pars}")
        fu = d["fuel"]
        if fu:
            fit = fu["fit_prime"]
            log(f"   fuel ({fu['model']} data sheet, prime): "
                + ", ".join(f"{p:.0f} kW {f:.1f} L/h" for p, f in zip(fu["prime"]["p_kw"], fu["prime"]["fuel_l_h"])))
            log(f"   fit: {fit['a_l_h']:.2f} L/h + {fit['b_l_kwh']:.4f} L/kWh x P (within {fit['max_residual_l_h']:.2f} L/h)"
                f"; at R{fu['diesel_price_r_per_l']:.2f}/L: {fit['a_r_h']:.1f} R/h + {fit['b_r_kwh']:.3f} R/kWh")
            log(f"   incremental use {', '.join(f'{s:.4f}' for s in fu['slopes_l_kwh'])} L/kWh: "
                + ("convex, the points can serve as breakpoints" if fu["convex"] else
                   "not convex, use the affine fit (or convexify) for a piecewise-linear cost"))
    b = model.get("battery")
    if b:
        log(f"\n{b['name']} (battery, {b['cls']}), {'in service' if b['in_service'] else 'OUT OF SERVICE'}, at {b['terminal']}")
        log(f"   rating {fmt(b['rating_kva'])} kVA, cos {fmt(b['cos_phi'], '{:.2f}')}")
        for key, e in b["limits"].items():
            if e["attribute"]:
                log(f"   {e['meaning']:<52} {e['attribute']:<26} {preview(e['value'])} {e['unit']}".rstrip())
        if b["storage_model"]:
            log(f"   storage model {b['storage_model']['name']}:")
            for key, e in b["storage_model"].items():
                if isinstance(e, dict) and e.get("attribute"):
                    log(f"      {e['meaning']:<49} {e['attribute']:<26} {preview(e['value'])} {e['unit']}".rstrip())
        else:
            log("   no storage model (ElmStorage) linked: energy, SOC limits and efficiencies are not in the model")
    for pv in model.get("pv", []):
        log(f"\n{pv['name']} (PV, {pv['cls']}), at {pv['terminal']}: rating {fmt(pv['rating_kva'])} kVA")
        for par, c in pv["characteristics"].items():
            log(f"   {par} -> {c['characteristic']} ({c['cls']}) "
                + ", ".join(f"{k} {v}" for k, v in c.items() if k not in ("characteristic", "cls", "values")))
    for lod in model.get("loads", []):
        log(f"\n{lod['name']} (load), plini {preview((lod['plini'] or {}).get('value'))} "
            f"{(lod['plini'] or {}).get('unit', '')}, voltage dependence {lod['voltage_dependence']}")
        for par, c in lod["characteristics"].items():
            log(f"   {par} -> {c['characteristic']} ({c['cls']})")
    net = model["network"]
    log(f"\nNetwork: {len(net['terminals'])} terminals, {len(net['lines'])} lines, "
        f"{len(net['transformers'])} transformers, {len(net['external_grids'])} external grids")
    for x in net["external_grids"]:
        log(f"   external grid {x['name']}: {'IN SERVICE' if x['in_service'] else 'out of service'}"
            + (" (ctrl.py takes it out of service for its runs, ISOLATED)" if x["in_service"] else ""))
    for ln in net["lines"]:
        log(f"   {ln['name']}: {ln['from_']} -> {ln['to']}, {fmt(ln['length_km'], '{:.3f}')} km, "
            f"rated {fmt(ln['i_rated_ka'], '{:.3f}')} kA, loading {fmt(ln['loading_pct'])} %")
    if model["losses_kw"] is not None:
        log(f"   network losses at this point: {model['losses_kw']:.2f} kW ({model['losses_share']:.2%} of the load)")
    elif model["balance_kw"] is not None:
        warn(f"generation minus load is {model['balance_kw']:.1f} kW at this point: more than losses can be. A "
             "source the script does not count (or a sign) is off, so no losses are given")
    der = model["derived"]
    if der["commitments"]:
        log(f"\nDerived (by this script): {der['note']}")
        for c in der["commitments"]:
            log(f"   running {' + '.join(c['running'])}: H = {fmt(c['h_system_s'], '{:.3f}')} s on "
                f"{fmt(c['rated_kw'], '{:.0f}')} kW, {fmt(c['rocof_hz_s_per_100kw'], '{:.2f}')} Hz/s per 100 kW lost")
            for x in c["loss_of"]:
                log(f"      loss of {x['what']} ({fmt(x['p_kw'], '{:.0f}')} kW): initial ROCOF about "
                    f"{fmt(x['initial_rocof_hz_s'], '{:.1f}')} Hz/s")
    log("\n" + "-" * 100)
    log("WHAT THE MPC NEEDS, AND WHERE IT COMES FROM")
    log("-" * 100)
    log(f"{'unit':<10} {'quantity':<28} {'PowerFactory':<56} {'ctrl.py / data sheet':<40} paper")
    for r in model["gaps"]:
        log(f"{r['unit']:<10} {r['quantity']:<28} {r['powerfactory'][:56]:<56} {r['ctrl_py'][:40]:<40} {r['paper']}")
    log("'default / not set': the attribute exists but still holds PowerFactory's default; the MPC needs a value "
        "from a data sheet or a decision, or the value ctrl.py uses.")


# ============================================================================
# MAIN
# ============================================================================
def main():
    connect()
    started = time.time()
    log(f"MPC system data, {datetime.now():%Y-%m-%d %H:%M}")
    project, case = app.GetActiveProject(), app.GetActiveStudyCase()
    if project is None or case is None:
        log("!! No active project or study case: activate the project of ctrl.py first.")
        return
    t = attr(case, "iStudyTime")
    when = datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if isinstance(t, (int, float)) else str(t)
    log(f"Project {project.loc_name}, study case {case.loc_name}, study time {when} (local)")
    schema = Schema()
    if schema.source:
        log(f"Attribute names from {schema.source}")
    reader = Reader(schema)
    ctrl = load_ctrl()

    lf = "not run"
    if RUN_LOAD_FLOW:
        try:
            lf = "ok" if app.GetFromStudyCase("ComLdf").Execute() == 0 else "did not converge"
        except Exception as e:
            lf = f"failed ({type(e).__name__}: {e})"
        log(f"Load flow at the study time: {lf}")

    gens = [g for g in calc_objects("ElmSym") if DG_NAMES is None or g.loc_name in DG_NAMES]
    gens.sort(key=lambda g: (g.loc_name != ctrl_value(ctrl, "DG1_NAME"), g.loc_name))
    pvs, bess = classify_genstats()
    loads = [o for cls in ("ElmLod", "ElmLodlv", "ElmLodmv") for o in calc_objects(cls)]
    for obj in gens + pvs + loads + ([bess] if bess else []) + calc_objects("ElmStorage") + calc_objects("ElmBattery"):
        reader.read(obj)
    log(f"Found: {len(gens)} gensets ({', '.join(g.loc_name for g in gens)}), "
        f"battery {bess.loc_name if bess else 'none'}, PV {', '.join(p.loc_name for p in pvs) or 'none'}, "
        f"{len(loads)} load(s)")

    model = dict(
        meta=dict(project=project.loc_name, study_case=case.loc_name, study_time=when, study_time_epoch=t,
                  load_flow=lf, read_at=f"{datetime.now():%Y-%m-%d %H:%M}", schema=schema.source,
                  script=os.path.basename(globals().get("__file__", "mpc_model_poll.py"))),
        gensets=[genset(reader, g, ctrl) for g in gens],
        battery=battery(reader, bess, ctrl) if bess else None,
        pv=[pv_plant(reader, p, ctrl) for p in pvs],
        loads=[load_model(reader, lod) for lod in loads],
        network=network(reader))
    gen = sum((d["load_flow"].get("p_kw") or 0) for d in model["gensets"])
    gen += sum((x["load_flow"].get("p_kw") or 0) for x in model["pv"] + ([model["battery"]] if bess else []))
    demand = sum((x["load_flow"].get("p_kw") or 0) for x in model["loads"])
    # generation minus load is the network loss only where it is small and positive
    model["balance_kw"] = gen - demand if lf == "ok" else None
    plausible = model["balance_kw"] is not None and 0 <= model["balance_kw"] <= 0.1 * max(demand, 1.0)
    model["losses_kw"] = model["balance_kw"] if plausible else None
    model["losses_share"] = model["balance_kw"] / demand if plausible and demand else None
    model["derived"] = derived(model)
    model["gaps"] = gaps(model)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "mpc_system_model.json"), "w", encoding="utf-8") as fh:
        json.dump(model, fh, indent=2, default=lambda o: full_name(o) if is_object(o) else str(o))
    n = reader.write_all(os.path.join(OUT_DIR, "attributes"))
    report(model)
    log(f"\nSaved in {OUT_DIR}: mpc_system_model.json, mpc_system_model_report.txt, and every attribute of "
        f"{n} objects in attributes\\   [{time.time() - started:.1f} s]")
    with open(os.path.join(OUT_DIR, "mpc_system_model_report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(_lines) + "\n")


if __name__ == "__main__":
    main()
