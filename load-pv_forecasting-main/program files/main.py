"""
Dispatch and RMS stability study of the isolated PV-BESS-diesel microgrid
PowerFactory 2024 SP5A  -  Python script
=====================================================================

One program, two parts, chosen in a dialog when it starts:
  1. QUASI-DYNAMIC DISPATCH. A dispatch controller (rule-based, MPC, or both
     head to head) steps through the time series in 5-minute load flows: up to
     each operating point after a warm-up, or over a period of your own. Its log
     (DISPATCH_DIR) holds the controller's state at every instant: battery
     power and SOC, DG_1, DG_2 and the PV it fed.
  2. RMS STABILITY. Each controller's state at an operating point (from its
     dispatch log, or typed into DISPATCH) is set in the model and the test
     cases are run on it. The controller whose dispatch leaves the island more
     stable wins.
Run both in one go, or each on its own: the stability study reads the logs
the dispatch left behind.

THE MPC (MODEL-PREDICTIVE DISPATCH further down)
  A stability-constrained heuristic MPC. At every step it tries DG_2 schedules
  times battery/curtailment rules over a horizon of a few hours, keeps every
  step's stability margin above m* against the credible sudden changes of power
  (spinning reserve, ISO 8528-5 load acceptance, frequency nadir above the
  inverters' trip, reverse power and over-frequency, ROCOF, inverter share,
  voltage dip) and the two sets staying in step through a fault at the main
  board, and applies the first move of the cheapest. It runs through the
  same load flows as the rule-based controller, or on its own model of the
  island without PowerFactory. Its decisions run in separate Python processes,
  one per CPU (MPC_WORKERS): PowerFactory's own Python runs them 25-40x slower.
  Its tools, in the dialog's tab MPC:
    - read the system data from PowerFactory (mpc_system_model.json, with a
      report of what the model holds and what is still at its default)
    - calibrate the stability model from the profiles and this study's own RMS
      results (mpc_calibration.json)
    - report every parameter with where it comes from
    - compare the dispatch logs head to head: fuel, cost and stability margins
      of every controller on one yardstick (dispatch_comparison.csv,
      dispatch_head_to_head_<point>.png)
  Every parameter can be changed in the tab MPC parameters (MPC_PARAMS).

Every curve and every number the stability study reports is read from
PowerFactory's own RMS (electromechanical) simulation of the network model.
What the script adds is listed under "WHAT THE SCRIPT ADDS TO POWERFACTORY'S
RESULT" below.

WHAT THE STABILITY STUDY DOES
  1. Survey. Reports what the model has for an RMS simulation and what is
     missing: genset inertia, governors and voltage regulators, and how PV,
     battery and load will behave.
  2. Governors and voltage regulators. A genset holds frequency and voltage
     only with a governor and a voltage regulator, and a network model has
     none until they are added. The script adds them itself, so nothing has
     to be clicked together: for each genset a composite model with the
     machine, a governor and a voltage regulator in its slots, with their
     values written in. It uses its own two models first: a few equations
     each, written out in this file under OWN_GOVERNOR and OWN_REGULATOR and
     simulated by PowerFactory. If PowerFactory does not take those, it uses
     DIgSILENT's library models DEGOV1 and SEXS, under whichever name the
     installed version has them (CONTROLLERS chooses). It reads back from
     PowerFactory what it has set up and then tests it: with DG_1 alone and
     with both sets, a load step of 10 % has to start from a steady state,
     the governors and regulators have to act, and frequency and voltage
     have to come back and rest. Only then do the scenarios run. If no set
     of controllers passes, everything is taken back and the run stops with
     PowerFactory's own messages in the log.
  3. Operating points: real instants of the time series (POINTS). For each
     dispatch controller the script moves the study time there, sets the
     battery, the PV curtailment and DG_2 as that controller left them
     (DISPATCH), runs a load flow and prints it.
  4. Scenarios, the large disturbances named in [1] p.4 and [3] p.1, applied
     at every operating point for every controller:
       G  loss of a source      the battery, DG_2, the PV plant
       R  loss of load          a part of it
       P  sudden load pickup    a block of load switched on at once
       F  faults                temporary faults at the main board:
                                single-line-to-ground, line-to-line and bolted
                                three-phase
     For each one it creates the simulation events, runs the RMS simulation
     and reads back
       - frequency: rate of change (ROCOF), lowest, highest, recovery, final
       - voltage at the genset busbar, the main board and the load terminal:
         before, lowest, highest, final, time outside 0.9 to 1.1 pu, recovery
       - active and reactive power of every source and of the load
       - rotor angle of DG_2 against DG_1 and pole slip (both sets running)
       - currents of the sources and of the cable, in multiples of their ratings
     and checks that the disturbance really took effect in the simulation.
  5. Critical clearing time of a temporary three-phase fault at the main
     board, by operating point and controller ([1] p.7, [3] p.14-16).
  Output (OUT_DIR): for each operating point a head-to-head figure of the
  controllers (head_to_head_<point>.png: ROCOF, largest frequency deviation,
  time until the frequency is back at 50 Hz to stay, voltage recovery and the
  outcome, case by case) and three summary plots (summary_frequency_<point>,
  summary_voltage_<point>, summary_faults_<point>), made for a report; PNG
  plots of every single run in plots/<point> (frequency with its stability
  measures, active power, reactive power, busbar voltages, phase voltages for
  the unbalanced faults, rotor angle, fault currents; T1 and T2 are the two
  runs of the self-test), a results table (CSV, and the
  log as text), one CSV of curves per case, the result objects in the study
  case ("Stability <case>"), plot pages inside PowerFactory and those pages
  saved as images by PowerFactory itself. Operating points, events and
  simulation settings are put back as they were. The governors and voltage
  regulators stay in the model: they are part of it from then on.

WHEN A CASE COUNTS AS STABLE
  [1] p.3: a microgrid is stable if, after a disturbance, "all state variables
  recover to (possibly new) steady-state values which satisfy operational
  constraints ... and without the occurrence of involuntary load shedding".
  Loads disconnected to isolate a faulted element do not count against it.
  Here that is read as: the frequency ends inside F_BAND of 50 Hz, every live
  busbar ends between U_LOW and U_HIGH, no genset and no cable ends beyond its
  rating, everything has come to rest, the two sets stay in step, and no load
  is lost. The limits are settings of this script, not values from [1]. The
  other outcomes:
    stable after a protection trip   an inverter or a genset reaches one of
                      its (assumed) protection settings, trips, and the island
                      still recovers. [1] p.6 names the blackout that such
                      trips can end in as a serious concern, so these cases
                      are kept apart from plain "stable".
    ..., best case    added to "stable" where a set takes up more load in one
                      step than the ISO 8528-5 guide for its engine: the
                      governor model lets it, a real engine dips further.
    outside limits    everything settles, but outside those limits
    unstable          blackout, loss of synchronism, a voltage that does not
                      come back, an oscillation that does not die out, or no
                      steady state by the end of the longest run
    supply lost       the faulted cable or feeder is tripped and what lies
                      behind it has no source that forms the grid: a matter of
                      the layout (one cable, one feeder), not of stability.
                      The gensets that stay energised are judged on their own.
    unstable as modelled   the gensets over-speed because PV and battery keep
                      feeding more than the load takes. The model keeps them
                      at their set-points; the outcome says how fast a real
                      inverter would have to cut its power to prevent it.
  [1] sorts instability by its cause (Fig. 2, Table I). The script cannot do
  that: with an unstable outcome it only says where it shows (in the
  frequency, the voltage, the rotor angle, or as a lasting oscillation).
  The genset transient is also compared with the limit values of the
  ISO 8528-5 performance classes G1 to G3 (G3 is the tightest). That is
  indicative: the standard sets them for one set and its own load steps.
  Critical clearing time here: the longest temporary fault after which the
  island is stable with nothing tripped ([3] p.14: synchronism is kept).
  [1] p.7 points out that the equal-area criterion does not carry over to
  microgrids, and p.6 that loss of synchronism has not been observed in them:
  a pole slip in these results needs the inertia and the stator resistance of
  the sets checked first.

WHAT THE SCRIPT ADDS TO POWERFACTORY'S RESULT
  - Protection the model does not contain. The curves are compared with the
    assumed inverter settings (frequency and voltage, NRS 097-2-1:2024: its
    longest permitted trip times are used as the trip times) and with the
    gensets' over-speed shutdown. If one would act, the case is run again in
    PowerFactory with that trip as an event ("emulated" in tables and plots).
    Reverse power on a genset is reported, not emulated.
  - The limits for the outcome, and the floor of 40 Hz below which a run is
    counted as a collapse (the script's own: 80 % speed).
  - Checks: steady start, the disturbance took effect, the fault was seen.
  - Time: a case that has not come to rest at its end is run again for twice
    as long (up to T_END_LONGEST).

NOT COVERED
  Auto-reclosing ([3] p.1), unplanned islanding from a grid
  ([1] p.4: this island has none), single inverters tripping, cloud ramps,
  motor loads that stall ([1] p.5), and everything faster than the
  electromechanical range: converter-driven instability and harmonic
  interaction ([1] Fig. 2), which an RMS simulation cannot show ([1] p.8,
  [2] p.1289). Where the battery or PV carry most of the load (OP2: PV is five
  times the load) that is a real limit of these results. Loss of DG_1 where it runs alone is not
  a case: it is a blackout as modelled, because the battery and PV inverters
  follow a grid and cannot form one.

COMPARING DISPATCH CONTROLLERS
  The same disturbances, applied to the island as each dispatch controller left
  it at the same moments. A controller decides which sets run, what the battery
  does and how much PV is cut, and so how much inertia and governor headroom the
  island has when something goes wrong, and how large the largest loss is. The
  controller whose dispatch leaves the island more stable wins.
    1. POINTS: three instants of the time series, each with its own demand and
       available PV: the peak demand (14 July 2002 11:20), the largest excess
       of PV (25 February 2002 12:05) and an evening in the worst PV week
       (30 July 2002 20:30), when the battery is empty and DG_2 runs.
    2. Run each dispatch script (rule-based, MPC, ...) up to those instants
       and write what it did there into DISPATCH: battery power and SOC, DG_1,
       DG_2 and the PV it fed.
    3. This script: for every point and every controller it moves the study
       time to the instant (the load and PV characteristics follow), sets the
       battery, the PV curtailment and DG_2 from DISPATCH, checks the load flow
       against POINTS, and applies CASES. A disturbance that means nothing at a
       point (losing an idle battery, or a DG_2 that is not running) is listed
       as not applicable. Results are labelled by controller and point; pages
       in PowerFactory show every controller's curves together (PF_PLOT_CASES).
    4. controller_study.py report: tables and plots that compare the controllers.
  INSTANTS_FILE can take the place of POINTS and DISPATCH: the instants.csv that
  controller_study.py select writes from the controllers' logs.

HOW TO RUN
  Inside PowerFactory (recommended): create a Python Script object (ComPython)
  that points to this file and Execute it. Or from a terminal, with
  PowerFactory closed: python stability.py
  A dialog asks what to run: the operating points, (1) the dispatch with its
  controller and period, (2) the stability study with its controllers, test
  cases and options. It starts from the settings in this file. With
  USE_DIALOG = False, or where no window can be shown, the settings decide
  (RUN_DISPATCH, RUN_STABILITY and the rest).
  For the stability study:
    MODE = "survey"       the report on the model and on what the library
                          offers; nothing is changed
    MODE = "controllers"  sets up the governors and voltage regulators and
                          tests them (a minute or two)
    MODE = "run"          all of that, then the scenarios
  Nothing has to be prepared in PowerFactory for the governors and voltage
  regulators: with CONTROLLERS = "auto" the script builds them, whatever the
  gensets have or lack at the start. If it stops, the log in OUT_DIR
  (stability_rms_log.txt) holds everything PowerFactory said.

WHAT THE SIMULATION RESTS ON
  Inertia: alternator and engine data sheets (the flywheel of DG_2 is
  assumed). With H of about 0.4 s and 0.2 s the sets are light against the
  "1 to 12 seconds" that [3] p.4 gives for machines; the rate of change of
  frequency and the swing of the rotor angle scale with 1/H, so check H in the
  model first.
  Governor and voltage regulator: the set that passes the self-test; the head
  of the results says which.
  - The script's own. Governor: PI speed control, a fuel actuator and an
    engine lag, fuel limits, droop for DG_2; its gains follow from the inertia
    in the model so that the speed loop crosses over at 8 rad/s (OWN_CROSSOVER).
    That is an assumption. It keeps DG_1 on its own inside class G2 of
    ISO 8528-5: a dip of about 4 % for the first load stage of the guide
    (35 %) and a rise of about 12 % when its full prime load is thrown off,
    back within three seconds. DG_2 has half the inertia for its size and
    moves twice as far when it runs alone. Voltage regulator: lead-lag,
    exciter lag and ceiling, the structure of SEXS.
  - DIgSILENT's library. Governor: Woodward DEGOV1 with typical time constants
    and a gain scaled to the inertia. As tuned it is a little stiffer than the
    script's own: about three quarters of the dip and of the rise. Its lead
    terms take damping away from the swing of the two sets against each other
    (roughly 10 Hz), which the machines' damper windings then have to make up
    for; the self-test with both sets shows whether they do. Voltage
    regulator: the simplified excitation system SEXS, tuned for the machines'
    field time constants.
  Both are stand-ins for the PowerCommand 3.3 control, whose own model Cummins
  does not publish, and neither is validated against the sets' step-load test
  records ([1] p.9 asks for validated models). What they leave out:
  - No turbocharger (boost) limit, so the engine takes a large load step
    faster than a real one: such results are marked "best case".
  - The regulator has no volts-per-hertz roll-off and its ceiling does not
    fall with the terminal voltage: voltage recovery during a deep frequency
    dip and after a close fault comes out too good.
  - DG_2 runs at a fixed set-point on its droop. The load sharing of the
    genset controls is not modelled: after a large loss of load DG_2 pushes
    DG_1 down to zero fuel and the frequency stays high.
  - PV and battery are static generators without a plant model. They keep
    feeding their set-points as current sources: no response to frequency, no
    reactive support, and back at once after a fault. [2] models its inverter
    in detail (pp.1293-1296) and finds a recovery time after the fault that
    this model cannot show; [1] pp.12-14 shows what a battery with virtual
    inertia does for exactly the rate of change of frequency found here.
  - The load has the model its type gives it (none: constant impedance in the
    RMS simulation, the most forgiving one; [1] p.9 uses a voltage exponent of
    1.5 for isolated microgrids).
  - Unbalanced faults (F1, F2) run in PowerFactory's unbalanced RMS
    simulation. They need zero-sequence data for SLG faults (cable, earthing of
    the genset neutrals) and the voltages read are positive-sequence: the dip of
    the faulted phase, which an inverter trips on, is deeper than the tables show.
  - RMS simulation is at fundamental frequency: no stator
    transients (the braking of a machine by the DC component of a close fault
    is missing), and a generator type without stator resistance takes no power
    from the set during a fault at all ([1] p.6: machines may even slow down).

SOURCES
  [1] M. Farrokhabadi et al. (IEEE PES task force), "Microgrid stability
      definitions, analysis, and examples", IEEE Trans. Power Systems, vol. 35,
      no. 1, pp. 13-29, 2020, doi 10.1109/TPWRS.2019.2925703. Page numbers are
      those of the accepted manuscript (1-17). Used for: the definition of
      stability (p.3), the classes of stability (Fig. 2, Table I), the large
      disturbances (p.4: faults, loss of a source, islanding), the blackout
      that tripping sources can end in (p.6), critical clearing time (p.7),
      the limits of RMS tools (p.8), and the size of two of its examples: a
      150 ms fault and a 20 % load step (p.11-12; there with a fault
      impedance, in a 60 kW system).
  [2] A. S. Pramanik and S. Sepasi, "Transient behavior analysis of microgrids
      in grid-connected and islanded modes: a comparative study of LVRT and
      HVRT capabilities", Clean Technologies, vol. 5, no. 4, pp. 1287-1303,
      2023, doi 10.3390/cleantechnol5040065. A PowerFactory comparison of
      RMS and EMT simulation of a PV-diesel microgrid. Used for: the event at
      1 s and the 1 ms step (p.1297), a busbar fault as the low-voltage
      ride-through test, and plots of active power, reactive power, voltage
      and current. Its fault lasts 0.2 s; its high-voltage test (a forced
      1.07 pu swell) is not repeated here: the swell comes from load loss.
  [3] University of Cape Town, EEE4126F course notes "Transient stability"
      (KAF). Used for: the disturbances (p.1: faults, loss of generation, loss
      of load and sudden load changes), a temporary fault with every
      breaker closed against a fault cleared by opening the line at both ends
      (p.1, p.13), loss of synchronism judged on the rotor angle, critical
      clearing time (p.14-15) and what it depends on: loading, fault
      location, clearing time, inertia, excitation (p.16).
  Inverter settings: NRS 097-2-1:2024 (edition 3), Table 3, and the South
  African grid connection code for renewable power plants, version 3.1.
  Genset limits: ISO 8528-5 and the Cummins data sheets.
"""

import calendar
import csv
import json
import math
import os
import pickle
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple

# ============================================================================
# USER SETTINGS
# ============================================================================

# --- PowerFactory -----------------------------------------------------------
PF_PYTHON_PATH = r"C:\Program Files\DIgSILENT\PowerFactory 2024 SP5A\Python\3.12"
PROJECT_NAME = "Iso_microgrid_DG_PV_BESS"
SHOW_GUI = True             # Show the PowerFactory window or run in terminal only

# --- What to run: chosen in a dialog when the script starts ---------------------
USE_DIALOG = True           # False: no dialog; the settings below decide
RUN_DISPATCH = False        # 1. quasi-dynamic dispatch: a controller steps through the time series
RUN_STABILITY = True        # 2. RMS stability study, MODE below

# --- Stability study --------------------------------------------------------------
MODE = "run"                # "survey": report on the model and on what the library offers; nothing
                            #           is changed and nothing is simulated
                            # "controllers": survey, set up the governors and voltage regulators,
                            #           test them (a minute or two), stop.
                            # "run":    all of the above then the scenarios
CASES_TO_RUN = ["G1", "G2", "G3", "R1", "R2", "P1", "P2", "F1", "F2", "F3"]       # None = every case; or a list of ids, e.g. ["G1", "F2", "F3"] 
CONTROLLERS = "keep"        # who gives the gensets their governor and voltage regulator:
                            # "auto":    the script builds them and tests them. First its own
                            #            (the equations in OWN_GOVERNOR and OWN_REGULATOR below); if
                            #            PowerFactory does not take those or they fail the test,
                            #            DIgSILENT's library models (DEGOV1 and SEXS). Models that
                            #            are in the way are put out of service, not deleted
                            # "own":     the script's own equations only
                            # "library": DIgSILENT's library models only
                            # "keep":    nothing is built or changed: what is in the model is used
                            #            (also the way to keep changes you make to them in PowerFactory)
SELF_TEST = False            # before any scenario: a small load step with DG_1 alone and with both
                            # sets. The run stops if the controllers do not hold the island there
UNDO_IF_FAILED = True       # if no set of controllers passes: take back everything the script did
                            # to the plant models. False: leave the last attempt in the model, to
                            # look at it in PowerFactory.
REQUIRE_CONTROLLERS = True  # with "keep": stop if DG_1 has no governor or no voltage regulator
SET_INERTIA = True          # True: write the data-sheet inertia into the generator types; False uses
                            # governor data

# --- Comparing dispatch controllers (COMPARING DISPATCH CONTROLLERS at the top) ----
INSTANTS_FILE = None        # None: the operating points POINTS and the controllers' set-points DISPATCH
                            # (below). Or the instants.csv written by controller_study.py select, e.g.
                            # r"C:\Users\Student\controller_study\instants.csv", which takes their place
COMPARE_CONTROLLERS = None  # None = every controller in DISPATCH (or the file); or a list of names, e.g. ["Rule-based"]
COMPARE_INSTANTS = None     # None = every operating point; or a list of ids, e.g. ["OP1", "OP3"]
COMPARE_CASE_PNGS = True    # the PNG plots of every single run (in OUT_DIR/plots/<point>), besides the
                            # summaries and the head-to-head figures; False: the summaries only

# --- Quasi-dynamic dispatch (QUASI-DYNAMIC DISPATCH further down) ----------------
QDS_CONTROLLERS = ["Rule-based"]    # the controllers to run (QDS_RUNNERS): "Rule-based", "MPC" or both
                            # (head to head); the MPC's own settings are under MPC below
QDS_PERIOD = "points"       # "points": one run per operating point, from QDS_WARMUP_DAYS before its
                            #           instant up to it. Its last row is the controller's state at the
                            #           point, which the stability study starts from
                            # "custom": one run from QDS_START to QDS_END
QDS_WARMUP_DAYS = 7         # days of dispatch before each operating point (the same for every controller)
QDS_START = (2002, 8, 28, 0, 0)     # (year, month, day, hour, minute), QDS_PERIOD = "custom"
QDS_END = (2002, 9, 29, 23, 55)     # (the period the forecasts of MPC_FORECAST_FILES cover)
QDS_STEP_MIN = 5            # minutes between two load flows
# Diesels
DG1_RATING_KW = None        # None = read from the generator type:
DG2_RATING_KW = None        #        S_rated x cos(phi)_rated x parallel units
DG1_MIN_LEVEL = 0.30        # DG_1 minimum loading (fraction of its rating). The battery discharges to
                            # keep DG_1 down at this level, and surplus PV that would push DG_1 below
                            # it charges the battery
DG2_ON_LEVEL = 0.65         # start DG_2 when the signal rises above 65 % of DG_1's rating
DG2_OFF_LEVEL = 0.50        # stop DG_2 when the signal falls below 50 %
DG2_P_KW = None             # DG_2 output while running; None = its rated kW
DG2_MIN_RUN_MIN = 60        # once started, stay on at least 60 min
DG2_MIN_STOP_MIN = 60       # once stopped, stay off at least 60 min
# Battery (a static generator, ElmGenstat: P > 0 = discharging, P < 0 = charging)
BESS_P_KW = None            # charge/discharge limit; None = rating from PowerFactory
BESS_E_KWH = 3200           # usable energy capacity at 100 % SOC, kWh  <-- REQUIRED
BESS_SOC_INIT = 0.50        # state of charge at the start of a dispatch run
BESS_SOC_MIN = 0.20         # never discharge below this
BESS_SOC_MAX = 0.95         # never charge above this
BESS_SOC_RESUME = 0.40      # after hitting SOC_MIN, no discharge until charged back to this
BESS_EFFICIENCY = 0.90      # round trip, split evenly between charging and discharging
CURTAIL_PV = True           # cut PV back when the battery can't absorb the surplus
                            # (otherwise DG_1 is pushed into reverse power)
OTHER_SOURCE_CLASSES = ["ElmPvsys", "ElmGenstat", "ElmSym"]     # everything else that produces or
                            # stores power; DG_1 and DG_2 are left out automatically
STOP_IF_LOAD_IS_FLAT = True # stop before the long run if the load never changes
QDS_PNG_PLOTS = True        # three PNG plots per run (<log>_power.png, _battery.png, _dg2_rule.png)
QDS_OPEN_PLOTS = False      # open those PNGs when saved (Windows)
PF_COMTRADE_NAME = "Dispatch results"   # COMTRADE object in PowerFactory (PF_PLOTS), + the run's name
PF_DATE_AXIS = True         # x-axis of those pages as date and time (False: seconds)

# --- Network (same names as the dispatch script) ------------------------------
DG1_NAME = "DG_1"           # reference machine
DG2_NAME = "DG_2"
PV_NAMES = None             # None = found automatically (ElmPvsys, or static generators named "PV...")
BESS_NAME = None            # None = the element whose name contains "BESS" or "batt"
ISOLATED = True             # put every External Grid out of service
LOAD_CLASSES = ["ElmLod", "ElmLodlv", "ElmLodmv"]
# Busbars. None = found from the elements: "gen" is the busbar DG_1 hangs on,
# "main" the one the battery hangs on, "load" the load's terminal.
BUS_NAMES = {"gen": None, "main": None, "load": None}
L1_NAME = "L_1"             # the cable from the genset busbar to the main board
                            # (None = the line found between those two busbars)
L1_FAULT_PERCENT = 50.0     # where along L_1 its fault sits, % of the length
TRIP_WITH = "auto"          # "auto": open the element's breaker(s), else take the element out of
                            # service.  "outage": always take the element out of service

# --- Operating points (POINTS and DISPATCH under DATA) ------------------------
USE_LOCAL_TIME = True                   # the times of POINTS are local time, as in the dispatch script

# --- Disturbances ---------------------------------------------------------------
T_EVENT = 1.0               # s, the disturbance ([2]); the second before it shows the steady state
FAULT_CLEARING_S = 0.080    # s, how long the faults of the F cases last: 80 ms, as recommended by Prof.
FAULT_R_OHM = 0.0           # ohm, fault impedance of every fault. 0 and 0 = bolted, as in [3];
FAULT_X_OHM = 0.0           # [1] uses 0.1 + j0.1 pu in its example

# --- Simulation -----------------------------------------------------------------
T_END = 10.0                # s, end of a source or load case
T_END_FAULT = 5.0           # s, end of a fault case
T_END_LONGEST = 60.0        # s: a case that has not come to rest at its end is run again for twice as
                            # long, and once more if need be, as long as that stays within this
STEP_S = 0.001              # s, integration step ([2]; the governor's fastest lag is 9 ms)
CCT_SEARCH = False          # critical clearing time of a three-phase fault at the main board, for
                            # every controller at every operating point (about eight runs each)
CCT_RANGE_S = (0.02, 0.1)   # s: the fault durations the search lies between
EMULATE_TRIPS = True        # run a case again with the protection trip its curves call for

# --- Units ----------------------------------------------------------------------
# Used only if PowerFactory reports no unit for a value (as in the dispatch script).
FALLBACK_RESULT_UNIT = "kW"     # m:P:bus1
FALLBACK_SETPOINT_UNIT = "kW"   # pgini
FALLBACK_RATING_UNIT = "kVA"    # sgn
GEN_SIGN = +1

# --- Output -----------------------------------------------------------------------
OUT_DIR = os.path.join(os.path.expanduser("~"), "stability_rms")
OUT_BASE = OUT_DIR          # with several forecasts (MPC_FORECAST_RUNS) OUT_DIR moves to OUT_BASE/<forecast>
DISPATCH_DIR = os.path.join(OUT_DIR, "dispatch")   # the dispatch logs: <controller>_<point>.csv
PNG_PLOTS = True            # one image per quantity and case: <case>_frequency.png, _power.png,
                            # _reactive.png, _voltage.png, and _angle.png / _current.png where they
                            # apply; plus summary_frequency.png, summary_voltage.png, summary_faults.png
PF_PLOTS = True             # plot pages inside PowerFactory ("Cmp <case> <point>", every controller's
                            # curves together): six plots in three columns, each scaled to its curves.
                            # Top: voltage and frequency at the main board, DG_1 and DG_2 active power;
                            # bottom: BESS and PV output, load active power. The case, the operating
                            # point and the controller are written into the page's title block
PF_PLOT_CASES = ["G1", "G2", "G3", "R2", "P2", "F1", "F2", "F3"]   # None = a page for every case
PF_PLOT_PNG = True          # also save each of those pages as <case>_powerfactory.png, with
                            # PowerFactory's own export of the page
RESTORE_MODEL_AFTER_RUN = True

# --- MPC (MODEL-PREDICTIVE DISPATCH further down; the dialog's MPC tabs set all of it) -----
MPC_ENGINE = "powerfactory" # how the MPC runs in the dispatch: "powerfactory" = through the same load
                            # flows as the rule-based controller (like for like); "model" = on its own
                            # model of the island, no load flows: no PowerFactory, ~10 s a simulated day
MPC_FORECAST = "perfect"    # load and PV over the horizon: "perfect" (the profiles), "yesterday"
                            # (the same time a day earlier) or a name in MPC_FORECAST_FILES; the present
                            # step is always measured
MPC_FORECAST_FILES = {      # name: (label, load CSV, PV CSV): a forecast of each 5-minute step, columns
    "best": ("Best CNN-LSTM", "best_load.csv", "best_pv.csv"),                  # timestamp and p_kw. A bare file
    "reference": ("Reference", "reference_load.csv", "reference_pv.csv"),       # name is looked for next to this file
}
MPC_FORECAST_RUNS = None    # forecasts to run one after the other: a list of names ("perfect", "yesterday", the names
                            # in MPC_FORECAST_FILES); None = just MPC_FORECAST. For an MPC dispatch over a period
                            # of your own each one goes in its own folder, OUT_DIR/<name> (the rule-based log
                            # is run once and copied to the others)
MPC_PROFILES = None         # (load CSV, PV CSV) of the actual load and PV, "perfect" and what the plant sees;
                            # None = aggregate_total_load.csv and pv_ac_profile_measured.csv next to this file
MPC_DIR = os.path.join(OUT_DIR, "mpc")  # system data, calibration
MPC_SYSTEM_JSON = None      # None = MPC_DIR\mpc_system_model.json (the tool below writes it), else
                            # next to this file, else ~\mpc_model
MPC_CALIBRATION_JSON = None # None = MPC_DIR\mpc_calibration.json, else next to this file
MPC_RMS_RESULTS = None      # the RMS results to calibrate from; None = OUT_DIR's, else the copy in
                            # stability_rms_rule_based_complete next to this file
MPC_PARAMS = {}             # MPC parameters other than MPC_PARAMETERS, the settings and the model give,
                            # {key: value}, e.g. {"m_star": 0.3, "s5_mode": "hard"}; the dialog fills it
MPC_WORKERS = "auto"        # the MPC's decisions run in this many separate Python processes at once,
                            # each with a share of the battery rules: "auto" = one per CPU (one left for
                            # PowerFactory), at most one per rule; 0 = in this script's own Python, which
                            # inside PowerFactory runs them 25-40x slower. The decisions are the same
MPC_WORKER_PYTHON = None    # python.exe for those processes; None = the Python this script runs on
# MPC tools, run before the dispatch (the comparison after it):
MPC_POLL = False            # read the PowerFactory model into MPC_DIR\mpc_system_model.json + a report
MPC_CALIBRATE = False       # fit rho, lambda, tau_eff and kappa from the profiles and the RMS results
MPC_REPORT = False          # the MPC's parameters, where each comes from, and the check of a step
MPC_COMPARE_LOGS = True     # every controller's dispatch log on one yardstick (fuel, cost, stability
                            # margins), point by point, head to head

# ============================================================================
# DATA
# ============================================================================
F_NOM = 50.0
W0_MECH = 2 * math.pi * F_NOM / 2       # rad/s at 1500 r/min

GENSET_DATA = {
    "dg1": dict(
        j_kgm2=16.455 + 11.6,       # alternator S6L1D-C4 + QSK23-G3 with flywheel
        prime_kw=600.0, standby_kw=660.0,
        # Woodward diesel governor DEGOV1. K is tuned to this inertia;
        # TMAX is the engine's fuel
        # stop (716 kWe), TMIN its braking at zero fuel, Droop 0 = isochronous.
        gov=dict(K=16.0, T1=0.01, T2=0.02, T3=0.2, T4=0.25, T5=0.009, T6=0.0384, TD=0.024,
                 TMAX=1.105, TMIN=-0.08, Droop=0.0, TE=0.05),
        # Simplified excitation system SEXS
        avr=dict(K=400.0, Ta=1.0, Tb=20.0, Te=0.05, Emin=0.0, Emax=6.0)),
    "dg2": dict(
        j_kgm2=2.3934 + 0.72 + 1.2,  # alternator UCDI274K + QSL9 rotating parts + flywheel (assumed)
        j_note="the flywheel's 1.2 kg m2 is assumed",
        prime_kw=200.0, standby_kw=220.0,
        gov=dict(K=8.0, T1=0.01, T2=0.02, T3=0.2, T4=0.25, T5=0.009, T6=0.0384, TD=0.024,
                 TMAX=1.39, TMIN=-0.08, Droop=0.04, TE=0.05),
        avr=dict(K=200.0, Ta=1.0, Tb=20.0, Te=0.05, Emin=0.0, Emax=6.0)),
}
# The governor gains above belong to the data-sheet inertia. The script scales them to the
# inertia it finds in the generator type (K in proportion to H), so that the speed loop keeps
# its margins whatever H the model has.

# Fuel (Cummins generator set data sheets, 50 Hz, diesel): litres per hour at 1/4, 1/2, 3/4 and
# full load of the prime and the standby rating. The MPC's fuel cost ([P] (14), [S] (23)) is the
# affine least-squares fit of the prime points, F = a*on + b*P, times the diesel price.
DIESEL_PRICE = 29.11        # R per litre (the price used in this study)
LOAD_STEPS = (0.25, 0.5, 0.75, 1.0)
FUEL_DATA = {
    "dg1": dict(model="C825 D5", prime_kw=600.0, prime_l_h=(49.2, 83.4, 115.4, 149.8),
                standby_kw=660.0, standby_l_h=(53.0, 89.9, 125.3, 165.3)),
    "dg2": dict(model="C275 D5", prime_kw=200.0, prime_l_h=(14.2, 25.8, 38.3, 52.5),
                standby_kw=220.0, standby_l_h=(16.7, 30.0, 45.8, 62.5)),
}

# DIgSILENT's library: names under which the script looks for the frame and the two models
# (first match wins; it also accepts a frame under any other name that has a slot for the
# machine, one for the voltage regulator and one for the governor). DIgSILENT renamed its
# library models with PowerFactory 2023 (gov_... and avr_... became gov_PSSE_... and
# exc_PSSE_...), so both spellings are tried, then anything with DEGOV1 or SEXS in its name.
FRAME_NAMES = ("SYM Frame_no droop", "SYM Frame*", "*Frame*no droop*", "*IEEE*frame*", "*Frame*", "*frame*")
LIBRARY_MODELS = {"gov": ("gov_PSSE_DEGOV1", "gov_DEGOV1", "pcu_DEGOV1", "*DEGOV1*"),
                  "avr": ("exc_PSSE_SEXS", "avr_SEXS", "vco_SEXS", "*SEXS*")}

# THE SCRIPT'S OWN GOVERNOR AND VOLTAGE REGULATOR (CONTROLLERS = "auto" or "own"). Each is
# a handful of equations in PowerFactory's simulation language (DSL). The script writes
# them into a block definition in the project library and PowerFactory simulates them like
# any other model. To change them, change them here: they are written afresh at every run
# (with CONTROLLERS = "keep" the script leaves them as they are in PowerFactory).
# {w}, {pt}, {u} and {ve} are replaced by the signal names of the frame: speed, turbine
# power, terminal voltage and excitation voltage (w, pt, u and uerrs in DIgSILENT's library).
#
# Governor: speed error -> PI controller -> fuel actuator (lag Ta) -> engine (lag Td, which
# stands for the combustion delay) -> engine torque. The machine wants a power (in pu of
# its rated active power), so the output is torque x speed, as in DEGOV1.
#   R            droop (0 = isochronous): the speed set-point falls by R pu per pu of fuel
#   Tmin, Tmax   fuel limits (torque). While the fuel command sits on one, the integrator
#                is led back with the time constant Tt, so that it does not wind up
#   Kp, Ki       follow from the inertia of the set, so that the speed loop crosses over at
#                OWN_CROSSOVER: Kp = crossover x 2 H / cos(phi), Ki = Kp / OWN_RESET
#                (phase margin 49 degrees, gain margin 16 dB with the two lags below)
# The crossover is an assumption: a higher value makes the governors stiffer. The frequency
# moves by about
#   step (pu of the rated active power) x cos(phi) / (2 H x crossover).
# 8 rad/s keeps DG_1 on its own inside class G2 of ISO 8528-5: a dip of about 2.5 % for a
# load step of 20 % of its rating and 4 % for the first load stage of the guide (35 %), and
# a rise of about 12 % (the limit of the class) when its full prime load is thrown off; back
# within three seconds. With half of that, both sets pass their over-speed shutdown when
# the island loses all its load. DG_2 has half the inertia for its size and moves twice as
# far when it runs alone. A set on droop returns to its set-point with the time constant
# (1 + R x Kp) / (R x Ki) after a transient: about 7 s for DG_2.
OWN_CROSSOVER = 8.0             # rad/s
OWN_RESET = 1.0                 # s: integral time of the speed controller
OWN_TA, OWN_TD = 0.05, 0.03     # s: fuel actuator, engine
OWN_TT = 0.25                   # s: the integrator's way back from a fuel limit
OWN_GOVERNOR = dict(
    name="gov_own_PI",
    title="Diesel governor: PI speed control, actuator and engine lag (stability_rms.py)",
    inputs="{w},{wset}", outputs="{pt}", states="xi,xa,xe", internal="dw,yu,yc",
    params=("Kp", "Ki", "Tt", "Ta", "Td", "R", "Tmin", "Tmax"),
    equations=(
        "! Diesel governor, written by stability_rms.py (change it there)",
        "! {wset}: speed set-point, stays where the initial conditions put it",
        "! xi: integrator, xa: fuel actuator, xe: engine torque",
        "inc(xe) = {pt}/{w}",
        "inc(xa) = {pt}/{w}",
        "inc(xi) = {pt}/{w}",
        "inc({wset}) = {w} + R*{pt}/{w}",
        "dw = {wset} - {w} - R*xa",
        "yu = Kp*dw + xi",
        "yc = lim(yu, Tmin, Tmax)",
        "xi. = Ki*dw + (yc - yu)/Tt",
        "xa. = (yc - xa)/Ta",
        "xe. = (xa - xe)/Td",
        "{pt} = xe*{w}",
    ))
# Voltage regulator: voltage error -> lead-lag (1 + s Ta)/(1 + s Tb), which lowers the gain
# for fast changes -> exciter (gain K, lag Te) -> excitation voltage between Emin and Emax.
# The same structure as SEXS, with the values of GENSET_DATA.
OWN_REGULATOR = dict(
    name="avr_own_simple",
    title="Voltage regulator: lead-lag, exciter lag and ceiling (stability_rms.py)",
    inputs="{u},{uset}", outputs="{ve}", states="xr,xe", internal="du,yr",
    params=("K", "Ta", "Tb", "Te", "Emin", "Emax"),
    equations=(
        "! Voltage regulator, written by stability_rms.py (change it there)",
        "! {uset}: voltage set-point, stays where the initial conditions put it",
        "inc(xe) = {ve}",
        "inc(xr) = {ve}/K",
        "inc({uset}) = {u} + {ve}/K",
        "du = {uset} - {u}",
        "xr. = (du - xr)/Tb",
        "yr = xr + Ta/Tb*(du - xr)",
        "xe. = (lim(K*yr, Emin, Emax) - xe)/Te",
        "{ve} = xe",
    ))
# A model talks to the machine through the signals of its slot in the frame, and PowerFactory
# matches them by name (upper and lower case count). The script reads the names from the
# frame. These are the names DIgSILENT's library uses, tried in this order where the frame
# does not say which of a slot's signals is which.
USUAL_SIGNALS = {"w": ("w", "xspeed", "speed", "omega", "n"), "pt": ("pt", "pturb", "pmech", "pm"),
                 "u": ("u", "ut", "u1", "uterm", "vt"), "ve": ("uerrs", "ve", "efd", "vf", "uf")}

# Self-test of the controllers, before any scenario
SELF_TEST_STEP_PCT = 10.0       # % of the load, switched on at T_EVENT
SELF_TEST_END = 10.0            # s
RING_TURNS = 6                  # more reversals than this after the step: poorly damped
RETUNE = (0.5, 0.25)            # gains tried, as multiples of the script's value, if the test shows an
                                # oscillation or a poorly damped swing
U_HELD = 0.03                   # pu: the regulators have to bring the genset busbar back to within
                                # this of where it was before the step

# ASSUMED INVERTER PROTECTION: NRS 097-2-1:2024 Table 3 (maximum trip times at a utility
# interface). In an island the settings commissioned on the plant govern: change them here.
F_UNDER, T_UNDER = 47.0, 0.2                  # Hz, s
F_OVER, T_OVER = 51.5, 4.0
U_UNDER_STEPS = ((0.50, 0.2), (0.85, 10.0))   # (below this many pu, for this long)
U_OVER_STEPS = ((1.20, 0.16), (1.15, 2.0), (1.10, 40.0))    # (above this many pu, for this long)
U_UNDER, T_U_UNDER = U_UNDER_STEPS[0]         # the fast under-voltage step, drawn in the plots
F_REDUCE = 50.5                               # Hz: above this the grid code asks for less power
ROCOF_LIMIT = 1.5                             # Hz/s: a plant has to stay connected up to this (grid
                                              # code); above it the island-detection relay of a generator
                                              # that is not an inverter trips within 2 s (NRS 097-2-1,
                                              # Table 3 and 5.3.5). Reported only.
# Gensets (PowerCommand 3.3 defaults and the Cummins data sheets)
REVERSE_FRACTION, REVERSE_S = 0.10, 3.0       # reverse power: 10 % of the standby rating for 3 s
OVERSPEED_HZ = 58.3                           # over-speed shutdown, 1800 +/- 50 r/min (its low end)
OVERSPEED_TRIP_S = 0.1                        # s from passing that speed to the open breaker (assumed)
BLACKOUT_HZ = 40.0                            # the script's own floor: below 80 % speed a run counts
                                              # as a collapse
# ISO 8528-5 guide for the sudden load increase of a four-stroke engine of about 24 bar mean
# effective pressure (QSK23-G3 and QSL9-G5): load stages, % of the prime rating. A step larger
# than the stage the engine is in is more than the guide gives.
GUIDE_STAGES = (0.0, 35.0, 63.0, 85.0, 100.0)
# Limits for the outcome (settings of this script)
F_BAND = 0.025                                # final frequency within 2.5 % of 50 Hz: the ISO 8528
                                              # band that [1] draws in its Fig. 17
U_LOW, U_HIGH = 0.90, 1.10                    # pu, final voltage: the usual limits of a low-voltage supply
OSC_F_HZ, OSC_U_PU = 0.25, 0.02               # still swinging at the end by more than this (peak to
                                              # peak): not a steady state. For comparison: ISO 8528-5
                                              # gives a class G3 set steady-state bands of 0.5 % and 1 %
REST_F_HZ, REST_U_PU, REST_S = 0.1, 0.01, 3.0 # at rest: within this of the final values for the last 3 s
FREEZE_POINT = True                           # after the load flow of an operating point: its values become
                                              # fixed set-points and the time characteristics of the loads,
                                              # PV, battery and DG_2 are switched off for the simulation
                                              # (switched back on for the next point, and at the end)
F_SETTLE_HZ = 0.1                             # Hz: settled back at nominal = within this of 50 Hz and staying
                                              # there (the settling time of the head-to-head comparison)
# ISO 8528-5 limit values: (class, dip %, rise %, recovery s, tolerance band %). The band is
# the width about the steady-state value (ISO 8528-5:2022, 3.1.15), so half of it either side.
ISO_FREQ = (("G3", 7.0, 10.0, 3.0, 2.0), ("G2", 10.0, 12.0, 5.0, 2.0), ("G1", 15.0, 18.0, 10.0, 3.5))
ISO_VOLT = (("G3", 15.0, 20.0, 4.0, 2.0), ("G2", 20.0, 25.0, 6.0, 5.0), ("G1", 25.0, 35.0, 10.0, 10.0))

# Operating points: instants of the time series. The study time is moved to the instant, so the
# load and PV profiles of the model give load and PV there; load and pv_avail are what the time
# series has (kW) and the load flow is checked against them.
POINTS = {
    "OP1": dict(text="Peak demand", time="2002-07-14 11:20", load=792.01, pv_avail=190.21),
    "OP2": dict(text="Largest excess PV", time="2002-02-25 12:05", load=114.5, pv_avail=599.29),
    "OP3": dict(text="Worst PV week, evening (DG_2 on)", time="2002-07-30 20:30", load=436.32, pv_avail=0.0),
}
# What each dispatch controller did at each point, from its log after running it up to that
# instant. kW, battery > 0 discharging, < 0 charging; soc in %; dg2 = 0 when DG_2 is off;
# pv = what the PV plant feeds after curtailment (None = all of pv_avail). DG_1 takes whatever
# balances: dg1 is only compared with the load flow, soc only reported. A point whose bess or
# dg2 is still None is not run.
DISPATCH = {
    "Rule-based": {
        "OP1": dict(bess=None, soc=None, dg1=None, dg2=None, pv=None),
        "OP2": dict(bess=None, soc=None, dg1=None, dg2=None, pv=None),
        "OP3": dict(bess=None, soc=None, dg1=None, dg2=None, pv=None),
    },
    "MPC": {
        "OP1": dict(bess=None, soc=None, dg1=None, dg2=None, pv=None),
        "OP2": dict(bess=None, soc=None, dg1=None, dg2=None, pv=None),
        "OP3": dict(bess=None, soc=None, dg1=None, dg2=None, pv=None),
    },
}
OPS = {}                    # filled at the start of a run: one entry per controller and point
# Events: ("trip", "bess" | "pv" | "dg2" | "dg1" | "load" | "L1" | "L2" [, delay after T_EVENT])
#                                          L1: the cable to the main board;  L2: the load feeder
#         ("load_step_pct", percent)       P and Q of the load change by this much at once
#         ("fault", "gen" | "L1" | "main" | "load", duration [, "3ph" | "ll" | "slg"])
#                                          fault at a busbar, or on the cable L_1 at L1_FAULT_PERCENT:
#                                          three-phase (the default), line-to-line or single-line-to-
#                                          ground. "ll" and "slg" run in the unbalanced RMS simulation
# A fault without a trip is a temporary fault: it is gone after its duration with every
# breaker still closed, the idealised case of [3] p.13. All faults below are temporary.
GROUPS = {"G": "Loss of a source", "R": "Loss of load", "P": "Sudden load pickup", "F": "Faults"}
# Fault types: PowerFactory's short-circuit code (EvtShc i_shc) and the text
FAULT_TYPES = {"3ph": (0, "three-phase"), "ll": (1, "line-to-line"), "slg": (2, "single-line-to-ground")}
# Applied to every controller at every operating point. Case ids are <case>_<point>_<controller>,
# e.g. G1_OP1_Rule-based. A disturbance that means nothing at a point (losing an idle battery,
# or a DG_2 that is not running) is listed as not applicable.
CASES = [
    # id, text, events
    ("G1", "Loss of the battery", [("trip", "bess")]),
    ("G2", "Loss of DG_2", [("trip", "dg2")]),
    ("G3", "Loss of the PV plant", [("trip", "pv")]),
    ("R1", "25 % of the load is lost", [("load_step_pct", -25.0)]),
    ("R2", "50 % of the load is lost", [("load_step_pct", -50.0)]),
    ("P1", "Load pickup of 10 %", [("load_step_pct", 10.0)]),
    ("P2", "Load pickup of 20 %", [("load_step_pct", 20.0)]),
    ("F1", "Temporary single-line-to-ground fault at the main board", [("fault", "main", FAULT_CLEARING_S, "slg")]),
    ("F2", "Temporary line-to-line fault at the main board", [("fault", "main", FAULT_CLEARING_S, "ll")]),
    ("F3", "Temporary bolted three-phase fault at the main board", [("fault", "main", FAULT_CLEARING_S, "3ph")]),
]
SUITE_LEAST_KW = 1.0        # kW: a source giving less than this at a point (DG_2 off, PV at night, an
                            # idle battery): tripping it is not simulated, the reason is reported instead
PLACES = {"gen": "the genset busbar", "L1": "the cable L_1", "main": "the main board", "load": "the load terminal"}
ROLE_TEXT = {"dg1": DG1_NAME, "dg2": DG2_NAME, "bess": "the battery", "pv": "the PV plant", "load": "the load",
             "L1": "the cable L_1", "L2": "the load feeder"}
BUS_KEYS = ("gen", "main", "load")
BUS_TEXT = {"gen": "Genset busbar", "main": "Main board", "load": "Load terminal"}

EVENT_PREFIX = "stab_"      # the script's own simulation events; all others are left alone
MAX_POINTS = 6000           # points kept per curve, about
FINE_S = 3.0                # s after the event for which every time step is kept; later ones are thinned


# ============================================================================
# CONNECTION AND LOGGING (as in the dispatch script)
# ============================================================================
pf = None           # the powerfactory module
app = None          # the PowerFactory application
INSIDE_PF = False


class Stop(Exception):
    """Ends the run early with a readable message (no traceback)."""


class CaseProblem(Exception):
    """One case cannot be set up or run; the other cases go on."""


class Skip(CaseProblem):
    """One case has nothing to do at its operating point."""


_log_lines = []


def log(msg=""):
    _log_lines.append(str(msg))
    if not INSIDE_PF:
        print(msg, flush=True)
    if app is not None:
        app.PrintPlain(str(msg))


def warn(msg):
    _log_lines.append("!! " + str(msg))
    if not INSIDE_PF:
        print("!! " + msg, flush=True)
    if app is not None:
        try:
            app.PrintWarn(str(msg))
        except Exception:
            app.PrintPlain("!! " + str(msg))


def import_powerfactory():
    try:
        import powerfactory  # type: ignore
        return powerfactory, True
    except ImportError:
        pass
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    if not os.path.isdir(PF_PYTHON_PATH):
        sys.exit(f"Folder not found: {PF_PYTHON_PATH}\nSet PF_PYTHON_PATH to ...\\<install>\\Python\\<version>.")
    if os.path.basename(PF_PYTHON_PATH.rstrip("\\/")) != py_ver:
        sys.exit(f"PF_PYTHON_PATH is for another Python version than the one running ({py_ver}).")
    sys.path.append(PF_PYTHON_PATH)
    try:
        import powerfactory  # type: ignore
        return powerfactory, False
    except ImportError as e:
        sys.exit(f"Found the folder but the import failed: {e}")


def connect():
    global pf, app, INSIDE_PF
    pf, native = import_powerfactory()
    if native:
        try:
            app = pf.GetApplication()
        except Exception:
            app = None
    if app is None:
        try:
            app = pf.GetApplicationExt()
        except pf.ExitError as err:
            if err.code == 4002:
                sys.exit("PowerFactory is already open (error 4002). Run this script from inside "
                         "PowerFactory, or close PowerFactory first.")
            sys.exit(f"Could not start PowerFactory (error code {err.code}).")
    try:
        INSIDE_PF = app.GetCurrentScript() is not None
    except Exception:
        INSIDE_PF = native and not (sys.stdin is not None and sys.stdin.isatty())


# ============================================================================
# GENERIC OBJECT HELPERS
# ============================================================================
def attr(obj, name, default=None):
    """obj.GetAttribute(name), or `default` if it does not exist / is None."""
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


def same(a, b):
    return a is not None and b is not None and full_name(a) == full_name(b)


def calc_objects(classes, include_out_of_service=False):
    found = []
    for cls in classes:
        for obj in app.GetCalcRelevantObjects(f"*.{cls}"):
            if include_out_of_service or attr(obj, "outserv", 0) == 0:
                found.append(obj)
    return found


def find_generator(name):
    for pattern in (f"{name}.ElmSym", f"{name}.ElmGenstat", f"{name}.*"):
        for obj in app.GetCalcRelevantObjects(pattern):
            return obj
    return None


_PREFIX_TO_KILO = {"": 1e-3, "k": 1.0, "M": 1e3, "G": 1e6}
_unit_cache = {}


def kilo_factor(unit):
    """Factor that converts a value in `unit` to kW / kVA / kvar. None if unknown."""
    if not unit:
        return None
    u = str(unit).strip().strip("[]").strip()
    for base in ("var", "VA", "W"):
        if u.endswith(base):
            return _PREFIX_TO_KILO.get(u[: -len(base)])
    return None


def to_kilo_factor(obj, name, fallback_unit):
    """Factor for obj.<name> -> kilo-units, asked from PowerFactory once per class and attribute."""
    key = (obj.GetClassName(), name)
    if key in _unit_cache:
        return _unit_cache[key]
    unit = None
    for ask in (lambda: obj.GetAttributeUnit(name), lambda: app.GetAttributeUnit(key[0], name)):
        try:
            unit = ask()
        except Exception:
            unit = None
        if kilo_factor(unit) is not None:
            break
    if kilo_factor(unit) is None:
        unit = fallback_unit
    _unit_cache[key] = kilo_factor(unit)
    return _unit_cache[key]


def get_kilo(obj, name, fallback_unit):
    return attr(obj, name, 0.0) * to_kilo_factor(obj, name, fallback_unit)


def p_kw(obj, generator=False):
    """Active power at the element's terminal after the last load flow, in kW."""
    value = get_kilo(obj, "m:P:bus1", FALLBACK_RESULT_UNIT)
    return GEN_SIGN * value if generator else value


def rating_kva_cos(gen):
    """(rated kVA of all parallel units, rated power factor)."""
    holder = attr(gen, "typ_id") if gen.GetClassName() == "ElmSym" else gen
    if holder is None:
        return 0.0, 1.0
    units = attr(gen, "ngnum", 1) or 1
    return get_kilo(holder, "sgn", FALLBACK_RATING_UNIT) * units, attr(holder, "cosn", 1.0) or 1.0


class ModelChanges:
    """Sets attributes and remembers the ORIGINAL value of each one, so the model
    can be put back exactly as it was, however often it changed."""

    def __init__(self):
        self._original = {}

    def set(self, obj, name, value):
        key = (full_name(obj), name)
        if key not in self._original:
            self._original[key] = (obj, name, attr(obj, name))
        obj.SetAttribute(name, value)

    def restore(self):
        failed = []
        for obj, name, value in self._original.values():
            if value is None:
                continue
            try:
                obj.SetAttribute(name, value)
            except BaseException as e:              # also an interruption: go on with the rest
                failed.append(f"{obj.loc_name}.{name} = {value!r} ({type(e).__name__})")
        if failed:
            warn("NOT RESTORED - set these back by hand: " + "; ".join(failed))
        n = len(self._original) - len(failed)
        self._original.clear()
        return n if not failed else -n


def quiet_set(changes, obj, name, value):
    """changes.set() without PowerFactory's 'object was changed' info lines."""
    app.EchoOff()
    try:
        changes.set(obj, name, value)
    finally:
        app.EchoOn()


def to_epoch(y, mo, d, h, mi):
    tt = (y, mo, d, h, mi, 0, 0, 0, -1)
    return int(time.mktime(tt)) if USE_LOCAL_TIME else calendar.timegm(tt)


def window_lines():
    """The lines of PowerFactory's output window (the script's own lines are in it too)."""
    try:
        return [str(line).strip() for line in app.GetOutputWindow().GetContent()]
    except Exception:
        return []


def pf_messages(keys, since=0, max_lines=12):
    """Lines that PowerFactory itself wrote to the output window after line `since`
    and that contain one of `keys` (every line if `keys` is None)."""
    own = {line.strip() for text in _log_lines for line in text.replace("!! ", "", 1).split("\n")}
    keep = [line for line in window_lines()[since:]
            if line and line not in own and (keys is None or any(k in line.lower() for k in keys))]
    return keep[-max_lines:]


# ============================================================================
# NETWORK: elements, busbars, breakers
# ============================================================================
def terminal_of(obj, side="bus1"):
    return attr(attr(obj, side), "cterm")


def cubicles_of(term):
    try:
        return list(term.GetContents("*.StaCubic") or [])
    except Exception:
        return []


def switch_neighbours(term):
    """(coupler, terminal on its other side) for every coupler (ElmCoup) at `term`."""
    out = []
    for cub in cubicles_of(term):
        elm = attr(cub, "obj_id")
        if elm is None or elm.GetClassName() != "ElmCoup":
            continue
        for side in ("bus1", "bus2"):
            other = terminal_of(elm, side)
            if other is not None and not same(other, term):
                out.append((elm, other))
    return out


def busbar_of(obj, side="bus1"):
    """The busbar an element (or one end of a line) hangs on: its own terminal, or the
    busbar a few couplers away if it sits on the inner node of a substation bay. Closed
    couplers are followed first; open ones only if that leads to no busbar (the element's
    own breaker may be open)."""
    start = terminal_of(obj, side)
    if start is None:
        return None
    for through_open in (False, True):
        seen, frontier = {full_name(start)}, [start]
        for _ in range(4):
            for term in frontier:
                if attr(term, "iUsage", 0) == 0:          # 0 = busbar, 1 = junction, 2 = internal node
                    return term
            nxt = []
            for term in frontier:
                for coupler, other in switch_neighbours(term):
                    if attr(coupler, "on_off", 1) == 0 and not through_open:
                        continue
                    if full_name(other) not in seen:
                        seen.add(full_name(other))
                        nxt.append(other)
            frontier = nxt
            if not frontier:
                break
    return start


def breakers_of(obj):
    """Switches whose opening disconnects `obj` at every one of its ends: the switch
    in each of its cubicles, or the coupler(s) of the substation bay that end sits
    in. Empty if an end has neither (the element is then taken out of service)."""
    found = []
    for side in ("bus1", "bus2"):
        cub = attr(obj, side)
        if cub is None:
            continue
        try:
            own = list(cub.GetContents("*.StaSwitch") or [])
        except Exception:
            own = []
        if not own:
            term = attr(cub, "cterm")
            if term is None or attr(term, "iUsage", 0) == 0:
                return []
            others = [attr(c, "obj_id") for c in cubicles_of(term)]
            others = [o for o in others if o is not None and o.GetClassName() != "ElmCoup" and not same(o, obj)]
            if others:                          # the bay node is shared: its couplers would trip more than obj
                return []
            own = [coupler for coupler, _ in switch_neighbours(term)]
            if not own:
                return []
        found += own
    return found


def find_cable(net):
    """The cable between the genset busbar and the main board (L_1), or None."""
    gen, main = net["bus"]["gen"], net["bus"]["main"]
    if gen is None or main is None or same(gen, main):
        return None
    wanted = {full_name(gen), full_name(main)}

    def ends(line):
        return {full_name(b) for b in (busbar_of(line, "bus1"), busbar_of(line, "bus2")) if b is not None}

    if L1_NAME:
        found = app.GetCalcRelevantObjects(f"{L1_NAME}.ElmLne")
        if found:
            if attr(found[0], "outserv", 0):
                warn(f"'{L1_NAME}' is out of service: the cases on that cable are skipped")
                return None
            if ends(found[0]) != wanted:
                warn(f"'{L1_NAME}' does not run from {gen.loc_name} to {main.loc_name} as far as the script can "
                     "see; it is used as the cable for the L_1 cases all the same (check L1_NAME and BUS_NAMES)")
            return found[0]
        warn(f"No line called '{L1_NAME}' (L1_NAME): looking for the line between {gen.loc_name} and {main.loc_name}")
    between = [line for line in calc_objects(["ElmLne"]) if ends(line) == wanted]
    return between[0] if between else None


def find_feeder(net):
    """The line from the main board to the load terminal (L_2), or None."""
    main, load = net["bus"]["main"], net["bus"]["load"]
    if main is None or load is None or same(main, load):
        return None
    wanted = {full_name(main), full_name(load)}
    for line in calc_objects(["ElmLne"]):
        ends = {full_name(b) for b in (busbar_of(line, "bus1"), busbar_of(line, "bus2")) if b is not None}
        if ends == wanted and not same(line, net.get("l1")):
            return line
    return None


def open_project():
    active = app.GetActiveProject()
    if active is None or active.loc_name != PROJECT_NAME:
        if app.ActivateProject(PROJECT_NAME) != 0:
            raise Stop(f"Could not activate project '{PROJECT_NAME}'")
    log(f"Active project:    {app.GetActiveProject().loc_name}")
    case = app.GetActiveStudyCase()
    if case is None:
        raise Stop("No active study case - activate one in the project first")
    log(f"Active study case: {case.loc_name}")
    return case


def find_network(changes):
    """Isolate the grid, make DG_1 the reference machine, find loads, PV, battery and busbars."""
    dg1, dg2 = find_generator(DG1_NAME), find_generator(DG2_NAME)
    if dg1 is None or dg2 is None:
        raise Stop(f"Generator '{DG1_NAME if dg1 is None else DG2_NAME}' not found")
    if ISOLATED:
        for xn in calc_objects(["ElmXnet"]):
            changes.set(xn, "outserv", 1)
            log(f"External grid '{xn.loc_name}' set out of service")
    changes.set(dg1, "outserv", 0)
    changes.set(dg1, "ip_ctrl", 1)
    changes.set(dg2, "ip_ctrl", 0)
    loads = calc_objects(LOAD_CLASSES)
    if not loads:
        raise Stop("No in-service loads found")
    skip = {full_name(dg1), full_name(dg2)}

    if BESS_NAME:
        found = app.GetCalcRelevantObjects(f"{BESS_NAME}.*")
        if not found:
            raise Stop(f"'{BESS_NAME}' not found - check BESS_NAME")
        bess = found[0]
    else:
        hits = [o for o in app.GetCalcRelevantObjects("*.*")
                if o.GetClassName().startswith("Elm") and attr(o, "pgini") is not None
                and full_name(o) not in skip and any(k in o.loc_name.lower() for k in ("bess", "batt"))]
        if not hits:
            raise Stop("No battery found by name - set BESS_NAME")
        bess = hits[0]
    if PV_NAMES:
        pvs = []
        for name in PV_NAMES:
            found = app.GetCalcRelevantObjects(f"{name}.*")
            if not found:
                raise Stop(f"'{name}' not found - check PV_NAMES")
            pvs.append(found[0])
    else:
        pvs = calc_objects(["ElmPvsys"], include_out_of_service=True)
        if not pvs:
            pvs = [o for o in calc_objects(["ElmGenstat"], include_out_of_service=True)
                   if "pv" in o.loc_name.lower()]
    pvs = [p for p in pvs if full_name(p) not in skip | {full_name(bess)}]
    for obj in pvs + [bess]:
        if attr(obj, "ip_ctrl", 0) == 1:
            changes.set(obj, "ip_ctrl", 0)
    changes.set(bess, "outserv", 0)

    net = {"dg1": dg1, "dg2": dg2, "loads": loads, "pv": pvs, "bess": bess, "bus": {}}
    for role in ("dg1", "dg2"):
        kva, cosn = rating_kva_cos(net[role])
        if kva <= 0:
            raise Stop(f"Could not read the rating of {net[role].loc_name} - check its type")
        net[role + "_kva"], net[role + "_base_kw"] = kva, kva * cosn
    kva, cosn = rating_kva_cos(bess)
    net["bess_kw"] = kva * cosn
    net["scale"] = {full_name(o): (o, float(attr(o, "scale0", 1.0))) for o in loads + pvs
                    if attr(o, "scale0") is not None}
    missing = [o.loc_name for o in loads + pvs if attr(o, "scale0") is None]
    if missing:
        raise Stop("These elements have no scaling factor (scale0), so the operating points "
                   "cannot be set: " + ", ".join(missing))

    guesses = {"gen": busbar_of(dg1), "main": busbar_of(bess), "load": busbar_of(loads[0])}
    for key, guess in guesses.items():
        name = BUS_NAMES.get(key)
        if name:
            found = app.GetCalcRelevantObjects(f"{name}.ElmTerm")
            if not found:
                raise Stop(f"Busbar '{name}' not found - check BUS_NAMES")
            net["bus"][key] = found[0]
        else:
            net["bus"][key] = guess
    log(f"\nReference machine: {dg1.loc_name} ({net['dg1_kva']:.0f} kVA)   second set: {dg2.loc_name} "
        f"({net['dg2_kva']:.0f} kVA)")
    log(f"Battery: {bess.loc_name} ({net['bess_kw']:.0f} kW)   PV: "
        + (", ".join(p.loc_name for p in pvs) or "none found")
        + "   Loads: " + ", ".join(l.loc_name for l in loads))
    for key, text in (("gen", "genset busbar"), ("main", "main board"), ("load", "load terminal")):
        term = net["bus"][key]
        log(f"   {text + ':':16s}{term.loc_name if term is not None else 'not found'}"
            + (f"  ({attr(term, 'uknom', 0):g} kV)" if term is not None else ""))
    if same(net["bus"]["gen"], net["bus"]["main"]):
        log("   (genset busbar and main board are the same terminal: set BUS_NAMES if that is wrong)")
    net["l1"] = find_cable(net)
    if net["l1"] is not None:
        # what the cable carries is recorded at its first end: counted positive towards the main board
        net["l1_sign"] = -1.0 if same(busbar_of(net["l1"], "bus1"), net["bus"]["main"]) else 1.0
        length = attr(net["l1"], "dline")
        try:
            unit = str(net["l1"].GetAttributeUnit("dline") or "").strip()
        except Exception:
            unit = ""
        log(f"   {'cable L_1:':16s}{net['l1'].loc_name}"
            + (f"  (length {length:g} {unit})".replace(" )", ")") if isinstance(length, (int, float)) else ""))
    else:
        log(f"   {'cable L_1:':16s}not found - the cases on that cable are skipped (set L1_NAME)")
    net["l2"] = find_feeder(net)
    log(f"   {'load feeder:':16s}" + (net["l2"].loc_name if net["l2"] is not None else
                                      "no line found between the main board and the load terminal"))
    return net


# ============================================================================
# PLANT MODELS: what a genset has for a governor and a voltage regulator
# ============================================================================
# A machine gets its controllers through a composite model ("plant model"): a frame
# with slots, one of them for the machine, and a DSL model in the slot of the governor
# and in the slot of the voltage regulator.
SLOT_WORDS = {"sym": (("sym",), ("elmsym",)), "avr": (("avr", "vco", "exc"), ("elmavr", "elmvco")),
              "gov": (("gov", "pcu"), ("elmgov", "elmpcu"))}
SLOT_TEXT = {"sym": "the machine", "avr": "the voltage regulator", "gov": "the governor"}


class BuildProblem(Exception):
    """The controllers cannot be set up this way (another way may still work)."""


# What this run has done to the plant models: objects it created, attributes it changed
# on objects that were there ({(object, name): (object, name, what it held at first)}),
# gains the self-test changed ({(role, "gov" | "avr"): factor}), the route in use
# ("library", "own" or None), whether that set-up has passed the self-test, and what
# PowerFactory's own check said about the script's models.
_plant = {"created": [], "undo": {}, "gain": {}, "route": None, "proven": False, "check": []}


def inertia_constant(j_kgm2, s_kva):
    return 0.5 * j_kgm2 * W0_MECH ** 2 / 1e3 / s_kva


def name_list(raw):
    """The names in an attribute that PowerFactory hands out as one text or as a list
    of texts, with commas or semicolons between the names."""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    return [n for n in re.split(r"[,;\s]+", " ".join(str(x) for x in raw)) if n]


def slot_roles(slots):
    """{"sym": index, "avr": index, "gov": index} of the slots that take the machine,
    the voltage regulator and the governor: by the slot's name, else by its class filter,
    else by the signals the slot lists (the machine takes turbine power and excitation
    voltage; the governor gives the one, the regulator the other)."""
    found = {}

    def free(i):
        return slots[i] is not None and i not in found.values()

    for by_name in (True, False):
        for key, (in_name, in_class) in SLOT_WORDS.items():
            for word in (in_name if by_name else in_class):         # the first word counts most
                if key in found:
                    break
                for i, slot in enumerate(slots):
                    if not free(i):
                        continue
                    text = (slot.loc_name if by_name else str(attr(slot, "filtmod", ""))).lower()
                    if word in text and not (word == "exc" and "lim" in text):     # not an excitation limiter
                        found[key] = i
                        break
    if len(found) < len(SLOT_WORDS):
        ins, outs = ({i: [n.lower() for n in name_list(attr(slot, side))] for i, slot in enumerate(slots)
                      if slot is not None} for side in ("sInput", "sOutput"))
        fits = {"sym": lambda i: "pt" in ins[i] and "ve" in ins[i],
                "gov": lambda i: any(n in outs[i] for n in USUAL_SIGNALS["pt"]),
                "avr": lambda i: any(n in outs[i] for n in USUAL_SIGNALS["ve"])}
        for key in SLOT_WORDS:
            hits = [i for i in ins if free(i) and fits[key](i)] if key not in found else []
            if len(hits) == 1:
                found[key] = hits[0]
    return found


def frame_slots(frame):
    """The slots a frame defines."""
    try:
        return list(frame.GetContents("*.BlkSlot") or [])
    except Exception:
        return []


def is_machine_frame(frame):
    """A frame with a slot for the machine, one for the regulator and one for the governor."""
    if frame is None:
        return False
    roles = slot_roles(frame_slots(frame))
    return all(key in roles for key in SLOT_WORDS)


def grid_of(obj):
    """The grid (ElmNet) an element is stored in; failing that, the folder it is in."""
    try:
        first = folder = obj.GetParent()
    except Exception:
        return attr(obj, "cpGrid")
    for _ in range(8):
        if folder is None:
            break
        if folder.GetClassName() == "ElmNet":
            return folder
        try:
            folder = folder.GetParent()
        except Exception:
            break
    return attr(obj, "cpGrid") or first


def composite_models(net):
    """Every composite model of the network, also those out of service."""
    found = []
    try:
        found = list(app.GetCalcRelevantObjects("*.ElmComp") or [])
    except Exception:
        pass
    for role in ("dg1", "dg2"):                     # and what lies in the gensets' grid, in case the first misses any
        grid = grid_of(net[role])
        try:
            inside = list(grid.GetContents("*.ElmComp", 1) or []) if grid is not None else []
        except Exception:
            inside = []
        found += [c for c in inside if not any(same(c, f) for f in found)]
    return found


def plant_state(gen, comps):
    """What PowerFactory holds as the plant model of a generator:
    comp      the composite model that has the machine in a slot (None if there is none)
    others    further composite models that hold the same machine
    frame, slots, elements (one per slot, None if empty), roles (see slot_roles)
    gov, avr  the models in the governor's and the regulator's slot
    extra     models in the other slots
    loose     DSL models stored in the composite model, in service, and in no slot"""
    st = dict(comp=None, others=[], frame=None, slots=[], elements=[], roles={}, gov=None, avr=None, extra=[],
              loose=[])
    holding = [c for c in comps
               if any(same(e, gen) for e in list(attr(c, "pelm", []) or []) if e is not None)]
    linked = attr(gen, "c_pmod")
    if linked is not None and not any(same(linked, c) for c in holding):
        holding.append(linked)
    if not holding:
        return st
    holding.sort(key=lambda c: (attr(c, "outserv", 0) != 0, not same(c, linked)))    # in service first
    comp = holding[0]
    slots = list(attr(comp, "pblk", []) or [])
    elements = list(attr(comp, "pelm", []) or [])
    elements += [None] * (len(slots) - len(elements))
    roles = slot_roles(slots)
    st.update(comp=comp, others=holding[1:], frame=attr(comp, "typ_id"), slots=slots, elements=elements, roles=roles)
    for i, elm in enumerate(elements):
        if elm is None or same(elm, gen):
            continue
        if i == roles.get("gov"):
            st["gov"] = elm
        elif i == roles.get("avr"):
            st["avr"] = elm
        elif "gov" in roles and "avr" in roles:
            st["extra"].append(elm)
        else:                               # a frame whose slots the script cannot tell apart: by the names
            typ = attr(elm, "typ_id")
            slot = slots[i].loc_name if i < len(slots) and slots[i] is not None else ""
            words = " ".join([slot, elm.loc_name, typ.loc_name if typ is not None else ""]).lower()
            if st["avr"] is None and any(k in words for k in ("avr", "vco", "sexs", "esac", "esst", "ieeet", "exc")):
                st["avr"] = elm
            elif st["gov"] is None and any(k in words for k in ("gov", "pcu", "gast", "ieeeg")):
                st["gov"] = elm
            else:
                st["extra"].append(elm)
    try:
        inside = list(comp.GetContents("*.ElmDsl") or [])
    except Exception:
        inside = []
    st["loose"] = [d for d in inside if not attr(d, "outserv", 0)
                   and not any(same(d, e) for e in elements if e is not None)]
    return st


def usable(st):
    """Governor and voltage regulator are there and in service."""
    return (st["comp"] is not None and not attr(st["comp"], "outserv", 0)
            and all(st[key] is not None and not attr(st[key], "outserv", 0) for key in ("gov", "avr")))


def parameter_names(model):
    """(the parameters of a DSL model as the model spells them, whether they are in the
    order of its parameter list). From the model itself (one comma-separated text, or a
    list); else from its definition: its parameters and its limiting parameters."""
    for key in ("parameterNames", "parnam"):
        names = name_list(attr(model, key))
        if names:
            return names, True
    typ, names = attr(model, "typ_id"), []
    for key in ("sParams", "sLowLimPar", "sUpLimPar"):
        names += [n for n in name_list(attr(typ, key)) if n not in names]
    return names, False


def dsl_parameters(dsl):
    """{name: value} of a DSL model, as far as PowerFactory hands the values out: from
    its parameter list where that holds one value per name, else one by one by name."""
    names, in_order = parameter_names(dsl)
    try:
        values = [float(v) for v in attr(dsl, "params")]
    except Exception:
        values = []
    if names and in_order and len(names) == len(values):
        return dict(zip(names, values))
    found = {}
    for name in names:
        value = attr(dsl, name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found[name] = float(value)
    if not found and names and len(names) == len(values):   # no reading by name: the list, in the definition's order
        return dict(zip(names, values))
    return found


def model_kind(model):
    """("library" | "own" | "other", "gov" | "avr" | "") of a DSL model, by its definition."""
    typ = attr(model, "typ_id")
    name = typ.loc_name.lower() if typ is not None else ""
    for kind, spec, word in (("gov", OWN_GOVERNOR, "degov1"), ("avr", OWN_REGULATOR, "sexs")):
        if name.startswith(spec["name"].lower()):
            return "own", kind
        if word in name:
            return "library", kind
    return "other", ""


def controller_values(net, role, route):
    """(governor values, regulator values, H used) that the script gives one genset on a
    route ("library" or "own"). The governor gain is scaled to the inertia in the
    generator type; a gain lowered by the self-test stays lowered."""
    data = GENSET_DATA[role]
    typ = attr(net[role], "typ_id")
    h_data = inertia_constant(data["j_kgm2"], net[role + "_kva"])
    h, cosn = attr(typ, "h"), attr(typ, "cosn")
    h = float(h) if isinstance(h, (int, float)) and h > 0 else h_data
    cosn = float(cosn) if isinstance(cosn, (int, float)) and 0 < cosn <= 1 else 0.8
    less_gov, less_avr = _plant["gain"].get((role, "gov"), 1.0), _plant["gain"].get((role, "avr"), 1.0)
    if route == "own":
        kp = OWN_CROSSOVER * less_gov * 2.0 * h / cosn
        gov = dict(Kp=kp, Ki=kp / OWN_RESET, Tt=OWN_TT, Ta=OWN_TA, Td=OWN_TD, R=data["gov"]["Droop"],
                   Tmin=data["gov"]["TMIN"], Tmax=data["gov"]["TMAX"])
    else:
        gov = dict(data["gov"], K=data["gov"]["K"] * h / h_data * less_gov)
    avr = dict(data["avr"], K=data["avr"]["K"] * less_avr)
    return gov, avr, h


def compare_parameters(what, model, suggested):
    """Log the model's parameters next to the values the script would write; return how
    many differ."""
    typ = attr(model, "typ_id")
    off = "  (out of service)" if attr(model, "outserv", 0) else ""
    log(f"         {what}: {model.loc_name}" + (f"  (model {typ.loc_name})" if typ is not None else "  (no model type)")
        + off)
    have = dsl_parameters(model)
    if not have:
        log("            its parameters could not be read from the script")
        return 0
    if not suggested:
        log("            a model the script has no values for: " + ", ".join(f"{k} = {v:g}" for k, v in have.items()))
        return 0
    lower = {k.lower(): v for k, v in have.items()}
    differ = 0
    for name, want in suggested.items():
        got = lower.get(name.lower())
        if got is None:
            continue
        if abs(got - want) > 0.05 * max(abs(want), 0.01):
            differ += 1
            log(f"            {name} = {got:g}   (the script's value: {want:g})")
    known = [n for n in suggested if n.lower() in lower]
    if known and not differ:
        log("            as the script would set it: " + ", ".join(f"{n} = {lower[n.lower()]:g}" for n in known))
    if len(known) < len(suggested):
        log("            this model has no parameter called: " + ", ".join(n for n in suggested if n not in known))
    return differ


# ============================================================================
# SURVEY: what the model has for an RMS simulation
# ============================================================================
def survey(net, changes):
    """Report on the dynamic data. Returns {"dg1": ready?, "dg2": ready?, "notes": [...]}."""
    log("\n=== Survey: what the model has for an RMS simulation ===")
    ready, inertia = {"notes": []}, []
    comps = composite_models(net)
    for role in ("dg1", "dg2"):
        gen, data = net[role], GENSET_DATA[role]
        typ = attr(gen, "typ_id")
        h_data = inertia_constant(data["j_kgm2"], net[role + "_kva"])
        h_model = attr(typ, "h")
        j_text = f"J = {data['j_kgm2']:.2f} kg m2" + (f", of which {data['j_note']}" if data.get("j_note") else "")
        log(f"\n   {gen.loc_name}: type {typ.loc_name if typ is not None else '?'}, {net[role + '_kva']:.0f} kVA, "
            f"torque base {net[role + '_base_kw']:.0f} kW" + ("  (out of service now)" if attr(gen, "outserv", 0) else ""))
        if h_model is None:
            log(f"      inertia: could not read H from the type. From the data sheets H = {h_data:.3f} s "
                f"({j_text}) on {net[role + '_kva']:.0f} kVA")
        elif abs(h_model - h_data) > 0.05 * h_data:
            written = False
            if SET_INERTIA:
                try:
                    typ.SetAttribute("h", h_data)
                    written = True
                except Exception as e:
                    warn(f"{gen.loc_name}: could not write H into the type ({e})")
            if written:
                log(f"      inertia: H was {h_model:.3f} s, now set to {h_data:.3f} s ({j_text}). "
                    "This change stays in the type.")
                h_model = h_data
            else:
                warn(f"{gen.loc_name}: H in the type is {h_model:.3f} s; the data sheets give {h_data:.3f} s "
                     f"({j_text}). Enter it on the type's RMS-Simulation page "
                     "(Inertia Constant H, rated to Sgn), or set SET_INERTIA = True.")
        else:
            log(f"      inertia: H = {h_model:.3f} s, as the data give ({j_text})")
        inertia.append(f"{gen.loc_name} H = {h_model:.3f} s" if isinstance(h_model, (int, float))
                       else f"{gen.loc_name} H not readable")
        machine_model = attr(typ, "model_inp")
        if isinstance(machine_model, str) and machine_model.lower().startswith("cl"):
            warn(f"{gen.loc_name}: its type uses the classical machine model ('{machine_model}'): a voltage behind a "
                 "reactance, on which a voltage regulator has no effect. Choose the standard model on the type's "
                 "RMS-Simulation page.")
        st = plant_state(gen, comps)
        ready[role] = usable(st)
        show_plant(net, role, st)
        rstr, xdss = attr(typ, "rstr"), attr(typ, "xdss")
        if rstr == 0:
            net.setdefault("no_rstr", []).append(gen.loc_name)
        if isinstance(rstr, (int, float)):
            log(f"      stator resistance rstr = {rstr:g} pu" + (f", xd'' = {xdss:g} pu" if isinstance(xdss, (int, float)) else "")
                + (": with zero resistance a fault at the terminals takes no power from the set, which then"
                   " speeds up as fast as it can" if rstr == 0 else ""))
    ready["notes"].append("inertia as simulated: " + ", ".join(inertia) + " (the rate of change of frequency and the "
                          "swing of the rotor angle scale with 1/H)")
    if net.get("no_rstr"):
        ready["notes"].append(" and ".join(net["no_rstr"]) + ": no stator resistance in the type (rstr = 0), so a "
                              "fault near the sets takes no power from them and they speed up as fast as they can; "
                              "with resistance they may even slow down ([1] p.6). Every fault case and every "
                              "critical clearing time depends on it: enter the data-sheet value")

    bare = [obj.loc_name for obj in net["pv"] + [net["bess"]] if attr(obj, "c_pmod") is None]
    if bare:
        ready["notes"].append(", ".join(bare) + ": no plant model, so they keep feeding their set-points as current "
                              "sources, do not respond to frequency and are back at once after a fault")
    for obj in net["pv"] + [net["bess"]]:
        comp = attr(obj, "c_pmod")
        option = {0: "according to connected input signals", 1: "current source", 2: "voltage source",
                  3: "constant impedance", 4: "constant power"}.get(attr(obj, "iSimModel"), "?")
        umin = attr(obj, "umin")
        log(f"\n   {obj.loc_name} ({obj.GetClassName()}): "
            + (f"plant model {comp.loc_name}" if comp is not None else "no plant model")
            + f"; RMS model option: {option}"
            + (f"; switches off below {umin:g} pu" if isinstance(umin, (int, float)) else ""))
        if comp is None:
            log("      Without a plant model it keeps feeding its set-point: it does not respond to frequency,")
            log("      so it neither trips nor cuts its power above 50.5 Hz as the grid code asks of a plant. The")
            log("      script applies the trips afterwards and runs the case again; the cut in power is not")
            log("      represented, so a rise in frequency that the inverters feed comes out too high.")
    for ld in net["loads"]:
        typ = attr(ld, "typ_id")
        if typ is None:
            log(f"\n   {ld.loc_name}: no load type")
            log("      PowerFactory then treats it as a constant impedance in an RMS simulation: its power")
            log("      falls with the square of the voltage, which eases every drop in frequency. [1] uses a")
            log("      voltage exponent of 1.5 for isolated microgrids: give the load a type with that exponent")
            log("      to see the difference.")
            ready["notes"].append(f"{ld.loc_name}: no load type, so constant impedance in the simulation "
                                  "(the most forgiving load model)")
        else:
            a, b = attr(typ, "aP"), attr(typ, "bP")
            terms = ""
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                terms = (f": P = {a:g} u^{attr(typ, 'kpu0', '?')} + {b:g} u^{attr(typ, 'kpu1', '?')} + "
                         f"{1 - a - b:g} u^{attr(typ, 'kpu', '?')}")
            log(f"\n   {ld.loc_name}: load type {typ.loc_name}{terms}; static share {attr(typ, 'lodst', '?')} %, "
                f"dynamic share {attr(typ, 'loddy', '?')} %")
            ready["notes"].append(f"{ld.loc_name}: load type {typ.loc_name}{terms}")
    if CONTROLLERS == "keep":
        for role in ("dg1", "dg2"):
            if not ready[role]:
                how_to(net, role)
    return ready


def show_plant(net, role, st):
    """Log the plant model of one genset as PowerFactory holds it. Returns how many of
    its values differ from the ones the script would write."""
    gen = net[role]
    if st["comp"] is None:
        log("      no plant (composite) model: no governor and no voltage regulator")
        return 0
    comp, frame = st["comp"], st["frame"]
    log(f"      plant model: {comp.loc_name}  (frame {frame.loc_name if frame is not None else 'missing'})"
        + ("  OUT OF SERVICE" if attr(comp, "outserv", 0) else ""))
    differ = 0
    for i, slot in enumerate(st["slots"]):
        elm = st["elements"][i]
        name = slot.loc_name if slot is not None else f"slot {i + 1}"
        if elm is not None and same(elm, gen):
            log(f"         {name}: {gen.loc_name}")
        elif elm is not None and (same(elm, st["gov"]) or same(elm, st["avr"])):
            route, _ = model_kind(elm)
            values = {}
            if route != "other":
                gov, avr, _ = controller_values(net, role, route)
                values = gov if same(elm, st["gov"]) else avr
            differ += compare_parameters(name, elm, values)
        elif elm is not None:
            log(f"         {name}: {elm.loc_name} ({elm.GetClassName()})")
        elif i in st["roles"].values():
            log(f"         {name}: empty")
    for key in ("gov", "avr"):
        if key not in st["roles"] and st[key] is None:
            log(f"         no slot for {SLOT_TEXT[key]} was recognised in this frame")
    for elm in st["loose"]:
        log(f"         {elm.loc_name}: a model stored in the plant model but in no slot")
    for other in st["others"]:
        log(f"      {gen.loc_name} also sits in a second plant model: {other.loc_name}"
            + ("  (out of service)" if attr(other, "outserv", 0) else ""))
    return differ


def how_to(net, role):
    gen, data = net[role], GENSET_DATA[role]
    h = inertia_constant(data["j_kgm2"], net[role + "_kva"])
    typ = attr(gen, "typ_id")
    log(f"\n   {gen.loc_name} has no governor or no voltage regulator in service. The script can set them up")
    log('   itself: set CONTROLLERS = "auto". By hand it goes like this:')
    log(f"      1. Right-click {gen.loc_name} in the single-line diagram > Define... > Governor and Turbine (gov)...")
    log("         and pick the Woodward diesel governor DEGOV1 from the library (gov_DEGOV1 up to PowerFactory")
    log("         2022, gov_PSSE_DEGOV1 in later versions). PowerFactory creates the plant (composite) model for")
    log("         the machine. Enter:")
    log("            " + ", ".join(f"{k} = {v:g}" for k, v in data["gov"].items()))
    log("            droop control: throttle feedback (0). If PowerFactory refuses a negative TMIN, enter 0.")
    log("            (the textbook gain K = 40 is unstable with the little inertia of these sets)")
    log(f"      2. Right-click {gen.loc_name} again > Define... > Automatic Voltage Regulator (avr)... and pick")
    log("         the simplified excitation system SEXS (avr_SEXS, or exc_PSSE_SEXS in later versions). Enter:")
    log("            " + ", ".join(f"{k} = {v:g}" for k, v in data["avr"].items()))
    log(f"            (if the model asks for the ratio Ta/Tb instead of Ta: {data['avr']['Ta'] / data['avr']['Tb']:g})")
    log(f"      3. Open the generator type{' ' + typ.loc_name if typ is not None else ''} > RMS-Simulation page and "
        f"enter the inertia constant H (rated to Sgn) = {h:.3f} s")


# ============================================================================
# CONTROLLERS: governor and voltage regulator, set up by the script
# ============================================================================
def is_mine(obj):
    return any(same(obj, mine) for mine in _plant["created"])


def free_name(folder, name, cls):
    """`name`, or `name (2)` and so on if the folder already holds an object of that
    class and name: two objects must never share a name, because the script tells its
    own objects from yours by where they are and what they are called."""
    for n in range(1, 50):
        candidate = name if n == 1 else f"{name} ({n})"
        try:
            if not folder.GetContents(f"{candidate}.{cls}"):
                return candidate
        except Exception:
            return candidate
    return name


def remember(obj, name, readable_only=False):
    """Note what an attribute of an object holds, the first time this run touches it,
    so that a set-up that fails can be taken back. Objects this run created need none."""
    key = (full_name(obj), name)
    if is_mine(obj) or key in _plant["undo"]:
        return
    old = attr(obj, name)
    if old is None and readable_only:
        return
    _plant["undo"][key] = (obj, name, list(old) if isinstance(old, (list, tuple)) else old)


def plant_set(obj, name, value):
    """Set an attribute of a plant-model object (see remember)."""
    remember(obj, name)
    obj.SetAttribute(name, value)


def create(parent, cls, name, what):
    """A new object of this run. PowerFactory refuses with None or with an error: both
    become a BuildProblem, so that the next way of setting the controllers up is tried."""
    try:
        obj = parent.CreateObject(cls, name)
    except Exception as e:
        raise BuildProblem(f"PowerFactory did not create {what} in {parent.loc_name} ({type(e).__name__}: {e})")
    if obj is None:
        raise BuildProblem(f"PowerFactory did not create {what} in {parent.loc_name}")
    _plant["created"].append(obj)
    return obj


def delete(obj):
    """Delete an object. Returns "" if it is gone, else why it is not: PowerFactory
    reports a refusal with a number, not with an error."""
    try:
        code = obj.Delete()
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return f"PowerFactory refused, code {code}" if code else ""


def undo_plant():
    """Take back everything this run did to the plant models: they are then as the
    script found them. Returns (steps taken back, steps that could not be)."""
    failed = []
    for obj, name, old in reversed(list(_plant["undo"].values())):
        try:
            obj.SetAttribute(name, old)
        except Exception as e:
            failed.append(f"{obj.loc_name}.{name} could not be set back ({e})")
    for obj in reversed(_plant["created"]):
        name = obj.loc_name
        refused = delete(obj)
        if refused:
            try:
                obj.SetAttribute("outserv", 1)
                failed.append(f"{name} could not be deleted ({refused}): it is out of service now, delete it by hand")
            except Exception:
                failed.append(f"{name} could not be deleted ({refused}): delete it by hand")
    n = len(_plant["undo"]) + len(_plant["created"])
    _plant.update(created=[], undo={}, gain={}, route=None, proven=False, check=[])
    if failed:
        warn("Not everything the script did to the plant models could be taken back: " + "; ".join(failed))
    return n - len(failed), len(failed)


def short_path(obj):
    """Where an object lives, without the class names PowerFactory adds to every step."""
    try:
        parent = obj.GetParent()
    except Exception:
        parent = None
    text = full_name(parent) if parent is not None else ""
    return re.sub(r"\.[A-Za-z]+(?=\\|$)", "", text)


def library_roots():
    """(label, folder) of the places the script looks for block definitions: the
    project first, then DIgSILENT's library."""
    roots = []
    for label, get in (("the project's dynamic models", lambda: app.GetProjectFolder("blk")),
                       ("the project library", lambda: app.GetProjectFolder("lib")),
                       ("DIgSILENT's library", lambda: app.GetGlobalLibrary())):
        try:
            folder = get()
        except Exception:
            folder = None
        if folder is not None and not any(same(folder, f) for _, f in roots):
            roots.append((label, folder))
    return roots


def search_library(names, accept, roots):
    """Block definitions [(object, where)] for the first of `names` (wildcards allowed)
    under which one is found that `accept` takes."""
    for pattern in names:
        hits = []
        for label, root in roots:
            try:
                found = list(root.GetContents(pattern + ".BlkDef", 1) or [])
            except Exception:
                found = []
            for obj in found:
                if not any(same(obj, h) for h, _ in hits) and accept(obj):
                    hits.append((obj, label))
        if hits:
            return hits
    return []


def frame_signals(frame):
    """({"w": ..., "pt": ..., "u": ..., "ve": ...}, how they were found, what is missing):
    the names under which the frame hands the speed to the governor and takes the turbine
    power from it, and hands the terminal voltage to the regulator and takes the
    excitation voltage from it. Read from the frame's connections; where those cannot
    be read, the usual names of DIgSILENT's library, if the slots list them."""
    slots = frame_slots(frame)
    roles = slot_roles(slots)
    found = {}

    def is_slot(obj, key):
        return obj is not None and key in roles and same(obj, slots[roles[key]])

    try:
        wires = list(frame.GetContents("*.BlkSig") or [])
    except Exception:
        wires = []
    for wire in wires:
        try:
            src, dst = attr(wire, "pnodfrom"), attr(wire, "pnodto")
            pairs = list(zip(name_list(wire.GetFromSigName()), name_list(wire.GetToSigName())))
        except Exception:
            continue
        for a, b in pairs:
            if is_slot(src, "sym") and is_slot(dst, "gov") and a in ("xspeed", "speed", "w", "n"):
                found.setdefault("w", b)
            elif is_slot(src, "gov") and is_slot(dst, "sym") and b == "pt":
                found.setdefault("pt", a)
            elif is_slot(src, "sym") and is_slot(dst, "avr") and a in ("ut", "u", "u1", "utr"):
                found.setdefault("u", b)
            elif is_slot(src, "avr") and is_slot(dst, "sym") and b in ("ve", "vf", "uerrs"):
                found.setdefault("ve", a)
    read = sorted(found)
    lists = {}
    for key, role, side in (("w", "gov", "sInput"), ("pt", "gov", "sOutput"), ("u", "avr", "sInput"),
                            ("ve", "avr", "sOutput")):
        lists[key] = name_list(attr(slots[roles[role]], side)) if role in roles else []
    missing = []
    for key, usual in USUAL_SIGNALS.items():
        names = lists[key]
        if key in found and (not names or found[key] in names):
            continue                                # read from the connections, and the slot agrees
        spelled = {n.lower(): n for n in names}     # PowerFactory tells upper and lower case apart
        pick = next((spelled[u] for u in usual if u in spelled), None)
        if pick is None and len(names) == 1:
            pick = names[0]                         # the slot has this one signal only
        if pick is None and not names:
            pick = usual[0]                         # the slot does not say: DIgSILENT's own name
        if pick is None:
            found.pop(key, None)
            missing.append(key)
        else:
            found[key] = pick
    how = ("read from the frame's connections" if len(read) == 4 else
           "from the signals its slots list" if all(lists.values()) else
           "the usual names of DIgSILENT's library" + (f" (the frame's connections gave {', '.join(read)})" if read else ""))
    return found, how, missing, lists


def find_frame(net, comps, roots):
    """(frame, where it comes from): the frame a genset's plant model already uses, else
    one from the libraries."""
    for role in ("dg1", "dg2"):
        st = plant_state(net[role], comps)
        if st["comp"] is not None and is_machine_frame(st["frame"]):
            return st["frame"], f"the frame {st['comp'].loc_name} already uses"
    hits = search_library(FRAME_NAMES + ("*",), is_machine_frame, roots)
    if not hits:
        return None, ""
    hits.sort(key=lambda h: ("no droop" not in h[0].loc_name.lower(), len(frame_slots(h[0]))))
    frame, where = hits[0]
    more = [h[0].loc_name for h in hits[1:4]]
    return frame, where + (f"; also found: {', '.join(more)}" if more else "")


def own_folder(make=False):
    """The project folder that takes block definitions: its "User Defined Models"
    (made if `make` and it is missing), failing that the project library."""
    tries = [lambda: app.GetProjectFolder("blk")]
    if make:
        tries.append(lambda: app.GetProjectFolder("blk", 1))
    tries.append(lambda: app.GetProjectFolder("lib"))
    for get in tries:
        try:
            folder = get()
        except Exception:
            folder = None
        if folder is not None:
            return folder
    return None


def bare_lines(lines):
    """Equations as a list of lines without the blanks around them."""
    if isinstance(lines, str):
        lines = [lines]
    return [part.strip() for line in (lines or []) for part in str(line).split("\n") if part.strip()]


def same_equations(a, b):
    """Two texts of equations say the same: comments and spacing aside."""
    def core(lines):
        return [re.sub(r"\s+", "", line) for line in bare_lines(lines) if not line.startswith("!")]
    return core(a) == core(b)


def own_definition(spec, signals, taken):
    """One of the script's own models as a block definition in the project library:
    written if it is not there, rewritten if it is. Returns (definition, what was done)."""
    folder = own_folder(make=True)
    if folder is None:
        raise BuildProblem("the project has no library folder for dynamic models to put the script's own models in")
    names = dict(signals)
    for key in ("wset", "uset"):                    # set-point inputs: names the frame does not use
        names[key] = key
        while names[key] in taken:
            names[key] += "x"
    mine = spec["states"].split(",") + spec["internal"].split(",") + list(spec["params"])
    clash = sorted({name for name in names.values() if name in mine})
    if clash:
        raise BuildProblem(f"the frame calls a signal {', '.join(clash)}, which is also a variable of {spec['name']}: "
                           "give that variable another name in the script")
    lines = [line.format(**names) for line in spec["equations"]]
    long = [line for line in lines if len(line) > 80]
    if long:
        raise BuildProblem(f"a line of {spec['name']} is longer than the 80 characters PowerFactory takes: {long[0]}")
    fields = [("sTitle", spec["title"]), ("sOutput", [spec["outputs"].format(**names)]),
              ("sInput", [spec["inputs"].format(**names)]), ("sStates", [spec["states"]]),
              ("sParams", [",".join(spec["params"])]), ("sIntern", [spec["internal"]]), ("sAddEquat", lines)]
    try:
        there = list(folder.GetContents(spec["name"] + ".BlkDef") or [])
    except Exception:
        there = []
    blk = there[0] if there else create(folder, "BlkDef", spec["name"], f"the block definition {spec['name']}")
    done = "written"
    if there:                                       # yours, or of an earlier run: it comes back if this set-up is undone
        same_text = same_equations(attr(blk, "sAddEquat"), lines)
        done = "was there, as the script has it" if same_text else "was there with other equations: rewritten"
    for key, value in fields:
        remember(blk, key)
        try:
            blk.SetAttribute(key, value)
        except Exception as e:
            raise BuildProblem(f"PowerFactory did not take '{key}' of the block definition {spec['name']} ({e})")
    if not same_equations(attr(blk, "sAddEquat"), lines):
        raise BuildProblem(f"the equations of {spec['name']} do not read back from PowerFactory as they were written")
    mark = len(window_lines())
    try:
        refused = blk.Check()                       # 0: fine, 1: PowerFactory cannot read the equations
    except Exception:
        refused = None                              # not every version offers the check from a script
    if refused == 1:
        said = pf_messages(None, since=mark)
        _plant["check"].append(f"PowerFactory's own check of {spec['name']} reported a problem"
                               + (": " + " / ".join(said[-3:]) if said else ""))
        log(f"   PowerFactory's own check of {spec['name']} reports a problem (the self-test will show whether it "
            "matters):")
        for line in said:
            log("      " + line)
    return blk, done


def same_definition(a, b):
    return a is not None and b is not None and (same(a, b) or a.loc_name.lower() == b.loc_name.lower())


def write_parameters(model, values, needed=()):
    """Write {name: value} into a DSL model: by parameter name, in whatever case the model
    spells it, or through its parameter list if it does not take names. Returns
    ({name as in the model: value}, [names the model does not have], {its other
    parameters}, whether the values could be read back)."""
    names, in_order = parameter_names(model)       # the names do not depend on any value being there yet
    actual = {n.lower(): n for n in names}
    todo, missing = {}, []
    for name, want in values.items():
        key = actual.get(name.lower())
        if key is None and names:
            missing.append(name)
        else:
            todo[key or name] = float(want)
    if "Ta" in missing and "Tb" in values:          # a model that asks for the ratio Ta/Tb instead of Ta
        ratio = next((k for low, k in actual.items() if re.fullmatch(r"ta[_/]?(o|over|by|d|div)?[_/]?tb", low)), None)
        if ratio:
            todo[ratio] = values["Ta"] / values["Tb"]
            missing.remove("Ta")
    lacking = [n for n in needed if n in missing]
    if lacking:
        raise BuildProblem(f"the model of {model.loc_name} has no parameter called " + ", ".join(lacking)
                           + (". Its parameters are: " + ", ".join(names) if names else ""))
    before = dsl_parameters(model)
    whole = attr(model, "params")                   # for the undo: the whole list if there is one, else name by name
    listed = in_order and isinstance(whole, (list, tuple)) and len(whole) == len(names)
    if listed:
        remember(model, "params")
    refused = []
    for key, want in todo.items():
        if not listed:
            remember(model, key, readable_only=True)
        try:
            model.SetAttribute(key, want)
        except Exception:
            refused.append(key)
    if refused and names and in_order:              # no writing by name here: the whole list at once
        try:
            model.SetAttribute("params", [todo.get(n, before.get(n, 0.0)) for n in names])
            refused = []
        except Exception as e:
            raise BuildProblem(f"PowerFactory did not take the parameters of {model.loc_name} ({e})")
    if refused:
        raise BuildProblem(f"PowerFactory did not take the parameters {', '.join(refused)} of {model.loc_name}")
    back = dsl_parameters(model)
    wrong = [k for k, want in todo.items() if k in back and abs(back[k] - want) > 1e-6 * max(1.0, abs(want))]
    if wrong:
        raise BuildProblem(f"{model.loc_name}: these parameters do not read back as written: " + ", ".join(wrong))
    written = {k: back.get(k, v) for k, v in todo.items()}
    return written, missing, {k: v for k, v in back.items() if k not in todo}, all(k in back for k in todo)


def retire(obj, why):
    """Take a model that is in the way out of the simulation: delete it if this run made
    it, else put it out of service (it stays where it is)."""
    if is_mine(obj):
        name, rest = obj.loc_name, [mine for mine in _plant["created"] if not same(obj, mine)]
        refused = delete(obj)
        if not refused:
            _plant["created"] = rest
            return f"{name} deleted again ({why})"
        try:                                        # still there: at least it must not take part
            obj.SetAttribute("outserv", 1)
        except Exception as e:
            raise BuildProblem(f"{name} is in the way ({why}); it could neither be deleted ({refused}) nor put out "
                               f"of service ({e})")
        return f"{name} could not be deleted ({refused}): put out of service instead ({why})"
    if attr(obj, "outserv", 0):
        return ""
    try:
        plant_set(obj, "outserv", 1)
    except Exception as e:
        raise BuildProblem(f"{obj.loc_name} is in the way ({why}) and could not be put out of service ({e})")
    return f"{obj.loc_name} put out of service ({why}); it stays in the model"


def assign(comp, elements):
    """Put one element into every slot of a composite model and read them back."""
    def wrong():
        back = list(attr(comp, "pelm", []) or [])
        back += [None] * (len(elements) - len(back))
        return [i for i, want in enumerate(elements)
                if (want is None) != (back[i] is None) or (want is not None and not same(want, back[i]))]

    problem = ""
    remember(comp, "pelm")
    for attempt in (1, 2):
        try:
            comp.SetAttribute("pelm", elements)
        except Exception as e:
            problem = str(e)
        if not wrong():
            return
        if attempt == 1:                            # let PowerFactory sort its slots first, then once more
            try:
                comp.SlotUpdate()
            except Exception:
                pass
    slots = list(attr(comp, "pblk", []) or [])
    raise BuildProblem(f"{comp.loc_name}: PowerFactory did not put the elements into the slots "
                       + ", ".join(slots[i].loc_name if i < len(slots) and slots[i] is not None else str(i + 1)
                                   for i in wrong()) + (f" ({problem})" if problem else ""))


def build_plant(net, role, route, res):
    """Governor and voltage regulator for one genset. Returns the lines to log."""
    gen = net[role]
    st = plant_state(gen, res["comps"])
    said, comp = [], st["comp"]
    if comp is None:
        comp = next((c for c in res["unassigned"] if gen.loc_name.lower() in c.loc_name.lower()), None)
        if comp is not None:
            res["unassigned"] = [c for c in res["unassigned"] if not same(c, comp)]
            said.append(f"plant model {comp.loc_name}: was there without a machine in it, now used for {gen.loc_name}")
        else:
            grid = grid_of(gen)
            if grid is None:
                raise BuildProblem(f"the grid that holds {gen.loc_name} was not found, so there is no place for its "
                                   "composite model")
            comp = create(grid, "ElmComp", free_name(grid, f"Plant_{gen.loc_name}", "ElmComp"),
                          f"a composite model for {gen.loc_name}")
            said.append(f"plant model {comp.loc_name}: created in {grid.loc_name}")
    else:
        said.append(f"plant model {comp.loc_name}: was there")
    old_frame = attr(comp, "typ_id")
    if not is_machine_frame(old_frame):
        remember(comp, "pelm", readable_only=True)  # a new frame empties the slots: what they held comes first
        plant_set(comp, "typ_id", res["frame"])
        said.append(f"   frame: {res['frame'].loc_name}"
                    + (f" (instead of {old_frame.loc_name}, which has no slots for a machine with its "
                       "controllers)" if old_frame is not None else ""))
    if attr(comp, "outserv", 0):
        plant_set(comp, "outserv", 0)
        said.append("   it was out of service: put in service")
    slots = list(attr(comp, "pblk", []) or [])
    if not all(key in slot_roles(slots) for key in SLOT_WORDS):
        try:                                        # the slots of the frame may only show after this
            comp.SlotUpdate()
        except Exception:
            pass
        slots = list(attr(comp, "pblk", []) or [])
    roles = slot_roles(slots)
    if not all(key in roles for key in SLOT_WORDS):
        frame = attr(comp, "typ_id")
        raise BuildProblem(f"{comp.loc_name}: its frame {frame.loc_name if frame is not None else '?'} shows the slots "
                           + (", ".join(s.loc_name for s in slots if s is not None) or "none")
                           + "; the script needs one each for the machine, the voltage regulator and the governor")
    elements = list(attr(comp, "pelm", []) or [])
    elements = (elements + [None] * len(slots))[:len(slots)]
    for i, elm in enumerate(elements):              # a governor or regulator model in some other slot
        if (elm is not None and i not in roles.values() and not same(elm, gen)
                and elm.GetClassName() == "ElmDsl" and model_kind(elm)[0] != "other"):
            elements[i] = None
            said.append(f"   {slots[i].loc_name}: {elm.loc_name} taken out of it (not the slot for such a model)")
    gov_values, avr_values, _ = controller_values(net, role, route)
    needed = {"gov": ("Kp", "Ki", "Tt", "Ta", "Td", "R", "Tmin", "Tmax") if route == "own" else
              ("K", "T1", "T2", "T3", "T4", "T5", "T6", "TD") + (("Droop",) if gov_values.get("Droop") else ()),
              "avr": ("K", "Tb", "Te", "Emax")}
    replaced = []
    for key, values in (("gov", gov_values), ("avr", avr_values)):
        definition, now = res[key], elements[roles[key]]
        if now is not None and same_definition(attr(now, "typ_id"), definition):
            model, how = now, "was there"
        else:
            # one with this definition that is stored in the plant model: in another slot, or in none
            try:
                inside = list(comp.GetContents("*.ElmDsl") or [])
            except Exception:
                inside = []
            model = next((d for d in inside if same_definition(attr(d, "typ_id"), definition)), None)
            how = "was in the plant model, put into its slot"
            if model is not None:
                elements = [None if e is not None and same(e, model) else e for e in elements]
            else:
                # named after the slot's own name filter ("avr*" and the like), so that PowerFactory's
                # slot update would put it where the script does
                lead = re.match(r"[A-Za-z]+", str(attr(slots[roles[key]], "filtnam", "") or "").strip())
                lead = lead.group(0) if lead and lead.group(0).lower() in SLOT_WORDS[key][0] else key
                name = free_name(comp, f"{lead}_{'own_' if route == 'own' else ''}{gen.loc_name}", "ElmDsl")
                model = create(comp, "ElmDsl", name, f"the model {name}")
                try:
                    model.SetAttribute("typ_id", definition)
                except Exception as e:
                    raise BuildProblem(f"PowerFactory did not take {definition.loc_name} as the model of {name} ({e})")
                how = "new"
            if now is not None:
                replaced.append((now, f"{slots[roles[key]].loc_name} now holds {model.loc_name}"))
        if attr(model, "outserv", 0):
            plant_set(model, "outserv", 0)
        written, missing, other, checked = write_parameters(model, values, needed[key])
        elements[roles[key]] = model
        said.append(f"   {slots[roles[key]].loc_name}: {model.loc_name} ({how}), model {definition.loc_name}")
        said.append("      " + ", ".join(f"{k} = {v:.4g}" for k, v in written.items())
                    + ("" if checked else "   (PowerFactory does not hand the values back to the script: not checked)"))
        if missing:
            said.append("      the model has no parameter called " + ", ".join(missing) + ": not set")
        if other:
            said.append("      left as they are: " + ", ".join(f"{k} = {v:g}" for k, v in other.items()))
            slow = [k for k, v in other.items() if v == 0 and k.lower().startswith("t")
                    and not k.lower().endswith(("min", "max"))]
            if slow:
                said.append("      (of these, " + ", ".join(slow) + " look like time constants that are zero)")
    elements[roles["sym"]] = gen
    assign(comp, elements)
    said.append(f"   {slots[roles['sym']].loc_name}: {gen.loc_name}")
    for old, why in replaced:
        if any(same(old, e) for e in elements if e is not None):
            continue                                # it only moved to the slot it belongs in
        gone = retire(old, why)
        if gone:
            said.append("   " + gone)
    try:
        inside = list(comp.GetContents("*.ElmDsl") or [])
    except Exception:
        inside = []
    for extra in inside:
        if not attr(extra, "outserv", 0) and not any(same(extra, e) for e in elements if e is not None):
            gone = retire(extra, "it is stored in the plant model but sits in no slot")
            if gone:
                said.append("   " + gone)
    for other_comp in st["others"]:
        gone = retire(other_comp, f"a second plant model that holds {gen.loc_name}")
        if gone:
            said.append("   " + gone)
    return said


def find_resources(net, route, write=True):
    """What a route needs: the frame, its signal names and the two model definitions
    ("library": looked up, "own": written into the project library, unless write is
    False). Logs what it finds; raises BuildProblem for what it does not."""
    roots = library_roots()
    comps = composite_models(net)
    log("   Looked in: " + ("; ".join(f"{label} ({root.loc_name})" for label, root in roots) or "nothing: PowerFactory "
        "handed out neither the project library nor its own"))
    frame, where = find_frame(net, comps, roots)
    if frame is None:
        seen = search_library(("*",), lambda obj: bool(frame_slots(obj)), roots)
        if seen:                                    # for whoever has to find out why: what the libraries do hold
            log("   No frame fits. Block definitions with slots that the libraries hold:")
            for obj, _ in seen[:20]:
                log(f"      {obj.loc_name}: " + ", ".join(slot.loc_name for slot in frame_slots(obj)))
            if len(seen) > 20:
                log(f"      ... and {len(seen) - 20} more")
        raise BuildProblem("no frame was found for a machine with a governor and a voltage regulator (looked for "
                           + ", ".join(FRAME_NAMES[:2]) + " and for any block definition with slots for the three)")
    slots = frame_slots(frame)
    signals, how, missing, lists = frame_signals(frame)
    log(f"   Frame: {frame.loc_name}   ({where}; {short_path(frame)})")
    log("      slots: " + ", ".join(s.loc_name for s in slots))
    if signals:
        log("      signals: " + ", ".join(f"{text} '{signals[key]}'" for key, text in (
            ("w", "speed to the governor"), ("pt", "turbine power from it"), ("u", "voltage to the regulator"),
            ("ve", "excitation voltage from it")) if key in signals) + f"   [{how}]")
    for key, name in (("w", "governor"), ("u", "regulator")):
        out = "pt" if key == "w" else "ve"
        if lists[key] or lists[out]:
            log(f"      the {name}'s slot takes {', '.join(lists[key]) or '?'} and gives {', '.join(lists[out]) or '?'}")
    res = dict(comps=comps, frame=frame, signals=signals)
    if route == "library":
        outputs = {"gov": signals.get("pt", USUAL_SIGNALS["pt"][0]), "avr": signals.get("ve", USUAL_SIGNALS["ve"][0])}
        for key, text in (("gov", "Governor model"), ("avr", "Voltage regulator model")):
            def accept(obj, out=outputs[key]):
                names = name_list(attr(obj, "sOutput"))
                return not frame_slots(obj) and (not names or out in names)
            hits = search_library(LIBRARY_MODELS[key], accept, roots)
            if not hits:
                kin = [obj.loc_name for word in SLOT_WORDS[key][0]
                       for obj, _ in search_library((word + "*",), lambda obj: not frame_slots(obj), roots)]
                if kin:                             # for whoever has to find out why: what the libraries do hold
                    log(f"   No {text.lower()} fits. Models of that kind in the libraries: " + ", ".join(kin[:40])
                        + (f" ... and {len(kin) - 40} more" if len(kin) > 40 else ""))
                raise BuildProblem(f"the library has no {text.lower()} called " + " or ".join(LIBRARY_MODELS[key][:-1])
                                   + f" with the output '{outputs[key]}' (looked in " + ", ".join(l for l, _ in roots) + ")")
            hits.sort(key=lambda h: (h[0].loc_name.lower() not in [n.lower() for n in LIBRARY_MODELS[key]],
                                     len(h[0].loc_name)))
            res[key] = hits[0][0]
            names = [n for part in ("sParams", "sLowLimPar", "sUpLimPar") for n in name_list(attr(res[key], part))]
            log(f"   {text}: {res[key].loc_name}   ({hits[0][1]}; {short_path(res[key])})"
                + (f"; also found: {', '.join(h[0].loc_name for h in hits[1:4])}" if len(hits) > 1 else ""))
            if names:
                log("      its parameters: " + ", ".join(names))
    else:
        if missing:
            raise BuildProblem("the script's own models need the frame's signal names, and these could not be made "
                               "out: " + "; ".join(f"{key}: the slot lists {', '.join(lists[key]) or 'nothing'}"
                                                   for key in missing))
        folder = own_folder()
        if folder is None:
            raise BuildProblem("the project has no library folder for dynamic models to put the script's own models in")
        taken = set(lists["w"]) | set(lists["u"]) | set(lists["pt"]) | set(lists["ve"])
        for key, spec, text in (("gov", OWN_GOVERNOR, "Governor model"), ("avr", OWN_REGULATOR, "Voltage regulator model")):
            if write:
                res[key], done = own_definition(spec, signals, taken)
                log(f"   {text}: {res[key].loc_name}   ({done}; {short_path(res[key])})")
                for line in bare_lines(attr(res[key], "sAddEquat")):
                    if not line.startswith("!"):
                        log("         " + line)
            else:
                log(f"   {text}: {spec['name']} would be written into {short_path(folder)}\\{folder.loc_name}")
    # composite models with such a frame and no machine in it: one may be meant for a genset
    res["unassigned"] = []
    for comp in comps:
        if attr(comp, "outserv", 0) or not is_machine_frame(attr(comp, "typ_id")):
            continue
        comp_slots = list(attr(comp, "pblk", []) or [])
        i = slot_roles(comp_slots).get("sym")
        held = list(attr(comp, "pelm", []) or [])
        if i is not None and (i >= len(held) or held[i] is None):
            res["unassigned"].append(comp)
    if res["unassigned"]:
        log("   Plant models with no machine in them: " + ", ".join(c.loc_name for c in res["unassigned"]))
    return res


def library_report(net):
    """Survey only: what the script would build the controllers from. Changes nothing."""
    log("\n=== What the script would set the governors and voltage regulators up from (nothing is changed) ===")
    for route, text in (("own", "The script's own models"), ("library", "DIgSILENT's library models")):
        log(f"\n   {text} (CONTROLLERS = \"{route}\"):")
        try:
            find_resources(net, route, write=False)
            log("   Everything this route needs is there.")
        except BuildProblem as why:
            log(f"   This route is not open: {why}")
        except Exception as e:
            log(f"   This route could not be checked ({type(e).__name__}: {e})")


def set_up_controllers(net, route):
    """Give both gensets a governor and a voltage regulator on one route ("library":
    DIgSILENT's models, "own": the script's equations), then read the result back from
    PowerFactory. Raises BuildProblem if something on the way cannot be done."""
    what = "DIgSILENT's library models" if route == "library" else "the script's own models"
    log(f"\n=== Governors and voltage regulators: {what}, set up by the script ===")
    _plant["gain"].clear()
    _plant.update(route=None, proven=False, check=[])
    res = find_resources(net, route)
    for role in ("dg1", "dg2"):
        said = build_plant(net, role, route, res)
        log(f"   {net[role].loc_name}: " + said[0])
        for line in said[1:]:
            log("      " + line)
    for comp in res["unassigned"]:
        gone = retire(comp, "a plant model with no machine in it")
        if gone:
            log("   " + gone)
    # read back from PowerFactory what is there now
    comps = composite_models(net)
    for role in ("dg1", "dg2"):
        gen = net[role]
        st = plant_state(gen, comps)
        if not usable(st) or not all(same_definition(attr(st[key], "typ_id"), res[key]) for key in ("gov", "avr")):
            raise BuildProblem(f"{gen.loc_name}: read back from PowerFactory, the plant model is not complete")
        if st["others"] and any(not attr(c, "outserv", 0) for c in st["others"]):
            raise BuildProblem(f"{gen.loc_name} still sits in more than one plant model that is in service")
        linked = attr(gen, "c_pmod")
        log(f"   {gen.loc_name}, read back from PowerFactory: {st['comp'].loc_name} holds the machine, "
            f"{st['gov'].loc_name} as governor and {st['avr'].loc_name} as voltage regulator"
            + ("" if same(linked, st["comp"]) else "; the machine itself does not show the link (c_pmod) yet"))
    _plant["route"] = route
    log("   These models are part of the network model now and stay in it, like models added by hand.")


def tidy(net):
    """Delete the block definitions this run wrote that no model uses in the end (the
    script's own, when DIgSILENT's library models are the ones that passed)."""
    used = []
    for comp in composite_models(net):
        try:
            inside = list(comp.GetContents("*.ElmDsl") or [])
        except Exception:
            inside = []
        used += [attr(model, "typ_id") for model in inside]
    for obj in list(_plant["created"]):
        if obj.GetClassName() != "BlkDef" or any(same(obj, typ) for typ in used):
            continue
        name, rest = obj.loc_name, [mine for mine in _plant["created"] if not same(obj, mine)]
        if not delete(obj):
            _plant["created"] = rest
            log(f"   {name}: written by this run and not used in the end, deleted again")


def rewrite_gains(net, role):
    """Write the controller values of one genset again (after the self-test changed a gain)."""
    st = plant_state(net[role], composite_models(net))
    gov, avr, _ = controller_values(net, role, _plant["route"])
    for model, values in ((st["gov"], gov), (st["avr"], avr)):
        if model is not None:
            write_parameters(model, values)


def controller_notes(net):
    """Lines for the head of the results: which controllers were simulated."""
    notes, comps = [], composite_models(net)
    turbo = False
    for role in ("dg1", "dg2"):
        gen = net[role]
        st = plant_state(gen, comps)
        if not usable(st):
            notes.append(f"{gen.loc_name}: no governor or no voltage regulator in service")
            continue
        parts = []
        for key, text, show in (("gov", "governor", ("K", "Kp", "Ki", "Droop", "R")), ("avr", "voltage regulator", ("K",))):
            model = st[key]
            typ = attr(model, "typ_id")
            have = dsl_parameters(model)
            lower = {k.lower(): v for k, v in have.items()}
            values = ", ".join(f"{n} = {lower[n.lower()]:.3g}" for n in show if n.lower() in lower)
            parts.append(f"{text} {typ.loc_name if typ is not None else model.loc_name}" + (f" ({values})" if values else ""))
            route, kind = model_kind(model)
            turbo = turbo or kind == "gov"
            if route != "other":
                gov, avr, _ = controller_values(net, role, route)
                want = gov if key == "gov" else avr
                differ = [n for n, v in want.items() if n.lower() in lower
                          and abs(lower[n.lower()] - v) > 0.05 * max(abs(v), 0.01)]
                if differ:
                    parts[-1] += f"; {', '.join(differ)} differ from the script's values"
        notes.append(f"{gen.loc_name}: " + "; ".join(parts))
    if _plant["route"]:
        notes.insert(0, "governors and voltage regulators: " + ("DIgSILENT's library models" if _plant["route"] == "library"
                     else "the script's own models (OWN_GOVERNOR, OWN_REGULATOR)") + ", set up by the script")
    for (role, key), factor in sorted(_plant["gain"].items()):
        notes.append(f"{net[role].loc_name}: the {'governor' if key == 'gov' else 'voltage regulator'} gain is "
                     f"{factor:g} times the script's value, because the self-test swung too much with the full value")
    if turbo:
        notes.append("the governor models have no turbocharger (boost) limit: large load pick-ups are the best case")
    return notes


# ============================================================================
# LOAD FLOW AND OPERATING POINTS
# ============================================================================
def run_command(cmd, quiet=True):
    """Execute a PowerFactory command; True if it succeeded."""
    if quiet:
        try:
            app.EchoOff()
        except Exception:
            quiet = False
    try:
        ierr = cmd.Execute()
    finally:
        if quiet:
            app.EchoOn()
    return ierr == 0


def solve(ldf, what):
    if run_command(ldf):
        return
    run_command(ldf, quiet=False)
    raise Stop(f"{what} failed - see PowerFactory's messages above")


def measure(net):
    load = sum(p_kw(o) for o in net["loads"])
    pv = sum(p_kw(o, generator=True) for o in net["pv"] if attr(o, "outserv", 0) == 0)
    bess = p_kw(net["bess"], generator=True)
    dg1 = p_kw(net["dg1"], generator=True)
    dg2 = p_kw(net["dg2"], generator=True) if attr(net["dg2"], "outserv", 0) == 0 else 0.0
    return dict(load=load, pv=pv, bess=bess, dg1=dg1, dg2=dg2)


def set_operating_point(case, ldf, net, changes, op):
    """Move to the instant of the point, set the controller's dispatch and check it with a load flow."""
    return set_instant(case, ldf, net, changes, op)


# ============================================================================
# SIMULATION: events, run, read back
# ============================================================================
def prepare_simulation(case, changes):
    inc = app.GetFromStudyCase("ComInc")
    sim = app.GetFromStudyCase("ComSim")
    if inc is None or sim is None:
        raise Stop("Could not get the simulation commands (ComInc / ComSim) from the study case")
    folder = attr(inc, "p_event") or app.GetFromStudyCase("IntEvt")
    if folder is None:
        raise Stop("Could not get the simulation events folder (IntEvt) from the study case")
    for name, value in (("iopt_sim", "rms"), ("iopt_net", "sym"), ("iopt_adapt", 0), ("dtgrd", STEP_S),
                        ("dtout", STEP_S), ("tstart", 0.0), ("iopt_outofstep", 1), ("iopt_show", 1)):
        try:
            changes.set(inc, name, value)
        except Exception as e:
            # pole-slip detection: the script also checks the angle itself. iopt_show ("verify
            # initial conditions") only makes PowerFactory say more when a model does not start at rest
            if name not in ("iopt_outofstep", "iopt_show"):
                warn(f"Could not set the simulation option {name} = {value!r} ({e}); PowerFactory's own "
                     "value is used")
    try:
        changes.set(sim, "tstop", attr(sim, "tstop"))            # remembered, so that it is put back
    except Exception:
        pass
    resvar0 = attr(inc, "p_resvar")                              # may be empty: put back by hand at the end
    ldf = app.GetFromStudyCase("ComLdf")
    if ldf is None:
        raise Stop("Could not get the load flow command (ComLdf) from the study case")
    try:
        clear_events(folder, ldf)                               # left over from an earlier session
    except CaseProblem as why:
        raise Stop(f"{why}. Delete them by hand in the study case's simulation events")
    own = 0
    for ev in list(folder.GetContents() or []):
        if attr(ev, "outserv", 0) == 0:
            changes.set(ev, "outserv", 1)
            own += 1
    if own:
        log(f"\n{own} simulation event(s) of your own are switched off for this run and switched back on after it")
    return {"inc": inc, "sim": sim, "ldf": ldf, "events": folder, "case": case, "changes": changes, "first": True,
            "resvar0": resvar0, "shown": 0, "all_lines": False}


def new_event(folder, cls, name, **values):
    ev = folder.CreateObject(cls, EVENT_PREFIX + name)
    if ev is None:
        raise CaseProblem(f"PowerFactory did not create the simulation event {cls} '{name}'")
    for key, value in values.items():
        ev.SetAttribute(key, value)
    return ev


def cable_fault_ready(changes, line):
    """Let PowerFactory put a short-circuit on the cable itself. Depending on the
    version, the line has to be made available for it and carries the location
    (its RMS-Simulation page, 'Short-Circuit at Line'), or the event carries the
    location. Both are served; what a version does not know is left alone."""
    for name, value in (("ishclne", 1), ("fshcloc", L1_FAULT_PERCENT)):
        now = attr(line, name)
        if not isinstance(now, (int, float)) or abs(float(now) - value) < 1e-9:
            continue
        try:
            quiet_set(changes, line, name, value)
        except Exception as e:
            warn(f"{line.loc_name}: could not set {name} = {value:g} ({e})")


def fault_location(shc):
    now = attr(shc, "shcLocation")
    if not isinstance(now, (int, float)) or abs(float(now) - L1_FAULT_PERCENT) < 1e-9:
        return
    try:
        shc.SetAttribute("shcLocation", L1_FAULT_PERCENT)
    except Exception:
        pass


def create_events(sim, net, spec, load_kw):
    """Create the events of one case. Returns (descriptions, things tripped as {role: time})."""
    folder = sim["events"]
    said, tripped = [], {}
    for n, ev in enumerate(spec):
        kind = ev[0]
        if kind == "trip":
            role = ev[1]
            when = T_EVENT + (ev[2] if len(ev) > 2 else 0.0)
            if role == "L2" and net.get("l2") is None:
                role = "load"                       # no feeder found: its load is disconnected instead
            targets = {"bess": [net["bess"]], "pv": net["pv"], "dg1": [net["dg1"]], "dg2": [net["dg2"]],
                       "load": net["loads"], "L1": [net.get("l1")], "L2": [net.get("l2")]}[role]
            if None in targets:
                raise CaseProblem("the cable L_1 was not found, so it cannot be tripped - set L1_NAME")
            targets = [target for target in targets if not attr(target, "outserv", 0)]
            if not targets:
                raise Skip(f"{ROLE_TEXT[role]} is out of service at this operating point: there is nothing to trip")
            for target in targets:
                switches = breakers_of(target) if TRIP_WITH == "auto" else []
                if switches:
                    for k, sw in enumerate(switches):
                        new_event(folder, "EvtSwitch", f"{n}_{target.loc_name}_{k}", p_target=sw, time=when, i_switch=0)
                    said.append(f"{target.loc_name} trips at {when:g} s (opens {', '.join(s.loc_name for s in switches)})")
                else:
                    new_event(folder, "EvtOutage", f"{n}_{target.loc_name}", p_target=target, time=when, i_what=0)
                    said.append(f"{target.loc_name} trips at {when:g} s (taken out of service)")
            tripped[role] = when
        elif kind == "load_step_pct":
            for ld in net["loads"]:
                new_event(folder, "EvtLod", f"{n}_{ld.loc_name}", p_target=ld, time=T_EVENT, dP=ev[1], dQ=ev[1])
            said.append(f"load P and Q {ev[1]:+.1f} % at {T_EVENT:g} s")
        elif kind == "fault":
            where, duration = ev[1], ev[2]
            code, label = FAULT_TYPES[ev[3] if len(ev) > 3 else "3ph"]
            if where == "L1":
                target = net.get("l1")
                if target is None:
                    raise CaseProblem("the cable L_1 was not found, so no fault can be put on it - set L1_NAME")
                cable_fault_ready(sim["changes"], target)
                place = f"on {target.loc_name} at {L1_FAULT_PERCENT:g} % of its length"
            else:
                target = net["bus"].get(where)
                if target is None:
                    raise CaseProblem(f"no busbar known for the fault location '{where}' - set BUS_NAMES")
                place = f"at {target.loc_name}"
            for tag, when, shc_code in (("on", T_EVENT, code), ("off", T_EVENT + duration, 4)):     # 4 = clear
                shc = new_event(folder, "EvtShc", f"{n}_{tag}", p_target=target, time=when, i_shc=shc_code)
                if where == "L1":
                    fault_location(shc)
                if tag == "on" and (FAULT_R_OHM or FAULT_X_OHM):
                    try:
                        shc.SetAttribute("R_f", FAULT_R_OHM)
                        shc.SetAttribute("X_f", FAULT_X_OHM)
                    except Exception as e:
                        raise CaseProblem(f"the fault impedance could not be set on the event ({e})")
            through = f" through {FAULT_R_OHM:g} + j{FAULT_X_OHM:g} ohm" if FAULT_R_OHM or FAULT_X_OHM else ""
            said.append(f"{label} fault {place}{through} from {T_EVENT:g} s for {duration * 1000:.0f} ms")
        else:
            raise CaseProblem(f"unknown event '{kind}'")
    return said, tripped


def clear_events(folder, ldf=None):
    """Delete the script's own events. Right after a simulation PowerFactory does not let go of its
    events: Delete() then fails without a word, the events stay in the folder and act again in every
    later run (until a new operating point, whose study time and load flows reset the calculation,
    released them). So the calculation is reset first, a load flow run if that is not enough, and
    the folder checked: no run starts with an event of another one."""
    def delete_all():                               # what is left, with PowerFactory's reason
        refused = {ev.loc_name: delete(ev) for ev in list(folder.GetContents() or [])
                   if ev.loc_name.startswith(EVENT_PREFIX)}
        return [f"{ev.loc_name} ({refused.get(ev.loc_name) or 'still there'})" for ev in folder.GetContents() or []
                if ev.loc_name.startswith(EVENT_PREFIX)]

    app.ResetCalculation()
    left = delete_all()
    if left and ldf is not None:
        run_command(ldf)
        left = delete_all()
    if left:
        raise CaseProblem("PowerFactory does not let the events of the run before be deleted: "
                          + ", ".join(left) + ". They would act again in this run")


def result_object(case, name):
    """The result object of a case: the one from an earlier run (plot pages may point
    at it), emptied, or a new one."""
    existing = list(case.GetContents(f"{name}.ElmRes") or [])
    if existing:
        try:
            existing[0].Clear()
        except Exception:
            pass
        return existing[0]
    res = case.CreateObject("ElmRes", name)
    if res is None:
        raise CaseProblem(f"PowerFactory did not create the result object '{name}'")
    return res


P_Q_I = (("m:P:bus1", "p"), ("m:Q:bus1", "q"), ("m:i1:bus1", "i"))      # i: multiples of the rated current
_not_recorded = set()        # (class, variable) already reported as missing


def monitor(res, net, unbalanced=False):
    """Tell the result object what to record. Returns {key: (object, variable)}."""
    want = {}
    for key, term in net["bus"].items():
        if term is not None:
            want[f"u_{key}"] = (term, "m:u1")
            want[f"f_{key}"] = (term, "m:fehz")
            if unbalanced:                          # the phase voltages, which the inverters trip on
                for ph in "abc":
                    want[f"u{ph}_{key}"] = (term, f"m:u:{ph.upper()}")
    for role in ("dg1", "dg2"):
        gen = net[role]
        if attr(gen, "outserv", 0):
            continue
        for var, key in (("s:xspeed", "speed"), ("s:pt", "pt"), ("s:ve", "ve"), ("c:firel", "angle"),
                         ("s:outofstep", "slip")) + P_Q_I:
            want[f"{key}_{role}"] = (gen, var)
    for n, p in enumerate(net["pv"]):
        if attr(p, "outserv", 0) == 0:
            for var, key in P_Q_I:
                want[f"{key}_pv{n}"] = (p, var)
    for var, key in P_Q_I:
        want[f"{key}_bess"] = (net["bess"], var)
    for n, ld in enumerate(net["loads"]):
        want[f"p_load{n}"] = (ld, "m:P:bus1")
        want[f"q_load{n}"] = (ld, "m:Q:bus1")
    if net.get("l1") is not None:
        for var, key in P_Q_I:
            want[f"{key}_l1"] = (net["l1"], var)
    kept = {}
    for key, (obj, var) in want.items():
        try:
            res.AddVariable(obj, var)
            kept[key] = (obj, var)
        except Exception as e:
            if (obj.GetClassName(), var) not in _not_recorded:
                _not_recorded.add((obj.GetClassName(), var))
                warn(f"Could not record {var} of {obj.loc_name}: {e}")
    return kept


def cell(raw):
    """ElmRes.GetValue returns [error, value] in Python."""
    if isinstance(raw, (list, tuple)):
        return float(raw[-1])
    return float(raw)


_bulk = {"ok": None}         # can this PowerFactory hand over a whole column in one call?


def whole_column(res, col, rows, holder, decides=True):
    """Every value of one column of the result object in a single call
    (ElmRes.GetColumnValues into an IntVec), or None if this PowerFactory does not
    do that or does not agree with ElmRes.GetValue; the values are then read one by one."""
    if _bulk["ok"] is False or holder is None:
        return None
    try:
        res.GetColumnValues(holder, col)
        values = [float(v) for v in holder.GetAttribute("V")]
        probe = rows // 2
        same_value = len(values) == rows and abs(values[probe] - cell(res.GetValue(probe, col))) <= 1e-9 * max(
            1.0, abs(values[probe]))
    except Exception:
        same_value = False
    if decides:
        _bulk["ok"] = bool(same_value)
    return values if same_value else None


def thin(times):
    """Rows to keep: every one until FINE_S after the event, where things move fast,
    and evenly spaced ones after that, so that a curve has about MAX_POINTS points."""
    early = [i for i, ti in enumerate(times) if ti <= T_EVENT + FINE_S]
    late = [i for i, ti in enumerate(times) if ti > T_EVENT + FINE_S]
    early = early[::max(1, -(-len(early) // int(0.7 * MAX_POINTS)))]
    picks = early + late[::max(1, -(-len(late) // max(MAX_POINTS - len(early), 1)))]
    if picks[-1] != len(times) - 1:
        picks.append(len(times) - 1)
    return picks


def read_back(res, kept, case):
    """{key: [values]} plus "t", read from the result object after the simulation."""
    res.Load()
    holder = None
    try:
        rows = int(res.GetNumberOfRows())
        if rows < 2:
            return {"t": []}
        if _bulk["ok"] is not False:
            try:
                holder = case.CreateObject("IntVec", EVENT_PREFIX + "column")
            except Exception:
                holder = None
        col_t, first = -1, res.GetValue(0, -1)
        if isinstance(first, (list, tuple)) and len(first) > 1 and first[0] != 0:      # no time in column -1
            col_t = res.FindColumn(res, "b:tnow")
            if col_t is None or col_t < 0:
                raise Stop("The simulation time could not be read from the result object")
        times = (whole_column(res, col_t, rows, holder, decides=False)
                 or [cell(res.GetValue(r, col_t)) for r in range(rows)])
        picks = thin(times)
        out = {"t": [times[r] for r in picks], "units": {}}
        for key, (obj, var) in kept.items():
            col = res.FindColumn(obj, var)
            if col is None or col < 0:
                if (obj.GetClassName(), var) not in _not_recorded:
                    _not_recorded.add((obj.GetClassName(), var))
                    warn(f"PowerFactory recorded no '{var}' for {obj.loc_name} ({obj.GetClassName()}); what "
                         "depends on it is left out")
                continue
            values = whole_column(res, col, rows, holder)
            out[key] = [values[r] for r in picks] if values else [cell(res.GetValue(r, col)) for r in picks]
            try:
                out["units"][key] = res.GetUnit(col)
            except Exception:
                pass
        return out
    finally:
        for close in (res.Release, getattr(holder, "Delete", None)):
            try:
                close()
            except Exception:
                pass


def to_kw(series, key, expected_kw=None):
    """A power curve in kW (or kvar). The factor comes from comparing the first value
    with what the load flow gave in kW; failing that, from the factor found that way
    for another curve of the same run; failing that, from the unit the result object
    reports. Sources count positive when they deliver; loads and lines as recorded."""
    values = series.get(key)
    if not values:
        return None
    factor = None
    if expected_kw is not None and abs(values[0]) > 1e-9 and abs(expected_kw) > 1.0:
        ratio = abs(expected_kw / values[0])
        factor = min((1e-3, 1.0, 1e3, 1e6), key=lambda f: abs(math.log(ratio / f)))
        series["kw_factor"] = factor
    if factor is None:
        factor = (series.get("kw_factor") or kilo_factor(series.get("units", {}).get(key))
                  or kilo_factor(FALLBACK_RESULT_UNIT))
    sign = 1.0 if key[2:].startswith(("load", "l1")) else GEN_SIGN
    return [sign * v * factor for v in values]


def total_kw(series, prefix, expected_kw=None):
    """Sum of the power curves whose key starts with `prefix`, in kW or kvar; None if none."""
    keys = [k for k in series if k.startswith(prefix) and isinstance(series[k], list)]
    if not keys:
        return None
    parts = [to_kw(series, k, expected_kw if len(keys) == 1 else None) for k in keys]
    return [sum(v) for v in zip(*parts)]


def run_rms(sim, net, name, spec, t_end, point):
    """One RMS run. Returns (curves, event descriptions, {role: trip time}, problem or None)."""
    rebuild_point(sim, net, point)                  # the point as for its first run, which also releases ...
    clear_events(sim["events"], sim["ldf"])         # ... the events of the run before, so that they can go
    # a line-to-line or single-line-to-ground fault needs the unbalanced (ABC) simulation
    unbalanced = any(e[0] == "fault" and len(e) > 3 and e[3] != "3ph" for e in spec)
    sim["inc"].SetAttribute("iopt_net", "rst" if unbalanced else "sym")
    said, tripped = create_events(sim, net, spec, point["load"])
    res = result_object(sim["case"], name)
    kept = monitor(res, net, unbalanced)
    sim["inc"].SetAttribute("p_resvar", res)
    quiet = not sim["first"]
    mark = len(window_lines())
    if not run_command(sim["inc"], quiet=quiet):
        if quiet:
            run_command(sim["inc"], quiet=False)
        sim["init_lines"] = pf_messages(None, since=mark, max_lines=30)
        return None, said, tripped, "the initial conditions could not be calculated (see PowerFactory's messages)"
    if sim["first"]:
        keys = None if sim["all_lines"] else ("error", "warn", "not satisfied", "not zero", "limit")
        lines = pf_messages(keys, since=mark, max_lines=30 if sim["all_lines"] else 12)
        if lines:
            log("   PowerFactory's messages at initialisation:")
            for line in lines:
                log("      " + line)
        sim["first"] = False
    sim["sim"].SetAttribute("tstop", t_end)
    finished = run_command(sim["sim"], quiet=True)
    try:
        curves = read_back(res, kept, sim["case"])
    except Stop:
        raise
    except Exception as e:
        return None, said, tripped, f"the results could not be read back ({e})"
    problem = None
    if not curves["t"]:
        problem = "the simulation produced no results"
    elif not finished or curves["t"][-1] < t_end - 0.05:
        problem = f"the simulation stopped at {curves['t'][-1]:.2f} s"
    curves["result"] = res
    return curves, said, tripped, problem


# ============================================================================
# INDICATORS: everything is read off PowerFactory's curves
# ============================================================================
def hold_time(t, flags, hold):
    """First instant by which `flags` has been true without a break for `hold` seconds.
    A spell is counted from the last sample before it, so that one lasting exactly
    `hold` is caught whatever the sampling."""
    start = None
    for i, (ti, flag) in enumerate(zip(t, flags)):
        if flag:
            if start is None:
                start = t[i - 1] if i else ti
            if ti - start >= hold - 1e-6:
                return ti
        else:
            start = None
    return None


def longest_spell(t, flags):
    """Longest unbroken time for which `flags` is true, counted like hold_time."""
    best, start = 0.0, None
    for i, (ti, flag) in enumerate(zip(t, flags)):
        if flag:
            if start is None:
                start = t[i - 1] if i else ti
            best = max(best, ti - start)
        else:
            start = None
    return best


def rocof(t, f, window):
    """Largest rate of change of frequency, averaged over a sliding window, Hz/s.
    None if the curve is shorter than the window."""
    best, j = None, 0
    for i in range(len(t)):
        while j < len(t) - 1 and t[j] - t[i] < window:
            j += 1
        if t[j] - t[i] >= 0.8 * window:
            best = max(best or 0.0, abs(f[j] - f[i]) / (t[j] - t[i]))
    return best


def last_true(t, flags, since):
    """Seconds after `since` at which `flags` was last true (0 if never after it)."""
    last = 0.0
    for ti, flag in zip(t, flags):
        if flag and ti >= since:
            last = ti - since
    return last


def motion(ys, tol):
    """What a curve still does over a stretch of time: (its range, how often it turns
    round). Turns smaller than `tol` do not count. A curve that only rises or falls
    does not turn; an oscillation turns again and again."""
    turns, way, extreme = 0, 0, ys[0]
    for y in ys[1:]:
        if way == 0:
            if abs(y - extreme) > tol:
                way, extreme = (1 if y > extreme else -1), y
        elif way * (y - extreme) > 0:               # still going the same way
            extreme = y
        elif way * (extreme - y) > tol:             # back from its extreme by more than tol: it has turned
            turns, way, extreme = turns + 1, -way, y
    return max(ys) - min(ys), turns


def unwrap(deg):
    out, shift = [], 0.0
    for i, a in enumerate(deg):
        if i and a + shift - out[-1] > 180.0:
            shift -= 360.0
        elif i and a + shift - out[-1] < -180.0:
            shift += 360.0
        out.append(a + shift)
    return out


def guide_step(before_pct):
    """Load step that the ISO 8528-5 guide gives an engine that already carries
    `before_pct` of its prime rating, % of that rating: the size of each guide stage at
    the load where it starts, and in proportion between those loads. None at the rating
    and above it: there the fuel stop sets the limit, and the governor model has that."""
    if before_pct >= 99.5:
        return None
    starts = GUIDE_STAGES[:-1]
    sizes = [hi - lo for lo, hi in zip(GUIDE_STAGES, GUIDE_STAGES[1:])]
    if before_pct <= starts[0]:
        return sizes[0]
    for k in range(len(starts) - 1):
        if before_pct < starts[k + 1]:
            share = (before_pct - starts[k]) / (starts[k + 1] - starts[k])
            return sizes[k] + share * (sizes[k + 1] - sizes[k])
    return sizes[-1]


def dropped_out(p, amps, last, soon):
    """True if a unit that carried p[last] kW before the event is out of service from some sample of
    `soon` on: below 5 % of that power to the end of `soon`, and (where its currents `amps` are
    recorded) without current. A set that stays connected only dips, or swings through zero, while
    the sets take up a sudden change by their stiffness: at its 60 kW minimum DG_2 fell to 2 kW for a
    few ms when half the load went (R2 at OP3), still carrying a fifth of its current."""
    low = [i for i in soon if abs(p[i]) < 0.05 * abs(p[last])]
    if not low or any(abs(p[i]) >= 0.05 * abs(p[last]) for i in soon if i > low[0]):
        return False                                # never out, or back again: a dip, not a trip
    before = max((a[last] for a in amps), default=0.0)
    if before <= 0.0:                               # its current is not recorded
        return True
    return all(max(a[i] for a in amps) < 0.05 * before for i in soon if i >= low[0])


def indicators(curves, net, point, spec, tripped, problem):
    """Everything the tables show for one run, read off PowerFactory's curves.
    `tripped` is {role: time} for every trip in the run."""
    t = curves["t"]
    faults = [e for e in spec if e[0] == "fault"]
    t_clear = T_EVENT + max((e[2] for e in faults), default=0.0)
    ind = dict(problem=problem, notes=[], is_fault=bool(faults), t_clear=t_clear - T_EVENT, protection=None,
               tripped={role: when - T_EVENT for role, when in tripped.items()})
    names = {"dg1": net["dg1"].loc_name, "dg2": net["dg2"].loc_name}

    def connected(role, when):
        return not (role in tripped and tripped[role] <= when + 1e-9)

    # ---- frequency of the island: the speed of DG_1 while it is connected, then of DG_2
    speeds = {role: [x * F_NOM for x in curves[f"speed_{role}"]] for role in ("dg1", "dg2") if f"speed_{role}" in curves}
    bus_f = curves.get("f_gen") or curves.get("f_main")
    if speeds:
        f = []
        for i, ti in enumerate(t):
            role = next((r for r in ("dg1", "dg2") if r in speeds and connected(r, ti)), None)
            f.append(speeds[role][i] if role else None)
    else:
        f = list(bus_f) if bus_f else None
        ind["notes"].append("no machine speed was recorded: the frequency is the busbar's own, which can show "
                            "spikes at the instants of a fault")
    if speeds and not any(connected(role, T_EVENT + 1e-6) for role in speeds):
        ind.update(verdict="unstable", why="blackout as modelled: with " + " and ".join(names[r] for r in speeds)
                   + " disconnected no source is left that forms the grid. The battery and PV inverters follow a "
                   "grid and cannot form one, so the simulation has nothing to show after the trip",
                   **{"class": "seen in the frequency"})
        return ind
    um = curves.get("u_main") or curves.get("u_gen") or curves.get("u_load")      # where the inverters sit
    # in an unbalanced run the inverters see their phase voltages: the lowest one counts
    phases = [curves[f"u{p}_main"] for p in "abc" if f"u{p}_main" in curves]
    if len(phases) == 3:
        um = [min(v) for v in zip(*phases)]
    else:
        phases = []
    if not t or f is None or um is None:
        ind.update(verdict="no result", why=problem or "frequency or voltage was not recorded")
        return ind
    pre = [i for i, x in enumerate(t) if x < T_EVENT - 0.005]
    post = [i for i, x in enumerate(t) if x >= T_EVENT - 1e-9]
    if not pre or not post or f[pre[-1]] is None:
        ind.update(verdict="no result", why=problem or "the run is shorter than the time of the event")
        return ind
    i0, last = post[0], pre[-1]
    if f[i0] is None:
        ind.update(verdict="unstable", why="blackout: no genset is left to form the grid", **{"class": "seen in the frequency"})
        return ind
    buses = [k for k in BUS_KEYS if ("u_" + k) in curves]
    ind["steady"] = (max(abs(f[i] - F_NOM) for i in pre) < 0.05
                     and all(max(abs(curves["u_" + k][i] - curves["u_" + k][pre[0]]) for i in pre) < 0.005
                             for k in buses))

    # ---- where the run stops meaning anything: no set left, a collapse, or the last set over-speeds
    end, ended = len(t) - 1, None                          # last index that counts, and why it is the last
    over = {}                                              # set -> index at which it passes its over-speed limit
    for role, series in speeds.items():
        hit = next((i for i in range(i0, len(t)) if connected(role, t[i]) and series[i] > OVERSPEED_HZ), None)
        if hit is not None:
            over[role] = hit
    for i in range(i0, len(t)):
        alive = [r for r in speeds if connected(r, t[i])]
        if speeds and not alive:
            end, ended = max(i - 1, i0), "no set"
            break
        if f[i] is not None and f[i] < BLACKOUT_HZ:
            end, ended = i, "collapse"
            break
        doomed = [r for r in alive if r in over and over[r] <= i]
        if doomed and len(doomed) == len(alive):           # every set still connected has reached its limit
            end, ended = i, "over-speed"
            break
        if not speeds and f[i] > OVERSPEED_HZ:
            end, ended = i, "over-speed"
            break
    tp, fp = t[i0:end + 1], f[i0:end + 1]
    n = len(tp)

    def after(series):
        return series[i0:i0 + n]

    ump = after(um)
    inside_f = [abs(x - F_NOM) <= F_BAND * F_NOM for x in fp]
    ind.update(f0=f[last], f_min=min(fp + [f[last]]), f_max=max(fp + [f[last]]), f_end=fp[-1], ended=ended,
               t_end=tp[-1] - T_EVENT, t_fmin=tp[fp.index(min(fp))] - T_EVENT, t_fmax=tp[fp.index(max(fp))] - T_EVENT,
               rocof_100=rocof(tp, fp, 0.1), rocof_500=rocof(tp, fp, 0.5),
               f_recovery=last_true(tp, [not x for x in inside_f], T_EVENT) if inside_f[-1] else None,
               below_47=longest_spell(tp, [x < F_UNDER for x in fp]),
               above_over=longest_spell(tp, [x > F_OVER for x in fp]))
    # head-to-head measures: how fast the frequency is back at its nominal value and stays there,
    # and how much frequency deviation the disturbance costs in all (area between curve and 50 Hz)
    home = [abs(x - F_NOM) <= F_SETTLE_HZ for x in fp]
    ind["f_settle_nom"] = last_true(tp, [not x for x in home], T_EVENT) if home[-1] and not ended else None
    ind["f_dev_int"] = sum(0.5 * (abs(fp[k] - F_NOM) + abs(fp[k + 1] - F_NOM)) * (tp[k + 1] - tp[k])
                           for k in range(n - 1)) if not ended else None
    ind["f_dev_max"] = max(F_NOM - ind["f_min"], ind["f_max"] - F_NOM)
    if phases:
        ind["uph_min_main"] = min(after(um))

    # ---- voltage at each busbar; a busbar counts as back when it stays between U_LOW and
    #      U_HIGH (or, if it was outside before the event, within 0.02 pu of where it was)
    def acceptable(u, u0):
        return U_LOW <= u <= U_HIGH or (not U_LOW <= u0 <= U_HIGH and abs(u - u0) <= 0.02)

    recover = []
    for k in buses:
        b0, bp = curves["u_" + k][last], after(curves["u_" + k])
        ind[f"u0_{k}"], ind[f"umin_{k}"], ind[f"umax_{k}"], ind[f"uend_{k}"] = b0, min(bp), max(bp), bp[-1]
        ind[f"ulow_{k}"] = longest_spell(tp, [x < U_LOW for x in bp])
        ind[f"uhigh_{k}"] = longest_spell(tp, [x > U_HIGH for x in bp])
        if acceptable(bp[-1], b0):
            ind[f"urec_{k}"] = last_true(tp, [not acceptable(x, b0) for x in bp], t_clear)
            recover.append(ind[f"urec_{k}"])
    dead = [k for k in buses if ind[f"u0_{k}"] >= 0.5 and ind[f"uend_{k}"] < 0.1]
    live = [k for k in buses if k not in dead]
    low0 = [f"the {BUS_TEXT[k].lower()} is at {ind[f'u0_{k}']:.3f} pu" for k in buses
            if not U_LOW <= ind[f"u0_{k}"] <= U_HIGH]
    if low0:
        ind["notes"].append("before the event " + " and ".join(low0) + f", outside {U_LOW:g} to {U_HIGH:g} pu: the "
                            "operating point itself does not keep that limit, and the case is judged on whether the "
                            "voltage returns to where it was")
    worst = min(buses, key=lambda k: ind[f"umin_{k}"])
    ind.update(u_min=ind[f"umin_{worst}"], u_min_at=worst, u_max=max(ind[f"umax_{k}"] for k in buses),
               u_below_09=max(ind[f"ulow_{k}"] for k in buses), u_above_11=max(ind[f"uhigh_{k}"] for k in buses),
               u_recovery=max(recover) if len(recover) == len(buses) else None,
               lv_time=longest_spell(tp, [x < U_UNDER for x in ump]), dead=dead)

    # ---- margin to protection: how close the run comes to the assumed trip settings, whether or
    #      not it reaches them (negative: past the threshold; a trip also needs its time, which the
    #      protection check below decides)
    ind["f_margin_uf"] = ind["f_min"] - F_UNDER                # to the inverters' under-frequency trip
    ind["f_margin_of"] = F_OVER - ind["f_max"]                 # to their over-frequency trip
    ind["f_margin"] = min(ind["f_margin_uf"], ind["f_margin_of"])
    ind["f_margin_to"] = "under" if ind["f_margin_uf"] <= ind["f_margin_of"] else "over"
    ind["os_margin"] = OVERSPEED_HZ - ind["f_max"]             # to the gensets' over-speed shutdown
    ind["u_low_inv"] = min(ump)                                # lowest voltage the inverters see
    # the fast under-voltage trip acts after T_U_UNDER below U_UNDER: the time left of that
    ind["u_margin_s"] = T_U_UNDER - ind["lv_time"] if ind["lv_time"] > 0 else None

    # ---- the gensets' transient against the ISO 8528-5 limit values (not for faults)
    if not faults and ended is None:
        dip, rise = max((F_NOM - ind["f_min"]) / F_NOM * 100, 0.0), max((ind["f_max"] - F_NOM) / F_NOM * 100, 0.0)
        ind["iso_f"] = next((name for name, d, r, rec, band in ISO_FREQ if dip <= d and rise <= r and last_true(
            tp, [abs(x - fp[-1]) > band / 200 * F_NOM for x in fp], T_EVENT) <= rec), "outside G1")
        if "u_gen" in curves and "gen" in live:          # the class is about the regulators: their busbar
            g0, gp = ind["u0_gen"], after(curves["u_gen"])
            udip, urise = max((g0 - min(gp)) / g0 * 100, 0.0), max((max(gp) - g0) / g0 * 100, 0.0)
            ind["iso_u"] = next((name for name, d, r, rec, band in ISO_VOLT if udip <= d and urise <= r and last_true(
                tp, [abs(x - gp[-1]) > band / 200 * g0 for x in gp], T_EVENT) <= rec), "outside G1")

    # ---- when everything has come to rest at its final value, and whether it has by the end
    ind["t_settle"] = max([last_true(tp, [abs(x - fp[-1]) > REST_F_HZ for x in fp], T_EVENT)]
                          + [last_true(tp, [abs(x - ind[f"uend_{k}"]) > REST_U_PU for x in after(curves["u_" + k])],
                                       T_EVENT) for k in buses])
    ind["t_run"] = tp[-1] - T_EVENT
    swinging, unsettled = [], ""
    too_short = ended is None and not problem and tp[-1] - t_clear < REST_S + 1.0
    if ended is None and not problem and not too_short:
        # the last REST_S seconds: an oscillation turns round at least twice in them
        tail = [i for i, ti in enumerate(tp) if ti >= tp[-1] - REST_S]
        ind["move_f"], turns = motion([fp[i] for i in tail], OSC_F_HZ / 10)
        ind["osc_f"] = ind["move_f"] if turns >= 2 else 0.0
        moves = [motion([after(curves["u_" + k])[i] for i in tail], OSC_U_PU / 10) for k in buses]
        ind["move_u"] = max(m[0] for m in moves)
        ind["osc_u"] = max([m[0] for m in moves if m[1] >= 2] or [0.0])
        if ind["osc_f"] > OSC_F_HZ:
            swinging.append(f"the frequency is still swinging by {ind['osc_f']:.2f} Hz peak to peak")
        if ind["osc_u"] > OSC_U_PU:
            swinging.append(f"the voltage is still swinging by {ind['osc_u']:.3f} pu peak to peak")
        if ind["t_settle"] > ind["t_run"] - REST_S:
            unsettled = (f"in the last {REST_S:g} s the frequency still moved by {ind['move_f']:.2f} Hz and the "
                         f"voltage by {ind['move_u']:.3f} pu")
    ind["restless"] = bool(swinging or unsettled or too_short)     # run_case gives such a run more time
    if too_short:
        ind["notes"].append(f"the run ends {tp[-1] - t_clear:.1f} s after the {'fault is cleared' if faults else 'event'}: "
                            "too short to tell whether everything has come to rest (T_END, T_END_FAULT, T_END_LONGEST)")

    # ---- rotor angle of DG_2 against DG_1, and pole slip (both sets connected throughout)
    both = "speed_dg1" in curves and "speed_dg2" in curves and not {"dg1", "dg2"} & set(tripped)
    slips = []                                             # instants at which a pole slip shows
    for role in ("dg1", "dg2"):
        if f"slip_{role}" in curves and role not in tripped:
            slips += [ti for ti, x in zip(tp, after(curves[f"slip_{role}"])) if x > 0.5][:1]
    if both and "angle_dg2" in curves:
        angle = unwrap(curves["angle_dg2"])
        ind["angle0"] = angle[last]
        apart = [abs(a - angle[last]) for a in after(angle)]
    elif both:                                             # not recorded: from the difference of the two speeds
        apart, angle = [0.0], 0.0
        for i in range(i0 + 1, i0 + n):
            angle += (speeds["dg2"][i] - speeds["dg1"][i]) * 360.0 * (t[i] - t[i - 1])
            apart.append(abs(angle))
        ind["angle_estimated"] = True
    if both:
        ind["angle_swing"] = max(apart)
        slips += [ti for ti, a in zip(tp, apart) if a >= 180.0][:1]
    ind["slip"] = bool(slips)

    # ---- powers (kW, kvar) and currents (multiples of the rating): before, extremes, end
    power = {}
    for role in ("dg1", "dg2", "bess", "pv", "load"):
        p = total_kw(curves, "p_" + role, point.get(role))
        if p:
            power[role], pp = p, after(p)
            ind[f"p_{role}_0"], ind[f"p_{role}_max"], ind[f"p_{role}_min"], ind[f"p_{role}_end"] = \
                p[last], max(pp), min(pp), pp[-1]
    for role in ("dg1", "dg2", "bess", "pv", "load"):
        q = total_kw(curves, "q_" + role)
        if q:
            qq = after(q)
            ind[f"q_{role}_0"], ind[f"q_{role}_max"], ind[f"q_{role}_min"], ind[f"q_{role}_end"] = \
                q[last], max(qq), min(qq), qq[-1]
    for role in ("dg1", "dg2", "bess", "pv", "l1"):
        series = [after(curves[k]) for k in curves if k.startswith("i_" + role) and isinstance(curves[k], list)]
        if series:
            ind[f"i_{role}_max"], ind[f"i_{role}_end"] = max(max(s) for s in series), max(s[-1] for s in series)

    def at(when):
        return min(range(len(t)), key=lambda i: abs(t[i] - when))

    def plant_kw(role, when, before=False):
        """Power of the PV plant or the battery at an instant (or before the event), or
        None if it is disconnected by that instant."""
        if not connected(role, when + 0.01):
            return None
        if role not in power:               # not recorded: in service it carries what the load flow gave
            return point[role] if role == "bess" or point[role] > 1.0 else None
        return power[role][last if before else at(when)]

    def surplus(when):
        """kW by which PV and battery feed more than the load takes at an instant (None if unknown)."""
        if "load" not in power:
            return None
        return sum(plant_kw(role, when) or 0.0 for role in ("bess", "pv")) - power["load"][at(when)]

    # ---- did the disturbance take effect in the simulation?
    missing = []
    ind["fault_seen"] = True
    for e in spec:
        if e[0] == "trip":
            when = T_EVENT + (e[2] if len(e) > 2 else 0.0)
            p = total_kw(curves, "p_l1") if e[1] == "L1" else power.get("load" if e[1] == "L2" else e[1])
            later = [i for i, ti in enumerate(t) if ti >= when + 0.1]
            if p and later and abs(p[last]) >= 1.0 and abs(p[later[0]]) > max(0.05 * abs(p[last]), 1.0):
                missing.append(f"{ROLE_TEXT[e[1]]} still carries {abs(p[later[0]]):.0f} kW after its trip")
        elif e[0] == "load_step_pct":
            p, ul = power.get("load"), curves.get("u_load")
            later = [i for i, ti in enumerate(t) if ti >= T_EVENT + 0.05]
            if p and later and abs(p[last]) >= 1.0:
                i1 = later[0]
                seen = [(p[i1] - p[last]) / abs(p[last]) * 100.0]              # as power ...
                if ul and ul[last] > 0.1 and ul[i1] > 0.1:                      # ... and as admittance
                    seen.append((p[i1] / ul[i1] ** 2 - p[last] / ul[last] ** 2) / abs(p[last] / ul[last] ** 2) * 100.0)
                # the step has to be about the size asked for, as power or (constant impedance) as admittance
                if not any(abs(s - e[1]) <= max(0.3 * abs(e[1]), 2.0) for s in seen):
                    missing.append(f"the load changed by {seen[0]:+.1f} % instead of {e[1]:+.0f} %")
        elif e[0] == "fault":
            window = [i for i, ti in enumerate(t) if T_EVENT - 1e-9 <= ti <= T_EVENT + e[2] + 1e-9]
            lows = [min(curves["u_" + k][i] for i in window) for k in buses] if len(window) >= 3 else []
            # a bolted three-phase fault takes some busbar below 0.5 pu; one through an impedance, or an
            # unbalanced one (u is the positive-sequence voltage), at least lowers it
            balanced = len(e) < 4 or e[3] == "3ph"
            floor = (0.5 if balanced and not (FAULT_R_OHM or FAULT_X_OHM)
                     else min(ind[f"u0_{k}"] for k in buses) - 0.02)
            if lows and min(lows) > floor:
                ind["fault_seen"] = False
                missing.append(f"no voltage dip during the fault (lowest {min(lows):.2f} pu)")
    # ---- what the case did not ask for: a unit that drops out (DG_2 may well stop when half the load
    # goes) or a load that moves. Noted, and the island is judged on what stays: whether it keeps
    # supplying the load with its voltage and frequency inside the limits
    unasked = []
    if not faults:                                  # in a fault the inverters drop out of themselves
        soon = [i for i, ti in enumerate(t) if T_EVENT + 0.002 <= ti <= T_EVENT + 0.05]
        for role in ("bess", "pv", "dg2"):
            p = power.get(role)
            amps = [curves[k] for k in curves if k.startswith("i_" + role) and isinstance(curves[k], list)]
            if p and soon and role not in tripped and abs(p[last]) >= 5.0 and dropped_out(p, amps, last, soon):
                unasked.append(f"{ROLE_TEXT[role]} drops out at the event, though this case does not trip it")
    # the load moves only with voltage and frequency: compared just after the event, or once the fault
    # is cleared, with the voltage back and the frequency near 50 Hz
    p, ul = power.get("load"), curves.get("u_load")
    since = t_clear + 0.3 if faults else T_EVENT + 0.05
    later = [i for i in range(i0, end + 1) if t[i] >= since and (not ul or ul[i] > 0.8)
             and f[i] is not None and abs(f[i] - F_NOM) < 2.0]
    if (p and later and abs(p[last]) >= 1.0 and not {"load", "L1", "L2"} & set(tripped)
            and not any(e[0] == "load_step_pct" for e in spec)):
        i1 = later[0]
        seen = [(p[i1] - p[last]) / abs(p[last]) * 100.0]                      # as power and as admittance
        if ul and ul[last] > 0.1 and ul[i1] > 0.1:
            seen.append((p[i1] / ul[i1] ** 2 - p[last] / ul[last] ** 2) / abs(p[last] / ul[last] ** 2) * 100.0)
        if min(abs(s) for s in seen) > 10.0:
            unasked.append(f"the load changes by {seen[0]:+.1f} % {'after the fault' if faults else 'at the event'}, "
                           "though this case has no load event")
    ind["notes"] += unasked
    ind["not_applied"] = missing

    # ---- protection the model does not contain: what would act first on these curves?
    acts = []                                              # (time, what trips, why, kW it carried before)
    checks = [(hold_time(tp, [x < F_UNDER for x in fp], T_UNDER), f"under-frequency (below {F_UNDER:g} Hz for {T_UNDER:g} s)"),
              (hold_time(tp, [x > F_OVER for x in fp], T_OVER), f"over-frequency (above {F_OVER:g} Hz for {T_OVER:g} s)")]
    checks += [(hold_time(tp, [x < limit for x in ump], hold), f"under-voltage (below {limit:g} pu for {hold:g} s)")
               for limit, hold in U_UNDER_STEPS]
    checks += [(hold_time(tp, [x > limit for x in ump], hold), f"over-voltage (above {limit:g} pu for {hold:g} s)")
               for limit, hold in U_OVER_STEPS]
    checks = [c for c in checks if c[0] is not None]
    ind["inverters"] = "none connected"
    if any(plant_kw(role, tp[-1]) is not None for role in ("bess", "pv")):
        ind["inverters"] = "no assumed setting reached"
    if checks:
        when, why = min(checks)
        # what a plant loses by tripping is what it carried before the event: at the instant of
        # the trip its power may be next to nothing because of the fault itself
        state = {role: plant_kw(role, when, before=True) for role in ("bess", "pv")}
        carried = {role: kw for role, kw in state.items() if kw is not None and abs(kw) > 1.0}
        idle = [role for role, kw in state.items() if kw is not None and abs(kw) <= 1.0]
        for role, kw in carried.items():
            acts.append((when, role, why, kw))
        if idle and not carried:
            names_idle = " and ".join("battery" if role == "bess" else "PV" for role in idle)
            ind["inverters"] = f"idle {names_idle} inverters disconnect on {why}, {when - T_EVENT:.2f} s after the event"
        elif carried:
            ind["inverters"] = f"trip on {why}"
    for role, hit in over.items():                         # a set shuts down a little after it passes its limit
        when = t[hit] + OVERSPEED_TRIP_S
        if role not in tripped and when < tp[-1] - 1e-9:
            acts.append((when, role, f"over-speed (above {OVERSPEED_HZ:g} Hz)", power[role][last] if role in power else 0.0))
    if acts:
        first = min(a[0] for a in acts)
        if not slips or first < min(slips):                # once the sets are out of step there is nothing to add
            ind["protection"] = [a for a in acts if a[0] <= first + 1e-9]

    # ---- notes on the end state and on what the model leaves out
    beyond = []                                            # end states beyond a rating: outside the limits
    for role in ("dg1", "dg2"):
        if role not in power or not connected(role, tp[-1]) or ended:
            continue
        data, p_end = GENSET_DATA[role], ind[f"p_{role}_end"]
        if p_end > 1.02 * data["standby_kw"]:
            beyond.append(f"{names[role]} ends at {p_end:.0f} kW, above its standby rating of {data['standby_kw']:.0f} kW")
        elif p_end > 1.02 * data["prime_kw"]:
            ind["notes"].append(f"{names[role]} ends at {p_end:.0f} kW, above its prime rating of {data['prime_kw']:.0f} kW")
        reverse = REVERSE_FRACTION * data["standby_kw"]
        spell = longest_spell(tp, [x < -reverse for x in after(power[role])])
        if role == "dg1":
            ind["reverse_s"] = spell
        if spell >= REVERSE_S:
            ind["notes"].append(f"{names[role]} takes more than {reverse:.0f} kW of reverse power for {spell:.1f} s: "
                                "its reverse-power relay would trip it (not emulated)")
        elif p_end < -10.0:
            ind["notes"].append(f"{names[role]} is left driven as a motor ({-p_end:.0f} kW, below the {reverse:.0f} kW "
                                "of its reverse-power relay)")
    for role, label in (("dg1", names["dg1"]), ("dg2", names["dg2"]), ("bess", "the battery"), ("pv", "the PV plant"),
                        ("l1", "the cable L_1")):
        if ind.get(f"i_{role}_end", 0.0) > 1.02 and not ended and (role in ("l1",) or connected(role, tp[-1])):
            text = f"{label} ends at {ind[f'i_{role}_end']:.2f} times its rated current"
            (ind["notes"] if role in ("bess", "pv") else beyond).append(text)
    # load taken up in one step, against the ISO 8528-5 guide: at the event and at every trip
    ind["best_case"] = False
    instants = sorted(set(([] if faults else [T_EVENT]) + list(tripped.values())))
    for role in ("dg1", "dg2"):
        if ended is not None or role not in power or not connected(role, tp[-1]):
            continue
        prime, worst = GENSET_DATA[role]["prime_kw"], None
        for when in instants:
            # the load before it: just before that instant, or before the event while a fault is still on
            before = power[role][last if when <= t_clear + 0.05 else max(at(when - 0.02), last)]
            window = [x for ti, x in zip(t, power[role]) if when < ti <= min(when + 1.0, tp[-1])]
            allowed = guide_step(before / prime * 100.0)
            if not window or allowed is None:
                continue
            step = (max(window) - before) / prime * 100.0
            if step > allowed + 2.0 and (worst is None or step - allowed > worst[0] - worst[1]):
                worst = (step, allowed, before)
        if worst:
            step, allowed, before = worst
            ind["best_case"] = True
            ind["notes"].append(
                f"best case: {names[role]} takes up {step / 100 * prime:.0f} kW within a second ({step:.0f} % of its "
                f"prime rating, from {before / prime * 100:.0f} %); the ISO 8528-5 guide for an engine like this is "
                f"about {allowed:.0f} % in one step at that load, and the governor model has no turbocharger limit "
                "to hold it to that. A real engine dips further")
    # DG_2 on its droop set-point against an unloaded DG_1
    if (ended is None and "dg1" in power and "dg2" in power and connected("dg1", tp[-1]) and connected("dg2", tp[-1])
            and fp[-1] > F_NOM + 0.5 and ind["p_dg1_end"] < 0.03 * GENSET_DATA["dg1"]["prime_kw"] < ind["p_dg2_end"]):
        ind["notes"].append(f"{names['dg2']} holds its set-point on its droop and pushes {names['dg1']} down to "
                            f"{ind['p_dg1_end']:.0f} kW, so the frequency stays at {fp[-1]:.2f} Hz: the load sharing "
                            "of the genset controls, which would lower that set-point, is not modelled")
    # a collapse that is a matter of power balance
    if ended == "collapse" and "load" in power:
        able = sum(GENSET_DATA[role]["standby_kw"] for role in speeds if connected(role, tp[-1]))
        able += sum(max(plant_kw(role, tp[-1], before=True) or 0.0, 0.0) for role in ("bess", "pv"))
        if abs(ind["p_load_0"]) > able + 1.0:
            ind["notes"].append(f"what is left can deliver about {able:.0f} kW (standby ratings, plus what PV and "
                                f"battery fed) against a load of {abs(ind['p_load_0']):.0f} kW: a matter of power "
                                "balance, since no load shedding is modelled")
    high = hold_time(tp, [x > F_REDUCE and ti > t_clear + 0.1 for ti, x in zip(tp, fp)], 0.5)
    fed = high is not None and (surplus(high) or 0.0) > 1.0
    if fed and ended != "over-speed":
        ind["notes"].append(f"above {F_REDUCE:g} Hz PV and battery still feed more than the load takes: as modelled they "
                            "hold their power, where the grid code asks for a reduction, so this rise in frequency "
                            "is the island without that response")

    # ---- outcome, by the definition of the IEEE task force on microgrid stability [1]
    off = []
    if not inside_f[-1]:
        off.append(f"the frequency settles at {ind['f_end']:.2f} Hz, outside {F_NOM * (1 - F_BAND):g} to "
                   f"{F_NOM * (1 + F_BAND):g} Hz")
    for k in live:
        if not acceptable(ind[f"uend_{k}"], ind[f"u0_{k}"]):
            off.append(f"the {BUS_TEXT[k].lower()} settles at {ind[f'uend_{k}']:.3f} pu, outside {U_LOW:g} to {U_HIGH:g} pu")
    off += beyond
    after_run = f"{ind['t_run']:.0f} s after the event"
    restless = ([text + " " + after_run for text in swinging]
                or ([f"no steady state {after_run}: {unsettled}"] if unsettled else []))
    meant = "load" in tripped or any(e[0] == "load_step_pct" and e[1] <= -95.0 for e in spec)
    gone = "load" in power and abs(ind["p_load_0"]) >= 1.0 and abs(ind["p_load_end"]) < 0.05 * abs(ind["p_load_0"])
    load_lost = not meant and (gone or "load" in dead)
    cut = [role for role in ("L1", "L2") if role in tripped]       # a circuit opened to clear a fault
    sets_text = ("the gensets ride through the loss of their load (highest "
                 f"{ind['f_max']:.2f} Hz, {ind['f_end']:.2f} Hz at the end)" if not off and not restless else
                 "what stays energised does not settle inside the limits either: " + "; ".join(off + restless))

    def result(verdict, why, kind=""):
        ind["verdict"], ind["why"], ind["class"] = verdict, why, kind

    if ended == "over-speed" and tp[-1] > t_clear + 0.05 and (surplus(tp[-1]) or 0.0) > 1.0:
        result("unstable as modelled",
               f"the gensets pass their over-speed shutdown ({OVERSPEED_HZ:g} Hz) {ind['t_end']:.2f} s after the event, "
               f"because PV and battery keep feeding {surplus(tp[-1]):.0f} kW more than the load takes. As modelled "
               f"they keep to their set-points. The grid code asks an inverter for less power above {F_REDUCE:g} Hz, "
               f"but only a reduction that is complete within these {ind['t_end']:.2f} s would prevent the shutdown, "
               f"and the {T_OVER:g} s of the over-frequency trip is far too slow for it", "seen in the frequency")
    elif ended == "over-speed":
        result("unstable", f"blackout: the last genset running passes its over-speed shutdown ({OVERSPEED_HZ:g} Hz) "
               f"{ind['t_end']:.2f} s after the event", "seen in the frequency")
        if faults and tp[-1] <= t_clear + 0.3 and net.get("no_rstr"):
            ind["notes"].append("the type of " + " and ".join(net["no_rstr"]) + " has no stator resistance, so the "
                                "fault takes no power from the set and it speeds up as fast as it can: enter the "
                                "resistance from the data sheet before relying on this result")
    elif ended == "collapse":
        result("unstable", f"blackout: the frequency falls below {BLACKOUT_HZ:g} Hz {ind['t_end']:.2f} s after the event",
               "seen in the frequency")
    elif ended == "no set":
        result("unstable", "blackout: no genset is left to form the grid", "seen in the frequency")
    elif problem and cut and abs(t[-1] - tripped[cut[0]]) <= 0.05:
        result("supply lost", f"tripping {ROLE_TEXT[cut[0]]} isolates the fault but leaves what lies behind it without "
               "a source that forms the grid: all load is lost. PowerFactory ended the simulation there, so what the "
               "gensets do after losing their load is not shown")
    elif problem:
        result("no result", problem)
    elif missing:
        result("no result", "the disturbance did not take effect in the simulation (" + "; ".join(missing)
               + "): see PowerFactory's messages for the events"
               + (". TRIP_WITH chooses between opening breakers and taking the element out of service"
                  if any("after its trip" in m for m in missing) else ""))
    elif max(ind[f"uend_{k}"] for k in buses) < 0.5:
        result("unstable", "the voltage does not come back: every busbar ends below 0.5 pu (check in PowerFactory's "
               "messages that the fault was cleared)", "seen in the voltage")
    elif ind["slip"]:
        result("unstable", f"out of step: the two sets lose synchronism (pole slip {min(slips) - T_EVENT:.2f} s after "
               "the event). [1] p.6 notes that this has not been observed in microgrids: check the inertia and the "
               "stator resistance of the sets before relying on it", "seen in the rotor angle")
    elif dead and cut:
        result("supply lost", f"tripping {ROLE_TEXT[cut[0]]} isolates the fault but leaves the "
               + " and the ".join(BUS_TEXT[k].lower() for k in dead) + " without a source that forms the grid: all "
               "load there is lost. That is the single circuit, not a loss of stability; " + sets_text)
    elif dead:
        result("unstable", "the voltage collapses at the " + " and the ".join(BUS_TEXT[k].lower() for k in dead),
               "seen in the voltage")
    elif faults and ("load" in tripped or (cut and (gone or ("L2" in cut and "load" not in power)))):
        result("supply lost", "clearing the fault disconnects " + ("the load" if "load" in tripped else ROLE_TEXT[cut[0]])
               + ", and all the load with it: there is one feeder only. That is the layout, not a loss of "
               "stability; " + sets_text)
    elif load_lost:
        result("unstable", "all load is lost")
    elif swinging:
        result("unstable", "; ".join(restless) + " (check the tuning of the governors and voltage regulators)",
               "seen as a lasting oscillation")
    elif unsettled:
        result("unstable", restless[0], "seen in the frequency" if ind["move_f"] > REST_F_HZ else "seen in the voltage")
    elif off:
        result("outside limits", "; ".join(off), "seen in the frequency" if not inside_f[-1] else
               "seen in the voltage" if len(off) > len(beyond) else "")
    else:
        result("stable", "")
    if ind["verdict"] not in ("stable", "outside limits", "supply lost") or problem:
        for key in ["f_recovery", "u_recovery", "t_settle"] + [f"urec_{k}" for k in buses]:
            ind[key] = None                                     # recovery times mean nothing here
    return ind


# ============================================================================
# ONE CASE, WITH THE PROTECTION TRIPS ITS CURVES CALL FOR
# ============================================================================
PLANT_TEXT = {"bess": "battery inverters", "pv": "PV inverters"}


def show_messages(sim, name, why):
    """Repeat the run that is still set up, with PowerFactory's own messages on screen."""
    if sim["shown"] >= 2:
        return
    sim["shown"] += 1
    log(f"   {name}: {why}")
    log("   The run is repeated once with PowerFactory's messages shown:")
    if run_command(sim["inc"], quiet=True):
        run_command(sim["sim"], quiet=False)


def run_case(sim, net, cid, spec, point, t_end, name=None):
    """One case: the run, again with every protection trip its curves call for (an
    inverter on its frequency or voltage settings, a genset on over-speed), and again
    for longer if it has not come to rest by its end.
    Returns (indicators, curves of the last run or None)."""
    spec = list(spec)
    name = name or f"Stability {cid}"
    history, marks, moved, fell_back, curves, t_first = [], [], False, False, None, t_end

    def on_cable(events):
        return any(e[0] == "fault" and e[1] == "L1" for e in events)

    def at_main_board(events):
        return [("fault", "main") + tuple(e[2:]) if e[0] == "fault" and e[1] == "L1" else e for e in events]

    if sim.get("no_cable_fault") and on_cable(spec):
        spec, moved = at_main_board(spec), True
    for _ in range(12):
        try:
            curves, said, tripped, problem = run_rms(sim, net, name, spec, t_end, point)
        except CaseProblem as why:
            return dict(problem=str(why), verdict="no result", why=str(why), notes=[], events=[], setup=True,
                        skipped=isinstance(why, Skip)), None
        if curves is None:
            ind = dict(problem=problem, verdict="no result", why=problem, notes=[])
        else:
            ind = indicators(curves, net, point, spec, tripped, problem)
        ind["events"] = said
        if on_cable(spec) and (curves is None or not ind.get("fault_seen", True)):
            # PowerFactory did not put the short-circuit on the cable: try the cable's main-board end
            spec, moved, fell_back = at_main_board(spec), True, True
            continue
        acts = ind.get("protection")
        if curves is not None and not acts and ind.get("restless") and 2 * t_end <= T_END_LONGEST + 1e-9:
            t_end *= 2                              # not at rest yet: the same run, twice as long
            continue
        if curves is None or not acts:
            break
        if not EMULATE_TRIPS:
            ind["notes"].append("protection would act and is not in this run (EMULATE_TRIPS is off): " + "; ".join(
                f"{PLANT_TEXT.get(role, net[role].loc_name if role in net else role)} on {why} "
                f"{when - T_EVENT:.2f} s after the event" for when, role, why, _ in acts))
            break
        for when, role, why, kw in acts:
            spec.append(("trip", role, when - T_EVENT))
            marks.append((when - T_EVENT, role))
            if role in PLANT_TEXT:
                doing = "charging" if kw < 0 else "delivering"
                history.append(f"{PLANT_TEXT[role]} trip on {why} {when - T_EVENT:.2f} s after the event "
                               f"({doing} {abs(kw):.0f} kW before it)")
            else:
                history.append(f"{net[role].loc_name} shuts down on {why} {when - T_EVENT:.2f} s after the event "
                               f"({kw:.0f} kW before it)")
    if fell_back and curves is None:            # the main-board end did not work either: not a matter of the cable
        moved = False
    elif fell_back:
        sim["no_cable_fault"] = True            # remembered: the other cases on the cable go there at once
    ind["cascade"] = history
    ind["trip_marks"] = marks
    ind["moved"] = moved
    if t_end > t_first:
        ind["notes"].append(f"the run was extended from {t_first:g} to {t_end:g} s, to let everything come to rest")
    if moved:
        ind["notes"].append(f"PowerFactory did not take a short-circuit along {net['l1'].loc_name}; the fault was "
                            f"put at its main-board end ({net['bus']['main'].loc_name}) instead")
    if history and ind["verdict"] == "stable":
        ind["verdict"] = "stable after a protection trip"
    if any(role in PLANT_TEXT for _, role in marks):
        ind["inverters"] = "trip (emulated: see table 1)"
    if ind["verdict"] == "no result" and curves is not None:
        show_messages(sim, name, ind.get("why", ""))
    return ind, curves


def critical_clearing_time(sim, net, point, where):
    """The longest temporary three-phase fault at `where` after which the island is
    stable with nothing tripped, by bisection between the two ends of CCT_RANGE_S (on
    the understanding that a fault longer than one that fails also fails). Returns a
    dict: lo (the longest fault tried that ends stable, s, or None), hi (the shortest
    one that does not, or None), at_lo and at_hi (the outcome there), note, moved (the
    fault had to be put at the main board instead of on the cable) and the number of
    cases run."""
    out = dict(lo=None, hi=None, at_lo="", at_hi="", note="", moved=False, runs=0)

    def run(duration):
        ind, _ = run_case(sim, net, "CCT", [("fault", where, duration)], point, T_END_FAULT,
                          name="Stability CCT search")
        out["moved"] = out["moved"] or bool(ind.get("moved"))
        out["runs"] += 1
        return ind

    def keep(end, duration, ind):
        out[end], out["at_" + end] = duration, outcome_text(ind, notes=False)
        out["best_case"] = out.get("best_case") or bool(ind.get("best_case"))

    lo, hi = CCT_RANGE_S
    ind = run(lo)
    if ind["verdict"] == "no result":
        out["note"] = "no result: " + ind.get("why", "")
        return out
    if ind["verdict"] != "stable":
        keep("hi", lo, ind)
        return out
    keep("lo", lo, ind)
    ind = run(hi)
    if ind["verdict"] == "no result":
        out["note"] = f"no result for a fault of {hi * 1000:.0f} ms: " + ind.get("why", "")
        return out
    if ind["verdict"] == "stable":
        keep("lo", hi, ind)
        return out
    keep("hi", hi, ind)
    while out["hi"] - out["lo"] > 0.011:
        mid = round(0.5 * (out["lo"] + out["hi"]), 3)
        ind = run(mid)
        if ind["verdict"] == "no result":
            out["note"] = f"search ended early: no result for a fault of {mid * 1000:.0f} ms ({ind.get('why', '')})"
            break
        keep("lo" if ind["verdict"] == "stable" else "hi", mid, ind)
    return out


# ============================================================================
# SELF-TEST OF THE CONTROLLERS, AND THE ROUTES TO A SET THAT PASSES IT
# ============================================================================
def test_points():
    """[(operating point, the sets that run there)]: one with DG_1 alone and one with
    both sets, without PV and battery if there is such a point."""
    def pick(both):
        fits = [op for op, o in OPS.items() if (o["dg2"] > 0) == both]
        plain = [op for op in fits if OPS[op]["pv"] == 0 and OPS[op]["bess"] == 0]
        return (plain or fits or [None])[0]

    return [(op, roles) for op, roles in ((pick(False), ("dg1",)), (pick(True), ("dg1", "dg2"))) if op]


def judge_test(net, ind, curves, roles):
    """What one self-test run shows. Returns (kind, lines, rings):
    kind   "passed", "f" (an oscillation that shows in the frequency: the governor), "u"
           (in the voltage: the regulator) or "failed"
    lines  what was seen, and why it is not passed
    rings  "f" or "u" if the island rides through but swings more than a well-damped loop
           does (more than RING_TURNS reversals), else "".
    The test is about the controllers: a steady start, a governor and a regulator that
    act, no collapse, at rest at the end, the frequency back inside its band and the
    genset busbar back where it was. A load terminal or a current that ends outside the
    limits a scenario is held to belongs to the operating point: reported, not counted."""
    if curves is None or ind.get("verdict") == "no result":
        return "failed", [ind.get("why") or "the run gave no result"], ""
    lines, dead = [], []
    if "f_min" in ind:
        lines.append(f"frequency: lowest {ind['f_min']:.2f} Hz {ind['t_fmin']:.2f} s after the step, highest "
                     f"{ind['f_max']:.2f} Hz, {ind['f_end']:.2f} Hz at the end"
                     + (f"; at rest after {ind['t_settle']:.1f} s" if ind.get("t_settle") is not None else ""))
    if "umin_gen" in ind:
        lines.append(f"voltage at the genset busbar: {ind['u0_gen']:.3f} pu before, lowest {ind['umin_gen']:.3f}, "
                     f"highest {ind['umax_gen']:.3f}, {ind['uend_gen']:.3f} at the end")
    for role in roles:
        name, pt, ve = net[role].loc_name, curves.get(f"pt_{role}"), curves.get(f"ve_{role}")
        if pt:
            lines.append(f"{name}: turbine power {pt[0]:.3f} > {pt[-1]:.3f} pu (between {min(pt):.3f} and {max(pt):.3f})"
                         + (f", excitation voltage {ve[0]:.3f} > {ve[-1]:.3f} pu" if ve else ""))
            if max(pt) - min(pt) < 1e-4:
                dead.append(f"the turbine power of {name} does not move at all: its governor does not act on the machine")
        if ve and max(ve) - min(ve) < 1e-5:
            dead.append(f"the excitation voltage of {name} does not move at all: its voltage regulator does not act "
                        "on the machine")
    problems = list(dead)
    if ind.get("steady") is False:
        problems.append("frequency or voltage already moves in the second before the step: the initial conditions "
                        "are not a steady state")
    rides = ind.get("verdict") in ("stable", "outside limits")
    if not rides:
        problems.append(f"the island does not ride through the step: {outcome_text(ind, notes=False)}")
    else:
        so_far = len(problems)
        if abs(ind["f_end"] - F_NOM) > F_BAND * F_NOM:
            problems.append(f"the frequency settles at {ind['f_end']:.2f} Hz: the governors do not bring it back to "
                            f"within {F_BAND * 100:g} % of {F_NOM:g} Hz")
        if "uend_gen" in ind and abs(ind["uend_gen"] - ind["u0_gen"]) > U_HELD:
            problems.append(f"the genset busbar settles at {ind['uend_gen']:.3f} pu, having started at "
                            f"{ind['u0_gen']:.3f}: the voltage regulators do not hold it")
        if ind["verdict"] == "outside limits" and len(problems) == so_far:
            lines.append("at this operating point something ends outside the limits a scenario is held to, which is "
                         f"not held against the controllers: {ind.get('why', '')}")
    t, f = curves["t"], [x * F_NOM for x in curves.get("speed_dg1", [])]
    after = [i for i, ti in enumerate(t) if ti >= T_EVENT]
    u = curves.get("u_gen") or curves.get("u_main") or []
    turns_f = motion([f[i] for i in after], OSC_F_HZ / 5)[1] if f and after else 0
    turns_u = motion([u[i] for i in after], OSC_U_PU / 5)[1] if u and after else 0
    rings = "f" if turns_f > RING_TURNS else "u" if turns_u > RING_TURNS else ""
    if not problems:
        if rings:
            lines.append(f"poorly damped: the {'frequency' if rings == 'f' else 'voltage'} reverses "
                         f"{turns_f if rings == 'f' else turns_u} times before it rests")
        return "passed", lines, rings
    kind = "failed"
    if not dead and not rides:
        if ind.get("osc_f", 0.0) > OSC_F_HZ or turns_f > RING_TURNS:
            kind = "f"
        elif ind.get("osc_u", 0.0) > OSC_U_PU or turns_u > RING_TURNS:
            kind = "u"
    return kind, lines + ["NOT PASSED: " + text for text in problems], ""


def self_test(sim, case, ldf, net, changes):
    """A small load step with DG_1 alone and with both sets, simulated by PowerFactory.
    If a point oscillates, or swings for long, and the script has set the controllers up
    itself, it runs the point again with less gain in the controller concerned (RETUNE)
    and keeps whichever value does best: its own value unless another one is better.
    Returns (passed, runs, why not)."""
    log(f"\n=== Self-test of the controllers: the load rises by {SELF_TEST_STEP_PCT:g} % in one step ===")
    spec = [("load_step_pct", SELF_TEST_STEP_PCT)]
    runs, comps = [], composite_models(net)
    sim["all_lines"] = True

    def run(n, op, roles, who, point):
        sim["first"], sim["init_lines"] = True, []
        try:
            curves, said, tripped, problem = run_rms(sim, net, f"Stability self-test {n}", spec, SELF_TEST_END, point)
        except CaseProblem as why:
            curves, said, tripped, problem = None, [], {}, str(why)
        if curves is None:
            ind = dict(problem=problem, verdict="no result", why=problem, notes=[])
        else:
            ind = indicators(curves, net, point, spec, tripped, problem)
        ind["events"] = said
        kind, lines, rings = judge_test(net, ind, curves, roles)
        ind["rings"] = rings
        log(f"   Test {n}, {who} ({op}): " + ("passed" if kind == "passed" else "not passed"))
        for line in lines:
            log("      " + line)
        for line in sim.get("init_lines") or []:
            log("      PowerFactory: " + line)
        return dict(kind=kind, lines=lines, rings=rings, ind=ind, curves=curves)

    def rank(got):                                  # 0: passed and well damped, 1: passed, 2: not passed
        return 2 if got["kind"] != "passed" else 1 if got["rings"] else 0

    try:
        for n, (op, roles) in enumerate(test_points(), 1):
            if len(roles) > 1 and not usable(plant_state(net["dg2"], comps)):
                log(f"\n   {net['dg2'].loc_name} has no governor or no voltage regulator: the test with both sets is left out")
                continue
            who = f"{net['dg1'].loc_name} alone" if len(roles) == 1 else "both sets"
            target = roles[-1]                      # whose gain is changed if this point swings
            point = set_operating_point(case, ldf, net, changes, op)
            got = run(n, op, roles, who, point)
            swings = got["kind"] if got["kind"] in ("f", "u") else got["rings"]
            if swings and _plant["route"]:
                key = (target, "gov" if swings == "f" else "avr")
                what = f"{net[target].loc_name}'s {'governor' if swings == 'f' else 'voltage regulator'}"
                log(f"      {'An oscillation' if got['kind'] != 'passed' else 'A poorly damped swing'} that shows in the "
                    f"{'frequency' if swings == 'f' else 'voltage'}: the test is run again with less gain in {what}.")
                tried = [(1.0, got)]
                for factor in RETUNE:
                    _plant["gain"][key] = factor
                    rewrite_gains(net, target)
                    log(f"      {what}: {factor:g} times the script's value")
                    tried.append((factor, run(n, op, roles, who, point)))
                    if rank(tried[-1][1]) == 0:
                        break
                best = min(range(len(tried)), key=lambda i: (rank(tried[i][1]), i))
                factor, got = tried[best]
                if factor == 1.0:
                    del _plant["gain"][key]
                else:
                    _plant["gain"][key] = factor
                if best != len(tried) - 1:          # not the value the model holds now: write it, and show its run
                    rewrite_gains(net, target)
                    if rank(got) < 2:
                        got = run(n, op, roles, who, point)
                log("      " + (f"Kept: {what} at {factor:g} times the script's value." if factor != 1.0 else
                                f"Less gain does not do better: {what} stays at the script's value."))
            runs.append(dict(case=f"T{n}", op=op, text=f"Self-test: load pickup of {SELF_TEST_STEP_PCT:g} %, {who}",
                             who=who, spec=spec, ind=got["ind"], curves=got["curves"], point=point))
            if got["kind"] != "passed":
                lines = got["lines"]
                why = next((line[12:] for line in lines if line.startswith("NOT PASSED: ")), lines[-1] if lines else "")
                return False, runs, f"test {n} ({who}, {op}): {why}"
    finally:
        sim["all_lines"] = False
        sim["first"] = True
    return True, runs, ""


def take_back():
    """After a set-up that did not prove itself: undo it, or say that it stays."""
    if not (_plant["created"] or _plant["undo"]):
        return
    if not UNDO_IF_FAILED:
        log("\nThe last attempt is left in the model (UNDO_IF_FAILED is off): open the composite models of the gensets "
            "in PowerFactory to look at it.")
        return
    n, failed = undo_plant()
    if failed:
        log(f"\nWhat the script did to the plant models is taken back as far as PowerFactory allowed ({n} steps; "
            f"{failed} could not be: see the warning above).")
    elif n:
        log(f"\nEverything the script did to the plant models is taken back ({n} steps): they are as it found them.")


def controllers_step(sim, case, ldf, net, changes, ready):
    """Set the controllers up (unless CONTROLLERS = "keep") and test them. Tries the
    routes in turn; if none gives a set that passes, everything is taken back and the
    run stops. The same if the run ends for any other reason before a set has passed:
    nothing half-built stays behind. Returns the self-test runs (for the plots)."""
    routes = {"auto": ["own", "library"], "library": ["library"], "own": ["own"]}.get(CONTROLLERS, [None])
    failures, runs = [], []
    try:
        for route in routes:
            label = {"library": "DIgSILENT's library models", "own": "the script's own models",
                     None: "the models in the network"}[route]
            try:
                if route:
                    set_up_controllers(net, route)
                    comps = composite_models(net)
                    for role in ("dg1", "dg2"):
                        ready[role] = usable(plant_state(net[role], comps))
                elif not ready["dg1"] and REQUIRE_CONTROLLERS:
                    raise Stop(f"{net['dg1'].loc_name} has no governor or no voltage regulator in service, so the "
                               "island cannot hold its frequency and voltage in a simulation. Set CONTROLLERS = "
                               "\"auto\" and the script sets them up itself (or add them by hand as listed above; if "
                               "they are there and the script did not recognise them, set REQUIRE_CONTROLLERS = False).")
                if not SELF_TEST:
                    log("\nThe self-test of the controllers is switched off (SELF_TEST): they are used untested.")
                    _plant["proven"] = True         # your choice: they stay as they are
                    return runs
                passed, runs, why = self_test(sim, case, ldf, net, changes)
                if passed:
                    _plant["proven"] = True
                    log(f"\n   The self-test is passed with {label}.")
                    if route:
                        try:
                            tidy(net)
                        except Exception:                 # only tidying up: never a reason to go on to another route
                            pass
                    return runs
                raise BuildProblem("; ".join([why] + _plant["check"]))
            except BuildProblem as why:
                failures.append(f"{label}: {why}")
                warn(f"{capital(label)} do not give a working set of controllers: {why}")
            except Stop:
                raise
            except Exception as e:                  # a call PowerFactory does not take as expected: the next route
                import traceback
                failures.append(f"{label}: the script failed with {type(e).__name__}: {e}")
                warn(f"Setting up {label} broke off with an error ({type(e).__name__}: {e}):")
                for line in traceback.format_exc().rstrip().split("\n"):
                    log("      " + line)
    except BaseException:                           # a stop for another reason, or an interruption
        if routes != [None] and not _plant["proven"]:
            take_back()
        raise
    if routes != [None]:
        take_back()
    raise Stop("No working governor and voltage regulator: " + "; ".join(failures) + ". The lines above say where it "
               "went wrong; the whole log is saved as stability_rms_log.txt."
               + ("" if routes != [None] else " Set CONTROLLERS = \"auto\" to let the script set them up, or "
                  "SELF_TEST = False to go on with the models as they are."))


# ============================================================================
# COMPARING DISPATCH CONTROLLERS: the island as each controller left it at the same instants
# ============================================================================
INSTANT_COLUMNS = ("instant", "label", "time", "controller", "load_kW", "pv_avail_kW", "pv_kW", "bess_kW",
                   "bess_soc_pct", "DG1_kW", "DG2_on", "DG2_kW")
SHORT_VERDICT = {"stable": "stable", "stable after a protection trip": "stable, trip", "outside limits": "outside",
                 "unstable": "UNSTABLE", "unstable as modelled": "UNSTABLE*", "supply lost": "supply lost",
                 "no result": "no result", "not applicable": "n/a"}


def controller_tag(name):
    """A controller's name as it can stand in a case id and a file name."""
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") or "controller"


def read_instants():
    """{op: operating point} from INSTANTS_FILE, in the order of the file (instant by
    instant, the controllers of one instant together)."""
    if not os.path.isfile(INSTANTS_FILE):
        raise Stop(f"INSTANTS_FILE not found: {INSTANTS_FILE}")
    try:
        with open(INSTANTS_FILE, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except OSError as e:
        raise Stop(f"INSTANTS_FILE cannot be read ({e})")
    missing = [c for c in INSTANT_COLUMNS if not rows or c not in rows[0]]
    if missing:
        raise Stop(f"{INSTANTS_FILE} has no rows or lacks the columns {', '.join(missing)}: write it with "
                   "controller_study.py select")
    ops, tags, names = {}, {}, set()
    for row in rows:
        name, iid = row["controller"].strip(), row["instant"].strip()
        names.add(name)
        if (COMPARE_CONTROLLERS is not None and name not in COMPARE_CONTROLLERS
                or COMPARE_INSTANTS is not None and iid not in COMPARE_INSTANTS):
            continue
        tag = controller_tag(name)
        if tags.setdefault(tag, name) != name:
            raise Stop(f"The controllers '{tags[tag]}' and '{name}' would get the same label in case ids ({tag}): "
                       "give one of them another name in controller_study.py")
        try:
            when = datetime.strptime(row["time"].strip(), "%Y-%m-%d %H:%M")
            num = {key: float(row[key]) for key in INSTANT_COLUMNS[4:]}
        except ValueError as e:
            raise Stop(f"{INSTANTS_FILE}: the row of {name} at {iid} cannot be read ({e})")
        on = num["DG2_on"] > 0.5
        ops[f"{iid}_{tag}"] = dict(
            text=f"{row['label'].strip()}, {name}", instant=iid, label=row["label"].strip(), controller=name,
            tag=tag, time=row["time"].strip(), epoch=to_epoch(when.year, when.month, when.day, when.hour, when.minute),
            load=num["load_kW"], pv=max(num["pv_kW"], 0.0), pv_avail=max(num["pv_avail_kW"], 0.0),
            bess=num["bess_kW"], soc=num["bess_soc_pct"], dg1=num["DG1_kW"], dg2=num["DG2_kW"] if on else 0.0)
    for what, wanted in (("controllers", COMPARE_CONTROLLERS), ("instants", COMPARE_INSTANTS)):
        known = names if what == "controllers" else {r["instant"].strip() for r in rows}
        unknown = sorted(set(wanted or []) - known)
        if unknown:
            warn(f"COMPARE_{what.upper()} names {', '.join(unknown)}, which {INSTANTS_FILE} does not have "
                 f"(it has {', '.join(sorted(known))})")
    if not ops:
        raise Stop("No instant is left to run: check COMPARE_CONTROLLERS and COMPARE_INSTANTS against the file")
    return ops


def points_from_settings():
    """{op: operating point} from POINTS and DISPATCH, point by point, the controllers of one
    point together. A controller whose battery or DG_2 set-point is still empty there is left out."""
    ops, empty = {}, []
    known = list(dict.fromkeys(list(DISPATCH) + list(QDS_RUNNERS)))
    names = [n for n in known if COMPARE_CONTROLLERS is None or n in COMPARE_CONTROLLERS]
    for pid, p in POINTS.items():
        if COMPARE_INSTANTS is not None and pid not in COMPARE_INSTANTS:
            continue
        try:
            when = datetime.strptime(p["time"], "%Y-%m-%d %H:%M")
        except ValueError as e:
            raise Stop(f"POINTS {pid}: the time '{p['time']}' cannot be read, write it as YYYY-MM-DD HH:MM ({e})")
        for name in names:
            d = DISPATCH.get(name, {}).get(pid, {})
            if d.get("bess") is None or d.get("dg2") is None:
                d = dispatch_from_log(name, pid, p)         # the row at the instant of its dispatch log
                if d is None:
                    empty.append(f"{name} at {pid}")
                    continue
                log(f"{name} at {pid}: the dispatch is taken from {d['source']}")
            pv = p["pv_avail"] if d.get("pv") is None else min(max(d["pv"], 0.0), p["pv_avail"])
            ops[f"{pid}_{controller_tag(name)}"] = dict(
                text=f"{p['text']}, {name}", instant=pid, label=p["text"], controller=name, tag=controller_tag(name),
                time=p["time"], epoch=to_epoch(when.year, when.month, when.day, when.hour, when.minute),
                load=p["load"], pv=pv, pv_avail=p["pv_avail"], bess=d["bess"], soc=d.get("soc"), dg1=d.get("dg1"),
                dg2=max(d["dg2"], 0.0))
    for what, wanted, known in (("controllers", COMPARE_CONTROLLERS, set(known)),
                                ("instants", COMPARE_INSTANTS, set(POINTS))):
        unknown = sorted(set(wanted or []) - known)
        if unknown:
            warn(f"COMPARE_{what.upper()} names {', '.join(unknown)}, which the settings do not have "
                 f"(they have {', '.join(sorted(known))})")
    if empty:
        warn("Not run, neither DISPATCH nor a dispatch log in DISPATCH_DIR has the battery and DG_2 at: "
             + ", ".join(empty))
    if not ops:
        raise Stop("No operating point is ready to run: run the dispatch up to the points first, or fill in "
                   "bess and dg2 in DISPATCH (or check COMPARE_CONTROLLERS and COMPARE_INSTANTS)")
    return ops


def suite_applies(events, o):
    """'' if the disturbance means something at this point, else why it does not: tripping a
    source that gives nothing there changes nothing, so the case is not simulated."""
    for e in events:
        if e[0] != "trip":
            continue
        if e[1] == "dg2" and o["dg2"] < SUITE_LEAST_KW:
            return f"{DG2_NAME} was already off"
        if e[1] == "pv" and o["pv"] < SUITE_LEAST_KW:
            return f"PV output was already <{SUITE_LEAST_KW:g}kW"
        if e[1] == "bess" and abs(o["bess"]) < SUITE_LEAST_KW:
            return f"the battery was already idle (<{SUITE_LEAST_KW:g}kW)"
    return ""


def comparison_plan(net):
    """(operating points, cases to run, rows for the cases that are not applicable)."""
    ops = read_instants() if INSTANTS_FILE else points_from_settings()
    suite = [s for s in CASES if CASES_TO_RUN is None or s[0] in CASES_TO_RUN]
    unknown = sorted(set(CASES_TO_RUN or []) - {s[0] for s in CASES})
    if unknown:
        warn("Unknown ids in CASES_TO_RUN: " + ", ".join(unknown) + " (CASES has "
             + ", ".join(s[0] for s in CASES) + ")")
    cases, skipped = [], []
    for op, o in ops.items():
        for sid, text, events in suite:
            cid, label = f"{sid}_{op}", f"{text}, {o['label']}, {o['controller']}"
            why = suite_applies(events, o)
            if not why and net["l1"] is None and any(e[1] == "L1" for e in events):
                why = "no cable L_1 was found"
            if why:
                skipped.append(dict(case=cid, op=op, text=label, spec=events, curves=None, point=None,
                                    ind=dict(problem=None, verdict="not applicable", why=why, notes=[])))
            else:
                cases.append((cid, op, label, events))
    return ops, cases, skipped


def char_refs(obj):
    """The time-characteristic references (ChaRef) attached to an element: PowerFactory keeps
    each one inside the element, named after the parameter it drives (plini, scale0, pgini ...)."""
    for get in (lambda: obj.GetContents("*.ChaRef"), lambda: obj.GetChildren(1, "*.ChaRef", 0)):    # 1: hidden too
        try:
            refs = list(get() or [])
        except Exception:
            refs = []
        if refs:
            return refs
    return []


_frozen = []        # the characteristics the script switched off for the point being simulated


def thaw(changes):
    """Switch the characteristics of the last point back on, so that the study time sets the next one."""
    for ref in _frozen:
        quiet_set(changes, ref, "outserv", 0)
    _frozen.clear()


def freeze_point(ldf, net, changes, op, quiet=False):
    """After the load flow of a point: write what it solved into the loads, the PV plant, the
    battery and DG_2 as fixed values and switch their time characteristics off, so that the
    simulation and its events act on those values alone. Returns a line for the log
    (quiet: no warnings, for a point built again)."""
    loads = list(net["loads"])
    gens = [p for p in net["pv"] if not attr(p, "outserv", 0)] + [net["bess"]]
    gens += [net["dg2"]] if not attr(net["dg2"], "outserv", 0) else []

    def p_q(obj):
        gen = obj not in loads
        return p_kw(obj, generator=gen), (GEN_SIGN if gen else 1) * get_kilo(obj, "m:Q:bus1", FALLBACK_RESULT_UNIT)

    target = {full_name(o): p_q(o) for o in loads + gens}
    refs = {full_name(o): [r for r in char_refs(o) if not attr(r, "outserv", 0)] for o in loads + gens}
    refs = {k: v for k, v in refs.items() if v}
    if not refs:
        return "no time characteristic on the loads, PV, battery or DG_2"
    for obj in loads + gens:
        for ref in refs.get(full_name(obj), []):
            quiet_set(changes, ref, "outserv", 1)
            _frozen.append(ref)
    # loads whose P and Q come from characteristics on plini/qlini: the values they gave, written in
    for obj in loads:
        driven = {r.loc_name for r in refs.get(full_name(obj), [])}
        if driven & {"plini", "qlini"} and str(attr(obj, "mode_inp", "PQ")).upper() == "PQ":
            p, q = target[full_name(obj)]
            scale = float(attr(obj, "scale0", 1.0)) or 1.0
            for name, value in (("plini", p), ("qlini", q)):
                if attr(obj, name) is not None:
                    quiet_set(changes, obj, name, value / scale / to_kilo_factor(obj, name, FALLBACK_SETPOINT_UNIT))
    # then every frozen load and PV unit brought back to exactly its power, with its scaling factor
    adjust = [o for o in loads + net["pv"] if full_name(o) in refs and o in loads + gens
              and attr(o, "scale0") is not None and abs(target[full_name(o)][0]) >= 0.5]
    kept = []
    for _ in range(8):
        solve(ldf, f"Load flow for {op} with the characteristics off")
        worst = 0.0
        for obj in list(adjust):
            want, now = target[full_name(obj)][0], p_kw(obj, generator=obj not in loads)
            if abs(now) < 1e-3:                     # nothing left without its characteristic: keep that on
                for ref in refs[full_name(obj)]:
                    quiet_set(changes, ref, "outserv", 0)
                    _frozen.remove(ref)
                adjust.remove(obj)
                kept.append(obj.loc_name)
                worst = max(worst, 1.0)
                continue
            worst = max(worst, abs(now - want))
            quiet_set(changes, obj, "scale0", float(attr(obj, "scale0", 1.0)) * want / now)
        if worst < 0.2:
            break
    solve(ldf, f"Load flow for {op} with the characteristics off")
    off, q_off = [], []
    for obj in loads + gens:
        (want_p, want_q), (p, q) = target[full_name(obj)], p_q(obj)
        if abs(p - want_p) > max(0.5, 0.005 * abs(want_p)):
            off.append(f"{obj.loc_name} {p:.1f} kW instead of {want_p:.1f}")
        if abs(q - want_q) > max(2.0, 0.02 * abs(want_q)):
            q_off.append(f"{obj.loc_name} {q:.1f} kvar instead of {want_q:.1f}")
    if off:
        raise CaseProblem("with the time characteristics switched off the point cannot be held: " + "; ".join(off))
    if q_off and not quiet:
        warn(f"{op}: with the time characteristics off the reactive power moved: " + "; ".join(q_off))
    if kept and not quiet:
        warn(f"{op}: {', '.join(kept)} gives nothing without its time characteristic (an irradiance or similar "
             "input): that characteristic stays on, at the study time of the point")
    names = sorted({f"{r.loc_name}" for v in refs.values() for r in v if r in _frozen})
    return (f"{len(_frozen)} time characteristics switched off ({', '.join(names)}); the simulation runs on the "
            "solved values as fixed set-points")


def set_instant(case, ldf, net, changes, op, quiet=False):
    """The island as a controller left it at an instant: the study time moves the load and
    PV characteristics there; the battery, the PV curtailment and DG_2 are set as in the
    dispatch log; the load flow has to give back what the log says. Then (FREEZE_POINT) the
    time characteristics are switched off and the solved values become fixed set-points.
    quiet: nothing in the log, for the point built again before a run (rebuild_point)."""
    o = OPS[op]
    thaw(changes)                                   # the last point's characteristics back on
    app.EchoOff()
    try:
        case.SetStudyTime(int(o["epoch"]))
    finally:
        app.EchoOn()
    daylight = o["pv_avail"] > 0.5
    factor = min(max(o["pv"] / o["pv_avail"], 0.0), 1.0) if daylight else 1.0
    pv_names = {full_name(p) for p in net["pv"]}
    for key, (obj, original) in net["scale"].items():
        quiet_set(changes, obj, "scale0", original * (factor if key in pv_names else 1.0))
    for p in net["pv"]:
        quiet_set(changes, p, "outserv", 0 if daylight else 1)          # at night the inverters are off
    bess, dg2 = net["bess"], net["dg2"]
    quiet_set(changes, bess, "pgini", o["bess"] / (attr(bess, "ngnum", 1) or 1)
              / to_kilo_factor(bess, "pgini", FALLBACK_SETPOINT_UNIT))
    if o["dg2"] > 0:
        quiet_set(changes, dg2, "pgini", o["dg2"] / (attr(dg2, "ngnum", 1) or 1)
                  / to_kilo_factor(dg2, "pgini", FALLBACK_SETPOINT_UNIT))
    quiet_set(changes, dg2, "outserv", 0 if o["dg2"] > 0 else 1)
    try:
        solve(ldf, f"Load flow for {op}")
    except Stop as why:
        raise CaseProblem(f"the load flow at {o['time']} does not solve ({why})")
    now = measure(net)
    off = [f"{text} {now[key]:.1f} kW where the log has {o[key]:.1f}"
           for key, text, tol in (("load", "load", max(2.0, 0.01 * abs(o["load"]))),
                                  ("pv", "PV", max(2.0, 0.02 * o["pv"])), ("bess", "battery", 2.0),
                                  ("dg2", DG2_NAME, 2.0))
           if abs(now[key] - o[key]) > tol]
    if off:
        raise CaseProblem(f"at {o['time']} the model gives " + "; ".join(off) + ". The log is not of this model "
                          "and these data, or it was written in another time zone (USE_LOCAL_TIME)")
    frozen = ""
    if FREEZE_POINT:
        try:
            frozen = freeze_point(ldf, net, changes, op, quiet)
        except Stop as why:
            raise CaseProblem(f"the load flow with the time characteristics off does not solve ({why})")
        before, now = now, measure(net)
        moved = [f"{key} {before[key]:.1f} > {now[key]:.1f} kW" for key in ("load", "pv", "bess", "dg1", "dg2")
                 if abs(now[key] - before[key]) > max(1.0, 0.005 * abs(before[key]))]
        if moved:
            raise CaseProblem("switching the time characteristics off moved the point: " + "; ".join(moved))
    volts = {key: attr(term, "m:u") for key, term in net["bus"].items() if term is not None}
    now["volts"], now["op"] = volts, op
    if quiet:
        return now
    log(f"\n{op}: {o['text']}, {o['time']}")
    log(f"   load {now['load']:.1f} kW, PV {now['pv']:.1f} (of {o['pv_avail']:.1f} available), battery "
        f"{now['bess']:+.1f} (SOC {fmt(o['soc'], '{:.0f}')} %), {net['dg1'].loc_name} {now['dg1']:.1f} "
        f"(log: {fmt(o['dg1'], '{:.1f}')}), "
        f"{net['dg2'].loc_name} {now['dg2']:.1f} kW;  voltage "
        + ", ".join(f"{k} {v:.3f}" for k, v in volts.items() if isinstance(v, float)) + " pu")
    if frozen:
        log("   " + frozen)
    log("   before every run the point is built again from scratch and has to come back exactly like this")
    if o["dg1"] is not None and abs(now["dg1"] - o["dg1"]) > max(5.0, 0.02 * abs(o["dg1"])):
        warn(f"{op}: {net['dg1'].loc_name} gives {now['dg1']:.1f} kW where the log has {o['dg1']:.1f}: the losses or "
             "a set-point differ between this model and the run that wrote the log")
    limit = GENSET_DATA["dg1"]["gov"]["TMAX"] * net["dg1_base_kw"]
    if not -1.0 <= now["dg1"] <= limit:
        warn(f"{net['dg1'].loc_name} would have to deliver {now['dg1']:.0f} kW at this point "
             f"(its engine stops at {limit:.0f} kW)")
    return now


def rebuild_point(sim, net, point):
    """Before every RMS run: the operating point built again from scratch, as at the start of a new
    point. PowerFactory went on acting out the trips and load steps of the earlier runs at a point,
    although the script deletes their events: not even a reset of the calculation and a change to the
    load cleared them; only moving to another point did. So the study time is moved off the point and
    the whole point set up again (characteristics, set-points, load flows, freeze), and it has to come
    back exactly as it was for the point's first run."""
    started = time.time()
    o = OPS[point["op"]]
    app.EchoOff()
    try:
        sim["case"].SetStudyTime(int(o["epoch"]) - 300)        # a real change of the study time, as between points
    finally:
        app.EchoOn()
    again = set_instant(sim["case"], sim["ldf"], net, sim["changes"], point["op"], quiet=True)
    off = [f"{key} {again[key]:.1f} kW where it was {point[key]:.1f}" for key in ("load", "pv", "bess", "dg1", "dg2")
           if abs(again[key] - point[key]) > max(0.5, 0.002 * abs(point[key]))]
    if off:
        raise CaseProblem("the operating point built again before this run is not the point: " + "; ".join(off))
    sim["rebuilds"] = sim.get("rebuilds", 0) + 1
    sim["rebuild_s"] = sim.get("rebuild_s", 0.0) + time.time() - started


def short_outcome(r):
    """A run in a few characters: verdict and the furthest frequency."""
    i = r["ind"]
    if i.get("verdict") == "not applicable":
        return i.get("why", "not run")
    text = SHORT_VERDICT.get(i.get("verdict"), i.get("verdict", "?"))
    lo, hi = i.get("f_min"), i.get("f_max")
    if isinstance(lo, float) and isinstance(hi, float) and i.get("verdict") not in ("no result", "not applicable"):
        far = lo if F_NOM - lo >= hi - F_NOM else hi
        text += f" {far:.1f}" + ("" if not i.get("ended") else "!")
        text += f" | {fmt(i.get('rocof_500'))} | {fmt(i.get('f_settle_nom'), '{:.1f}')} | {fmt(i.get('f_margin'), '{:.1f}')}"
    return text


def print_comparison(results, cct):
    """The controllers next to each other: instant by instant, case by case."""
    rule = "=" * 124
    log("\n" + rule)
    log("RESULTS: every number below is read from PowerFactory's RMS simulation")
    log(rule)
    found = {r["case"]: r for r in results}
    cct_by_op = {c["op"]: c for c in cct if c["where"] == "main"}
    instants = []
    for op, o in OPS.items():
        if "instant" in o and o["instant"] not in instants and any(r["op"] == op for r in results):
            instants.append(o["instant"])
    suite = [s for s in CASES if CASES_TO_RUN is None or s[0] in CASES_TO_RUN]
    width = 38
    for iid in instants:
        ops = [op for op, o in OPS.items() if o.get("instant") == iid]
        first = OPS[ops[0]]
        log(f"\n{iid}  {first['label']}, {first['time']}")
        for op in ops:
            o = OPS[op]
            log(f"   {o['controller'][:30]:30s} load {o['load']:5.0f} kW, PV {o['pv']:5.0f} of {o['pv_avail']:5.0f}, "
                f"battery {o['bess']:+6.0f} (SOC {fmt(o['soc'], '{:3.0f}'):>3s} %), {DG2_NAME} {'on ' if o['dg2'] > 0 else 'off'}, "
                f"{DG1_NAME} {fmt(o['dg1'], '{:5.0f}'):>5s} kW")
        log("   " + f"{'case':46s}" + "".join(f"{OPS[op]['controller'][:width - 2]:{width}s}" for op in ops))
        for sid, text, _ in suite:
            cells = [short_outcome(found[f"{sid}_{op}"]) if f"{sid}_{op}" in found else "-" for op in ops]
            log("   " + f"{(sid + ' ' + text)[:45]:46s}" + "".join(f"{c:{width}s}" for c in cells))
        if any(op in cct_by_op for op in ops):
            log("   " + f"{'critical clearing time, main board':46s}"
                + "".join(f"{cct_text(cct_by_op[op]) if op in cct_by_op else '-':{width}s}" for op in ops))
    log("\nEach cell: the outcome (as in the full table of a single run: stable, stable after a protection trip, "
        "outside limits, unstable; a sentence such as 'DG_2 was already off' = the case trips a source that gives "
        "nothing at that point, so it was not simulated), then: the frequency furthest from "
        f"{F_NOM:g} Hz, in Hz (! = the run ends early: blackout or over-speed) | ROCOF over 500 ms, Hz/s | seconds "
        f"until the frequency is back within {F_SETTLE_HZ:g} Hz of {F_NOM:g} Hz to stay (- = not by the end) | margin "
        f"to the inverters' frequency trip ({F_UNDER:g} or {F_OVER:g} Hz), Hz, negative = past it. "
        "UNSTABLE* = unstable as modelled. "
        "Every run is in stability_rms_results.csv with all its numbers, labelled by controller and instant; "
        "controller_study.py report puts them side by side with plots.")
    for r in results:
        if r["ind"].get("verdict") in ("no result",) and r["ind"].get("why"):
            log(f"   {r['case']}: no result: {r['ind']['why']}")


def nice_range(values, include=(), min_span=0.0, floor=None):
    """An axis range around the values and the levels in `include`, from one round number to
    another. A quantity that stays on one side of zero keeps zero as the end of its axis; one
    that cannot be negative (floor=0: a frequency, a voltage) does not get an axis below it."""
    data = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)] or list(include) or [0.0]
    lo, hi = min(data + list(include)), max(data + list(include))
    if hi - lo < min_span:
        mid = 0.5 * (lo + hi)
        lo, hi = mid - 0.5 * min_span, mid + 0.5 * min_span
    if hi <= lo:
        lo, hi = lo - 0.5, hi + 0.5
    tol = 1e-6 * max(1.0, max(abs(v) for v in data))
    pad = 0.06 * (hi - lo)
    lo = 0.0 if 0.0 in include and -tol <= min(data) and max(data) > tol else lo - pad
    hi = 0.0 if 0.0 in include and max(data) <= tol and min(data) < -tol else hi + pad
    if floor is not None:
        lo = max(lo, floor)
    decade, ends = math.floor(math.log10(hi - lo)), []
    for k in range(decade - 2, decade + 2):          # the tightest round ends, at most 8 steps apart
        for m in (1.0, 2.0, 2.5, 5.0):
            step = m * 10.0 ** k
            a, b = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
            if b - a <= 8.0 * step * (1 + 1e-9):
                ends.append((b - a, -step, round(a, 9), round(b, 9)))
    return min(ends)[2:] if ends else (lo, hi)


_title = {"said": False}        # where the title block's text went, told once


def pf_title_block(board, page, lines, annex):
    """The case written into the title block of the page, and the block switched on (PowerFactory's
    'Title Block On'). The title object is the page's own if it has one, else the graphics board's,
    else the study case's; if there is none, one is made in the graphics board. The block may be one
    for all pages: it is written just before the page is shown and exported, so each image has its own."""
    holder = None
    for where in (page, board, app.GetActiveStudyCase()):
        try:
            found = list(where.GetContents("*.SetTitm") or [])
        except Exception:
            found = []
        if found:
            holder = found[0]
            break
    if holder is None:
        holder = board.CreateObject("SetTitm", "Title")
    if holder is None:
        raise RuntimeError("no title object found, and PowerFactory did not create one")
    for name, value in zip(("sub1z", "sub2z", "sub3z"), (list(lines) + ["", "", ""])[:3]):
        holder.SetAttribute(name, value)
    for name in ("anex", "annex"):
        try:
            holder.SetAttribute(name, annex)
        except Exception:
            pass
    if not attr(holder, "nproj"):
        project = app.GetActiveProject()
        if project is not None:
            holder.SetAttribute("nproj", project.loc_name)
    try:
        board.SetAttribute("sh_tit", 1)             # the graphics board's "show title block"
        shown = True
    except Exception:
        shown = False
    if not _title["said"]:
        _title["said"] = True
        log(f"   (title block: the case is written into {full_name(holder)}, Title 1-3 and annex"
            + ("; title block switched on" if shown else "; switch it on with the page's Title Block button") + ")")


def pf_six_page(board, net, name, runs, block):
    """A plot page drawn by PowerFactory itself from the runs' result objects: six plots on a grid of
    three columns, each scaled to its curves. Top row: voltage and frequency at the main board, active
    power of DG_1 and DG_2; bottom row: output of the battery and of the PV plant, active power of the
    load. runs: [(label, curves)], the label naming the run in the legends (empty for a single run);
    block: (lines, annex), the case as written into the page's title block. Returns the page."""
    hub = "main" if net["bus"].get("main") is not None else "gen"
    pvs, loads = net["pv"], net["loads"]
    # row by row: (plot name, which is its title; [(element, variable, curve key, name in the legend)];
    #              levels on the axis; least span; lowest the axis may go; legend even for one curve)
    specs = [("Voltage at the main board", [(net["bus"][hub], "m:u1", f"u_{hub}", "")], (1.0,), 0.1, 0.0, False),
             ("Frequency at the main board", [(net["bus"][hub], "m:fehz", f"f_{hub}", "")], (F_NOM,), 1.0, 0.0,
              False),
             ("DGs active power", [(net["dg1"], "m:P:bus1", "p_dg1", net["dg1"].loc_name),
                                   (net["dg2"], "m:P:bus1", "p_dg2", net["dg2"].loc_name)], (0.0,), 1.0, None, True),
             ("BESS output", [(net["bess"], "m:P:bus1", "p_bess", "")], (0.0,), 1.0, None, False),
             ("PV output", [(p, "m:P:bus1", f"p_pv{n}", p.loc_name if len(pvs) > 1 else "")
                            for n, p in enumerate(pvs)], (0.0,), 1.0, None, False),
             ("Load active power", [(ld, "m:P:bus1", f"p_load{n}", ld.loc_name if len(loads) > 1 else "")
                                    for n, ld in enumerate(loads)], (0.0,), 1.0, None, False)]
    t_end = max([curves["t"][-1] for _, curves in runs if curves.get("t")] or [T_END])

    old = board.GetPage(name, 0, "GrpPage")
    if old is not None:
        old.RemovePage()                            # built fresh: nothing of an earlier layout stays
    page = board.GetPage(name, 1, "GrpPage")
    if page is None:
        raise RuntimeError("PowerFactory did not create the page")
    for plot in page.GetContents("*.PltLinebarplot") or []:
        if plot.loc_name not in [spec[0] for spec in specs]:
            delete(plot)                            # a plot of an earlier layout that the page kept
    page.SetLayoutMode(2)                           # on a grid, filled row by row ...
    page.SetAttribute("numLayoutColumns", 3)        # ... in three columns
    try:
        page.SetAttribute("curveAreaAlignment", 2)  # the curve areas of all plots lined up
    except Exception:
        pass
    for order, (title, items, include, min_span, floor, legend) in enumerate(specs):
        plot = page.GetOrInsertPlot(title, 1)
        plot.SetAttribute("pageOrderIndex", order)
        ds = plot.GetDataSeries()
        ds.ClearCurves()
        ds.SetAttribute("useIndividualResults", 1)
        labels, values = [], []
        for label, curves in runs:
            for obj, var, key, what in items:
                if obj is None or not curves.get(key):
                    continue
                ds.AddCurve(obj, var, curves["result"])
                labels.append(" ".join(x for x in (what, label) if x))
                values += curves[key]
        for step in (lambda: ds.SetAttribute("curveTableLabel", labels),
                     lambda: plot.GetLegend().SetAttribute("showLegend", 1 if len(labels) > 1 or (legend and labels)
                                                          else 0),
                     lambda: plot.GetTitleObject().SetAttribute("showTitle", 1),
                     lambda: plot.GetTitleObject().SetAttribute("titleString", [title])):
            try:
                step()
            except Exception:
                pass                                # PowerFactory's default is kept
        plot.SetAutoScaleModeX(0)                   # the ranges below, not PowerFactory's own scaling
        plot.SetAutoScaleModeY(0)
        plot.SetScaleX(0.0, t_end)
        plot.SetScaleY(*nice_range(values, include, min_span, floor))
    try:
        pf_title_block(board, page, *block)
    except Exception as e:
        warn(f"The title block of {name} could not be written ({e})")
    page.Show()
    return page


def pf_export(page, path):
    """The page as an image, by PowerFactory's own export (with its watermark)."""
    page.Show()
    command = app.GetFromStudyCase("ComWr")             # PowerFactory's "export graphic" command
    command.SetAttribute("iopt_rd", "png")
    command.SetAttribute("iopt_savas", 0)               # no file dialog
    command.SetAttribute("f", path)
    if command.Execute():
        raise RuntimeError("the export command reported an error")


def pf_compare_pages(results, net):
    """A page per point and case (PF_PLOT_CASES) inside PowerFactory with every controller's
    curves, each from its own result object (pf_six_page), and as an image (PF_PLOT_PNG)."""
    board = app.GetGraphicsBoard() or app.GetFromStudyCase("SetDesktop")
    if board is None:
        warn("PowerFactory plot pages skipped: no graphics board is open")
        return
    groups = {}
    for r in results:
        o = OPS.get(r["op"], {})
        sid = r["case"].split("_")[0]
        if not r["curves"] or not r["curves"].get("result") or "instant" not in o:
            continue
        if PF_PLOT_CASES is not None and sid not in PF_PLOT_CASES:
            continue
        groups.setdefault((sid, o["instant"]), []).append(r)
    texts = {s[0]: s[1] for s in CASES}
    made, saved, export = [], [], PF_PLOT_PNG
    for (sid, iid), rs in groups.items():
        o, several = OPS[rs[0]["op"]], len(rs) > 1
        controllers = [OPS[r["op"]]["controller"] for r in rs]
        block = ([f"{sid}  {texts.get(sid, '')}".strip(), f"{iid}  {o['label']}, {o['time']}",
                  ("Controllers: " if several else "Controller: ") + ", ".join(controllers)], f"{sid} {iid}")
        runs = [(controller if several else "", r["curves"]) for controller, r in zip(controllers, rs)]
        name = f"Cmp {sid} {iid}"
        try:
            page = pf_six_page(board, net, name, runs, block)
            made.append(name)
        except Exception as e:
            warn(f"Could not build the PowerFactory page {name}: {e}")
            continue
        if export:
            try:
                pf_export(page, os.path.join(OUT_DIR, f"compare_{sid}_{iid}_powerfactory.png"))
                saved.append(name)
            except Exception as e:
                export = False
                warn(f"PowerFactory's own image export did not work ({e}); the pages themselves are there.")
    if made:
        log("PowerFactory pages with every controller's curves (Graphics Board tabs): " + ", ".join(made))
    if saved:
        log(f"The same pages as images from PowerFactory: compare_<case>_<instant>_powerfactory.png in {OUT_DIR}")


# ============================================================================
# OUTPUT: tables, files, plots
# ============================================================================
def fmt(value, pattern="{:.2f}", missing="-"):
    """A number as text ('-' if there is none; never '-0')."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return missing
    return re.sub(r"^-(0(?:\.0+)?)(?![\d.])", r"\1", pattern.format(value))


def capital(text):
    return text[:1].upper() + text[1:]


def verdict_text(ind):
    """The outcome as it is shown. One that rests on a load step larger than the ISO
    8528-5 guide, which the governor model takes more easily than an engine, says so."""
    if ind.get("skipped"):
        return "not run"
    best = ", best case" if ind.get("best_case") and ind["verdict"] in ("stable", "stable after a protection trip") else ""
    return ind["verdict"] + best + (f" ({ind['class']})" if ind.get("class") else "")


def outcome_text(ind, notes=True):
    """Outcome, where an instability shows, and what happened, in one line."""
    text = verdict_text(ind)
    parts = ind.get("cascade", []) + [ind.get("why", "")] + (ind.get("notes", []) if notes else [])
    parts = [p for p in parts if p]
    return text + (": " + "; ".join(parts) if parts else "")


def three(ind, prefix, pattern="{:.0f}"):
    """'before > furthest from it > at the end' of a power, or '-'."""
    p0 = ind.get(prefix + "_0")
    if p0 is None:
        return "-"
    hi, lo = ind[prefix + "_max"], ind[prefix + "_min"]
    far = hi if abs(hi - p0) >= abs(lo - p0) else lo
    return " > ".join(fmt(v, pattern) for v in (p0, far, ind[prefix + "_end"]))


def freq_low(ind):
    return f"< {BLACKOUT_HZ:g}" if ind.get("ended") == "collapse" else fmt(ind.get("f_min"))


def freq_high(ind):
    return f"> {OVERSPEED_HZ:g}" if ind.get("ended") == "over-speed" else fmt(ind.get("f_max"))


def cct_text(c):
    """The bracket of one critical clearing time, in words."""
    lo, hi = c["lo"], c["hi"]
    if lo is not None and hi is not None:
        return f"{lo * 1000:.0f} to {hi * 1000:.0f} ms"
    if lo is not None:
        return f"above {lo * 1000:.0f} ms"
    if hi is not None:
        return f"below {hi * 1000:.0f} ms"
    return "no result"


def print_tables(results, cct, model_notes):
    wide = max([len(r["text"]) for r in results] or [11]) + 2
    rule = "=" * 124

    def say(text, indent=3, width=124):
        for line in textwrap.wrap(text, width - indent, subsequent_indent="   ") or [""]:
            log(" " * indent + line)

    def groups():
        last = None
        for r in results:
            if r["case"][0] != last:
                last = r["case"][0]
                log(f"  {GROUPS.get(last, last)}")
            yield r, r["ind"]

    log("\n" + rule)
    log("RESULTS: every curve and every number below is read from PowerFactory's RMS simulation")
    log(rule)
    log("Operating points (the load flow before the runs), kW")
    points = {r["op"]: r["point"] for r in results if r.get("point")}
    for op in OPS:
        if op in points:
            p = points[op]
            log(f"   {op}  {OPS[op]['text']}: load {p['load']:.0f}, {DG1_NAME} {p['dg1']:.0f}, "
                f"{DG2_NAME} {p['dg2']:.0f}, PV {p['pv']:.0f}, battery {p['bess']:+.0f}")
    if model_notes:
        log("The model as it was simulated")
        for note in model_notes:
            say("- " + note)
    log("Protection that the model does not contain, " + ("applied by the script: a case in which it acts is run "
        "again in PowerFactory with the trip as an event" if EMULATE_TRIPS else "reported only (EMULATE_TRIPS is off)"))
    say(f"- inverters (assumed settings): below {F_UNDER:g} Hz for {T_UNDER:g} s, above {F_OVER:g} Hz for {T_OVER:g} s, "
        + ", ".join(f"below {u:g} pu for {s:g} s" for u, s in U_UNDER_STEPS) + ", "
        + ", ".join(f"above {u:g} pu for {s:g} s" for u, s in U_OVER_STEPS) + " (voltage at the main board)")
    say(f"- gensets: over-speed shutdown above {OVERSPEED_HZ:g} Hz, the breaker open {OVERSPEED_TRIP_S:g} s later "
        f"(assumed). A run in which the frequency falls below {BLACKOUT_HZ:g} Hz counts as a collapse")
    say(f"Stable means: the frequency ends within {F_BAND * 100:g} % of {F_NOM:g} Hz, every live busbar ends between "
        f"{U_LOW:g} and {U_HIGH:g} pu, no genset and no cable ends beyond its rating, everything has come to rest, "
        "the sets stay in step and no load is lost.", indent=0)

    log("\n1. Frequency and outcome")
    head = f"{'case':5s}{'OP':5s}{'disturbance':{wide}s}"
    log(head + f"{'ROCOF, Hz/s':>14s}{'f low':>8s}{'f high':>8s}{'f back':>8s}{'settled':>8s}{'f end':>8s}"
        f"  {'ISO 8528-5':11s} outcome")
    log(" " * len(head) + f"{'0.1 s':>7s}{'0.5 s':>7s}{'Hz':>8s}{'Hz':>8s}{'s':>8s}{'s':>8s}{'Hz':>8s}  {'f / V':11s}")
    for r, i in groups():
        iso = "-"
        if i.get("iso_f") and not i.get("best_case"):
            iso = f"{i['iso_f']} / {i.get('iso_u', '-')}".replace("outside G1", ">G1")
        outcome = verdict_text(i)
        log(f"{r['case']:5s}{r['op']:5s}{r['text']:{wide}s}{fmt(i.get('rocof_100'), '{:.1f}'):>7s}"
            f"{fmt(i.get('rocof_500'), '{:.1f}'):>7s}{freq_low(i):>8s}{freq_high(i):>8s}"
            f"{fmt(i.get('f_recovery'), '{:.1f}'):>8s}{fmt(i.get('t_settle'), '{:.1f}'):>8s}"
            f"{fmt(None if i.get('ended') else i.get('f_end')):>8s}  {iso:11s} {outcome}")
        for line in i.get("cascade", []) + ([i["why"]] if i.get("why") else []):
            say(line, indent=10)
        for note in i.get("notes", []):
            say("note: " + note, indent=10)
    say(f"The frequency is the speed of {DG1_NAME} (of {DG2_NAME} once {DG1_NAME} is disconnected). ROCOF: largest "
        "rate of change of frequency over a window of 0.1 s and of 0.5 s. f back: seconds after the event until the "
        f"frequency stays within {F_BAND * 100:g} % of {F_NOM:g} Hz. settled: until frequency and voltages stay within "
        f"{REST_F_HZ:g} Hz and {REST_U_PU:g} pu of their final values. ISO 8528-5: the class whose limit values the transient keeps "
        "(G3 is the tightest, >G1 is outside G1), for the frequency and for the voltage at the genset busbar; "
        "indicative, and not given for faults nor for a best case. best case: a set takes up more load in one step "
        "than the ISO 8528-5 guide for its engine; the governor model has no turbocharger limit, so a real engine "
        "dips further and the outcome is the most favourable one. (seen in ...): where an instability shows, not "
        "its cause.")
    known = [r for r in results if r["ind"].get("rocof_500") is not None]
    fast = [r["case"] for r in known if r["ind"]["rocof_500"] > ROCOF_LIMIT]
    if fast:
        slow = [r["case"] for r in known if r["case"] not in fast]
        which = ", ".join(fast) if len(fast) <= len(slow) or not slow else "every case but " + ", ".join(slow)
        say(f"ROCOF over 0.5 s is above {ROCOF_LIMIT:g} Hz/s in {'every case' if not slow else which}. The grid "
            f"code asks a plant to stay connected up to {ROCOF_LIMIT:g} Hz/s only, so every outcome here assumes that "
            "the inverters ride through the rate shown: confirm that with their data sheets. ([1] counts a high "
            "rate of change among the signs of frequency instability; NRS 097-2-1 uses the same level for loss-of-"
            "mains detection at a utility connection, which this island does not have.)")
    swinging = [r["case"] for r in results if "still swinging" in r["ind"].get("why", "")]
    if len(swinging) >= 3:
        say("Several cases are still swinging at the end (" + ", ".join(swinging) + "): that points at the tuning of "
            "a governor or a voltage regulator rather than at the disturbances. Compare their values with the survey.")

    log("\n2. Voltage at the busbars, pu")
    low, high = f"below {U_LOW:g}", f"above {U_HIGH:g}"
    log(f"{'case':6s}{'busbar':16s}{'before':>8s}{'lowest':>8s}{'highest':>9s}{'end':>8s}{low:>11s}{high:>11s}{'back':>8s}")
    log(f"{'':47s}{'':>8s}{'s':>11s}{'s':>11s}{'s':>8s}")
    for r, i in groups():
        rows = [k for k in BUS_KEYS if f"umin_{k}" in i]
        for n, k in enumerate(rows):
            log(f"{r['case'] if n == 0 else '':6s}{BUS_TEXT[k].lower():16s}{i[f'u0_{k}']:8.3f}{i[f'umin_{k}']:8.3f}"
                f"{i[f'umax_{k}']:9.3f}{i[f'uend_{k}']:8.3f}{i[f'ulow_{k}']:11.2f}{i[f'uhigh_{k}']:11.2f}"
                f"{fmt(i.get(f'urec_{k}')):>8s}")
        if not rows:
            log(f"{r['case']:6s}-")
    say(f"lowest, highest, end: after the event. {low}, {high}: longest unbroken time there. back: seconds after "
        f"the event (after clearing, for a fault) until the busbar stays between {U_LOW:g} and {U_HIGH:g} pu.")

    for number, name, unit, q in ((3, "Active power", "kW", "p"), (4, "Reactive power", "kvar", "q")):
        log(f"\n{number}. {name}, {unit}: before > furthest from it > at the end")
        log(f"{'case':6s}{DG1_NAME:23s}{DG2_NAME:23s}{'battery':23s}{'PV':23s}{'load':23s}")
        for r, i in groups():
            log(f"{r['case']:6s}" + "".join(f"{three(i, f'{q}_{role}'):23s}" for role in ("dg1", "dg2", "bess", "pv", "load")))
        say("Sources: positive when they deliver (battery: positive discharging, negative charging). Load: what it "
            "takes. In a case that ends in a blackout the last value is the one at that instant." if q == "p" else
            "Sources: positive when they deliver reactive power. Load: what it takes.")

    log("\n5. Synchronism, currents and inverters")
    angle = f"angle of {DG2_NAME} against {DG1_NAME}"
    log(f"{'case':6s}{angle:30s}{'largest current, multiples of the rated current':48s}  PV and battery inverters")
    log(f"{'':6s}{'before':>10s}{'departure':>12s}{'':8s}{DG1_NAME:>8s}{DG2_NAME:>8s}{'battery':>9s}{'PV':>7s}"
        f"{'L_1':>7s}")
    for r, i in groups():
        swing = fmt(i.get("angle_swing"), "{:.0f} deg") + (" *" if i.get("angle_estimated") else "")
        if i.get("slip"):
            swing = "pole slip"
        log(f"{r['case']:6s}{fmt(i.get('angle0'), '{:.0f} deg'):>10s}{swing:>12s}{'':8s}"
            f"{fmt(i.get('i_dg1_max'), '{:.1f}'):>8s}{fmt(i.get('i_dg2_max'), '{:.1f}'):>8s}"
            f"{fmt(i.get('i_bess_max'), '{:.1f}'):>9s}{fmt(i.get('i_pv_max'), '{:.1f}'):>7s}"
            f"{fmt(i.get('i_l1_max'), '{:.1f}'):>7s}   {i.get('inverters', '-')}")
    say("departure: largest change of the rotor angle from its value before the event (* estimated from the two "
        "speeds, because the angle was not recorded). Currents are positive-sequence currents as PowerFactory gives "
        "them in per unit of each element's rated current.")

    if cct:
        log("\n6. Critical clearing time of a temporary three-phase fault")
        log(f"{'OP':5s}{'fault':24s}{'between':18s}what happens")
        for c in cct:
            place = ("on " if c["where"] == "L1" else "at ") + PLACES[c["where"]]
            first = True
            edge = ""
            if not c["note"] and c["lo"] is not None and c["hi"] is None:
                edge = "the longest fault tried"
            elif not c["note"] and c["hi"] is not None and c["lo"] is None:
                edge = "the shortest fault tried"
            for label, text in ((f"at {c['lo'] * 1000:.0f} ms" if c["lo"] is not None else "", c["at_lo"]),
                                (f"at {c['hi'] * 1000:.0f} ms" if c["hi"] is not None else "", c["at_hi"]),
                                ("", edge), ("", c["note"]),
                                ("", "the fault was put at the main-board end of the cable" if c["moved"] else "")):
                if not text:
                    continue
                lines = textwrap.wrap((label + ": " if label else "") + text, 76) or [""]
                for n, line in enumerate(lines):
                    lead = f"{c['op']:5s}{place:24s}{cct_text(c):18s}" if first else " " * 47
                    log(lead + ("   " if n else "") + line)
                    first = False
        say(f"The longest fault after which the island is stable with nothing tripped lies between the two values "
            f"(bisection from {CCT_RANGE_S[0] * 1000:.0f} to {CCT_RANGE_S[1] * 1000:.0f} ms, to within 10 ms, on the "
            "understanding that a fault longer than one that fails also fails). What happens is given for both "
            "ends. Where the limit is an inverter trip, it is the assumed setting that decides."
            + (" A best case as in table 1 where it says so." if any(c.get("best_case") for c in cct) else ""))


def save_outputs(results, cct, tests=()):
    os.makedirs(OUT_DIR, exist_ok=True)
    cols = (["case", "op", "text", "verdict", "class", "why", "cascade", "notes", "iso_f", "iso_u", "best_case",
             "rocof_100", "rocof_500", "f0", "f_min", "t_fmin", "f_max", "t_fmax", "f_end", "f_recovery", "t_settle",
             "f_settle_nom", "f_dev_max", "f_dev_int", "uph_min_main",
             "f_margin", "f_margin_to", "f_margin_uf", "f_margin_of", "os_margin", "u_low_inv", "u_margin_s",
             "ended", "t_end", "t_run", "below_47", "above_over", "osc_f", "move_f", "osc_u", "move_u"]
            + [f"{what}_{k}" for k in BUS_KEYS for what in ("u0", "umin", "umax", "uend", "ulow", "uhigh", "urec")]
            + ["u_min", "u_min_at", "u_max", "u_below_09", "u_above_11", "u_recovery", "lv_time", "dead",
               "angle0", "angle_swing", "angle_estimated", "slip"]
            + [f"{q}_{role}_{what}" for q in ("p", "q") for role in ("dg1", "dg2", "bess", "pv", "load")
               for what in ("0", "max", "min", "end")]
            + ["i_dg1_max", "i_dg2_max", "i_bess_max", "i_pv_max", "i_l1_max", "reverse_s", "inverters", "steady",
               "moved", "not_applied", "events"]
            + ["controller", "instant", "instant_label", "time", "suite", "soc_pct", "cct_lo_s", "cct_hi_s"])

    def context(op, suite):
        """Who and when, for the runs of a comparison of controllers (empty otherwise)."""
        o = OPS.get(op, {})
        if "instant" not in o:
            return {}
        return dict(controller=o["controller"], instant=o["instant"], instant_label=o["label"], time=o["time"],
                    suite=suite, soc_pct=o["soc"])

    def cells(row):
        return ["; ".join(str(v) for v in row[c]) if isinstance(row.get(c), (list, tuple)) else
                f"{row[c]:.4f}" if isinstance(row.get(c), float) else
                "" if row.get(c) is None else row[c] for c in cols]

    rows = []
    for r in results:
        row = dict(r["ind"], case=r["case"], op=r["op"], text=r["text"])
        row.update(context(r["op"], r["case"].split("_")[0]))
        rows.append(cells(row))
    for c in cct:
        row = {"case": f"CCT {c['where']}", "op": c["op"],
               "text": "Critical clearing time, fault " + ("on " if c["where"] == "L1" else "at ") + PLACES[c["where"]],
               "verdict": cct_text(c), "class": "", "cct_lo_s": c["lo"], "cct_hi_s": c["hi"],
               "why": "; ".join(t for t in (
                   f"at {c['lo'] * 1000:.0f} ms: {c['at_lo']}" if c["lo"] is not None else "",
                   f"at {c['hi'] * 1000:.0f} ms: {c['at_hi']}" if c["hi"] is not None else "",
                   c["note"], "fault put at the main-board end of the cable" if c["moved"] else "") if t)}
        row.update(context(c["op"], f"CCT {c['where']}"))
        rows.append(cells(row))
    blocked = []

    def write(name, header, lines):
        try:
            with open(os.path.join(OUT_DIR, name), "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(header)
                w.writerows(lines)
            return 1
        except OSError as e:
            blocked.append(f"{name} ({e.strerror or e})")
            return 0

    table = write("stability_rms_results.csv", cols, rows) if rows else 0
    saved = 0
    for r in list(tests) + results:
        curves = r["curves"]
        if not curves or not curves.get("t"):
            continue
        columns = {}                                    # header -> values
        for key in list(curves):
            if key in ("t", "units", "result", "kw_factor"):
                continue
            if key.startswith(("p_", "q_")):
                expected = r["point"].get(key[2:]) if key.startswith("p_") else None
                columns[key + ("_kW" if key.startswith("p_") else "_kvar")] = to_kw(curves, key, expected)
            elif key.startswith("speed_"):
                columns["f_" + key[6:] + "_Hz"] = [v * F_NOM for v in curves[key]]
            else:
                unit = {"u": "pu", "f": "Hz", "pt": "pu", "ve": "pu", "i": "pu", "angle": "deg",
                        "slip": ""}.get(key.split("_")[0], "")
                columns[key + ("_" + unit if unit else "")] = curves[key]
        saved += write(f"{r['case']}_curves.csv", ["t_s"] + list(columns),
                       ([f"{ti:.4f}"] + [f"{values[n]:.6g}" for values in columns.values()]
                        for n, ti in enumerate(curves["t"])))
    log(f"\nSaved in {OUT_DIR}: " + ("the results table (stability_rms_results.csv), " if table else "")
        + f"the curves of {saved} runs (<case>_curves.csv" + ("; T1, T2: the self-test" if tests else "")
        + ") and this log (stability_rms_log.txt)")
    if blocked:
        warn("Could not write " + ", ".join(blocked) + ": close the file if it is open in another program and run again")


def save_log(head=0, first=0):
    """The log of this run; for a later forecast of several, the first `head` lines (the setup) and those from `first`."""
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        lines = _log_lines[:head] + _log_lines[first:] if first else _log_lines
        with open(os.path.join(OUT_DIR, "stability_rms_log.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception as e:
        warn(f"The log could not be saved in {OUT_DIR}: {e}")


# ---------------------------------------------------------------------------- PNG plots
# One colour per element in every plot, as in the dispatch plots. The busbars have
# their own colours and line styles; the load and its terminal are drawn in black.
COLOUR = {"dg1": "#2a78d6", "dg2": "#eb6834", "bess": "#1baf7a", "pv": "#eda100", "load": "#0b0b0b", "l1": "#8a8983"}
BUS_COLOUR = {"gen": "#4a3aa7", "main": "#008300", "load": "#0b0b0b"}
BUS_STYLE = {"gen": "-", "main": (0, (5, 2)), "load": (0, (1, 1.6))}
INK, INK2, GRID, AXIS, SOFT = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7", "#8a8983"
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
VERDICT_COLOUR = {"stable": GOOD, "stable after a protection trip": WARNING, "outside limits": WARNING,
                  "supply lost": SERIOUS, "unstable as modelled": SERIOUS, "unstable": CRITICAL}
FAULT_FILL, BAND_FILL = "#f6dedb", "#eef1f4"
DOTS, DASHES = (0, (1, 2)), (0, (4, 2))
NAN = float("nan")


def two_lines(note, limit=86):
    """A note above a plot as one line, or as two if it is too long for the width."""
    if len(note) <= limit:
        return [note] if note else []
    cut = limit
    for marks in ((".  ", ";  "), (",  ", ", "), (" ",)):       # between sentences if possible, else at a comma
        cuts = [n + len(mark) for mark in marks for n in range(len(note)) if note.startswith(mark, n)]
        fits = [c for c in cuts if c <= limit and len(note) - c <= limit] or [c for c in cuts if c <= limit]
        if fits:
            cut = max(fits)
            break
    return [note[:cut].rstrip(" ,;"), note[cut:].strip()]


FAULT_KIND_TEXT = {"slg": "single-phase fault (line to ground), unbalanced simulation",
                   "ll": "two-phase fault (line to line), unbalanced simulation",
                   "3ph": "three-phase fault"}
PHASE_COLOUR = ("#2a78d6", "#eb6834", "#1baf7a")    # phases A, B, C: the first three categorical slots


def fault_kind(r):
    """'slg', 'll' or '3ph' for a fault case, else None."""
    return next((e[3] if len(e) > 3 else "3ph" for e in r.get("spec") or [] if e[0] == "fault"), None)


def case_title(r, width=64):
    """The title of a case plot, as lines that fit the sheet: the case, then where and whose dispatch."""
    o = OPS.get(r["op"], {})
    if "instant" in o:
        sid = r["case"].split("_")[0]
        text = next((c[1] for c in CASES if c[0] == sid), r["text"])
        head = f"{sid}  {text}"
        tail = f"{o['instant']} {o['label']}, {o['time']}   |   {o['controller']} dispatch"
    else:
        head, tail = f"{r['case']}  {r['text']}", str(r.get("op", ""))
    kind = fault_kind(r)
    if kind in ("slg", "ll"):
        head += f"   [{FAULT_KIND_TEXT[kind]}]"
    return textwrap.wrap(head, width) + ([tail] if tail else [])


def case_folder(r):
    """Where the plots of a case go: OUT_DIR, or OUT_DIR/plots/<point> in a comparison of controllers."""
    o = OPS.get(r["op"], {})
    folder = os.path.join(OUT_DIR, "plots", o["instant"]) if "instant" in o else OUT_DIR
    os.makedirs(folder, exist_ok=True)
    return folder


def measures(ind):
    """The stability measures of a run, as lines for a box on its frequency plot."""
    low = F_NOM - ind["f_min"] >= ind["f_max"] - F_NOM
    lines = [f"ROCOF  {fmt(ind.get('rocof_500'))} Hz/s over 500 ms,  {fmt(ind.get('rocof_100'))} over 100 ms",
             (f"Nadir  {ind['f_min']:.2f} Hz at {ind['t_fmin']:.2f} s" if low else
              f"Zenith  {ind['f_max']:.2f} Hz at {ind['t_fmax']:.2f} s")]
    if ind.get("f_settle_nom") is not None:
        lines.append(f"Back at {F_NOM:g} \u00b1 {F_SETTLE_HZ:g} Hz to stay after  {ind['f_settle_nom']:.2f} s")
    elif ind.get("ended"):
        lines.append("Does not settle: the run ends in a blackout")
    else:
        lines.append(f"Not back within {F_SETTLE_HZ:g} Hz of {F_NOM:g} Hz by the end ({ind['f_end']:.2f} Hz)")
    if ind.get("f_dev_int") is not None:
        lines.append(f"Frequency deviation  \u222b|\u0394f| dt = {ind['f_dev_int']:.2f} Hz\u00b7s")
    if ind.get("f_margin") is not None:
        limit = F_UNDER if ind["f_margin_to"] == "under" else F_OVER
        lines.append(f"Margin to the inverter frequency trip  {ind['f_margin']:.2f} Hz (to {limit:g} Hz)"
                     + ("  PAST IT" if ind["f_margin"] < 0 else ""))
    if ind.get("u_margin_s") is not None:
        lines.append(f"Ride-through margin  {ind['u_margin_s']:.2f} s left of {T_U_UNDER:g} s below {U_UNDER:g} pu"
                     + ("  PAST IT" if ind["u_margin_s"] < 0 else ""))
    return lines


def new_figure(plt, width_cm, height_cm):
    try:
        return plt.subplots(figsize=(width_cm / 2.54, height_cm / 2.54), layout="constrained")
    except TypeError:                               # matplotlib older than 3.5
        return plt.subplots(figsize=(width_cm / 2.54, height_cm / 2.54), constrained_layout=True)


def plot_pngs(results, net, tests=(), compared=False):
    """One image per quantity and case (the self-test runs included), and the summaries:
    the three over all cases, or, in a comparison of controllers, those three and the
    head-to-head of the controllers for each operating point."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        warn(f"PNG plots skipped: matplotlib is not available ({e})")
        return
    plt.rcParams.update({
        "font.size": 9, "axes.titlesize": 9.5, "axes.labelsize": 9, "xtick.labelsize": 8, "ytick.labelsize": 8,
        "legend.fontsize": 8, "axes.edgecolor": AXIS, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
        "axes.labelcolor": INK, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
        "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True, "savefig.dpi": 200})
    made = 0
    for r in list(tests) + (results if not compared or COMPARE_CASE_PNGS else []):
        if r["curves"] and r["curves"].get("t") and "f_min" in r["ind"]:
            try:
                made += plot_case(plt, r, net)
            except Exception as e:
                warn(f"{r['case']}: its plots could not be drawn ({type(e).__name__}: {e})")
            finally:
                plt.close("all")
    sets = [("", "", results)]                      # (file suffix, title tail, runs)
    if compared:
        sets = []
        for iid in dict.fromkeys(OPS[r["op"]]["instant"] for r in results if "instant" in OPS.get(r["op"], {})):
            o = next(o for o in OPS.values() if o.get("instant") == iid)
            sets.append((f"_{iid}", f"\n{iid} {o['label']}, {o['time']}: every controller",
                         [r for r in results if OPS.get(r["op"], {}).get("instant") == iid]))
    summaries = 0
    for suffix, tail, runs in sets:
        rows = by_case(runs) if compared else runs
        draws = [("frequency", lambda: summary_frequency(plt, rows, suffix, tail)),
                 ("voltage", lambda: summary_voltage(plt, rows, suffix, tail)),
                 ("faults", lambda: summary_faults(plt, rows, suffix, tail))]
        if compared:
            draws.insert(0, ("head-to-head", lambda: summary_head_to_head(plt, runs, suffix[1:])))
        for name, draw in draws:
            if not runs:
                break
            try:
                summaries += draw()
            except Exception as e:
                warn(f"The summary plot of the {name}{suffix} could not be drawn ({type(e).__name__}: {e})")
            finally:
                plt.close("all")
    if made:
        where = os.path.join(OUT_DIR, "plots", "<point>") if compared else OUT_DIR
        log(f"{made} PNG plots of single runs in {where}: <case>_frequency, _power, _reactive and _voltage for every "
            "case" + (" (T1, T2: the self-test of the controllers)" if tests else "") + ", _phases for the unbalanced "
            "faults, _angle where both sets run, _current for the faults")
    if summaries:
        log(f"{summaries} summary plots in {OUT_DIR}: " + ("head_to_head_<point>, and summary_frequency_<point>, "
            "summary_voltage_<point>, summary_faults_<point>" if compared else
            "summary_frequency, summary_voltage, summary_faults"))


def plot_case(plt, r, net):
    """The images of one case. Each is drawn on its own, so that a quantity that was
    not recorded costs that image only. Returns how many were saved."""
    curves, ind, point = r["curves"], r["ind"], r["point"]
    fault, clear = ind.get("is_fault"), ind.get("t_clear", 0.0)
    x_all = [ti - T_EVENT for ti in curves["t"]]
    stop = ind["t_end"] if ind.get("ended") else x_all[-1]
    n = len([xi for xi in x_all if xi <= stop + 1e-9])      # the curves end where the run stops meaning anything
    x = x_all[:n]
    names = {"dg1": net["dg1"].loc_name, "dg2": net["dg2"].loc_name}
    off = ind.get("tripped", {})                            # what is disconnected in this run, and when
    marks = {}
    for when, role in ind.get("trip_marks", []):
        marks.setdefault(round(when, 3), []).append(PLANT_TEXT.get(role) or names.get(role, role))
    title = "\n".join(case_title(r))
    unbalanced = fault_kind(r) in ("slg", "ll")
    x0 = max(x[0], -0.25 if fault else -0.5)
    if ind.get("ended"):
        x1 = stop + max(0.12 * (stop - x0), 0.15)
    else:
        busy = max([ind.get("t_settle") or 0.0, ind.get("t_fmin") or 0.0, ind.get("t_fmax") or 0.0,
                    ind.get("f_settle_nom") or 0.0] + list(marks))
        x1 = min(x[-1], max(2.5 if fault else 4.0, 1.3 * busy + 1.0))        # show the event, not the quiet tail
    shown = [i for i, xi in enumerate(x) if x0 <= xi <= x1]
    made = []

    def span(series_list, pad=0.06, least=None):
        """Axis limits from what is on screen, with a margin and at least the range `least`."""
        values = [s[i] for s in series_list for i in shown if s[i] == s[i]]      # NaN is left out
        lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
        if least:
            lo, hi = min(lo, least[0]), max(hi, least[1])
        room = pad * (hi - lo) or 1.0
        return lo - room, hi + room

    def figure(ylabel, note=""):
        fig, ax = new_figure(plt, 16, 8.6)
        lines = two_lines(note)
        ax.set_title(title, loc="left", pad=6 + 11 * len(lines), linespacing=1.3)
        for k, text in enumerate(reversed(lines)):
            ax.annotate(text, xy=(0, 1), xycoords="axes fraction", xytext=(0, 4 + 11 * k), textcoords="offset points",
                        va="bottom", ha="left", fontsize=8, color=INK2).set_in_layout(False)
        ax.set_xlabel("Time after the disturbance [s]")
        ax.set_ylabel(ylabel)
        ax.set_xlim(x0, x1)
        return fig, ax, len(lines)

    def finish(fig, ax, what, note_lines=0):
        """Mark the fault, the emulated trips and the end of the run; legend; save."""
        if fault and clear > 0:
            ax.axvspan(0.0, clear, color=FAULT_FILL, lw=0, zorder=0, label=f"Fault, {clear * 1000:.0f} ms")
        for when, who in sorted(marks.items()):
            ax.axvline(when, color=INK2, lw=0.9, ls=DASHES, zorder=1,
                       label=f"Emulated trip: {' and '.join(who)}, {when:.2f} s")
        if ind.get("ended"):
            ax.axvline(stop, color=CRITICAL, lw=1.0, zorder=1, label={
                "over-speed": f"Blackout: over-speed, {stop:.2f} s", "collapse": f"Blackout: below {BLACKOUT_HZ:g} Hz, "
                f"{stop:.2f} s", "no set": f"Blackout: no genset left, {stop:.2f} s"}[ind["ended"]])
        labels = ax.get_legend_handles_labels()[1]
        if labels:
            ncol = 3 if max(len(text) for text in labels) <= 26 else 2
            rows = -(-len(labels) // ncol)
            fig.set_size_inches(16 / 2.54, (7.5 + 0.43 * rows + 0.39 * note_lines + 0.45 * title.count("\n")) / 2.54)
            legend_below(fig, ax, ncol)
        path = os.path.join(case_folder(r), f"{r['case']}_{what}.png")
        fig.savefig(path, facecolor="white")
        plt.close(fig)
        made.append(path)

    def powers(q):
        """{role: (label, curve)} of the active (q = "p") or reactive (q = "q") power."""
        out = {}
        for role, label in (("dg1", f"{names['dg1']} (genset busbar)"), ("dg2", f"{names['dg2']} (genset busbar)"),
                            ("bess", "Battery (main board)"), ("pv", "PV (main board)"),
                            ("load", "Load (load terminal)")):
            series = total_kw(curves, f"{q}_{role}", point.get(role) if q == "p" else None)
            if series:
                out[role] = (label, series[:n])
        return out

    def before_end(q, unit, series):
        text = ",  ".join(f"{label.split(' (')[0]} {fmt(ind[f'{q}_{role}_0'], '{:.0f}')} > "
                          f"{fmt(ind[f'{q}_{role}_end'], '{:.0f}')}" for role, (label, _) in series.items()
                          if f"{q}_{role}_0" in ind)
        return f"Before > at the {'blackout' if ind.get('ended') else 'end'}, {unit}:  {text}" if text else ""

    def draw_powers(ax, series):
        for role, (label, values) in series.items():
            ax.plot(x, values, color=COLOUR[role], lw=1.4 if role == "load" else 1.7,
                    ls=DASHES if role == "load" else "-", label=label, zorder=3)

    # --- frequency: the genset speeds (what the numbers are read from) and the busbars' own frequency
    def frequency():
        note = (f"{capital(verdict_text(ind).split(' (')[0])}.  {freq_low(ind)} to {freq_high(ind)} Hz"
                + (f", {ind['f_end']:.2f} Hz at the end" if not ind.get("ended") else ""))
        fig, ax, lines = figure("Frequency [Hz]", note)
        rise = ind["f_max"] - F_NOM > F_NOM - ind["f_min"]           # the box goes where the curve is not
        ax.text(0.985, 0.04 if rise else 0.96, "\n".join(measures(ind)), transform=ax.transAxes, ha="right",
                va="bottom" if rise else "top", fontsize=7.5, color=INK, linespacing=1.35, zorder=6,
                bbox=dict(facecolor="white", edgecolor=AXIS, boxstyle="round,pad=0.45", alpha=0.92))
        ax.axhline(F_NOM + F_SETTLE_HZ, color=INK2, lw=0.6, ls=DOTS, zorder=1)
        ax.axhline(F_NOM - F_SETTLE_HZ, color=INK2, lw=0.6, ls=DOTS, zorder=1,
                   label=f"Settled: within {F_SETTLE_HZ:g} Hz of {F_NOM:g} Hz")
        if ind.get("f_settle_nom"):
            ax.axvline(ind["f_settle_nom"], color=INK, lw=0.9, ls=(0, (6, 2, 1, 2)), zorder=1,
                       label=f"Back at {F_NOM:g} Hz to stay, {ind['f_settle_nom']:.2f} s")
        speeds = []
        for role, width in (("dg1", 2.6), ("dg2", 1.5)):                     # the thinner one stays visible on top
            if f"speed_{role}" in curves:
                gone = off.get(role)
                speeds.append([v * F_NOM if gone is None or xi <= gone + 1e-9 else NAN
                               for xi, v in zip(x, curves[f"speed_{role}"])])
                ax.plot(x, speeds[-1], color=COLOUR[role], lw=width, label=f"{names[role]} speed, as frequency", zorder=3)
        buses = []
        for key in BUS_KEYS:
            if f"f_{key}" not in curves or f"u_{key}" not in curves or (
                    key == "main" and same(net["bus"]["gen"], net["bus"]["main"])):
                continue
            # a frequency measured on a dead or faulted busbar means nothing, nor do the
            # spikes at the instants the fault comes and goes
            buses.append([fv if uv >= 0.3 and not (fault and (abs(xi) < 0.02 or abs(xi - clear) < 0.02)) else NAN
                          for xi, fv, uv in zip(x, curves[f"f_{key}"], curves[f"u_{key}"])])
            ax.plot(x, buses[-1], color=BUS_COLOUR[key], lw=0.9, ls=BUS_STYLE[key], zorder=4,
                    label=f"{BUS_TEXT[key]} frequency")
        lo, hi = span(speeds or buses, least=(F_NOM * (1 - F_BAND) - 0.2, F_NOM * (1 + F_BAND) + 0.2))
        if ind.get("ended") == "over-speed":
            hi = max(hi, OVERSPEED_HZ + 0.8)
        lo, hi = max(lo, 38.0), min(hi, 62.0)
        ax.set_ylim(lo, hi)
        ax.axhspan(F_NOM * (1 - F_BAND), F_NOM * (1 + F_BAND), color=BAND_FILL, lw=0, zorder=0,
                   label=f"Stable: ends within {F_BAND * 100:g} % of {F_NOM:g} Hz")
        for limit, label in ((F_UNDER, f"Inverter trip, assumed: < {F_UNDER:g} Hz for {T_UNDER:g} s"),
                             (F_OVER, f"Inverter trip, assumed: > {F_OVER:g} Hz for {T_OVER:g} s")):
            if lo < limit < hi:
                ax.axhline(limit, color=CRITICAL, lw=0.8, ls=DOTS, zorder=1, label=label)
        if lo < OVERSPEED_HZ < hi:
            ax.axhline(OVERSPEED_HZ, color=CRITICAL, lw=0.8, ls=DASHES, zorder=1,
                       label=f"Genset over-speed shutdown: > {OVERSPEED_HZ:g} Hz")
        finish(fig, ax, "frequency", lines)

    # --- active power of every source and of the load, and what the cable carries
    def power():
        series = powers("p")
        if not series:
            return
        fig, ax, lines = figure("Active power [kW]", before_end("p", "kW", series))
        if "bess" in series:
            series["bess"] = ("Battery (main board), + discharging", series["bess"][1])
        draw_powers(ax, series)
        drawn = [values for _, values in series.values()]
        cable = total_kw(curves, "p_l1")
        if cable and ("dg2" in series or "L1" in off or any(e[1] == "L1" for e in r.get("spec", []))):
            cable = [net.get("l1_sign", 1.0) * v for v in cable[:n]]
            ax.plot(x, cable, color=COLOUR["l1"], lw=1.1, ls=(0, (6, 1.5, 1, 1.5)), zorder=2,
                    label=f"Through {net['l1'].loc_name} to the main board")
            drawn.append(cable)
        lo, hi = span(drawn)
        ax.set_ylim(lo, hi)
        for role in ("dg1", "dg2"):
            prime = GENSET_DATA[role]["prime_kw"]
            top = max(series[role][1][i] for i in shown) if role in series else 0.0
            if lo < prime < hi and top >= 0.95 * prime and top > ind.get(f"p_{role}_0", 0.0) + 0.05 * prime:
                ax.axhline(prime, color=COLOUR[role], lw=0.8, ls=DOTS, zorder=1,
                           label=f"{names[role]} prime rating, {prime:.0f} kW")
        finish(fig, ax, "power", lines)

    # --- reactive power
    def reactive():
        series = powers("q")
        if not series:
            return
        fig, ax, lines = figure("Reactive power [kvar]", before_end("q", "kvar", series))
        draw_powers(ax, series)
        ax.set_ylim(*span([values for _, values in series.values()]))
        finish(fig, ax, "reactive", lines)

    # --- voltage at the busbars
    def voltage():
        where = BUS_TEXT[ind["u_min_at"]].lower()
        note = (f"Lowest {ind['u_min']:.2f} pu ({where}), highest {ind['u_max']:.2f} pu"
                + (f";  below {U_LOW:g} pu for {ind['u_below_09']:.2f} s" if ind["u_below_09"] > 0 else "")
                + (f";  above {U_HIGH:g} pu for {ind['u_above_11']:.2f} s" if ind["u_above_11"] > 0 else "")
                + (f";  back {ind['u_recovery']:.2f} s after {'clearing' if fault else 'the event'}"
                   if ind.get("u_recovery") else ""))
        fig, ax, lines = figure("Positive-sequence voltage [pu]" if unbalanced else "Voltage [pu]", note)
        drawn = []
        for key in BUS_KEYS:
            if f"u_{key}" in curves and not (key == "main" and same(net["bus"]["gen"], net["bus"]["main"])):
                drawn.append(curves[f"u_{key}"][:n])
                ax.plot(x, drawn[-1], color=BUS_COLOUR[key], lw=1.6, ls=BUS_STYLE[key], label=BUS_TEXT[key], zorder=3)
        lo, hi = span(drawn, least=(0.86, 1.12))
        lo = max(lo, -0.02)
        ax.set_ylim(lo, hi)
        ax.axhline(U_LOW, color=INK2, lw=0.8, ls=DOTS, zorder=1)
        ax.axhline(U_HIGH, color=INK2, lw=0.8, ls=DOTS, zorder=1, label=f"Stable: ends within {U_LOW:g} to {U_HIGH:g} pu")
        reach = [values[i] for values in drawn for i in shown] or [1.0]
        steps = [(u, f"Inverter trip, assumed: < {u:g} pu for {s:g} s") for u, s in U_UNDER_STEPS
                 if lo < u < U_LOW and min(reach) < U_LOW]
        steps += [(u, f"Inverter trip, assumed: > {u:g} pu for {s:g} s") for u, s in U_OVER_STEPS
                  if U_HIGH < u < hi and max(reach) > U_HIGH]
        for u, label in steps:
            ax.axhline(u, color=CRITICAL, lw=0.8, ls=DOTS, zorder=1, label=label)
        finish(fig, ax, "voltage", lines)

    # --- the three phase voltages at the main board, where the inverters sit (unbalanced faults)
    def phase_voltages():
        if not all(f"u{ph}_main" in curves for ph in "abc"):
            return
        note = (f"Lowest phase {fmt(ind.get('uph_min_main'))} pu after the event: the inverters' assumed "
                "under-voltage trip is judged on the lowest phase")
        fig, ax, lines = figure("Phase voltage at the main board [pu]", note)
        drawn = []
        for ph, colour in zip("abc", PHASE_COLOUR):
            drawn.append(curves[f"u{ph}_main"][:n])
            ax.plot(x, drawn[-1], color=colour, lw=1.6, label=f"Phase {ph.upper()}", zorder=3)
        if "u_main" in curves:
            ax.plot(x, curves["u_main"][:n], color=INK, lw=0.9, ls=DASHES, label="Positive sequence", zorder=4)
        lo, hi = span(drawn, least=(0.86, 1.12))
        lo = max(lo, -0.02)
        ax.set_ylim(lo, hi)
        for u, hold in U_UNDER_STEPS:
            if lo < u < U_LOW:
                ax.axhline(u, color=CRITICAL, lw=0.8, ls=DOTS, zorder=1,
                           label=f"Inverter trip, assumed: < {u:g} pu for {hold:g} s")
        finish(fig, ax, "phases", lines)

    # --- rotor angle of DG_2 against DG_1 (both sets running throughout)
    def angle():
        if "angle_dg2" not in curves or "angle0" not in ind:
            return
        note = (f"Before {ind['angle0']:.0f} deg, largest departure {ind['angle_swing']:.0f} deg"
                + (";  the sets fall out of step" if ind.get("slip") else ""))
        fig, ax, lines = figure(f"Rotor angle of {names['dg2']}\nagainst {names['dg1']} [deg]", note)
        values = unwrap(curves["angle_dg2"])[:n]
        ax.plot(x, values, color=COLOUR["dg2"], lw=1.7, zorder=3)
        lo, hi = span([values], least=(ind["angle0"] - 10.0, ind["angle0"] + 10.0))
        ax.set_ylim(max(lo, ind["angle0"] - 400.0), min(hi, ind["angle0"] + 400.0))
        finish(fig, ax, "angle", lines)

    # --- currents during a fault
    def current():
        if not fault:
            return
        drawn, biggest = [], []
        for role, label in (("dg1", names["dg1"]), ("dg2", names["dg2"]), ("bess", "Battery"), ("pv", "PV"),
                            ("l1", f"Cable {net['l1'].loc_name}" if net.get("l1") is not None else "Cable")):
            keys = [k for k in curves if k.startswith("i_" + role) and isinstance(curves[k], list)]
            if keys and f"i_{role}_max" in ind:
                biggest.append(f"{label} {ind[f'i_{role}_max']:.1f}")
            for k, key in enumerate(keys):
                drawn.append((role, label if k == 0 else None, curves[key][:n]))
        if not drawn:
            return
        fig, ax, lines = figure("Current [multiples of the rated current]", "Largest:  " + ",  ".join(biggest))
        for role, label, values in drawn:
            ax.plot(x, values, color=COLOUR[role], lw=1.1 if role == "l1" else 1.7,
                    ls=(0, (6, 1.5, 1, 1.5)) if role == "l1" else "-", label=label, zorder=2 if role == "l1" else 3)
        ax.set_ylim(0.0, span([values for _, _, values in drawn], least=(0.0, 1.2))[1])
        finish(fig, ax, "current", lines)

    for what, draw in (("frequency", frequency), ("power", power), ("reactive power", reactive),
                       ("voltage", voltage), ("phase voltage", phase_voltages), ("rotor angle", angle),
                       ("current", current)):
        try:
            draw()
        except Exception as e:
            warn(f"{r['case']}: the {what} plot could not be drawn ({type(e).__name__}: {e})")
        finally:
            plt.close("all")
    return len(made)


def legend_below(fig, ax, ncol):
    try:
        fig.legend(loc="outside lower center", ncol=ncol, frameon=False)
    except ValueError:                              # matplotlib older than 3.7
        ax.legend(loc="best", frameon=False)


def summary_base(plt, rows, title, xlabel, right=None):
    """A figure with one line per case: the case and its text on the left, and
    optionally a text on the right of the plot."""
    labels = [textwrap.fill(f"{r['case']}  {r['text']}", 44, subsequent_indent="      ") for r in rows]
    y = list(range(len(rows)))[::-1]
    fig, ax = new_figure(plt, 19, 3.6 + 0.72 * len(rows))
    fig.suptitle(title, x=0.01, ha="left", fontsize=9.5)
    ax.set_xlabel(xlabel)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, ha="left", fontsize=7.5, linespacing=1.05)
    ax.tick_params(axis="y", length=0, pad=4.1 * max(len(line) for text in labels for line in text.split("\n")))
    ax.grid(axis="y", visible=False)
    ax.set_ylim(-0.7, len(rows) - 0.3)
    for yi, text in zip(y, right or []):
        ax.annotate(text, xy=(1, yi), xycoords=("axes fraction", "data"), xytext=(6, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=7.5, color=INK2, annotation_clip=False, linespacing=1.05)
    return fig, ax, y


def summary_frequency(plt, results, suffix="", tail=""):
    """All cases side by side: the range of frequency each one goes through, and its outcome."""
    rows = [r for r in results if "f_min" in r["ind"]]
    if not rows:
        return 0
    fig, ax, y = summary_base(plt, rows, "Frequency range in each case, and the outcome (PowerFactory RMS)" + tail,
                              "Frequency [Hz]",
                              right=[textwrap.fill(verdict_text(r["ind"]).split(" (")[0], 31) for r in rows])
    ax.axvspan(F_NOM * (1 - F_BAND), F_NOM * (1 + F_BAND), color=BAND_FILL, lw=0, zorder=0,
               label=f"Stable: ends within {F_BAND * 100:g} % of {F_NOM:g} Hz")
    ax.axvline(F_UNDER, color=CRITICAL, lw=0.8, ls=DOTS)
    ax.axvline(F_OVER, color=CRITICAL, lw=0.8, ls=DOTS,
               label=f"Inverter trip limits, assumed: {F_UNDER:g} and {F_OVER:g} Hz")
    ax.axvline(OVERSPEED_HZ, color=CRITICAL, lw=0.8, ls=DASHES, label=f"Genset over-speed shutdown, {OVERSPEED_HZ:g} Hz")
    def colour_of(i):
        return WARNING if i.get("best_case") and i["verdict"] == "stable" else VERDICT_COLOUR.get(i["verdict"], SOFT)

    for yi, r in zip(y, rows):
        i = r["ind"]
        lo, hi = max(i["f_min"], BLACKOUT_HZ), min(i["f_max"], OVERSPEED_HZ)
        ax.plot([lo, hi], [yi, yi], color=colour_of(i), lw=5, solid_capstyle="butt", zorder=3)
        if not i.get("ended"):
            ax.plot([i["f_end"]], [yi], marker="|", color=INK, ms=8, mew=1.3, zorder=4, ls="none")
        ax.text(hi + 0.3, yi, f"{freq_low(i)} to {freq_high(i)}", va="center", fontsize=7.5, color=INK2, zorder=5,
                bbox=dict(facecolor="white", edgecolor="none", pad=0.8))
    ax.plot([], [], marker="|", color=INK, ms=8, mew=1.3, ls="none", label="Frequency at the end")
    for colour, label in ((GOOD, "Stable"), (WARNING, "Stable with a reservation, or outside limits"),
                          (SERIOUS, "Unstable as modelled, or supply lost"), (CRITICAL, "Unstable"),
                          (SOFT, "No result")):                  # the outcome itself is written beside each case
        if any(colour_of(r["ind"]) == colour for r in rows):
            ax.plot([], [], color=colour, lw=5, solid_capstyle="butt", label=label)
    ax.set_xlim(BLACKOUT_HZ - 0.5, 67.0)
    legend_below(fig, ax, 2)
    fig.savefig(os.path.join(OUT_DIR, f"summary_frequency{suffix}.png"), facecolor="white")
    plt.close(fig)
    return 1


def summary_voltage(plt, results, suffix="", tail=""):
    """Cases without a fault: the lowest and highest voltage at each busbar, and where it ends."""
    rows = [r for r in results if "u_min" in r["ind"] and not r["ind"].get("is_fault")]
    if not rows:
        return 0
    fig, ax, y = summary_base(plt, rows, "Voltage range at each busbar, cases without a fault (PowerFactory RMS)" + tail,
                              "Voltage [pu]")
    ax.axvline(U_LOW, color=INK2, lw=0.8, ls=DOTS)
    ax.axvline(U_HIGH, color=INK2, lw=0.8, ls=DOTS, label=f"Stable: ends within {U_LOW:g} to {U_HIGH:g} pu")
    offsets = {"gen": 0.24, "main": 0.0, "load": -0.24}
    lowest, highest = U_LOW, U_HIGH
    for n, (yi, r) in enumerate(zip(y, rows)):
        i = r["ind"]
        for k in BUS_KEYS:
            if f"umin_{k}" not in i:
                continue
            lo, hi = i[f"umin_{k}"], min(i[f"umax_{k}"], 1.5)
            lowest, highest = min(lowest, lo), max(highest, hi)
            ax.plot([lo, hi], [yi + offsets[k]] * 2, color=BUS_COLOUR[k], lw=2.4, solid_capstyle="butt",
                    label=BUS_TEXT[k] if n == 0 else None, zorder=3)
            ax.plot([min(i[f"uend_{k}"], 1.5)], [yi + offsets[k]], marker="|", color=INK, ms=5.5, mew=1.2, zorder=4,
                    ls="none")
    ax.plot([], [], marker="|", color=INK, ms=5.5, mew=1.2, ls="none", label="Voltage at the end")
    ax.set_xlim(lowest - 0.04, highest + 0.04)
    legend_below(fig, ax, 3)
    fig.savefig(os.path.join(OUT_DIR, f"summary_voltage{suffix}.png"), facecolor="white")
    plt.close(fig)
    return 1


def summary_faults(plt, results, suffix="", tail=""):
    """Fault cases: how long each busbar stays below U_LOW, against how long the fault lasts."""
    rows = [r for r in results if "u_min" in r["ind"] and r["ind"].get("is_fault")]
    if not rows:
        return 0
    # a busbar that does not come back must not set the scale
    longest = max([0.3] + [r["ind"][f"ulow_{k}"] for r in rows for k in BUS_KEYS
                           if f"ulow_{k}" in r["ind"] and r["ind"].get(f"uend_{k}", 1.0) >= U_LOW])
    right = []
    for r in rows:
        i = r["ind"]
        gone = [BUS_TEXT[k].lower() for k in BUS_KEYS if i.get(f"ulow_{k}", 0.0) > 1.2 * longest]
        if gone:
            right.append(f"{i['verdict']}:\n{' and '.join(gone)}\nnot restored")
        elif i.get("u_recovery") is not None:
            right.append(f"back {i['u_recovery']:.2f} s after clearing")
        else:
            right.append(i["verdict"])
    fig, ax, y = summary_base(plt, rows, f"Time below {U_LOW:g} pu at each busbar in the fault cases (PowerFactory RMS)" + tail,
                              f"Longest time below {U_LOW:g} pu [s]", right=right)
    offsets = {"gen": 0.24, "main": 0.0, "load": -0.24}
    for n, (yi, r) in enumerate(zip(y, rows)):
        i = r["ind"]
        for k in BUS_KEYS:
            if f"ulow_{k}" in i:
                ax.barh(yi + offsets[k], i[f"ulow_{k}"], height=0.2, color=BUS_COLOUR[k],
                        label=BUS_TEXT[k] if n == 0 else None, zorder=3)
        ax.plot([i.get("t_clear", 0.0)] * 2, [yi - 0.42, yi + 0.42], color=CRITICAL, lw=1.2, zorder=4,
                label="How long the fault lasts" if n == 0 else None)
    ax.set_xlim(0.0, 1.2 * longest)
    ax.grid(axis="x", visible=True)
    legend_below(fig, ax, 4)
    fig.savefig(os.path.join(OUT_DIR, f"summary_faults{suffix}.png"), facecolor="white")
    plt.close(fig)
    return 1


CONTROLLER_COLOUR = ("#2a78d6", "#eb6834", "#1baf7a")   # the first three categorical slots, by controller


def controller_colour(name):
    """A controller keeps its colour in every figure, whichever controllers a run has."""
    known = list(dict.fromkeys(list(DISPATCH) + list(QDS_RUNNERS)))
    if name not in known:
        known.append(name)
    k = known.index(name)
    return CONTROLLER_COLOUR[k] if k < len(CONTROLLER_COLOUR) else SOFT


def by_case(runs):
    """The runs of one operating point, case by case and the controllers together, labelled
    for the summary plots: the case id, its text and the controller."""
    order = {c[0]: n for n, c in enumerate(CASES)}
    names = list(dict.fromkeys(OPS[r["op"]]["controller"] for r in runs))
    rows = []
    for r in sorted(runs, key=lambda r: (order.get(r["case"].split("_")[0], len(order)),
                                         names.index(OPS[r["op"]]["controller"]))):
        sid = r["case"].split("_")[0]
        text = next((c[1] for c in CASES if c[0] == sid), r["text"])
        rows.append(dict(r, case=sid, text=f"{text}  ({OPS[r['op']]['controller']})"))
    return rows


def summary_head_to_head(plt, runs, iid):
    """The controllers side by side at one operating point, case by case: rate of change of
    frequency, margin to the inverters' frequency trip, how fast the frequency is back at 50 Hz
    to stay, the time left before their fast under-voltage trip, and the outcome. Made for a
    report: one figure that says which dispatch leaves the island more stable at that point."""
    ops = [op for op, o in OPS.items() if o.get("instant") == iid]
    names = [OPS[op]["controller"] for op in ops]
    found = {(r["case"].split("_")[0], OPS[r["op"]]["controller"]): r for r in runs}
    cases = [c for c in CASES if any((c[0], name) in found for name in names)]
    if not cases or not names:
        return 0
    first = OPS[ops[0]]
    def unsettled(i):
        return "blackout" if i.get("ended") else "no result" if i.get("verdict") == "no result" else "not settled"

    # (title, value, its label, text where there is no value)
    panels = [("ROCOF\n[Hz/s]", lambda i: i.get("rocof_500"), lambda i, v: f"{v:.2f}", unsettled),
              ("Frequency margin\nto trip [Hz]", lambda i: i.get("f_margin"),
               lambda i, v: f"{v:.2f} to {F_UNDER if i.get('f_margin_to') == 'under' else F_OVER:g} Hz", unsettled),
              ("Settling\ntime [s]", lambda i: i.get("f_settle_nom"), lambda i, v: f"{v:.2f}", unsettled),
              ("Ride-through\nmargin [s]", lambda i: i.get("u_margin_s"),
               lambda i, v: f"{v:.2f}",
               lambda i: (unsettled(i) if i.get("verdict") == "no result" or "u_low_inv" not in i
                          else f"never < {U_UNDER:g} pu (low {i['u_low_inv']:.2f})"))]
    k = len(names)
    height = 0.8 / k                                        # the bars of one case share 0.8 of a row
    fig, axes = plt.subplots(1, len(panels) + 1, sharey=True, figsize=(18 / 2.54, (3.4 + 0.45 * k + 0.62 * len(cases) * max(k, 2) / 2) / 2.54),
                             gridspec_kw=dict(width_ratios=[1] * len(panels) + [0.85]), layout="constrained")
    dispatch = "\n".join(
        f"{OPS[op]['controller']}: battery {OPS[op]['bess']:+.0f} kW (SOC {fmt(OPS[op]['soc'], '{:.0f}')} %), "
        f"PV {OPS[op]['pv']:.0f} kW, {DG2_NAME} {'on' if OPS[op]['dg2'] > 0 else 'off'}" for op in ops)
    fig.suptitle(f"Head-to-head at {iid}: {first['label']}, {first['time']} (load {first['load']:.0f} kW)\n{dispatch}",
                 x=0.01, ha="left", fontsize=8.5, linespacing=1.4)
    y = list(range(len(cases)))[::-1]
    for ax, (title, value, label, none_text) in zip(axes, panels):
        ax.set_title(title, fontsize=7.5, loc="left")
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="x", labelsize=7)
        top, bottom = 0.0, 0.0
        for yi, (sid, _, _) in zip(y, cases):
            for j, name in enumerate(names):
                yy = yi + 0.4 - height * (j + 0.5)
                r = found.get((sid, name))
                i = r["ind"] if r else {}
                v = value(i) if r else None
                if r is None:
                    continue
                if i.get("verdict") == "not applicable":
                    ax.text(0, yy, " " + i.get("why", "not run"), va="center", ha="left", fontsize=6, color=SOFT)
                elif v is None:
                    why = none_text(i)
                    ax.text(0, yy, " " + why, va="center", ha="left", fontsize=6,
                            color=CRITICAL if why == "blackout" else INK2)
                else:
                    top, bottom = max(top, v), min(bottom, v)
                    ax.barh(yy, v, height=height * 0.9, color=controller_colour(name), edgecolor="white", lw=0.8,
                            zorder=3)
                    # a negative margin: past the threshold, said in words as well as by the bar
                    ax.text(max(v, 0.0), yy, " " + label(i, v) + (" (past it)" if v < 0 else ""), va="center",
                            ha="left", fontsize=6, color=CRITICAL if v < 0 else INK2, zorder=4)
        span_x = (top - bottom) or 1.0
        ax.set_xlim(bottom - (0.1 * span_x if bottom < 0 else 0.0), top + 0.6 * span_x)
        if bottom < 0:
            ax.axvline(0.0, color=INK2, lw=0.8, zorder=2)
    # the outcome of each run, as words: colour never stands alone
    ax = axes[-1]
    ax.set_title("Outcome", fontsize=7.5, loc="left")
    ax.axis("off")
    for yi, (sid, _, _) in zip(y, cases):
        for j, name in enumerate(names):
            r = found.get((sid, name))
            if r is None:
                continue
            i = r["ind"]
            verdict = i.get("verdict", "?")
            word = "not run" if verdict == "not applicable" else SHORT_VERDICT.get(verdict, verdict)
            colour = SOFT if verdict == "not applicable" else (
                WARNING if i.get("best_case") and verdict == "stable" else VERDICT_COLOUR.get(verdict, SOFT))
            yy = yi + 0.4 - height * (j + 0.5)
            ax.plot([0.03], [yy], marker="s", ms=4.5, color=colour, transform=ax.get_yaxis_transform(), clip_on=False)
            ax.text(0.1, yy, word, va="center", ha="left", fontsize=6.5, color=INK, transform=ax.get_yaxis_transform())
    axes[0].set_yticks(y)
    axes[0].set_yticklabels([textwrap.fill(f"{sid}  {text}", 30) for sid, text, _ in cases], fontsize=6.5,
                            linespacing=1.0)
    axes[0].set_ylim(-0.6, len(cases) - 0.4)
    fig.supxlabel(textwrap.fill(
        f"ROCOF over 500 ms.  Frequency margin: how far the frequency stays from the inverters' assumed trip at "
        f"{F_UNDER:g} or {F_OVER:g} Hz (negative: past it).  Settling time: until the frequency is back within "
        f"{F_SETTLE_HZ:g} Hz of {F_NOM:g} Hz to stay.  Ride-through margin: time left of the {T_U_UNDER:g} s the "
        f"inverters may spend below {U_UNDER:g} pu (lowest phase in unbalanced faults).", 150),
        fontsize=6.5, color=INK2, x=0.01, ha="left")
    from matplotlib.patches import Patch
    handles = [Patch(color=controller_colour(name), label=f"{name} dispatch") for name in names]
    try:
        fig.legend(handles=handles, loc="outside upper right", ncol=1, frameon=False)
    except ValueError:                              # matplotlib older than 3.7
        axes[0].legend(handles=handles, loc="best", frameon=False)
    fig.savefig(os.path.join(OUT_DIR, f"head_to_head_{iid}.png"), facecolor="white")
    plt.close(fig)
    return 1


# ---------------------------------------------------------------------------- plots inside PowerFactory
def pf_pages(results, net):
    """A plot page per case inside PowerFactory (pf_six_page), drawn by PowerFactory from that
    case's result object, and saved as an image with PowerFactory's own export if wanted."""
    board = app.GetGraphicsBoard() or app.GetFromStudyCase("SetDesktop")
    if board is None:
        warn("PowerFactory plot pages skipped: no graphics board is open")
        return
    texts = {s[0]: s[1] for s in CASES}
    made, saved, export = [], [], PF_PLOT_PNG
    for r in results:
        curves, sid = r["curves"], r["case"].split("_")[0]
        if not curves or not curves.get("result") or (PF_PLOT_CASES is not None and sid not in PF_PLOT_CASES):
            continue
        o = OPS.get(r["op"], {})
        block = ([f"{sid}  {texts.get(sid, r.get('text', ''))}".strip(),
                  ", ".join(str(o[k]) for k in ("label", "time") if o.get(k)),
                  f"Controller: {o['controller']}" if o.get("controller") else ""], r["case"])
        try:
            page = pf_six_page(board, net, f"Stab {r['case']}", [("", curves)], block)
            made.append(r["case"])
        except Exception as e:
            warn(f"Could not build the PowerFactory plot page for {r['case']}: {e}")
            continue
        if export:
            try:
                pf_export(page, os.path.join(OUT_DIR, f"{r['case']}_powerfactory.png"))
                saved.append(r["case"])
            except Exception as e:
                export = False
                warn(f"PowerFactory's own image export did not work ({e}); the pages themselves are there. Set "
                     "PF_PLOT_PNG = False to skip it, or use the page's export button.")
    if made:
        log("PowerFactory plot pages (Graphics Board tabs 'Stab <case>'): " + ", ".join(made))
    if saved:
        log(f"The same pages as images from PowerFactory (<case>_powerfactory.png in {OUT_DIR}): " + ", ".join(saved))


# ============================================================================
# MAIN
# ============================================================================
def run_stability():
    """The RMS stability study (MODE). The model and the study time are put back at the end."""
    log(f"\nRMS stability study, {datetime.now():%Y-%m-%d %H:%M}")

    changes = ModelChanges()
    case, original_time, results, net, sim, cct, model_notes, tests = None, None, [], None, None, [], [], []
    try:
        if T_EVENT < 0.2 or STEP_S <= 0 or STEP_S > 0.02:
            raise Stop("T_EVENT has to be at least 0.2 s (the time before it shows the steady state) and STEP_S "
                       "between 0 and 0.02 s")
        if MODE not in ("survey", "controllers", "run") or CONTROLLERS not in ("auto", "library", "own", "keep"):
            raise Stop('MODE has to be "survey", "controllers" or "run", and CONTROLLERS "auto", "library", "own" '
                       'or "keep"')
        case = open_project()
        original_time = attr(case, "iStudyTime")
        ldf = app.GetFromStudyCase("ComLdf")
        net = find_network(changes)
        ready = survey(net, changes)
        wanted, points = [], []
        if MODE == "survey":
            if CONTROLLERS != "keep":
                library_report(net)
            log("\nSurvey only (MODE = 'survey'): nothing was changed. MODE = 'controllers' sets the governors and "
                "voltage regulators up and tests them; MODE = 'run' goes on to the scenarios.")
        else:
            ops, wanted, skipped = {}, [], []
            if MODE == "run" or SELF_TEST:              # first, so that the self-test can use the points
                ops, wanted, skipped = comparison_plan(net)
                OPS.update(ops)
            sim = prepare_simulation(case, changes)
            tests = controllers_step(sim, case, ldf, net, changes, ready)
            model_notes = controller_notes(net) + ready["notes"]
            passed = [f"{r['who']}: lowest {r['ind']['f_min']:.2f} Hz, at rest after {r['ind']['t_settle']:.1f} s"
                      + (", poorly damped" if r["ind"].get("rings") else "")
                      for r in tests if r["ind"].get("verdict") in ("stable", "outside limits")
                      and r["ind"].get("t_settle") is not None]
            if passed:
                model_notes.insert(1 if _plant["route"] else 0, f"self-test of the controllers passed, a load step of "
                                   f"{SELF_TEST_STEP_PCT:g} % (" + "; ".join(passed) + ")")
        if MODE == "controllers":
            wanted = []
            log("\nThe controllers are set up and tested (MODE = 'controllers'). Set MODE = 'run' for the scenarios.")
        elif MODE == "run":
            results += skipped
            if CCT_SEARCH:
                points = [(op, "main") for op in ops]
            names = list(dict.fromkeys(o["controller"] for o in ops.values()))
            source = INSTANTS_FILE or "POINTS and DISPATCH"
            log(f"\n=== Comparison of {len(names)} controller(s) ({', '.join(names)}) at "
                f"{len({o['instant'] for o in ops.values()})} operating point(s) from {source}: {len(wanted)} runs, "
                f"{len(skipped)} cases not simulated" + (f", {len(points)} critical clearing times (about eight "
                "runs each)" if points else "") + f"; step {STEP_S * 1000:g} ms ===")
            for r in skipped:
                log(f"   {r['case']}  not simulated: {r['ind']['why']}")
        for op in OPS:
            mine = [c for c in wanted if c[1] == op]
            search = [where for o, where in points if o == op]
            if not mine and not search:
                continue
            if OPS[op]["dg2"] > 0 and not ready["dg2"] and REQUIRE_CONTROLLERS:
                warn(f"{op} is skipped: {net['dg2'].loc_name} runs there and has no governor or voltage regulator")
                continue
            try:
                point = set_operating_point(case, ldf, net, changes, op)
            except CaseProblem as why:                  # an instant of a log that this model does not reproduce
                warn(f"{op} is not run: {why}")
                for cid, _, text, spec in mine:
                    results.append(dict(case=cid, op=op, text=text, spec=spec, curves=None, point=None,
                                        ind=dict(problem=str(why), verdict="no result", why=str(why), notes=[])))
                continue
            failed, sim["rebuilds"], sim["rebuild_s"] = 0, 0, 0.0
            for n, (cid, _, text, spec) in enumerate(mine):
                t_end = T_END_FAULT if any(e[0] == "fault" for e in spec) else T_END
                started = time.time()
                try:
                    ind, curves = run_case(sim, net, cid, spec, point, t_end)
                except Stop:
                    raise
                except Exception as e:               # one case must not cost the others
                    import traceback
                    why = f"the script failed on this case ({type(e).__name__}: {e})"
                    ind, curves = dict(problem=why, verdict="no result", why=why, notes=[]), None
                    for line in traceback.format_exc().rstrip().split("\n"):
                        log("      " + line)
                results.append(dict(case=cid, op=op, text=text, spec=spec, ind=ind, curves=curves, point=point))
                if ind.get("skipped"):
                    log(f"   {cid}  {text}: not run ({ind['why']})")
                    continue
                log(f"   {cid}  {text}: {outcome_text(ind, notes=False)}   [computed in {time.time() - started:.0f} s]")
                if ind.get("steady") is False:
                    warn(f"{cid}: frequency or voltage was already moving before the event - the initial "
                         "conditions are not a steady state (check the governor and regulator limits)")
                if curves is not None or ind.get("setup"):
                    failed = 0
                    continue
                failed += 1
                if failed >= 2:
                    left = [c[0] for c in mine[n + 1:]]
                    warn(f"Two cases in a row could not be simulated at {op}"
                         + (": its other cases are not run (" + ", ".join(left) + ")" if left else ""))
                    break
            for where in search:
                if failed >= 2:
                    break
                place = f"{'on' if where == 'L1' else 'at'} {PLACES[where]}"
                log(f"\n   Critical clearing time, three-phase fault {place}, {op}:")
                started = time.time()
                found = dict(critical_clearing_time(sim, net, point, where), op=op, where=where)
                cct.append(found)
                log(f"   {cct_text(found)}   [{found['runs']} cases computed in {time.time() - started:.0f} s]")
                for text in (found["at_hi"], found["note"]):
                    if text:
                        log("      " + text)
            if sim["rebuilds"]:
                log(f"   (the point was built again before each of its {sim['rebuilds']} runs: "
                    f"{sim['rebuild_s']:.1f} s in all, {sim['rebuild_s'] / sim['rebuilds']:.2f} s a run)")
    except Stop as msg:
        warn(f"STOPPED: {msg}")
    except KeyboardInterrupt:
        warn("Interrupted. The model is put back and the cases finished so far are reported below.")
    except Exception:
        import traceback
        warn("The run broke off with an error. The cases finished so far are reported below.")
        for line in traceback.format_exc().rstrip().split("\n"):
            log("   " + line)
    finally:
        if sim is not None:
            try:
                clear_events(sim["events"], sim["ldf"])
            except BaseException as e:
                warn(f"The script's own simulation events ('{EVENT_PREFIX}...') could not be deleted "
                     f"({type(e).__name__}: {e}): delete them by hand, or they act in your next simulation")
            try:
                sim["inc"].SetAttribute("p_resvar", sim["resvar0"])
            except BaseException:
                pass
        if RESTORE_MODEL_AFTER_RUN:
            n = changes.restore()
            if case is not None and original_time is not None:
                try:
                    app.EchoOff()
                    try:
                        case.SetStudyTime(int(original_time))
                    finally:
                        app.EchoOn()
                except BaseException as e:
                    warn(f"The study time could not be put back ({type(e).__name__})")
            if n > 0:
                log(f"\nModel restored ({n} settings and the study time put back)."
                    + (" The result objects 'Stability ...' stay in the study case." if results or tests else "")
                    + (" The governors and voltage regulators the script set up stay in the model."
                       if _plant["route"] and _plant["proven"] else ""))
            elif n < 0:
                warn(f"The model is only partly restored ({-n} settings put back): see the list above")

    if results or cct or tests:
        compared = any("instant" in OPS.get(r["op"], {}) for r in results)
        if compared:                            # instant by instant, the suite in its order, the controllers together
            ops = list(OPS)
            suite = {s[0]: n for n, s in enumerate(CASES)}
            results.sort(key=lambda r: (OPS[r["op"]]["instant"] if r["op"] in OPS else "",
                                        suite.get(r["case"].split("_")[0], len(suite)),
                                        ops.index(r["op"]) if r["op"] in ops else len(ops)))
        else:
            order = {c[0]: n for n, c in enumerate(CASES)}
            results.sort(key=lambda r: order.get(r["case"], len(order)))
        steps = []
        if results or cct:
            steps.append(("the tables", (lambda: print_comparison(results, cct)) if compared else
                          (lambda: print_tables(results, cct, model_notes))))
        steps.append(("the result files", lambda: save_outputs(results, cct, tests)))
        if PNG_PLOTS:
            steps.append(("the PNG plots", lambda: plot_pngs(results, net, tests, compared=compared)))
        if PF_PLOTS and results:
            steps.append(("the PowerFactory plot pages", (lambda: pf_compare_pages(results, net)) if compared else
                          (lambda: pf_pages(results, net))))
        for what, step in steps:                     # each on its own: one that fails does not cost the others
            try:
                step()
            except (Exception, KeyboardInterrupt):
                import traceback
                warn(f"Writing {what} broke off with an error:")
                for line in traceback.format_exc().rstrip().split("\n"):
                    log("   " + line)
        log("\nDone.")


# ============================================================================
# QUASI-DYNAMIC DISPATCH: a controller steps through the time series, one load flow at a time
# ============================================================================
# Every QDS_STEP_MIN minutes of the period the dispatch
#   1. sets the PowerFactory study time, so every time characteristic (load profile, PV)
#      moves to that instant,
#   2. runs a load flow to see the load and the PV the sun allows,
#   3. applies the controller's rules and sets DG_2, the battery and the PV curtailment,
#   4. runs the load flow again with those set-points and logs the result.
# Output per run: a CSV log (DISPATCH_DIR), three PNG plots and three plot pages inside
# PowerFactory, every active power in kW. A run that ends at an operating point is saved
# as <controller>_<point>.csv: its last row is the state the stability study starts from.
#
# THE RULE-BASED CONTROLLER (classic "load following", applied at every step, in this order)
#   1. PV first: every kW the sun gives is used, unless rule 4 has to cut it.
#   2. The battery discharges to cover whatever load is above DG_1's minimum loading, so the
#      diesels burn as little fuel as possible.
#   3. Surplus that would push DG_1 below its minimum charges the battery. (At night this
#      means DG_1's minimum output tops the battery up.)
#   4. If the battery is full or at its power limit, PV is curtailed, so DG_1 never drops
#      below its minimum or into reverse power.
#   5. DG_2 is an on/off backup with hysteresis (start above DG2_ON_LEVEL, stop below
#      DG2_OFF_LEVEL of DG_1's rating, minimum run and stop times), judged on what the diesels
#      would still have to supply with the battery discharging as hard as it can. DG_2 is
#      therefore the backup for when the battery is empty or too small.
#   6. Once the battery reaches SOC_MIN it may not discharge again until it has been
#      recharged to SOC_RESUME, so it doesn't flick on and off.
QDS_COLUMNS = ["time", "load_kW", "pv_avail_kW", "pv_kW", "pv_curtailed_kW",
               "bess_kW", "bess_soc_pct", "other_kW", "dispatch_signal_kW",
               "DG1_kW", "DG1_loading_pct", "DG2_on", "DG2_kW",
               "DG2_on_level_kW", "DG2_off_level_kW"]
_qds = {"t0": 0, "t1": 0, "label": ""}      # the run in progress: first and last instant, name


def qds_step_s():
    return QDS_STEP_MIN * 60


def to_datetime(t):
    """Epoch seconds -> naive datetime in the same time basis as to_epoch."""
    if USE_LOCAL_TIME:
        return datetime.fromtimestamp(t)
    return datetime.fromtimestamp(t, timezone.utc).replace(tzinfo=None)


def fmt_time(t):
    return to_datetime(t).strftime("%Y-%m-%d %H:%M")


def epoch_of(text):
    """'YYYY-MM-DD HH:MM' -> epoch seconds."""
    when = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
    return to_epoch(when.year, when.month, when.day, when.hour, when.minute)


def set_study_time(case, t):
    """Move the study time with PowerFactory's info messages switched off."""
    app.EchoOff()
    try:
        case.SetStudyTime(int(t))
    finally:
        app.EchoOn()


def rating_kw(gen, override_kw=None):
    """Rated active power in kW = S_rated x cos(phi)_rated x parallel units."""
    if override_kw:
        return float(override_kw)
    kva, cosn = rating_kva_cos(gen)
    return kva * cosn


def characteristics_of(obj):
    """Time characteristics attached to obj, e.g. ['plini -> Load_Feb (ChaTime)'].
    PowerFactory stores each one as a ChaRef inside the element, named after the
    parameter it drives, with typ_id pointing at the characteristic."""
    refs = []
    for get in (lambda: obj.GetContents("*.ChaRef"),
                lambda: obj.GetChildren(1, "*.ChaRef", 0)):    # 1 = incl. hidden
        try:
            refs = list(get() or [])
        except Exception:
            refs = []
        if refs:
            break
    out = []
    for ref in refs:
        cha = attr(ref, "typ_id")
        text = f"{ref.loc_name} -> "
        text += f"{cha.loc_name} ({cha.GetClassName()})" if cha else "(no characteristic)"
        if attr(ref, "outserv", 0):
            text += " OUT OF SERVICE"
        out.append(text)
    return out


def connection_report(obj):
    cub = attr(obj, "bus1")
    if cub is None:
        return "NOT CONNECTED (no terminal)"
    term = attr(cub, "cterm")
    if term is None:
        return "NOT CONNECTED (cubicle without terminal)"
    info = f"{term.loc_name} ({attr(term, 'uknom', 0):g} kV)"
    if attr(term, "outserv", 0):
        info += "  !! terminal OUT OF SERVICE"
    try:
        for sw in cub.GetContents("*.StaSwitch"):
            if attr(sw, "on_off", 1) == 0:
                info += "  !! switch OPEN"
    except Exception:
        pass
    return info


def diagnose_load_flow():
    log("\n--- Messages from PowerFactory ---")
    keys = ("error", "warn", "isolated", "not converge", "reference", "slack")
    for line in pf_messages(keys, max_lines=25) or ["(no error/warning lines found)"]:
        log("  " + line)
    log("\n--- Connection check ---")
    for obj in calc_objects(["ElmSym", "ElmGenstat", "ElmPvsys", "ElmXnet"] + LOAD_CLASSES):
        ref = "  [REF]" if attr(obj, "ip_ctrl", 0) == 1 else ""
        log(f"  {obj.loc_name:<18}{obj.GetClassName():<11}-> {connection_report(obj)}{ref}")


def qds_solve(ldf, t, what="load flow"):
    """Load flow at the current study time; stop with diagnostics if it fails."""
    if run_command(ldf):
        return
    run_command(ldf, quiet=False)        # once more, with messages visible
    diagnose_load_flow()
    raise Stop(f"{what} failed at {fmt_time(t)} - see the messages above")


def qds_check_settings():
    if not 0 < DG2_OFF_LEVEL < DG2_ON_LEVEL:
        raise Stop("Need 0 < DG2_OFF_LEVEL < DG2_ON_LEVEL")
    if QDS_STEP_MIN <= 0:
        raise Stop("QDS_STEP_MIN has to be above 0")
    for unit in (FALLBACK_RESULT_UNIT, FALLBACK_SETPOINT_UNIT, FALLBACK_RATING_UNIT):
        if kilo_factor(unit) is None:
            raise Stop(f"Fallback unit '{unit}' not understood (use W/kW/MW, VA/kVA/MVA)")
    if BESS_E_KWH is None or BESS_E_KWH <= 0:
        raise Stop("Set BESS_E_KWH to the battery's usable energy capacity in kWh")
    if not 0 <= BESS_SOC_MIN < BESS_SOC_RESUME <= BESS_SOC_MAX <= 1:
        raise Stop("Need 0 <= BESS_SOC_MIN < BESS_SOC_RESUME <= BESS_SOC_MAX <= 1")
    if not BESS_SOC_MIN <= BESS_SOC_INIT <= BESS_SOC_MAX:
        raise Stop("BESS_SOC_INIT must lie between BESS_SOC_MIN and BESS_SOC_MAX")
    if not 0 < BESS_EFFICIENCY <= 1:
        raise Stop("BESS_EFFICIENCY must be between 0 and 1")


def qds_network(changes):
    """Isolate the grid, make DG_1 the reference machine, collect the objects."""
    dg1, dg2 = find_generator(DG1_NAME), find_generator(DG2_NAME)
    if dg1 is None:
        raise Stop(f"Generator '{DG1_NAME}' not found")
    if dg2 is None:
        raise Stop(f"Generator '{DG2_NAME}' not found")

    if ISOLATED:
        for xn in calc_objects(["ElmXnet"]):
            changes.set(xn, "outserv", 1)
            log(f"External grid '{xn.loc_name}' set out of service")

    changes.set(dg1, "outserv", 0)
    changes.set(dg1, "ip_ctrl", 1)          # reference (slack) machine
    changes.set(dg2, "ip_ctrl", 0)
    if attr(dg1, "ip_ctrl", 0) != 1:
        raise Stop(f"{DG1_NAME} could not be set as reference machine")

    loads = calc_objects(LOAD_CLASSES)
    if not loads:
        raise Stop("No in-service loads found")
    skip = {full_name(dg1), full_name(dg2)}
    others = [o for o in calc_objects(OTHER_SOURCE_CLASSES) if full_name(o) not in skip]

    r1 = rating_kw(dg1, DG1_RATING_KW)
    r2 = rating_kw(dg2, DG2_RATING_KW)
    if r1 <= 0 or r2 <= 0:
        raise Stop("Could not read a generator rating - check the generator types, "
                   "or set DG1_RATING_KW / DG2_RATING_KW")
    # A microgrid diesel outside 1 kW - 50 MW means a unit was misread, even
    # if every value is off by the same factor (the checks later can't see that).
    for gen, r in ((dg1, r1), (dg2, r2)):
        if not 1.0 <= r <= 50_000.0:
            warn(f"{gen.loc_name} rating reads as {r:,.1f} kW. If that is wrong, a unit "
                 "is misread: check the FALLBACK_* settings, or set DG1_RATING_KW / DG2_RATING_KW.")
    p_dg2 = float(DG2_P_KW) if DG2_P_KW else r2

    # DG_2 must take its P from 'pgini'; these input modes derive P otherwise.
    if str(attr(dg2, "mode_inp", "")).upper() in ("SC", "QC"):
        warn(f"{DG2_NAME} dispatch input mode is '{attr(dg2, 'mode_inp')}', which ignores "
             "the active power setpoint. Set it to P,Q (Load Flow page).")

    log(f"\nReference machine: {dg1.loc_name:<10} {r1:9.1f} kW  -> {connection_report(dg1)}")
    log(f"Backup (on/off):   {dg2.loc_name:<10} {r2:9.1f} kW  -> {connection_report(dg2)}")
    log(f"\nLoads ({len(loads)}) and their time characteristics:")
    for ld in loads:
        chars = characteristics_of(ld)
        log(f"   {ld.loc_name:<20} " + ("; ".join(chars) if chars else "!! none found"))
    log(f"Other sources ({len(others)}):")
    for o in others:
        chars = characteristics_of(o)
        log(f"   {o.loc_name:<20} {o.GetClassName():<11}"
            + ("; ".join(chars) if chars else "(no characteristic; PV solar model is fine)"))

    return {"dg1": dg1, "dg2": dg2, "r1": r1, "r2": r2, "p_dg2": p_dg2,
            "loads": loads, "others": others}


def set_dg2(changes, net, running, kw=None):
    """Start DG_2 at kw (None: its fixed output, DG2_P_KW or its rating), or stop it (out of service)."""
    dg2 = net["dg2"]
    if running:
        units = attr(dg2, "ngnum", 1) or 1          # pgini is per parallel unit
        per_unit_kw = (net["p_dg2"] if kw is None else kw) / units
        changes.set(dg2, "pgini",
                    per_unit_kw / to_kilo_factor(dg2, "pgini", FALLBACK_SETPOINT_UNIT))
    changes.set(dg2, "outserv", 0 if running else 1)


def qds_measure(net, dg2_on):
    """(load, other sources, DG_1, DG_2) in kW from the last load flow."""
    load = sum(p_kw(o) for o in net["loads"])
    other = sum(p_kw(o, generator=True) for o in net["others"])
    p1 = p_kw(net["dg1"], generator=True)
    p2 = p_kw(net["dg2"], generator=True) if dg2_on else 0.0
    return load, other, p1, p2


def preflight(case, ldf, net, t0, t1):
    """Catch the causes of flat or nonsense curves in seconds: 8 sample load flows (DG_2 off)."""
    log("\n--- Start-up checks: 8 sample load flows (DG_2 off) ---")
    r1 = net["r1"]
    samples = []
    for day, hour in ((0, 0), (0, 6), (0, 12), (0, 18), (1, 3), (1, 9), (1, 15), (1, 21)):
        t = t0 + day * 86400 + hour * 3600
        if t > t1:
            continue
        set_study_time(case, t)
        got = attr(case, "iStudyTime")
        if got is not None and int(got) != int(t):
            raise Stop(f"Study time did not change (asked {t}, PowerFactory has {got})")
        qds_solve(ldf, t, "Start-up load flow")
        load, other, p1, p2 = qds_measure(net, False)
        samples.append((t, load, other, p1))
        log(f"   {fmt_time(t)}   load {load:9.1f} kW   {DG1_NAME} {p1:9.1f} kW   "
            f"PV/BESS/other {other:9.1f} kW")
    if not samples:
        return

    loads = [s[1] for s in samples]
    peak = max(abs(x) for x in loads)

    # 1) Units: the load must be of the same order as the diesel rating.
    if peak == 0:
        raise Stop("Total load is 0 kW at every sample - are the loads in service and connected?")
    if not 1e-3 * r1 < peak < 20 * r1:
        raise Stop(f"Load ({peak:.3g} kW) and {DG1_NAME} rating ({r1:.3g} kW) differ by "
                   f"a factor of {peak / r1:.3g}. A unit is being misread - fix the matching "
                   "FALLBACK_* setting, or set DG1_RATING_KW.")

    # 2) Power balance: generation must cover load + losses.
    for t, load, other, p1 in samples:
        if load <= 0:
            continue
        ratio = (p1 + other) / load
        if -1.3 < ratio < -0.7:
            raise Stop("Generators come back with negative power. Set GEN_SIGN = -1.")
        if not 0.8 <= ratio <= 1.3:
            warn(f"At {fmt_time(t)} generation is {ratio:.0%} of load. Expected about "
                 "100-110 %. Check for consumers not in LOAD_CLASSES, or sources not "
                 "in OTHER_SOURCE_CLASSES.")
            break

    # 3) Does the load actually change with time?
    spread = max(loads) - min(loads)
    if len(loads) > 1 and spread <= 1e-3 * peak:
        msg = (f"The total load is {loads[0]:.1f} kW at every sample time: no time "
               "characteristic is changing it. Check the 'Loads ... time characteristics' "
               "list above: each load needs one (normally on 'plini' or 'scale0') whose "
               "data covers the period (or repeats yearly), and it must be in service.")
        if STOP_IF_LOAD_IS_FLAT:
            raise Stop(msg + " (Set STOP_IF_LOAD_IS_FLAT = False to run anyway.)")
        warn(msg)
    else:
        log(f"   OK: load varies between {min(loads):.1f} and {max(loads):.1f} kW")

    if net["others"] and all(abs(s[2]) < 1e-6 for s in samples):
        warn("PV/BESS/other sources give 0 kW at every sample time, including midday. "
             "Check their characteristics if they should be producing.")


class OnOffController:
    """Hysteresis with minimum run and stop times.

    Off -> on  when the signal rises above on_kw.
    On  -> off when the signal falls below off_kw.
    After a switch the state is held for at least min_on / min_off steps."""

    def __init__(self, on_kw, off_kw, min_on_steps, min_off_steps):
        self.on_kw, self.off_kw = on_kw, off_kw
        self.min_on, self.min_off = min_on_steps, min_off_steps
        self.on = False
        self.steps_in_state = 10 ** 9      # free to start at the very first step

    def update(self, signal_kw):
        """Advance one step. Returns True if the state changed."""
        self.steps_in_state += 1
        if self.steps_in_state < (self.min_on if self.on else self.min_off):
            return False                    # still inside the minimum time
        want_on = signal_kw >= self.off_kw if self.on else signal_kw > self.on_kw
        if want_on == self.on:
            return False
        self.on = want_on
        self.steps_in_state = 0
        return True


class Battery:
    """State-of-charge bookkeeping and power limits for the BESS.
    Power is at the BESS terminals: > 0 discharging, < 0 charging."""

    def __init__(self, p_max_kw, e_kwh, soc_init):
        self.p_max = p_max_kw
        self.e_kwh = e_kwh
        self.eta = BESS_EFFICIENCY ** 0.5       # one-way (charge = discharge)
        self.dt_h = QDS_STEP_MIN / 60.0
        self.soc = soc_init
        self.locked = self.soc <= BESS_SOC_MIN  # rule 6

    def max_discharge_kw(self):
        """Most it can deliver this step: power limit or energy left above SOC_MIN."""
        if self.locked:
            return 0.0
        kwh_out = (self.soc - BESS_SOC_MIN) * self.e_kwh * self.eta
        return max(0.0, min(self.p_max, kwh_out / self.dt_h))

    def max_charge_kw(self):
        """Most it can absorb this step: power limit or room left below SOC_MAX."""
        kwh_in = (BESS_SOC_MAX - self.soc) * self.e_kwh / self.eta
        return max(0.0, min(self.p_max, kwh_in / self.dt_h))

    def step(self, p_kw):
        """Advance the SOC by one step at terminal power p_kw."""
        if p_kw >= 0:
            self.soc -= p_kw * self.dt_h / (self.eta * self.e_kwh)
        else:
            self.soc += -p_kw * self.dt_h * self.eta / self.e_kwh
        self.soc = min(max(self.soc, 0.0), 1.0)
        if self.soc <= BESS_SOC_MIN + 1e-6:
            self.locked = True
        elif self.locked and self.soc >= BESS_SOC_RESUME:
            self.locked = False


def dispatch_pv_bess(net_kw, pv_avail_kw, battery, dg1_min_kw, dg2_kw, can_curtail):
    """Rules 2-4 for one step.

    net_kw   power the BESS and the diesels must cover together
             (load + losses - PV - other sources)
    Returns (BESS setpoint kW, PV to curtail kW)."""
    floor = dg1_min_kw + dg2_kw                 # least the running diesels should give
    if net_kw > floor:                          # rule 2: discharge
        return min(net_kw - floor, battery.max_discharge_kw()), 0.0
    surplus = floor - net_kw
    charge = min(surplus, battery.max_charge_kw())                       # rule 3
    curtail = min(surplus - charge, pv_avail_kw) if can_curtail else 0.0  # rule 4
    return -charge, curtail


def _objects_named(names):
    found = []
    for name in names:
        objs = app.GetCalcRelevantObjects(f"{name}.*")
        if not objs:
            raise Stop(f"'{name}' not found in the active grid - check PV_NAMES / BESS_NAME")
        found.append(objs[0])
    return found


def _elements_with_setpoint(skip):
    """Calculation-relevant elements that take an active power setpoint."""
    return [o for o in app.GetCalcRelevantObjects("*.*")
            if o.GetClassName().startswith("Elm") and attr(o, "pgini") is not None
            and full_name(o) not in skip]


def prepare_pv_bess(changes, net):
    """Find PV and BESS, put them in service, read ratings and add them to `net`."""
    skip = {full_name(net["dg1"]), full_name(net["dg2"])}

    # --- BESS ---------------------------------------------------------------
    if BESS_NAME:
        bess = _objects_named([BESS_NAME])[0]
    else:
        candidates = _elements_with_setpoint(skip)
        hits = [o for o in candidates
                if any(k in o.loc_name.lower() for k in ("bess", "batt"))]
        if not hits:
            names = ", ".join(f"{o.loc_name} ({o.GetClassName()})" for o in candidates)
            raise Stop("No BESS found by name. Set BESS_NAME to one of: " + (names or "(none)"))
        bess = hits[0]

    # --- PV -----------------------------------------------------------------
    if PV_NAMES:
        pvs = _objects_named(PV_NAMES)
    else:
        pvs = calc_objects(["ElmPvsys"], include_out_of_service=True)
        if not pvs:
            pvs = [o for o in calc_objects(["ElmGenstat"], include_out_of_service=True)
                   if "pv" in o.loc_name.lower()]
    pvs = [p for p in pvs if full_name(p) not in skip | {full_name(bess)}]
    if not pvs:
        warn("No PV found - running with BESS and diesels only. Set PV_NAMES if that's wrong.")

    # In service for the run, and never the reference machine.
    for obj in pvs + [bess]:
        if attr(obj, "outserv", 0):
            changes.set(obj, "outserv", 0)
            log(f"{obj.loc_name} was out of service - put in service for this run")
        if attr(obj, "ip_ctrl", 0) == 1:
            changes.set(obj, "ip_ctrl", 0)
    if str(attr(bess, "mode_inp", "")).upper() in ("SC", "QC"):
        warn(f"{bess.loc_name} dispatch input mode is '{attr(bess, 'mode_inp')}', which "
             "ignores the active power setpoint. Set it to P,Q (Load Flow page).")

    # qds_measure() sums net["others"], so PV and BESS must be in it.
    known = {full_name(o) for o in net["others"]}
    net["others"] += [o for o in pvs + [bess] if full_name(o) not in known]

    # Curtailment works through each PV unit's scaling factor (scale0).
    pv_scale = {}
    curtail_ok = CURTAIL_PV and bool(pvs)
    for p in pvs:
        s = attr(p, "scale0")
        curtail_ok = curtail_ok and s is not None
        pv_scale[full_name(p)] = (p, float(s) if s is not None else 1.0)
    if CURTAIL_PV and pvs and not curtail_ok:
        warn("A PV unit has no scaling factor (scale0), so surplus PV can't be curtailed. "
             "DG_1 will absorb it, which may show up as reverse power.")

    p_bess = rating_kw(bess, BESS_P_KW)
    if p_bess <= 0:
        raise Stop(f"Could not read {bess.loc_name}'s rating - set BESS_P_KW")

    dg1_min = DG1_MIN_LEVEL * net["r1"]
    log("\nPV and BESS:")
    for p in pvs:
        chars = characteristics_of(p)
        log(f"   PV    {p.loc_name:<14} {p.GetClassName():<11} -> {connection_report(p)}   "
            + ("; ".join(chars) if chars else "(no characteristic; solar model is fine)"))
    log(f"   BESS  {bess.loc_name:<14} {bess.GetClassName():<11} -> {connection_report(bess)}")
    log(f"         {p_bess:.1f} kW / {BESS_E_KWH:.1f} kWh, "
        f"limits {BESS_SOC_MIN:.0%}-{BESS_SOC_MAX:.0%}, resume at {BESS_SOC_RESUME:.0%}, "
        f"round-trip efficiency {BESS_EFFICIENCY:.0%}")
    log(f"   {DG1_NAME} minimum loading {dg1_min:.1f} kW ({DG1_MIN_LEVEL:.0%}); "
        f"PV curtailment {'on' if curtail_ok else 'off'}")

    net.update({"pv": pvs, "pv_scale": pv_scale, "pv_factor": 1.0,
                "curtail_ok": curtail_ok, "bess": bess, "bess_set": None,
                "p_bess": p_bess, "dg1_min": dg1_min})


def set_bess(changes, net, kw):
    """BESS active power setpoint in kW: > 0 discharge, < 0 charge."""
    if net["bess_set"] is not None and abs(kw - net["bess_set"]) < 1e-6:
        return                                  # unchanged - skip the write
    bess = net["bess"]
    units = attr(bess, "ngnum", 1) or 1         # pgini is per parallel unit
    quiet_set(changes, bess, "pgini",
              kw / units / to_kilo_factor(bess, "pgini", FALLBACK_SETPOINT_UNIT))
    net["bess_set"] = kw


def set_pv_factor(changes, net, factor):
    """Let PV produce `factor` x what the sun allows (1.0 = no curtailment)."""
    if abs(factor - net["pv_factor"]) < 1e-6:
        return
    for obj, scale in net["pv_scale"].values():
        quiet_set(changes, obj, "scale0", scale * factor)
    net["pv_factor"] = factor


def measure_pv_bess(net):
    """(PV, BESS) active power in kW from the last load flow."""
    pv = sum(p_kw(o, generator=True) for o in net["pv"])
    return pv, p_kw(net["bess"], generator=True)


def run_rule_based(case, ldf, net, changes, csv_path, soc_init):
    """The rule-based controller over the period in _qds. Returns the logged rows."""
    t0, t1, step_s = _qds["t0"], _qds["t1"], qds_step_s()
    r1, p_dg2 = net["r1"], net["p_dg2"]
    batt, dg1_min = Battery(net["p_bess"], BESS_E_KWH, soc_init), net["dg1_min"]
    on_kw, off_kw = DG2_ON_LEVEL * r1, DG2_OFF_LEVEL * r1
    min_on = max(1, round(DG2_MIN_RUN_MIN / QDS_STEP_MIN))
    min_off = max(1, round(DG2_MIN_STOP_MIN / QDS_STEP_MIN))
    n_steps = (t1 - t0) // step_s + 1
    steps_per_day = 24 * 60 // QDS_STEP_MIN

    log(f"\n=== Dispatch run, rule-based, {fmt_time(t0)} -> {fmt_time(t1)}, "
        f"{QDS_STEP_MIN} min steps (up to {2 * n_steps} load flows), SOC at the start {soc_init:.0%} ===")
    log("Order each step: measure load and PV -> decide DG_2 -> set BESS and PV -> load flow")
    log(f"BESS holds {DG1_NAME} at >= {dg1_min:.1f} kW while it can; "
        f"{DG2_NAME} judged on the demand left with the BESS at full discharge")
    log(f"{DG2_NAME} starts above {on_kw:.1f} kW, stops below {off_kw:.1f} kW, "
        f"runs at {p_dg2:.1f} kW")
    log(f"Minimum run {DG2_MIN_RUN_MIN} min ({min_on} steps), "
        f"minimum stop {DG2_MIN_STOP_MIN} min ({min_off} steps)")
    if p_dg2 > off_kw:
        warn(f"{DG2_NAME} runs at {p_dg2:.1f} kW, above the OFF level ({off_kw:.1f} kW). "
             f"Just before it stops, {DG1_NAME} would have to absorb power (reverse power). "
             f"Consider DG2_P_KW below {off_kw:.1f}.")
    log(f"CSV log: {csv_path}\n")

    ctrl = OnOffController(on_kw, off_kw, min_on, min_off)
    set_dg2(changes, net, False)            # DG_2 starts the period stopped

    rows = []
    starts = 0
    checked = set()                         # first-time checks already done
    wall0 = time.time()

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(QDS_COLUMNS)

        for i in range(n_steps):
            t = t0 + i * step_s
            set_study_time(case, t)                     # characteristics move to t

            # 1) Measure: PV uncurtailed, BESS idle, DG_2 as it was last step.
            set_pv_factor(changes, net, 1.0)
            set_bess(changes, net, 0.0)
            qds_solve(ldf, t)
            load, other_all, p1, p2 = qds_measure(net, ctrl.on)
            pv_avail, _ = measure_pv_bess(net)
            net_kw = p1 + p2                # what BESS + diesels must cover

            # 2) DG_2 (rule 5): judged on what the diesels would still supply
            #    with the BESS discharging as hard as it can.
            signal = net_kw - batt.max_discharge_kw()
            switched = ctrl.update(signal)
            if switched:
                set_dg2(changes, net, ctrl.on)
                starts += ctrl.on
                log(f"{fmt_time(t)}  signal {signal:9.1f} kW  SOC {batt.soc:4.0%}  ->  "
                    f"{DG2_NAME} {'ON ' if ctrl.on else 'OFF'}")

            # 3) BESS and PV setpoints (rules 2-4).
            bess_kw, curtail = dispatch_pv_bess(net_kw, pv_avail, batt, dg1_min,
                                                p_dg2 if ctrl.on else 0.0, net["curtail_ok"])
            bess_kw = 0.0 if abs(bess_kw) < 0.01 else bess_kw
            curtail = 0.0 if curtail < 0.01 else curtail
            set_bess(changes, net, bess_kw)
            set_pv_factor(changes, net, 1.0 - curtail / pv_avail if curtail else 1.0)

            # 4) Final load flow with everything dispatched (only if something changed).
            if switched or bess_kw or curtail:
                qds_solve(ldf, t, "Load flow after dispatch")
                load, other_all, p1, p2 = qds_measure(net, ctrl.on)
            pv, p_bess = measure_pv_bess(net)
            other = other_all - pv - p_bess
            batt.step(p_bess)                       # SOC follows what really flowed

            # First time each control acts: confirm PowerFactory really applied it.
            if ctrl.on and "dg2" not in checked:
                checked.add("dg2")
                if abs(p2 - p_dg2) > 0.02 * p_dg2:
                    warn(f"{DG2_NAME} delivers {p2:.1f} kW but was set to {p_dg2:.1f} kW. "
                         "Check its dispatch input mode, active power limits and "
                         "any characteristic on 'pgini'.")
            mode = "discharge" if bess_kw > 1 else "charge" if bess_kw < -1 else None
            if mode and mode not in checked:
                checked.add(mode)
                if abs(p_bess - bess_kw) > 0.02 * abs(bess_kw) + 0.5:
                    warn(f"BESS asked to {mode} at {abs(bess_kw):.1f} kW but gives "
                         f"{p_bess:.1f} kW. Check its input mode and active power limits "
                         "(charging needs a negative minimum P).")
            if curtail > 1 and "curtail" not in checked:
                checked.add("curtail")
                if abs(pv - (pv_avail - curtail)) > 0.02 * pv_avail + 0.5:
                    warn("PV curtailment through the scaling factor (scale0) did not take "
                         f"effect: expected {pv_avail - curtail:.1f} kW, got {pv:.1f} kW.")

            row = {"time": fmt_time(t), "load_kW": load, "pv_avail_kW": pv_avail,
                   "pv_kW": pv, "pv_curtailed_kW": max(pv_avail - pv, 0.0),
                   "bess_kW": p_bess, "bess_soc_pct": 100.0 * batt.soc,
                   "other_kW": other, "dispatch_signal_kW": signal,
                   "DG1_kW": p1, "DG1_loading_pct": 100.0 * p1 / r1,
                   "DG2_on": int(ctrl.on), "DG2_kW": p2,
                   "DG2_on_level_kW": on_kw, "DG2_off_level_kW": off_kw}
            rows.append(row)
            writer.writerow([row["time"]] + [f"{row[c]:.3f}" if isinstance(row[c], float)
                                             else row[c] for c in QDS_COLUMNS[1:]])

            if (i + 1) % steps_per_day == 0 or i + 1 == n_steps:
                done = (i + 1) / n_steps
                elapsed = time.time() - wall0
                eta = elapsed / done - elapsed
                log(f"  {row['time'][:10]} done ({done:4.0%})  SOC {batt.soc:4.0%}  "
                    f"elapsed {elapsed / 60:4.1f} min, about {eta / 60:4.1f} min left")

    summarise(rows, net, starts)
    return rows


# The controllers the quasi-dynamic dispatch can run: name -> function(case, ldf, net, changes,
# csv_path, soc_init) that steps through _qds and returns its rows (columns QDS_COLUMNS).
# None: not in this file yet. Its log can still be used: a CSV with the columns QDS_COLUMNS,
# saved as DISPATCH_DIR/<controller>_<point>.csv, is read by the stability study like the others.
QDS_RUNNERS = {"Rule-based": run_rule_based, "MPC": None}


def summarise(rows, net, starts):
    """Key figures of the run, energies in kWh."""
    r1, dg1_min = net["r1"], net["dg1_min"]
    h = QDS_STEP_MIN / 60.0
    n = len(rows)
    on_steps = sum(r["DG2_on"] for r in rows)

    def kwh(col):
        return sum(r[col] for r in rows) * h

    load_e = kwh("load_kW")
    pv_avail, pv_used = kwh("pv_avail_kW"), kwh("pv_kW")
    dis = sum(max(r["bess_kW"], 0.0) for r in rows) * h
    chg = sum(max(-r["bess_kW"], 0.0) for r in rows) * h
    diesel = kwh("DG1_kW") + kwh("DG2_kW")
    window = BESS_E_KWH * (BESS_SOC_MAX - BESS_SOC_MIN)     # usable kWh
    socs = [r["bess_soc_pct"] for r in rows]

    def share(part, whole):
        return f"({part / whole:5.1%})" if whole > 0 else ""

    log("\n--- Summary ---")
    log(f"Steps simulated:            {n}")
    log(f"Peak load:                  {max(r['load_kW'] for r in rows):10.1f} kW")
    log(f"Load energy:                {load_e:10.0f} kWh")
    log(f"PV available:               {pv_avail:10.0f} kWh")
    log(f"PV used:                    {pv_used:10.0f} kWh {share(pv_used, pv_avail)} of available")
    log(f"PV curtailed:               {kwh('pv_curtailed_kW'):10.0f} kWh")
    log(f"BESS discharged / charged:  {dis:7.0f} / {chg:.0f} kWh  "
        f"({dis / window:.1f} full cycles of the {BESS_SOC_MIN:.0%}-{BESS_SOC_MAX:.0%} window)")
    log(f"BESS SOC range:             {min(socs):5.1f} - {max(socs):.1f} %   "
        f"(end {socs[-1]:.1f} %)")
    log(f"Other sources:              {kwh('other_kW'):10.0f} kWh")
    log(f"{DG1_NAME} energy:               {kwh('DG1_kW'):10.0f} kWh")
    log(f"{DG2_NAME} energy:               {kwh('DG2_kW'):10.0f} kWh")
    log(f"Diesel total:               {diesel:10.0f} kWh {share(diesel, load_e)} of load energy")
    log(f"{DG2_NAME} starts:               {starts:10d}")
    log(f"{DG2_NAME} running time:         {on_steps * h:10.1f} h ({on_steps / n:.1%} of the period)")
    log(f"{DG1_NAME} overload steps (>100 %): {sum(r['DG1_kW'] > r1 for r in rows):7d}")
    log(f"{DG1_NAME} below-minimum steps:  {sum(r['DG1_kW'] < dg1_min - 1.0 for r in rows):10d}")
    log(f"{DG1_NAME} reverse-power steps:  {sum(r['DG1_kW'] < 0 for r in rows):10d}")


# ---------------------------------------------------------------------------- dispatch plots
# PowerFactory can't plot Python values directly, but it reads COMTRADE files (the IEEE
# C37.111 format used for disturbance records) as a result source, the same route the
# powfacpy library uses to plot external data. So after a run:
#   1. every CSV column, plus the limit lines, is written as a COMTRADE file pair (.cfg
#      header + .dat data) stamped with the real start date,
#   2. the file is loaded into the study case as a COMTRADE object (IntComtrade, folder
#      "Comtrade"),
#   3. three plot pages draw its channels against time.
# Every PowerFactory call is guarded: if one fails, the CSV and PNGs still stand.

# (COMTRADE channel, unit, CSV column - or a fixed value for a limit line)
PF_CHANNELS = [
    ("P_load", "kW", "load_kW"),
    ("P_DG1", "kW", "DG1_kW"),
    ("P_DG2", "kW", "DG2_kW"),
    ("P_PV", "kW", "pv_kW"),
    ("P_PV_avail", "kW", "pv_avail_kW"),
    ("P_BESS", "kW", "bess_kW"),
    ("P_other", "kW", "other_kW"),
    ("SOC", "%", "bess_soc_pct"),
    ("SOC_min", "%", 100.0 * BESS_SOC_MIN),
    ("SOC_resume", "%", 100.0 * BESS_SOC_RESUME),
    ("SOC_max", "%", 100.0 * BESS_SOC_MAX),
    ("P_signal", "kW", "dispatch_signal_kW"),
    ("P_DG2_start", "kW", "DG2_on_level_kW"),
    ("P_DG2_stop", "kW", "DG2_off_level_kW"),
    ("DG1_loading", "%", "DG1_loading_pct"),
]
# and in an MPC run, its stability margin (MPC_COLUMNS)
PF_MPC_CHANNELS = [
    ("m_k", "pu", "margin"),
    ("m_horizon", "pu", "horizon_min_margin"),
    ("zero", "pu", 0.0),
]


def write_comtrade(rows, base):
    """Write `rows` as an ASCII COMTRADE file pair <base>.cfg / <base>.dat
    (1999 revision, which PowerFactory reads).

    COMTRADE stores each analogue value as an integer between -99999 and
    99998 plus a per-channel scale a and offset b (value = a * integer + b),
    so every channel keeps a resolution of its own range / 200 000."""
    lo, hi = -99999, 99998
    step_s = qds_step_s()
    channels = PF_CHANNELS + (PF_MPC_CHANNELS + [("m_star", "pu", mpc_params().m_star)] if "margin" in rows[0] else [])
    header = ["Microgrid dispatch,PowerFactory script,1999",
              f"{len(channels)},{len(channels)}A,0D"]
    columns = []
    for i, (name, unit, source) in enumerate(channels, 1):
        # margins below -1 are drawn at -1 (MPC_M_NONE would squash the rest)
        values = ([max(float(r[source]), -1.0) if unit == "pu" else float(r[source]) for r in rows]
                  if isinstance(source, str) else [float(source)] * len(rows))
        vmin, vmax = min(values), max(values)
        if vmax - vmin < 1e-9:                  # constant channel, e.g. a limit line
            a, b = (abs(vmax) or 1.0) / hi, 0.0
        else:
            a = (vmax - vmin) / (hi - lo)
            b = (vmin * hi - vmax * lo) / (hi - lo)
        columns.append([int(round((v - b) / a)) for v in values])
        # index, id, phase, circuit component, unit, a, b, skew, min, max,
        # primary, secondary, primary/secondary
        header.append(f"{i},{name},,{i},{unit},{a!r},{b!r},0,{lo},{hi},1.0,1.0,S")
    start = to_datetime(_qds["t0"]).strftime("%d/%m/%Y,%H:%M:%S.%f")
    header += ["50",                            # line frequency
               "1",                             # one sampling rate ...
               f"{1.0 / step_s!r},{len(rows)}",  # ... of 1 sample per step, for all rows
               start, start,                    # first sample, trigger
               "ASCII",
               "1000000"]                       # timestamps below are in seconds
    with open(base + ".cfg", "w") as f:
        f.write("\n".join(header) + "\n")
    with open(base + ".dat", "w") as f:
        for n, values in enumerate(zip(*columns)):
            f.write(f"{n + 1},{n * step_s}," + ",".join(map(str, values)) + "\n")


def build_dispatch_pages(rows, csv_path):
    """Load the run into PowerFactory and draw three plot pages from it."""
    base = os.path.splitext(csv_path)[0] + "_pf"
    name = f"{PF_COMTRADE_NAME} {_qds['label']}"
    try:
        write_comtrade(rows, base)
        folder = app.GetFromStudyCase("Comtrade.IntFolder")
        found = folder.GetContents(f"{name}.IntComtrade")
        record = found[0] if found else folder.CreateObject("IntComtrade", name)
        try:
            record.Release()                    # drop data cached from an earlier run
        except Exception:
            pass
        record.SetAttribute("f_name", base + ".cfg")
        try:
            record.Load()                       # read the new file now
        except Exception:
            pass
    except Exception as e:
        warn(f"PowerFactory plots skipped: could not load the COMTRADE file ({e}). "
             "The CSV and PNGs are not affected.")
        return
    log(f"COMTRADE file: {base}.cfg  ->  '{name}' in the study case folder 'Comtrade'")

    has_other = any(abs(r["other_kW"]) > 1e-6 for r in rows)
    has_curtail = any(r["pv_curtailed_kW"] > 0.01 for r in rows)
    label = _qds["label"]
    # (channel, label, colour, line width)
    pages = [
        (f"Dispatch {label} - power", [
            ("P_load", "Total load", COLOR_LOAD, 1.0),
            ("P_DG1", DG1_NAME, COLOR_DG1, 0.8),
            ("P_DG2", DG2_NAME, COLOR_DG2, 0.8),
            ("P_PV", "PV used", COLOR_PV, 0.8)]
            + ([("P_PV_avail", "PV available", COLOR_PV, 0.4)] if has_curtail else [])
            + [("P_BESS", "BESS (+ discharge / - charge)", COLOR_BESS, 0.8)]
            + ([("P_other", "Other sources", COLOR_OTHER, 0.8)] if has_other else [])),
        (f"Dispatch {label} - battery", [
            ("SOC", "BESS state of charge", COLOR_BESS, 1.0),
            ("SOC_max", f"Max {BESS_SOC_MAX:.0%}", COLOR_SIGNAL, 0.4),
            ("SOC_resume", f"Discharge resumes {BESS_SOC_RESUME:.0%}", COLOR_SIGNAL, 0.4),
            ("SOC_min", f"Min {BESS_SOC_MIN:.0%}", COLOR_SIGNAL, 0.4)]),
        (f"Dispatch {label} - stability margin", [
            ("m_k", "Margin m_k of the step", COLOR_SIGNAL, 0.8),
            ("m_horizon", "Smallest m_k over the horizon", COLOR_BESS, 0.5),
            ("m_star", "Target m*", COLOR_DG1, 0.6),
            ("zero", "0: a stability limit reached", COLOR_DG2, 0.6)]) if "margin" in rows[0] else
        (f"Dispatch {label} - {DG2_NAME} rule", [
            ("P_signal", "Diesel demand with BESS at full discharge", COLOR_SIGNAL, 0.8),
            ("P_DG2_start", "Start level", COLOR_DG2, 0.6),
            ("P_DG2_stop", "Stop level", COLOR_DG1, 0.6),
            ("P_DG2", f"{DG2_NAME} output", COLOR_DG2, 0.8)]),
    ]
    board = app.GetGraphicsBoard()
    if board is None:
        board = app.GetFromStudyCase("SetDesktop")
    made = []
    for title, curves in pages:
        try:
            _pf_page(board, record, title, curves)
            made.append(title)
        except Exception as e:
            warn(f"Could not build the PowerFactory plot page '{title}': {e}")
    if made:
        log("PowerFactory plot pages (Graphics Board tabs): " + ", ".join(made))


def _pf_page(board, record, title, curves):
    """One plot page with the given COMTRADE channels as curves."""
    page = board.GetPage(title, 1, "GrpPage")
    plot = page.GetOrInsertCurvePlot(title, 1)
    ds = plot.GetDataSeries()
    ds.ClearCurves()
    ds.SetAttribute("useIndividualResults", 1)
    for name, *_ in curves:
        ds.AddCurve(None, name)                 # a COMTRADE channel has no element
        files = list(ds.GetAttribute("curveTableResultFile"))
        files[-1] = record
        ds.SetAttribute("curveTableResultFile", files)
    table = {"curveTableLabel": [c[1] for c in curves],
             "curveTableLineWidth": [int(round(c[3] * 100)) for c in curves]}
    try:
        table["curveTableColor"] = [
            app.EncodeColour(*(int(c[2][i:i + 2], 16) for i in (1, 3, 5))) for c in curves]
    except Exception:
        pass                                    # PowerFactory's default colours
    for key, value in table.items():
        try:
            ds.SetAttribute(key, value)
        except Exception as e:
            warn(f"'{title}': could not set {key} ({e})")
    if PF_DATE_AXIS:
        try:
            plot.GetAxisX().SetAttribute("axisMode", 2)     # 2 = date and time
        except Exception:
            pass
    page.DoAutoScaleX()
    page.DoAutoScaleY()
    page.Show()


# Fixed colour per entity, in validated order (colour-blind safe as lines).
COLOR_DG1 = "#2a78d6"       # blue
COLOR_DG2 = "#eb6834"       # orange
COLOR_BESS = "#1baf7a"      # aqua
COLOR_PV = "#eda100"        # yellow
COLOR_OTHER = "#e87ba4"     # magenta
COLOR_LOAD = "#0b0b0b"      # ink
COLOR_SIGNAL = "#52514e"    # secondary ink
COLOR_GRID = "#d9d8d4"


def plot_dispatch(rows, csv_path):
    """Save three separate PNGs with the same time axis, all powers in kW:
       <csv>_power.png    - load, DG_1, DG_2, PV, BESS (+ discharge / - charge)
       <csv>_battery.png  - BESS state of charge with its limits
       <csv>_dg2_rule.png - the DG_2 rule: diesel demand left with the BESS
                            at full discharge, against the start/stop levels"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
    except ImportError:
        py = os.path.join(sys.exec_prefix, "python.exe")
        py = py if os.path.isfile(py) else sys.executable
        warn(f"matplotlib is not installed for the Python PowerFactory uses ({py}).")
        log(f'   Install it once:     "{py}" -m pip install matplotlib')
        return None

    t = [datetime.strptime(r["time"], "%Y-%m-%d %H:%M") for r in rows]
    col = {k: [r[k] for r in rows] for k in rows[0] if k != "time"}
    on_kw, off_kw = rows[0]["DG2_on_level_kW"], rows[0]["DG2_off_level_kW"]
    has_other = any(abs(v) > 1e-6 for v in col["other_kW"])
    has_curtail = any(v > 0.01 for v in col["pv_curtailed_kW"])
    base = os.path.splitext(csv_path)[0]
    period = f"{_qds['label']}, {t[0]:%d %b} - {t[-1]:%d %b %Y} ({QDS_STEP_MIN}-min steps)"
    days = max((t[-1] - t[0]).days, 1)

    plt.rcParams.update({"font.size": 9, "axes.edgecolor": COLOR_GRID,
                         "axes.labelcolor": COLOR_SIGNAL, "xtick.color": COLOR_SIGNAL,
                         "ytick.color": COLOR_SIGNAL})

    def new_figure(height, title, ylabel):
        fig, ax = plt.subplots(figsize=(15, height), layout="constrained")
        fig.suptitle(f"{title}, {period}", x=0.01, ha="left", fontsize=12, color=COLOR_LOAD)
        ax.set_ylabel(ylabel)
        return fig, ax

    def save(fig, ax, name):
        # Legend just above the plot so it never covers the data.
        ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=7, frameon=False,
                  borderaxespad=0.2)
        ax.grid(True, color=COLOR_GRID, lw=0.5)
        ax.spines[["top", "right"]].set_visible(False)
        ax.margins(x=0)
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=1 if days <= 10 else 2))
        ax.xaxis.set_minor_locator(mdates.DayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        out = f"{base}_{name}.png"
        fig.savefig(out, dpi=130)
        plt.close(fig)
        log(f"Plot saved: {out}")
        return out

    saved = []

    # --- 1) who supplies what ------------------------------------------------
    fig, ax = new_figure(7, "Active power of load and sources", "Active power [kW]")
    ax.plot(t, col["load_kW"], color=COLOR_LOAD, lw=1.0, label="Total load")
    ax.plot(t, col["DG1_kW"], color=COLOR_DG1, lw=0.9, label=DG1_NAME)
    ax.plot(t, col["DG2_kW"], color=COLOR_DG2, lw=0.9, label=DG2_NAME)
    ax.plot(t, col["pv_kW"], color=COLOR_PV, lw=0.9, label="PV used")
    if has_curtail:
        ax.plot(t, col["pv_avail_kW"], color=COLOR_PV, lw=0.7, ls="--", label="PV available")
    ax.plot(t, col["bess_kW"], color=COLOR_BESS, lw=0.9, label="BESS (+ discharge / - charge)")
    if has_other:
        ax.plot(t, col["other_kW"], color=COLOR_OTHER, lw=0.9, label="Other sources")
    ax.axhline(0, color=COLOR_SIGNAL, lw=0.6)
    saved.append(save(fig, ax, "power"))

    # --- 2) battery state of charge --------------------------------------------
    fig, ax = new_figure(4.5, "BESS state of charge", "SOC [%]")
    ax.plot(t, col["bess_soc_pct"], color=COLOR_BESS, lw=1.0, label="BESS state of charge")
    ax.axhline(100 * BESS_SOC_MAX, color=COLOR_SIGNAL, ls=":", lw=1.0,
               label=f"Max {BESS_SOC_MAX:.0%}")
    ax.axhline(100 * BESS_SOC_RESUME, color=COLOR_SIGNAL, ls="-.", lw=0.8,
               label=f"Discharge resumes {BESS_SOC_RESUME:.0%}")
    ax.axhline(100 * BESS_SOC_MIN, color=COLOR_SIGNAL, ls="--", lw=1.0,
               label=f"Min {BESS_SOC_MIN:.0%}")
    ax.set_ylim(0, 100)
    saved.append(save(fig, ax, "battery"))

    # --- 3) why DG_2 switched --------------------------------------------------
    if "margin" in rows[0]:
        # the MPC: its stability margin m_k, which is what makes it start DG_2
        m_star = mpc_params().m_star
        fig, ax = new_figure(5, f"Stability margin m_k and {DG2_NAME}", "Margin m_k")
        ax.fill_between(t, 0, 1, where=[bool(v) for v in col["DG2_on"]], step="post",
                        transform=ax.get_xaxis_transform(), color=COLOR_DG2, alpha=0.15,
                        lw=0, label=f"{DG2_NAME} running")
        ax.plot(t, [max(float(v), -1.0) for v in col["margin"]], color=COLOR_SIGNAL, lw=0.8,
                label="m_k of the step (below -1 drawn at -1)")
        ax.axhline(m_star, color=COLOR_DG1, ls="--", lw=1.0, label=f"Target m* = {m_star:g}")
        ax.axhline(0.0, color=COLOR_DG2, ls=":", lw=1.2, label="0: a stability limit reached")
        ax.set_ylim(-1.05, 1.05)
        saved.append(save(fig, ax, "margin"))
    else:
        fig, ax = new_figure(5, f"{DG2_NAME} start/stop rule", "Dispatch signal [kW]")
        ax.fill_between(t, 0, 1, where=[bool(v) for v in col["DG2_on"]], step="post",
                        transform=ax.get_xaxis_transform(), color=COLOR_DG2, alpha=0.15,
                        lw=0, label=f"{DG2_NAME} running")
        ax.plot(t, col["dispatch_signal_kW"], color=COLOR_SIGNAL, lw=0.8,
                label="Diesel demand with BESS at full discharge")
        ax.axhline(on_kw, color=COLOR_DG2, ls="--", lw=1.0,
                   label=f"Start level {on_kw:.0f} kW ({DG2_ON_LEVEL:.0%} of {DG1_NAME})")
        ax.axhline(off_kw, color=COLOR_DG1, ls=":", lw=1.2,
                   label=f"Stop level {off_kw:.0f} kW ({DG2_OFF_LEVEL:.0%} of {DG1_NAME})")
        saved.append(save(fig, ax, "dg2_rule"))

    if QDS_OPEN_PLOTS:
        for out in saved:
            try:
                os.startfile(out)           # Windows only
            except Exception:
                pass
    return saved


def dispatch_runs():
    """[(label, controller, first instant, last instant, CSV path)] for the dispatch to run:
    one per controller and operating point (up to its instant), or one per controller over
    the custom period."""
    os.makedirs(DISPATCH_DIR, exist_ok=True)
    runs = []
    for name in QDS_CONTROLLERS:
        tag = controller_tag(name)
        if QDS_PERIOD == "points":
            for pid, p in POINTS.items():
                if COMPARE_INSTANTS is not None and pid not in COMPARE_INSTANTS:
                    continue
                t1 = epoch_of(p["time"])
                runs.append((f"{tag} {pid}", name, t1 - int(QDS_WARMUP_DAYS * 86400), t1,
                             os.path.join(DISPATCH_DIR, f"{tag}_{pid}.csv")))
        else:
            t0, t1 = to_epoch(*QDS_START), to_epoch(*QDS_END)
            kind = f"_{MPC_FORECAST}" if name == "MPC" else ""      # runs on different forecasts keep their logs
            runs.append((f"{tag}{kind} {fmt_time(t0)[:10]}", name, t0, t1,
                         os.path.join(DISPATCH_DIR, f"{tag}_{fmt_time(t0)[:10]}_{fmt_time(t1)[:10]}{kind}.csv")))
    return runs


def mpc_on_model(name):
    """True for a run of the MPC on its own model (MPC_ENGINE "model"): no PowerFactory."""
    return name == "MPC" and MPC_ENGINE == "model"


def run_dispatch_study():
    """The quasi-dynamic dispatch of every controller in QDS_CONTROLLERS. The model and the
    study time are put back at the end. Returns the number of runs that finished."""
    log(f"\nQuasi-dynamic dispatch, {datetime.now():%Y-%m-%d %H:%M}")
    changes = ModelChanges()
    case, original_time, done = None, None, 0
    try:
        qds_check_settings()
        runs = dispatch_runs()
        missing = sorted({name for _, name, *_ in runs if QDS_RUNNERS.get(name) is None})
        if missing:
            warn("Not in this file yet, so not run: " + ", ".join(missing) + ". A log of its own with the "
                 f"columns of the rule-based one, saved in {DISPATCH_DIR} as <controller>_<point>.csv, is "
                 "read by the stability study.")
            runs = [r for r in runs if r[1] not in missing]
        if not runs:
            raise Stop("No dispatch to run: choose a controller (and an operating point)")
        for _, _, t0, t1, _ in runs:
            if t1 < t0:
                raise Stop("The dispatch period ends before it starts")
        if any(not mpc_on_model(name) for _, name, *_ in runs):
            case = open_project()
            original_time = attr(case, "iStudyTime")
            ldf = app.GetFromStudyCase("ComLdf")
            net = qds_network(changes)
            prepare_pv_bess(changes, net)       # find PV + BESS, put them in service
        try:
            yardstick = mpc_params()
        except ValueError as e:
            yardstick = None
            warn(f"The MPC's parameters are not valid ({e}): the runs are not scored on its yardstick")
        for label, name, t0, t1, csv_path in runs:
            _qds.update(t0=t0, t1=t1, label=label)
            started = time.time()
            if mpc_on_model(name):
                rows = run_mpc_model(csv_path, BESS_SOC_INIT)
            else:
                set_dg2(changes, net, False)    # checks run with DG_2 stopped ...
                set_bess(changes, net, 0.0)     # ... the BESS idle
                set_pv_factor(changes, net, 1.0)    # ... and no curtailment left over from the last run
                preflight(case, ldf, net, t0, t1)
                started = time.time()
                rows = QDS_RUNNERS[name](case, ldf, net, changes, csv_path, BESS_SOC_INIT)
            last = rows[-1]
            log(f"\n{label} at {last['time']}: load {last['load_kW']:.1f} kW, PV {last['pv_kW']:.1f} (of "
                f"{last['pv_avail_kW']:.1f}), battery {last['bess_kW']:+.1f} kW (SOC {last['bess_soc_pct']:.1f} %), "
                f"{DG1_NAME} {last['DG1_kW']:.1f}, {DG2_NAME} {last['DG2_kW']:.1f} kW   "
                f"[{(time.time() - started) / 60:.1f} min]")
            if yardstick is not None:
                for line in mpc_score_lines(f"{label} on the MPC's yardstick", mpc_score(yardstick, rows), yardstick):
                    log(line)
            done += 1
            for what, step in (("the PNG plots", lambda: plot_dispatch(rows, csv_path) if QDS_PNG_PLOTS else None),
                               ("the PowerFactory pages", lambda: build_dispatch_pages(rows, csv_path)
                                if PF_PLOTS and app is not None else None)):
                try:
                    step()
                except Exception as e:
                    warn(f"Dispatch {label}: {what} could not be made ({type(e).__name__}: {e})")
    except Stop as msg:
        warn(f"STOPPED: {msg}")
    except KeyboardInterrupt:
        warn("Interrupted. The model is put back.")
    except Exception:
        import traceback
        warn("The dispatch broke off with an error.")
        for line in traceback.format_exc().rstrip().split("\n"):
            log("   " + line)
    finally:
        if RESTORE_MODEL_AFTER_RUN:
            n = changes.restore()
            if case is not None and original_time is not None:
                set_study_time(case, original_time)
            if n > 0:
                log(f"\nModel restored after the dispatch ({n} settings and the study time put back).")
            elif n < 0:
                warn(f"The model is only partly restored ({-n} settings put back): see the list above")
    return done


def dispatch_from_log(name, pid, p):
    """A controller's state at a point, from the row at its instant in a dispatch log in
    DISPATCH_DIR: first <controller>_<point>.csv, then any other log of that controller.
    None if no log has that instant."""
    tag = controller_tag(name)
    if not os.path.isdir(DISPATCH_DIR):
        return None
    first = os.path.join(DISPATCH_DIR, f"{tag}_{pid}.csv")
    others = sorted(os.path.join(DISPATCH_DIR, f) for f in os.listdir(DISPATCH_DIR)
                    if f.startswith(tag + "_") and f.endswith(".csv"))
    for path in [first] + [o for o in others if o != first]:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                row = next((r for r in csv.DictReader(fh) if r.get("time", "").strip() == p["time"]), None)
            if row is None:
                continue
            got = dict(bess=float(row["bess_kW"]), soc=float(row["bess_soc_pct"]), dg1=float(row["DG1_kW"]),
                       dg2=float(row["DG2_kW"]) if float(row["DG2_on"]) > 0.5 else 0.0, pv=float(row["pv_kW"]),
                       source=os.path.basename(path))
        except (OSError, KeyError, ValueError) as e:
            warn(f"The dispatch log {path} cannot be read ({e})")
            continue
        load = float(row.get("load_kW") or p["load"])
        if abs(load - p["load"]) > max(2.0, 0.01 * p["load"]):
            warn(f"{os.path.basename(path)} has a load of {load:.1f} kW at {p['time']}, POINTS {pid} has "
                 f"{p['load']:.1f} kW: check the time of the point")
        return got
    return None


# ============================================================================
# MODEL-PREDICTIVE DISPATCH (MPC): THE MODEL
# ============================================================================
# A stability-constrained heuristic MPC ([P] Parisio, Rikos, Glielmo, IEEE TCST 22(5), 2014;
# [S] Sachs, Sawodny, IEEE TSTE 7(3), 2016). At every step it lists DG_2 schedules (stay as it
# is, or switch at one of MPC switch_steps, keeping the minimum run and stop times) times
# battery/curtailment rules, rolls each one out over the horizon through the plant model,
# repairs the steps whose stability margin is below m*, and keeps the cheapest. Only its
# first move is applied; the next step decides again.
#   plant      gensets: limits, affine fuel, starts, minimum run and stop times; battery: SOC
#              with charge and discharge efficiency and the SOC_MIN lockout (as Battery);
#              PV curtailment; DG_1 is the slack, DG_2 runs at its minimum unless DG_1 is full
#   events     the credible sudden changes of power. Down (under-frequency): a cloud (rho of the
#              PV output), the battery tripping while it discharges, a load step (lambda_up of
#              the load), DG_2 tripping. Up (over-frequency): the battery tripping while it
#              charges, a loss of load (lambda_down), a PV surge
#   stability  for each event, how much change each constraint allows:
#                S1 spinning reserve: the headroom of the units that stay
#                S2 ISO 8528-5 load acceptance: a(l) of DG_1's loading l (GUIDE_STAGES)
#                S3 nadir above the inverters' UF trip: df = f0 dP tau_eff / (2 E) (energy
#                   deficit; tau_eff fitted to this study's own RMS runs)
#                S4 reverse power on DG_1, and the over-frequency peak below over-speed
#                S5 ROCOF over the relay's window (reported only by default, as in the study)
#                S6 inverter share of the running genset kVA (kappa)
#                S7 reactive power, and the voltage dip X''d dS / S against the ISO class
#                S8 with both sets running, a bolted fault at the main board for t_fault: their
#                   electrical output collapses and each speeds up by P / (2 E), so they swing
#                   apart by pi f0 t_fault^2 / 2 |P1 / E1 - P2 / E2|, at most fault_swing_max
#                   (a heavily loaded DG_2 with its small inertia slips a pole: this study's F3)
#              margin m = 1 - dP / dP_allowed; m_k = the smallest over the constraints in use.
#              m_k < 0: not allowed; 0 <= m_k < m*: allowed, penalised; m_k >= m*: met
#   objective  fuel + DG_2 starts + curtailment + battery wear + w phi(m_k), and at the end of
#              the horizon the energy the battery is short of soc_ref
#   repair     where m_k < m*: curtail PV, discharge less, charge less (with as much PV
#              curtailed), or move load from DG_1 onto a running DG_2; the least of each move
#              that reaches m*, and of those the cheapest
# Its data, each value with a note of where it came from (the tool "MPC parameters" lists them):
# MPC_PARAMETERS, then the settings of this file (one set of numbers for both controllers),
# the system data from PowerFactory (mpc_system_model.json, and the model itself while the MPC
# runs on its load flows), the calibration (mpc_calibration.json) and last MPC_PARAMS.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
MPC_INF = float("inf")
MPC_M_NONE = -9.99      # the margin where nothing is allowed and something would still have to be met
MPC_MODES = ("hard", "report", "off")
MPC_CONSTRAINTS = {
    "S1": "spinning reserve",
    "S2": "ISO 8528-5 load acceptance",
    "S3": "frequency nadir above the inverter UF trip",
    "S4r": "reverse power on DG_1",
    "S4f": "over-frequency peak",
    "S5": "ROCOF over the relay window",
    "S6": "inverter share",
    "S7q": "reactive power",
    "S7u": "voltage dip",
    "S8": "sets in step through a fault",
}
# a(l): the load step allowed from each loading, % of prime: the next stage of GUIDE_STAGES
MPC_ACCEPT = tuple((a, b - a) for a, b in zip(GUIDE_STAGES, GUIDE_STAGES[1:]))
# the columns an MPC dispatch log has after QDS_COLUMNS
MPC_COLUMNS = ["margin", "binding", "fuel_L", "cost_R", "rule_p1_target_kW", "rule_soc_floor",
               "horizon_min_margin", "feasible_candidates"]

# (group, key, default, unit, meaning, where the default comes from). "OPEN": a decision still to
# be made; "ASSUMED": no data behind it yet. Settings of this file named in the source column
# replace the default when the parameters are built.
MPC_PARAMETERS = [
    ("Time", "dt_min", 5.0, "min", "time step", "QDS_STEP_MIN"),
    ("Time", "f0", 50.0, "Hz", "nominal frequency", "F_NOM"),
    ("DG_1", "dg1_s_kva", 810.0, "kVA", "alternator rating", "S6L1D-C4 data sheet"),
    ("DG_1", "dg1_rating_kw", 648.0, "kW", "rating S x cos phi_n (base of the minimum loading)", "810 kVA x 0.8"),
    ("DG_1", "dg1_p_max", 600.0, "kW", "upper dispatch limit (prime rating)", "C825 D5 data sheet"),
    ("DG_1", "dg1_p_standby", 660.0, "kW", "standby rating (base of the reverse-power relay)", "C825 D5 data sheet"),
    ("DG_1", "dg1_min_level", 0.30, "", "minimum loading, share of the rating", "DG1_MIN_LEVEL"),
    ("DG_1", "dg1_e_kws", 346.0, "kWs", "stored kinetic energy H x S", "GENSET_DATA j_kgm2"),
    ("DG_1", "dg1_fuel_a", 16.00, "L/h", "fuel while running, fixed part", "FUEL_DATA (affine fit)"),
    ("DG_1", "dg1_fuel_b", 0.2225, "L/kWh", "fuel per kWh", "FUEL_DATA (affine fit)"),
    ("DG_1", "dg1_xdss", 0.15, "pu", "sub-transient reactance X''d", "ASSUMED typical value"),
    ("DG_2", "dg2_s_kva", 250.0, "kVA", "alternator rating", "UCDI274K data sheet"),
    ("DG_2", "dg2_rating_kw", 200.0, "kW", "rating S x cos phi_n (base of the minimum loading)", "250 kVA x 0.8"),
    ("DG_2", "dg2_p_max", 200.0, "kW", "upper dispatch limit (prime rating)", "C275 D5 data sheet"),
    ("DG_2", "dg2_p_standby", 220.0, "kW", "standby rating", "C275 D5 data sheet"),
    ("DG_2", "dg2_min_level", 0.30, "", "minimum loading, share of the rating", "ASSUMED as DG_1"),
    ("DG_2", "dg2_e_kws", 53.25, "kWs", "stored kinetic energy H x S", "GENSET_DATA j_kgm2"),
    ("DG_2", "dg2_fuel_a", 0.85, "L/h", "fuel while running, fixed part", "FUEL_DATA (affine fit)"),
    ("DG_2", "dg2_fuel_b", 0.2548, "L/kWh", "fuel per kWh", "FUEL_DATA (affine fit)"),
    ("DG_2", "dg2_xdss", 0.15, "pu", "sub-transient reactance X''d", "ASSUMED typical value"),
    ("DG_2", "dg2_min_up_min", 60.0, "min", "minimum run time", "DG2_MIN_RUN_MIN"),
    ("DG_2", "dg2_min_down_min", 60.0, "min", "minimum stop time", "DG2_MIN_STOP_MIN"),
    ("Battery", "bess_p_max", 400.0, "kW", "charge and discharge limit", "400 kVA, cos phi 1"),
    ("Battery", "bess_e_kwh", 3200.0, "kWh", "usable energy at 100 % SOC", "BESS_E_KWH"),
    ("Battery", "soc_min", 0.20, "", "lowest SOC", "BESS_SOC_MIN"),
    ("Battery", "soc_max", 0.95, "", "highest SOC", "BESS_SOC_MAX"),
    ("Battery", "soc_resume", 0.40, "", "after SOC_MIN, no discharge until charged back to this", "BESS_SOC_RESUME"),
    ("Battery", "soc_init", 0.50, "", "SOC at the start of a run", "BESS_SOC_INIT"),
    ("Battery", "bess_eta_rt", 0.90, "", "round-trip efficiency, split evenly", "BESS_EFFICIENCY"),
    ("PV, load, network", "pv_p_max", 800.0, "kW", "PV rating", "PV_Plant 1000 kVA x 0.8"),
    ("PV, load, network", "losses", 0.0066, "", "network losses, share of the load", "load flow at 2002-07-01 06:20"),
    ("PV, load, network", "load_tan_phi", 0.0, "", "Q/P of the load", "Ld_1: Q about 0 kvar in the RMS runs"),
    ("Protection and standards", "f_uf", 47.0, "Hz", "inverter under-frequency trip", "F_UNDER"),
    ("Protection and standards", "f_over_limit", 58.3, "Hz", "the over-frequency peak must stay below this",
     "OVERSPEED_HZ (the inverters' 51.5 Hz trip waits 4 s)"),
    ("Protection and standards", "reverse_fraction", 0.10, "", "reverse-power relay, share of DG_1's standby rating",
     "REVERSE_FRACTION"),
    ("Protection and standards", "rocof_limit", 1.5, "Hz/s", "ROCOF the plant must ride through", "ROCOF_LIMIT"),
    ("Protection and standards", "rocof_window", 0.5, "s", "measuring window of the ROCOF relay",
     "ASSUMED (the study reports 100 and 500 ms)"),
    ("Protection and standards", "iso_class", "G2", "", "ISO 8528-5 class for the voltage dip: G1 | G2 | G3",
     "OPEN decision"),
    ("Protection and standards", "t_fault", 0.08, "s", "how long a fault at the main board lasts (S8)",
     "FAULT_CLEARING_S"),
    ("Calibrated", "tau_eff", 0.21, "s", "energy-deficit time of a step down (nadir model)", "handoff estimate"),
    ("Calibrated", "tau_eff_over", 0.21, "s", "energy-deficit time of a step up (peak model)", "handoff estimate"),
    ("Calibrated", "rho", 0.37, "", "PV fast-drop fraction", "handoff estimate"),
    ("Calibrated", "lambda_up", 0.24, "", "credible load step up, share of the load", "handoff estimate"),
    ("Calibrated", "lambda_down", 0.19, "", "credible load loss, share of the load", "handoff estimate"),
    ("Calibrated", "kappa", 0.73, "", "largest inverter share of the running genset kVA", "handoff estimate"),
    ("Stability constraints", "s1_mode", "hard", "", "S1 spinning reserve: hard | report | off", "handoff"),
    ("Stability constraints", "s2_mode", "hard", "", "S2 ISO 8528-5 load acceptance", "handoff"),
    ("Stability constraints", "s3_mode", "hard", "", "S3 frequency nadir above the UF trip", "handoff"),
    ("Stability constraints", "s4_mode", "hard", "", "S4 reverse power and over-frequency peak", "handoff"),
    ("Stability constraints", "s5_mode", "report", "", "S5 ROCOF over the relay window",
     "reported only, as in the study (1.5 Hz/s over 0.5 s allows about 50 kW)"),
    ("Stability constraints", "s6_mode", "hard", "", "S6 inverter share", "handoff"),
    ("Stability constraints", "s7_mode", "hard", "", "S7 reactive power and voltage dip", "handoff"),
    ("Stability constraints", "s8_mode", "hard", "", "S8 the sets stay in step through a fault at the main board",
     "this study's F3 runs: DG_2 at 200 kW slipped a pole"),
    ("Stability constraints", "fault_swing_max", 45.0, "deg", "S8: largest swing of the sets apart over a fault",
     "between this study's F3 runs: 8 deg held, 88-90 deg slipped"),
    ("Stability constraints", "pv_surge", "delivered", "", "PV surge after a cloud, rho x: delivered | curtailed | none",
     "PV is curtailed by scale0, so its output follows the sun (handoff: curtailed)"),
    ("Stability constraints", "m_star", 0.20, "", "target margin m*", "OPEN decision"),
    ("Stability constraints", "delta_f", 0.2, "Hz", "frequency margin kept from every limit", "handoff"),
    ("Stability constraints", "delta_p", 10.0, "kW", "power margin kept from the reverse-power limit", "ASSUMED"),
    ("Objective", "diesel_price", 29.11, "R/L", "diesel price", "DIESEL_PRICE"),
    ("Objective", "c_start", 250.0, "R", "DG_2 start-up cost", "OPEN decision (placeholder)"),
    ("Objective", "c_curtail", 0.15, "R/kWh", "cost of curtailed PV", "PowerFactory costCurtailment 150 R/MWh"),
    ("Objective", "c_wear", 0.50, "R/kWh", "battery wear per kWh in or out", "OPEN decision (placeholder)"),
    ("Objective", "w_margin", 1000.0, "R", "weight w of phi(m) per step", "ASSUMED"),
    ("Objective", "soc_ref", 0.50, "", "SOC the horizon should end at or above", "ASSUMED as BESS_SOC_INIT"),
    ("Objective", "c_terminal", None, "R/kWh", "value of each kWh the horizon ends below soc_ref (blank: auto)",
     "auto = DG_1's fuel per kWh x eta_d"),
    ("Heuristic", "horizon", 36, "steps", "prediction horizon N", "3 h"),
    ("Heuristic", "switch_steps", (0, 1, 2, 3, 6, 12, 24), "steps", "where a DG_2 schedule may switch", "ASSUMED"),
    ("Heuristic", "rule_p1_offsets", (0.0, 100.0, 250.0), "kW", "battery rules: DG_1 target above its minimum",
     "ASSUMED"),
    ("Heuristic", "rule_soc_floors", (0.20, 0.40), "", "battery rules: SOC kept back from discharge", "ASSUMED"),
    ("Heuristic", "tie_tolerance", 0.005, "", "costs this close count as equal; the larger margin wins", "ASSUMED"),
]
MPC_CALIBRATED = ("tau_eff", "tau_eff_over", "rho", "lambda_up", "lambda_down", "kappa")
MPC_DEFAULTS = {key: value for _, key, value, *_ in MPC_PARAMETERS}


def mpc_fuel_curve(role):
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


class MpcParams:
    """The MPC's data. Attributes are the keys of MPC_PARAMETERS; .source[key] says where each
    value came from; finish() works out what the inner loops use."""

    def __init__(self):
        self.source, self.notes = {}, []
        for _, key, value, _, _, source in MPC_PARAMETERS:
            setattr(self, key, value)
            self.source[key] = source

    def set(self, key, value, source):
        if key not in self.source:
            raise ValueError(f"the MPC has no parameter '{key}'")
        setattr(self, key, value)
        self.source[key] = source

    def finish(self):
        for key in ("s1_mode", "s2_mode", "s3_mode", "s4_mode", "s5_mode", "s6_mode", "s7_mode", "s8_mode"):
            if getattr(self, key) not in MPC_MODES:
                raise ValueError(f"{key} is '{getattr(self, key)}': {', '.join(MPC_MODES)}")
        if self.pv_surge not in ("delivered", "curtailed", "none"):
            raise ValueError(f"pv_surge is '{self.pv_surge}': delivered, curtailed or none")
        dips = {c: dip for c, dip, *_ in ISO_VOLT}
        if self.iso_class not in dips:
            raise ValueError(f"iso_class is '{self.iso_class}': {', '.join(dips)}")
        if min(self.tau_eff, self.tau_eff_over, self.rocof_window, self.dt_min, self.bess_e_kwh) <= 0:
            raise ValueError("tau_eff, tau_eff_over, rocof_window, dt_min and bess_e_kwh have to be above 0")
        if min(self.t_fault, self.fault_swing_max, self.dg1_e_kws) <= 0:
            raise ValueError("t_fault, fault_swing_max and dg1_e_kws have to be above 0")
        if not 0 < self.bess_eta_rt <= 1:
            raise ValueError("bess_eta_rt has to lie between 0 and 1")
        if int(self.horizon) < 1:
            raise ValueError("horizon has to be at least 1 step")
        self.horizon = int(self.horizon)
        self.dt_h = self.dt_min / 60.0
        self.eta = self.bess_eta_rt ** 0.5
        self.dg1_p_min = self.dg1_min_level * self.dg1_rating_kw
        self.dg2_p_min = self.dg2_min_level * self.dg2_rating_kw
        self.dg2_min_up = max(1, round(self.dg2_min_up_min / self.dt_min))
        self.dg2_min_down = max(1, round(self.dg2_min_down_min / self.dt_min))
        self.c_terminal_value = (self.dg1_fuel_b * self.diesel_price * self.eta if self.c_terminal is None
                                 else float(self.c_terminal))
        # allowances per kWs of stored energy (S3, S4f, S5)
        self.k_uf = 2.0 * (self.f0 - self.f_uf - self.delta_f) / (self.f0 * self.tau_eff)
        self.k_of = 2.0 * (self.f_over_limit - self.f0 - self.delta_f) / (self.f0 * self.tau_eff_over)
        self.k_rocof = 2.0 * self.rocof_limit * self.rocof_window / (self.f0 * min(self.rocof_window, self.tau_eff))
        self.dip = dips[self.iso_class] / 100.0
        self.reverse_kw = self.reverse_fraction * self.dg1_p_standby - self.delta_p
        # S8: the swing pi f0 t_fault^2 / 2 |P1 / E1 - P2 / E2| as dP = |P2 - P1 E2 / E1| (DG_2's
        # output beyond what would keep it in step with DG_1) against the dP that swings fault_swing_max
        self.s8_ratio = self.dg2_e_kws / self.dg1_e_kws
        self.allow_s8 = (2.0 * math.radians(self.fault_swing_max) * self.dg2_e_kws
                         / (math.pi * self.f0 * self.t_fault ** 2))
        modes = {cid: getattr(self, f"s{cid[1]}_mode") for cid in MPC_CONSTRAINTS}
        self.hard = {cid: m == "hard" for cid, m in modes.items()}
        self.shown = {cid: m != "off" for cid, m in modes.items()}
        # for mpc_margin, by DG_2 off/on: the (smallest allowance, one not above 0) of the hard
        # constraints whose allowance does not depend on the step (as mpc_checks works them out)
        # for a down event, a down event that loses DG_2 and an up event; and S6's allowance
        self.allow_fixed, self.allow_s6 = [], []
        for u2 in (0, 1):
            e_all = self.dg1_e_kws + (self.dg2_e_kws if u2 else 0.0)
            s_all = self.dg1_s_kva + (self.dg2_s_kva if u2 else 0.0)
            xd = max(self.dg1_xdss, self.dg2_xdss) if u2 else self.dg1_xdss
            groups = []
            for kind in ("down", "lost", "up"):
                if kind == "up":
                    allows = (("S4f", self.k_of * e_all), ("S5", self.k_rocof * e_all),
                              ("S7u", self.dip * s_all / xd))
                else:
                    e = e_all - (self.dg2_e_kws if kind == "lost" else 0.0)
                    s = s_all - (self.dg2_s_kva if kind == "lost" else 0.0)
                    allows = (("S3", self.k_uf * e), ("S5", self.k_rocof * e), ("S7u", self.dip * s / xd))
                least, none = MPC_INF, False
                for cid, allow in allows:
                    if self.hard[cid]:
                        least, none = _mpc_fold(least, none, allow)
                groups.append((least, none))
            self.allow_fixed.append(tuple(groups))
            self.allow_s6.append(self.kappa * s_all)
        return self

    def report(self):
        lines, group = [], None
        for g, key, _, unit, meaning, _ in MPC_PARAMETERS:
            if g != group:
                group = g
                lines.append(f"  {g}")
            lines.append(f"    {key:<17} {mpc_text(getattr(self, key)):>16} {unit:<6} {meaning[:54]:<54} "
                         f"{self.source[key]}")
        return lines


def mpc_text(value):
    """A parameter as the dialog and the reports show it."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.5g}"
    if isinstance(value, (tuple, list)):
        return ", ".join(mpc_text(v) for v in value)
    return str(value)


def mpc_parse(key, text):
    """A parameter from the dialog's text, of the type its default has."""
    default = MPC_DEFAULTS[key]
    text = text.strip()
    try:
        if default is None:
            return None if text.lower() in ("", "auto", "none") else float(text)
        if isinstance(default, tuple):
            return tuple(type(default[0])(float(x)) if isinstance(default[0], float) else int(float(x))
                         for x in text.replace(";", ",").split(",") if x.strip())
        if isinstance(default, bool):
            return text.lower() in ("1", "true", "yes", "on")
        if isinstance(default, int):
            return int(float(text))
        if isinstance(default, float):
            return float(text)
    except ValueError:
        raise ValueError(f"'{text}' is not a number" + (" list" if isinstance(default, tuple) else ""))
    return text


def mpc_find(*places):
    return next((x for x in places if x and os.path.isfile(x)), None)


def mpc_system_json():
    return mpc_find(MPC_SYSTEM_JSON, os.path.join(MPC_DIR, "mpc_system_model.json"),
                    os.path.join(SCRIPT_DIR, "mpc_system_model.json"),
                    os.path.join(os.path.expanduser("~"), "mpc_model", "mpc_system_model.json"))


def mpc_calibration_json():
    return mpc_find(MPC_CALIBRATION_JSON, os.path.join(MPC_DIR, "mpc_calibration.json"),
                    os.path.join(SCRIPT_DIR, "mpc_calibration.json"))


def mpc_from_settings(p):
    """This file's settings: one set of numbers for both controllers."""
    def put(key, value, name):
        if value is not None:
            p.set(key, value, f"setting {name}")

    put("dt_min", float(QDS_STEP_MIN), "QDS_STEP_MIN")
    put("f0", float(F_NOM), "F_NOM")
    for role in ("dg1", "dg2"):
        d = GENSET_DATA.get(role, {})
        put(f"{role}_p_max", d.get("prime_kw"), "GENSET_DATA prime_kw")
        put(f"{role}_p_standby", d.get("standby_kw"), "GENSET_DATA standby_kw")
        if d.get("j_kgm2"):
            put(f"{role}_e_kws", 0.5 * d["j_kgm2"] * W0_MECH ** 2 / 1000.0, "GENSET_DATA j_kgm2 (E = J w0^2 / 2)")
        fit = (mpc_fuel_curve(role) or {}).get("fit_prime")
        if fit:
            put(f"{role}_fuel_a", fit["a_l_h"], "FUEL_DATA (affine fit of the prime points)")
            put(f"{role}_fuel_b", fit["b_l_kwh"], "FUEL_DATA (affine fit of the prime points)")
    put("dg1_rating_kw", float(DG1_RATING_KW) if DG1_RATING_KW else None, "DG1_RATING_KW")
    put("dg2_rating_kw", float(DG2_RATING_KW) if DG2_RATING_KW else None, "DG2_RATING_KW")
    put("dg1_min_level", float(DG1_MIN_LEVEL), "DG1_MIN_LEVEL")
    put("dg2_min_up_min", float(DG2_MIN_RUN_MIN), "DG2_MIN_RUN_MIN")
    put("dg2_min_down_min", float(DG2_MIN_STOP_MIN), "DG2_MIN_STOP_MIN")
    put("bess_p_max", float(BESS_P_KW) if BESS_P_KW else None, "BESS_P_KW")
    put("bess_e_kwh", float(BESS_E_KWH), "BESS_E_KWH")
    put("soc_min", BESS_SOC_MIN, "BESS_SOC_MIN")
    put("soc_max", BESS_SOC_MAX, "BESS_SOC_MAX")
    put("soc_resume", BESS_SOC_RESUME, "BESS_SOC_RESUME")
    put("soc_init", BESS_SOC_INIT, "BESS_SOC_INIT")
    put("bess_eta_rt", BESS_EFFICIENCY, "BESS_EFFICIENCY")
    put("f_uf", F_UNDER, "F_UNDER")
    put("f_over_limit", OVERSPEED_HZ, "OVERSPEED_HZ (the inverters' 51.5 Hz trip waits 4 s)")
    put("reverse_fraction", REVERSE_FRACTION, "REVERSE_FRACTION")
    put("rocof_limit", ROCOF_LIMIT, "ROCOF_LIMIT")
    put("t_fault", FAULT_CLEARING_S, "FAULT_CLEARING_S")
    put("diesel_price", DIESEL_PRICE, "DIESEL_PRICE")


def _mpc_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _mpc_uc(entry, kw=True):
    """A unit-commitment entry of the system data ({value, unit, kw}): the number, None if not set."""
    if not isinstance(entry, dict) or poll_is_default(entry.get("value")):
        return None
    v = entry.get("kw") if kw else entry.get("value")
    return float(v) if _mpc_number(v) else None


def mpc_from_json(p, path):
    """What the PowerFactory model holds (mpc_system_model.json, the tool 'Read the system data')."""
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    tag = f"system data ({(m.get('meta') or {}).get('study_time', '?')})"

    def put(key, value, where, scale=1.0):
        if _mpc_number(value):
            p.set(key, float(value) * scale, f"{tag}: {where}")

    for d in m.get("gensets") or []:
        role = "dg1" if d.get("name") == DG1_NAME else "dg2" if d.get("name") == DG2_NAME else None
        if role is None:
            continue
        uc = d.get("unit_commitment") or {}
        put(f"{role}_s_kva", d.get("rating_kva"), "rating_kva")
        put(f"{role}_rating_kw", d.get("rating_kw"), "rating_kw")
        put(f"{role}_e_kws", d.get("kinetic_energy_kws"), "H x S")
        put(f"{role}_xdss", (d.get("machine") or {}).get("xdss"), "TypSym.xdss")
        put(f"{role}_p_max", _mpc_uc(uc.get("p_max_operational")), "Pmax_uc")
        pmin, rating = _mpc_uc(uc.get("p_min_operational")), d.get("rating_kw")
        if pmin is not None and _mpc_number(rating) and rating > 0:
            put(f"{role}_min_level", pmin / rating, "Pmin_uc / rating")
        if role == "dg2":
            for key, field in (("dg2_min_up_min", "min_up_time"), ("dg2_min_down_min", "min_down_time")):
                entry = uc.get(field)
                v = _mpc_uc(entry, kw=False)
                if v is not None:
                    unit = str(entry.get("unit") or "").strip("[] ").lower()
                    put(key, v * {"h": 60.0, "s": 1 / 60.0}.get(unit, 1.0), field)
            put("c_start", _mpc_uc(uc.get("cost_start_up"), kw=False), "cost_up")
    b = m.get("battery")
    if b:
        lim, st = b.get("limits") or {}, b.get("storage_model") or {}
        if _mpc_uc(lim.get("p_max_operational")) is not None:
            put("bess_p_max", _mpc_uc(lim["p_max_operational"]), "Pmax_uc")
        elif not BESS_P_KW and _mpc_number(b.get("rating_kva")):
            put("bess_p_max", b["rating_kva"] * (b.get("cos_phi") or 1.0), "rating_kva x cos_phi")
        put("bess_e_kwh", _mpc_uc(st.get("capacity")), "ElmStorage.capacity")
        put("c_curtail", _mpc_uc(lim.get("cost_curtailment"), kw=False), "costCurtailment (per MWh)", 1e-3)
    pvs = m.get("pv") or []
    if pvs and all(_mpc_number(x.get("rating_kva")) for x in pvs):
        put("pv_p_max", sum(x["rating_kva"] * (x.get("cos_phi") or 1.0) for x in pvs), "rating_kva x cos_phi")
    for lod in (m.get("loads") or [])[:1]:
        lf = lod.get("load_flow") or {}
        if _mpc_number(lf.get("p_kw")) and _mpc_number(lf.get("q_kvar")) and lf["p_kw"] > 1:
            put("load_tan_phi", lf["q_kvar"] / lf["p_kw"], "load flow Q/P")
    put("losses", m.get("losses_share"), "losses_share")


def mpc_from_model(p, net):
    """The PowerFactory model itself, while the MPC runs on its load flows (net of qds_network
    and prepare_pv_bess)."""
    for role, gen, rating in (("dg1", net["dg1"], net["r1"]), ("dg2", net["dg2"], net["r2"])):
        kva, _ = rating_kva_cos(gen)
        typ = attr(gen, "typ_id")
        if kva > 0:
            p.set(f"{role}_s_kva", kva, "PowerFactory model: sgn x ngnum")
            p.set(f"{role}_rating_kw", rating, "PowerFactory model: sgn x cosn x ngnum")
            h = attr(typ, "h")
            if _mpc_number(h) and h > 0:
                p.set(f"{role}_e_kws", h * kva, "PowerFactory model: H x S")
        xd = attr(typ, "xdss")
        if _mpc_number(xd) and xd > 0:
            p.set(f"{role}_xdss", float(xd), "PowerFactory model: TypSym.xdss")
    if net.get("p_bess"):
        p.set("bess_p_max", net["p_bess"], "PowerFactory model (or BESS_P_KW)")
    pv = sum(k * c for k, c in (rating_kva_cos(x) for x in net.get("pv") or []))
    if pv > 0:
        p.set("pv_p_max", pv, "PowerFactory model: sgn x cosn")


def mpc_from_calibration(p, path):
    with open(path, encoding="utf-8") as fh:
        cal = json.load(fh)
    for key in MPC_CALIBRATED:
        entry = cal.get(key)
        if isinstance(entry, dict) and _mpc_number(entry.get("value")):
            p.set(key, float(entry["value"]), f"calibrated {cal.get('made', '')}: {entry.get('source', '')}")


def mpc_params(net=None, soc_init=None, overrides=None):
    """The MPC's parameters: MPC_PARAMETERS, this file's settings, the system data, the model
    (net, during a run in PowerFactory), the calibration, and last the overrides (MPC_PARAMS)."""
    p = MpcParams()
    mpc_from_settings(p)
    path = mpc_system_json()
    if path:
        try:
            mpc_from_json(p, path)
        except (OSError, ValueError, KeyError, TypeError) as e:
            p.notes.append(f"{path} could not be read ({e})")
    else:
        p.notes.append("no system data (mpc_system_model.json; tool 'Read the system data from PowerFactory'): "
                       "the data sheets and the settings of this file are used")
    if net is not None:
        mpc_from_model(p, net)
    path = mpc_calibration_json()
    if path:
        try:
            mpc_from_calibration(p, path)
        except (OSError, ValueError) as e:
            p.notes.append(f"{path} could not be read ({e})")
    else:
        p.notes.append("no calibration (mpc_calibration.json; tool 'Calibrate'): the handoff estimates are used")
    if soc_init is not None:
        p.set("soc_init", soc_init, "this run")
    for key, value in (MPC_PARAMS if overrides is None else overrides).items():
        p.set(key, value, "dialog (MPC_PARAMS)")
    return p.finish()


# ---------------------------------------------------------------------------- the plant
class MpcRule(NamedTuple):
    """A battery and curtailment rule: the battery holds DG_1 at p1_target (DG_2 at its
    minimum) while it can, and does not discharge below soc_floor."""
    p1_target: float
    soc_floor: float


@dataclass(slots=True)
class MpcState:
    soc: float
    locked: bool = False    # discharge locked out after SOC_MIN until SOC_RESUME
    u2: int = 0             # DG_2 running
    t2: int = 10 ** 6       # steps DG_2 has been in its present state (on or off)


@dataclass(slots=True)
class MpcOp:
    """One step as dispatched, with its margin."""
    load: float
    pv_avail: float
    curtail: float
    bess: float
    u2: int
    p1: float
    p2: float
    m: float = MPC_INF
    binding: str = ""

    @property
    def pv(self):
        return self.pv_avail - self.curtail


def mpc_discharge_max(p, soc, locked, floor=0.0):
    """Most the battery can deliver this step (power limit, or the energy above the floor)."""
    if locked:
        return 0.0
    kwh = (soc - max(p.soc_min, floor)) * p.bess_e_kwh * p.eta
    return max(0.0, min(p.bess_p_max, kwh / p.dt_h))


def mpc_charge_max(p, soc):
    kwh = (p.soc_max - soc) * p.bess_e_kwh / p.eta
    return max(0.0, min(p.bess_p_max, kwh / p.dt_h))


def mpc_next_soc(p, soc, locked, bess):
    """SOC and lockout after one step at terminal power bess (the equations of Battery)."""
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


def mpc_soc_before(p, soc_after, bess):
    """The SOC a step started from (mpc_next_soc backwards, without its limits)."""
    if bess >= 0:
        return soc_after + bess * p.dt_h / (p.eta * p.bess_e_kwh)
    return soc_after + bess * p.dt_h * p.eta / p.bess_e_kwh


def mpc_advance(p, st, op):
    soc, locked = mpc_next_soc(p, st.soc, st.locked, op.bess)
    return MpcState(soc, locked, op.u2, st.t2 + 1 if op.u2 == st.u2 else 1)


def mpc_build(p, load, pv_avail, curtail, bess, u2, p2=None):
    """The balance with DG_1 as the slack. DG_2, when running, stays at its minimum and takes
    only what DG_1 cannot (its fuel per kWh is higher, and a smaller DG_2 is a smaller event
    when it trips), unless p2 sets it. Only m is worked out: mpc_margins names the binding
    constraint of the steps that are logged."""
    diesel = (1.0 + p.losses) * load - (pv_avail - curtail) - bess
    if not u2:
        p2 = 0.0
    elif p2 is None:
        p2 = min(max(diesel - p.dg1_p_max, p.dg2_p_min), p.dg2_p_max)
    else:
        p2 = min(max(p2, p.dg2_p_min), p.dg2_p_max)
    op = MpcOp(load, pv_avail, curtail, bess, u2, diesel - p2, p2)
    op.m = mpc_margin(p, op)
    return op


def mpc_operate(p, load, pv_avail, u2, soc, locked, rule=None, repair=True, memo=None):
    """The step a rule gives (battery, then curtailment so the diesels keep their minimum),
    repaired where its margin is below m*. memo: a dict for the steps already worked out (one
    decision's rollouts reach the same step by many paths, e.g. with the battery at its limit);
    the steps in it are shared, so they must not be changed."""
    if rule is None:
        rule = MpcRule(p.dg1_p_min, p.soc_min)
    demand = (1.0 + p.losses) * load
    floor = p.dg1_p_min + (p.dg2_p_min if u2 else 0.0)
    want = demand - pv_avail - max(rule.p1_target, p.dg1_p_min) - (p.dg2_p_min if u2 else 0.0)
    if want > 0:
        bess = min(want, mpc_discharge_max(p, soc, locked, rule.soc_floor))
    else:
        bess = -min(-want, mpc_charge_max(p, soc))
    curtail = min(max(floor - (demand - pv_avail - bess), 0.0), pv_avail)
    if memo is not None:
        key = (load, pv_avail, curtail, bess, u2, repair)
        op = memo.get(key)
        if op is not None:
            return op
    op = mpc_build(p, load, pv_avail, curtail, bess, u2)
    if repair and op.m < p.m_star:
        op = mpc_repair(p, op)
    if memo is not None:
        memo[key] = op
    return op


MPC_REPAIR_STEPS = (0.125, 0.25, 0.5, 0.75, 1.0)    # shares of the most a repair move can do
MPC_REPAIR_BISECT = 3                               # halvings to find the least share that is enough
MPC_REPAIR_MOVES = (
    # curtail PV (the diesels take up what PV no longer gives)
    (lambda p, o, x: mpc_build(p, o.load, o.pv_avail, o.curtail + x, o.bess, o.u2),
     lambda p, o: o.pv),
    # discharge less (the diesels take it up)
    (lambda p, o, x: mpc_build(p, o.load, o.pv_avail, o.curtail, o.bess - x, o.u2),
     lambda p, o: max(o.bess, 0.0)),
    # charge less, with as much PV curtailed, so the diesels keep their load
    (lambda p, o, x: mpc_build(p, o.load, o.pv_avail, min(o.curtail + x, o.pv_avail), o.bess + x, o.u2),
     lambda p, o: max(-o.bess, 0.0)),
    # move load from DG_1 to a running DG_2: DG_1's load acceptance a(l) grows as its loading falls
    (lambda p, o, x: mpc_build(p, o.load, o.pv_avail, o.curtail, o.bess, o.u2, o.p2 + x),
     lambda p, o: min(p.dg2_p_max - o.p2, o.p1 - p.dg1_p_min) if o.u2 else 0.0),
)


def mpc_repair_value(p, op):
    """What a repaired step costs: the step cost less the value of the energy it puts in the
    battery (so curtailing PV that would have charged it, or charging it from diesel, both show)."""
    stored = -op.bess * (p.eta if op.bess < 0 else 1.0 / p.eta) * p.dt_h
    return mpc_step_cost(p, op, False)[0] - p.c_terminal_value * stored


def mpc_repair(p, op):
    """Curtail PV, limit the battery, or move load onto a running DG_2. Each move takes the
    least share that reaches m*; of the moves that do, the cheapest is kept. If none does, the
    one that helps most is kept and the moves are tried once more from there. Starting DG_2 is
    left to the schedules."""
    for _ in range(2):
        enough, best = [], op
        for move, room in MPC_REPAIR_MOVES:
            x_max = room(p, op)
            if x_max <= 1e-6:
                continue
            lo = 0.0
            for frac in MPC_REPAIR_STEPS:
                cand = move(p, op, frac * x_max)
                if cand.m >= p.m_star:
                    hi = frac
                    for _ in range(MPC_REPAIR_BISECT):
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
            return min(enough, key=lambda o: mpc_repair_value(p, o))
        if best is op:
            return op
        op = best
    return op


# ---------------------------------------------------------------------------- events, stability, margin
def mpc_acceptance(loading_pct):
    """a(l): the load step DG_1 may take from loading l, % of prime (ISO 8528-5 guide)."""
    if loading_pct <= MPC_ACCEPT[0][0]:
        return MPC_ACCEPT[0][1]
    for (x0, y0), (x1, y1) in zip(MPC_ACCEPT, MPC_ACCEPT[1:]):
        if loading_pct <= x1:
            return y0 + (y1 - y0) * (loading_pct - x0) / (x1 - x0)
    return MPC_ACCEPT[-1][1]


def mpc_events(p, op):
    """(name, dP kW, DG_2 lost) of the credible sudden changes. Down = generation lost or load
    gained (under-frequency), up = generation gained or load lost (over-frequency)."""
    down = (("PV drop", p.rho * op.pv, False),
            ("BESS trip", max(op.bess, 0.0), False),
            ("load step", p.lambda_up * op.load, False),
            ("DG_2 trip", op.p2 if op.u2 else 0.0, True))
    surge = op.pv if p.pv_surge == "delivered" else op.curtail if p.pv_surge == "curtailed" else 0.0
    up = (("BESS trip charging", max(-op.bess, 0.0)),
          ("load loss", p.lambda_down * op.load),
          ("PV surge", p.rho * surge))
    return down, up


def mpc_checks(p, op):
    """Every (constraint, event, dP, allowed dP) of a step."""
    u2 = op.u2
    e_all = p.dg1_e_kws + (p.dg2_e_kws if u2 else 0.0)
    s_all = p.dg1_s_kva + (p.dg2_s_kva if u2 else 0.0)
    xd = max(p.dg1_xdss, p.dg2_xdss) if u2 else p.dg1_xdss
    reserve1 = p.dg1_p_max - op.p1
    reserve2 = (p.dg2_p_max - op.p2) if u2 else 0.0
    accept = mpc_acceptance(100.0 * op.p1 / p.dg1_p_max) / 100.0 * p.dg1_p_max
    down, up = mpc_events(p, op)
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
    if u2:      # one set has nothing to slip against: the inverters follow the voltage they find
        out.append(("S8", "fault at the main board", abs(op.p2 - op.p1 * p.s8_ratio), p.allow_s8))
    return out


def mpc_margin_of(dp, allow):
    """m = 1 - dP / dP_allowed."""
    if allow > 0.0:
        return 1.0 - dp / allow
    return 1.0 if dp <= 0.0 else MPC_M_NONE


def _mpc_event_margin(m, dp, least, none):
    """m_k with one more event: m = 1 - dP / allowed is smallest at the smallest allowance in use
    (least, MPC_INF if none is), and MPC_M_NONE where an allowance is not above 0 (none)."""
    if least < MPC_INF:
        m = min(m, 1.0 - dp / least)
    return min(m, MPC_M_NONE) if none else m


def _mpc_fold(least, none, allow):
    """(least, none) with one more allowance in use."""
    return (min(least, allow), none) if allow > 0.0 else (least, True)


def mpc_margin(p, op):
    """m_k alone, exactly as mpc_margins gives it, for the inner loops (millions of steps in a
    run): no table, no name of the binding constraint. m = 1 - dP / allowed falls as dP grows
    and as the allowance shrinks, so the events that share their allowances (the down events
    that keep DG_2, the up events) count only with their largest dP, and only the smallest
    allowance is divided; the allowances that do not depend on the step come from finish()."""
    u2, hard = op.u2, p.hard
    down, lost, up = p.allow_fixed[u2]
    pv = op.pv_avail - op.curtail
    m = MPC_INF
    dp = max(p.rho * pv, op.bess, p.lambda_up * op.load)                    # PV drop, BESS trip, load step
    dp2 = op.p2 if u2 else 0.0                                              # DG_2 trip
    if dp > 0.0 or dp2 > 0.0:
        reserve1 = p.dg1_p_max - op.p1
        reserve2 = (p.dg2_p_max - op.p2) if u2 else 0.0
        accept = mpc_acceptance(100.0 * op.p1 / p.dg1_p_max) / 100.0 * p.dg1_p_max
        for dp_k, (least, none), reserve in ((dp, down, reserve1 + reserve2), (dp2, lost, reserve1 + 0.0)):
            if dp_k > 0.0:
                if hard["S1"]:
                    least, none = _mpc_fold(least, none, reserve)
                if hard["S2"]:
                    least, none = _mpc_fold(least, none, accept)
                m = _mpc_event_margin(m, dp_k, least, none)
    surge = pv if p.pv_surge == "delivered" else op.curtail if p.pv_surge == "curtailed" else 0.0
    dp = max(-op.bess, p.lambda_down * op.load, p.rho * surge)             # BESS trip charging, load loss, PV surge
    if dp > 0.0:
        least, none = up
        if hard["S4r"]:
            least, none = _mpc_fold(least, none, op.p1 + p.reverse_kw)
        m = _mpc_event_margin(m, dp, least, none)
    if hard["S6"]:
        m = min(m, mpc_margin_of(pv + abs(op.bess), p.allow_s6[u2]))
    if hard["S7q"]:
        q_cap = math.sqrt(max(p.dg1_s_kva ** 2 - op.p1 ** 2, 0.0))
        if u2:
            q_cap += math.sqrt(max(p.dg2_s_kva ** 2 - op.p2 ** 2, 0.0))
        m = min(m, mpc_margin_of(p.load_tan_phi * op.load, q_cap))
    if u2 and hard["S8"]:
        m = min(m, mpc_margin_of(abs(op.p2 - op.p1 * p.s8_ratio), p.allow_s8))
    return m


def mpc_margins(p, op):
    """(m_k, 'constraint: event' that sets it) over the hard constraints."""
    best, why = MPC_INF, ""
    hard = p.hard
    for cid, name, dp, allow in mpc_checks(p, op):
        if hard[cid]:
            m = mpc_margin_of(dp, allow)
            if m < best:
                best, why = m, f"{cid}: {name}"
    return best, why


def mpc_margin_table(p, op):
    """[(constraint, event, dP, allowed, m, mode)] of every constraint not switched off."""
    return [(cid, name, dp, allow, mpc_margin_of(dp, allow), "hard" if p.hard[cid] else "report")
            for cid, name, dp, allow in mpc_checks(p, op) if p.shown[cid]]


# ---------------------------------------------------------------------------- the objective
def mpc_fuel_l(p, op):
    """Litres burnt in one step ([P] (14), [S] (23): affine in P while running)."""
    f = p.dg1_fuel_a + p.dg1_fuel_b * max(op.p1, 0.0)
    if op.u2:
        f += p.dg2_fuel_a + p.dg2_fuel_b * max(op.p2, 0.0)
    return f * p.dt_h


def mpc_phi(p, m):
    return 0.0 if m >= p.m_star else (p.m_star - max(m, 0.0)) ** 2


def mpc_step_cost(p, op, started):
    """(cost of the step in rand, violation = how far m_k is below zero)."""
    c = p.diesel_price * mpc_fuel_l(p, op)
    if started:
        c += p.c_start
    c += (p.c_curtail * op.curtail + p.c_wear * abs(op.bess)) * p.dt_h
    c += p.w_margin * mpc_phi(p, op.m)
    return c, (-op.m if op.m < 0.0 else 0.0)


def mpc_terminal_cost(p, soc):
    return p.c_terminal_value * max(0.0, p.soc_ref - soc) * p.bess_e_kwh


# ---------------------------------------------------------------------------- the heuristic
@dataclass
class MpcDecision:
    u2: int
    rule: MpcRule
    cost: float
    min_margin: float
    violation: float
    schedule: tuple
    candidates: int
    feasible: int
    op: MpcOp = None            # its first step as dispatched (with the binding constraint), from MpcWorkers


class HeuristicMPC:
    def __init__(self, p):
        self.p = p
        self.rules = [MpcRule(p.dg1_p_min + off, floor) for off in p.rule_p1_offsets for floor in p.rule_soc_floors]

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

    def rollout(self, ri, rule, sched, st, load, pv, cache, memo):
        """(cost J, violation, smallest m_k) of one candidate. Candidates that share the start of
        their schedule share those steps through the cache, and every candidate the steps
        already dispatched through memo (mpc_operate)."""
        p = self.p
        soc, locked, prev = st.soc, st.locked, st.u2
        cost = viol = 0.0
        mmin = MPC_INF
        for k, u in enumerate(sched):
            key = (ri, k, u, prev, soc, locked)
            hit = cache.get(key)
            if hit is None:
                op = mpc_operate(p, load[k], pv[k], u, soc, locked, rule, memo=memo)
                c, v = mpc_step_cost(p, op, u > prev)
                hit = cache[key] = (c, v, op.m) + mpc_next_soc(p, soc, locked, op.bess)
            c, v, m, soc, locked = hit
            cost += c
            viol += v
            mmin = min(mmin, m)
            prev = u
        return cost + mpc_terminal_cost(p, soc), viol, mmin

    def evaluate(self, st, load, pv, scheds, rules=None):
        """[(violation, cost J, smallest m_k, rule index, schedule index)] of the candidates of
        the rules (indices into self.rules; None: all of them), rule by rule, each in the order
        of scheds. The rules are independent: MpcWorkers evaluates them in separate processes."""
        cache, memo, runs = {}, {}, []
        for ri in range(len(self.rules)) if rules is None else rules:
            rule = self.rules[ri]
            for si, sched in enumerate(scheds):
                cost, viol, mmin = self.rollout(ri, rule, sched, st, load, pv, cache, memo)
                runs.append((viol, cost, mmin, ri, si))
        return runs

    def choose(self, runs, scheds):
        """The decision among all the candidates (runs of evaluate, in its order). Feasible ones
        (m_k >= 0 throughout) first; among those the cheapest, and within tie_tolerance of it the
        one with the larger smallest margin. If none is feasible, the least violation."""
        p = self.p
        feasible = [r for r in runs if r[0] <= 0.0]
        if feasible:
            j_best = min(r[1] for r in feasible)
            close = [r for r in feasible if r[1] <= j_best + p.tie_tolerance * abs(j_best)]
            viol, cost, mmin, ri, si = max(close, key=lambda r: (r[2], -r[1]))
        else:
            viol, cost, mmin, ri, si = min(runs, key=lambda r: (r[0], r[1]))
        sched = scheds[si]
        return MpcDecision(sched[0], self.rules[ri], cost, mmin, viol, sched, len(runs), len(feasible))

    def decide(self, st, load, pv):
        """The best candidate over the forecasts (kW, from this step on)."""
        n = min(self.p.horizon, len(load), len(pv))
        scheds = self.schedules(st, n)
        return self.choose(self.evaluate(st, load, pv, scheds), scheds)


def mpc_check_step(p, load=279.0, pv=0.0, bess=87.0, p1=193.7, u2=0, p2=0.0, label="2002-07-01 06:20"):
    """The margin table of one step (by default the point the system data was polled at)."""
    op = MpcOp(load=load, pv_avail=pv, curtail=0.0, bess=bess, u2=u2, p1=p1, p2=p2)
    op.m, op.binding = mpc_margins(p, op)
    lines = [f"Check of one step ({label}): load {load:.0f} kW, PV {pv:.0f} kW, battery {bess:+.0f} kW, "
             f"{DG1_NAME} {p1:.1f} kW, {DG2_NAME} {'%.0f kW' % p2 if u2 else 'off'}",
             f"   {'constraint':<46} {'event':<20} {'dP kW':>8} {'allowed':>8} {'m':>7}"]
    for cid, name, dp, allow, m, mode in mpc_margin_table(p, op):
        lines.append(f"   {cid + ' ' + MPC_CONSTRAINTS[cid]:<46} {name:<20} {dp:8.1f} {allow:8.1f} {m:7.2f}"
                     + ("" if mode == "hard" else "  (reported only)"))
    lines.append(f"   m_k = {op.m:.3f} ({op.binding}); m* = {p.m_star}")
    return lines


def mpc_report():
    """The tool 'Report the MPC parameters': every value with its source, and the check of a step."""
    p = mpc_params()
    log("\n" + "=" * 100)
    log("MPC PARAMETERS (where each value comes from)")
    log("=" * 100)
    for note in p.notes:
        warn(note)
    for line in p.report():
        log(line)
    log("")
    for line in mpc_check_step(p):
        log(line)
    prof = mpc_profile_paths()
    log(f"\nProfiles for the forecasts: {' and '.join(prof) if prof else 'NOT FOUND (persistence is used)'}")


# ============================================================================
# MPC: CALIBRATION
# ============================================================================
# Fits the quantities of the stability model that data can give, and writes them to
# MPC_DIR\mpc_calibration.json (read by mpc_params):
#   rho          PV fast-drop fraction: the MPC_CAL_QUANTILE quantile of the drop of PV output
#                over one step, as a share of the output before it (PV above 10 % of rating)
#   lambda_up    load step up / loss of load, share of the load: the same quantile of the
#   lambda_down  change of the load over one step
#   tau_eff      energy-deficit time of the nadir model df = f0 dP tau / (2 E), from every RMS run
#   tau_eff_over of this study with a single step of power (loss of a source, load pick-up, loss of
#                load) that stayed stable: tau = 2 E df / (f0 dP); dP is what the surviving diesels
#                took up by the end, E their kinetic energy. The largest is kept
#   kappa        the largest inverter share (PV + |battery|) / running genset kVA of a stable RMS
#                run: a lower bound, since no run has failed on it yet
# It also checks the S5 window model against the 500 ms ROCOF the runs measured, and how long
# the frequency stayed above the inverters' over-frequency trip after a loss of load.
MPC_CAL_QUANTILE = 0.95
MPC_CAL_PV_MIN_SHARE = 0.10


def mpc_rms_results():
    return mpc_find(MPC_RMS_RESULTS, os.path.join(OUT_DIR, "stability_rms_results.csv"),
                    os.path.join(SCRIPT_DIR, "stability_rms_rule_based_complete", "stability_rms_results.csv"))


def _mpc_quantile(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))] if v else None


def _mpc_float(row, key):
    try:
        return float(row[key])
    except (KeyError, ValueError, TypeError):
        return None


def _mpc_time_above(path, limit):
    """Seconds the frequency at any bus was above `limit` in a curve file (None without the file)."""
    if not os.path.isfile(path):
        return None
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return None
    cols = [c for c in rows[0] if c.startswith("f_") and c.endswith("_Hz")]
    total, t_prev = 0.0, None
    for r in rows:
        t = float(r["t_s"])
        if t_prev is not None and max(float(r[c]) for c in cols) > limit:
            total += t - t_prev
        t_prev = t
    return total


def mpc_calibrate():
    """The tool 'Calibrate': fits, writes mpc_calibration.json, returns the lines of its report."""
    p = mpc_params(overrides={})
    cal = dict(made=f"{datetime.now():%Y-%m-%d %H:%M}", quantile=MPC_CAL_QUANTILE)
    lines = []
    prof = mpc_profiles()
    if prof:
        floor = MPC_CAL_PV_MIN_SHARE * p.pv_p_max
        pv, load = prof["pv"], prof["load"]
        drops = [max(0.0, (a - b) / a) for a, b in zip(pv, pv[1:]) if a > floor]
        rel = [(b - a) / a for a, b in zip(load, load[1:]) if a > 0]
        q = MPC_CAL_QUANTILE
        for key, values, text in (
                ("rho", drops, f"{q:.0%} quantile of the PV drop per step, PV > {floor:.0f} kW ({len(drops)} steps)"),
                ("lambda_up", [max(0.0, x) for x in rel], f"{q:.0%} quantile of the load rise per step"),
                ("lambda_down", [max(0.0, -x) for x in rel], f"{q:.0%} quantile of the load fall per step")):
            cal[key] = dict(value=_mpc_quantile(values, q), source=text,
                            quantiles={f"{x:g}": _mpc_quantile(values, x) for x in (0.9, 0.95, 0.99, 0.999)})
    else:
        lines.append("!! The profiles were not found: rho and lambda are not fitted")
    path = mpc_rms_results()
    runs, shares = [], []
    if path:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            if not r.get("verdict") or r["verdict"] == "not applicable":
                continue
            case = r["case"].split("_")[0]
            dg2_on = (_mpc_float(r, "p_dg2_0") or 0.0) > 1.0
            s = p.dg1_s_kva + (p.dg2_s_kva if dg2_on else 0.0)
            if r["verdict"] == "stable":
                share = ((_mpc_float(r, "p_pv_0") or 0.0) + abs(_mpc_float(r, "p_bess_0") or 0.0)) / s
                shares.append((share, r["case"]))
            if r["verdict"] != "stable" or case[:1] not in ("G", "P", "R"):
                continue
            lost2 = case == "G2"
            e = p.dg1_e_kws + (p.dg2_e_kws if dg2_on and not lost2 else 0.0)
            dp = (_mpc_float(r, "p_dg1_end") or 0.0) - (_mpc_float(r, "p_dg1_0") or 0.0)
            if dg2_on and not lost2:
                dp += (_mpc_float(r, "p_dg2_end") or 0.0) - (_mpc_float(r, "p_dg2_0") or 0.0)
            over = case[0] == "R"
            f_ext = _mpc_float(r, "f_max" if over else "f_min")
            if f_ext is None or abs(dp) < 1.0:
                continue
            df = (f_ext - p.f0) if over else (p.f0 - f_ext)
            if df <= 0:
                continue
            tau = 2.0 * e * df / (p.f0 * abs(dp))
            model_r500 = p.f0 * abs(dp) / (2 * e) * min(0.5, tau) / 0.5
            above = (_mpc_time_above(os.path.join(os.path.dirname(path), f"{r['case']}_curves.csv"), F_OVER)
                     if over else None)
            runs.append(dict(case=r["case"], direction="up" if over else "down", dp_kw=abs(dp), e_kws=e, df_hz=df,
                             tau_s=tau, rocof_500_measured=_mpc_float(r, "rocof_500"),
                             rocof_500_model=model_r500, above_f_over_s=above))
        down = [x["tau_s"] for x in runs if x["direction"] == "down"]
        up = [x["tau_s"] for x in runs if x["direction"] == "up"]
        if down:
            cal["tau_eff"] = dict(value=max(down), source=f"largest of {len(down)} stable RMS steps down "
                                                          f"(range {min(down):.3f}-{max(down):.3f} s)")
        if up:
            cal["tau_eff_over"] = dict(value=max(up), source=f"largest of {len(up)} stable RMS steps up "
                                                             f"(range {min(up):.3f}-{max(up):.3f} s)")
        if shares:
            best = max(shares)
            cal["kappa"] = dict(value=best[0], source=f"largest inverter share in a stable RMS run ({best[1]}); "
                                                      "a lower bound")
        cal["rms_results"] = path
        cal["rms_runs"] = runs
    else:
        lines.append("!! No RMS results (stability_rms_results.csv) were found: tau_eff and kappa are not fitted. "
                     "Run the stability study first.")
    os.makedirs(MPC_DIR, exist_ok=True)
    out = os.path.join(MPC_DIR, "mpc_calibration.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(cal, fh, indent=2)
    lines.insert(0, f"Calibration written to {out}")
    for key in MPC_CALIBRATED:
        if key in cal:
            lines.append(f"   {key:<13} {cal[key]['value']:8.3f}   {cal[key]['source']}")
    if runs:
        lines.append(f"   {'RMS run':<24} {'dP kW':>7} {'E kWs':>6} {'df Hz':>6} {'tau s':>6}  "
                     f"{'ROCOF500 meas':>13} {'model':>6}  {'>%g Hz s' % F_OVER:>10}")
        for x in runs:
            above = f"{x['above_f_over_s']:10.2f}" if x["above_f_over_s"] is not None else ""
            meas = f"{x['rocof_500_measured']:13.2f}" if x["rocof_500_measured"] is not None else f"{'':13}"
            lines.append(f"   {x['case']:<24} {x['dp_kw']:7.1f} {x['e_kws']:6.0f} {x['df_hz']:6.3f} {x['tau_s']:6.3f}  "
                         f"{meas} {x['rocof_500_model']:6.2f}  {above}")
    return lines


def run_calibration():
    log("\n" + "=" * 100)
    log("MPC CALIBRATION (profiles and RMS results)")
    log("=" * 100)
    for line in mpc_calibrate():
        if line.startswith("!! "):
            warn(line[3:])
        else:
            log(line)


# ============================================================================
# MPC: THE DISPATCH
# ============================================================================
# The MPC runs in the dispatch like the rule-based controller, on one of two engines
# (MPC_ENGINE):
#   "powerfactory"  through PowerFactory's load flows, step by step as run_rule_based: measure
#                   the load and the PV the sun allows (battery idle, PV uncurtailed), decide over
#                   the horizon, set DG_2, the battery and the curtailment, solve again, and move
#                   the SOC on with what really flowed. The load the MPC's model sees is what PV,
#                   battery and diesels have to cover together (losses included)
#   "model"         on its own model of the island (losses as a share of the load), no load flows:
#                   no PowerFactory, about ten seconds a simulated day
# The forecasts over the horizon come from the profiles (MPC_FORECAST "perfect": what the profile
# has, scaled to the load measured now; "yesterday": the same time a day earlier); the present
# step is always the measured one. Without the profiles the MPC assumes nothing changes.
# Its log has the columns of the rule-based one (QDS_COLUMNS) and MPC_COLUMNS after them.
_mpc_profile_cache = {}


def mpc_profile_paths():
    """(load CSV, PV CSV) the forecasts come from, or None."""
    if MPC_PROFILES:
        return MPC_PROFILES if all(os.path.isfile(x) for x in MPC_PROFILES) else None
    for folder in (SCRIPT_DIR, os.path.join(os.path.expanduser("~"), "Documents", "DigSILENT Outputs"), os.getcwd()):
        pair = (os.path.join(folder, "aggregate_total_load.csv"), os.path.join(folder, "pv_ac_profile_measured.csv"))
        if all(os.path.isfile(x) for x in pair):
            return pair
    return None


def mpc_profiles():
    """{'times', 'load', 'pv', 'index'} from the profiles, or None. Read once."""
    paths = mpc_profile_paths()
    if paths is None:
        return None
    if paths not in _mpc_profile_cache:
        with open(paths[0], newline="", encoding="utf-8-sig") as fh:
            load = [(r["timestamp"][:16], float(r["total_active_power_kw"])) for r in csv.DictReader(fh)]
        with open(paths[1], newline="", encoding="utf-8-sig") as fh:
            pv = {r["timestamp"][:16]: float(r["p_ac_kw"]) for r in csv.DictReader(fh)}
        times = [t for t, _ in load]
        _mpc_profile_cache[paths] = dict(times=times, load=[v for _, v in load], pv=[pv.get(t, 0.0) for t in times],
                                         index={t: i for i, t in enumerate(times)}, paths=paths)
    return _mpc_profile_cache[paths]


def mpc_forecast_paths(kind):
    """(load CSV, PV CSV) of the forecast `kind` of MPC_FORECAST_FILES, or None if one is missing."""
    paths = []
    for name in MPC_FORECAST_FILES[kind][1:]:
        found = [x for x in ([name] if os.path.isabs(name) else
                             [os.path.join(SCRIPT_DIR, name), os.path.join(os.getcwd(), name)]) if os.path.isfile(x)]
        if not found:
            return None
        paths.append(found[0])
    return tuple(paths)


def mpc_forecast_series(kind):
    """{'times', 'load', 'pv', 'index', 'paths'} of the forecast `kind`, or None. Read once."""
    paths = mpc_forecast_paths(kind)
    if paths is None:
        return None
    if paths not in _mpc_profile_cache:
        with open(paths[0], newline="", encoding="utf-8-sig") as fh:
            load = [(r["timestamp"][:16], float(r["p_kw"])) for r in csv.DictReader(fh)]
        with open(paths[1], newline="", encoding="utf-8-sig") as fh:
            pv = {r["timestamp"][:16]: float(r["p_kw"]) for r in csv.DictReader(fh)}
        times = [t for t, _ in load]
        _mpc_profile_cache[paths] = dict(times=times, load=[v for _, v in load], pv=[pv.get(t, 0.0) for t in times],
                                         index={t: i for i, t in enumerate(times)}, paths=paths)
    return _mpc_profile_cache[paths]


def mpc_forecast_problem(kind, first=None, last=None):
    """Why the forecast `kind` cannot be used for the period first -> last ('YYYY-MM-DD HH:MM'), or None."""
    if kind not in MPC_FORECAST_FILES:
        return None
    label, *files = MPC_FORECAST_FILES[kind]
    series = mpc_forecast_series(kind)
    if series is None:
        return f"The {label} forecast needs {' and '.join(files)} next to this file (MPC_FORECAST_FILES)."
    if first is not None and (first not in series["index"] or last not in series["index"]):
        return (f"The {label} forecast runs from {series['times'][0]} to {series['times'][-1]}: "
                f"{first} -> {last} is not in it.")
    return None


class MpcForecaster:
    def __init__(self, p, kind=None):
        self.prof, self.kind = mpc_profiles(), kind or MPC_FORECAST
        self.series = mpc_forecast_series(self.kind) if self.kind in MPC_FORECAST_FILES else None
        self.day = round(24 * 60 / p.dt_min)
        self.missing = 0

    def horizon(self, when, n, load_now, pv_now):
        """(load, PV) over n steps from the instant `when` ('YYYY-MM-DD HH:MM'); the first is now."""
        if self.kind in MPC_FORECAST_FILES:
            return self.from_file(when, n, load_now, pv_now)
        i = self.prof["index"].get(when) if self.prof else None
        if i is None:
            self.missing += 1
            return [load_now] * n, [pv_now] * n
        L, P = self.prof["load"], self.prof["pv"]
        n = max(1, min(n, len(L) - i))
        if self.kind == "yesterday":
            src = [j - self.day if j >= self.day else j for j in range(i, i + n)]
            scale = 1.0
        else:
            src = list(range(i, i + n))
            scale = min(max(load_now / L[i], 0.8), 1.25) if L[i] > 1.0 else 1.0
        return [load_now] + [L[j] * scale for j in src[1:]], [pv_now] + [P[j] for j in src[1:]]

    def from_file(self, when, n, load_now, pv_now):
        """The same from a forecast file. The load is scaled by what is measured now over what the
        profile has now, as for "perfect" (the MPC's load is what the sources cover, losses included),
        so the forecast's own error is kept."""
        f = self.series
        j = f["index"].get(when) if f else None
        if j is None:
            self.missing += 1
            return [load_now] * n, [pv_now] * n
        n = max(1, min(n, len(f["load"]) - j))
        i = self.prof["index"].get(when) if self.prof else None
        scale = min(max(load_now / self.prof["load"][i], 0.8), 1.25) if i is not None and self.prof["load"][i] > 1.0 else 1.0
        return ([load_now] + [f["load"][j + k] * scale for k in range(1, n)],
                [pv_now] + [f["pv"][j + k] for k in range(1, n)])


def mpc_row(p, when, op, soc, other, signal, d):
    """A log row (QDS_COLUMNS + MPC_COLUMNS) of one step as it flowed."""
    cost, _ = mpc_step_cost(p, op, False)
    return {"time": when, "load_kW": op.load, "pv_avail_kW": op.pv_avail, "pv_kW": op.pv,
            "pv_curtailed_kW": op.curtail, "bess_kW": op.bess, "bess_soc_pct": 100.0 * soc,
            "other_kW": other, "dispatch_signal_kW": signal, "DG1_kW": op.p1,
            "DG1_loading_pct": 100.0 * op.p1 / p.dg1_rating_kw, "DG2_on": op.u2, "DG2_kW": op.p2,
            "DG2_on_level_kW": 0.0, "DG2_off_level_kW": 0.0,
            "margin": op.m, "binding": op.binding, "fuel_L": mpc_fuel_l(p, op), "cost_R": cost,
            "rule_p1_target_kW": d.rule.p1_target, "rule_soc_floor": d.rule.soc_floor,
            "horizon_min_margin": d.min_margin, "feasible_candidates": d.feasible}


def mpc_write_row(writer, row):
    writer.writerow([f"{row[c]:.3f}" if isinstance(row[c], float) else row[c] for c in QDS_COLUMNS + MPC_COLUMNS])


def mpc_log_start(p, fc, t0, t1, n_steps, soc_init, csv_path, engine):
    log(f"\n=== Dispatch run, MPC ({engine}), {fmt_time(t0)} -> {fmt_time(t1)}, {QDS_STEP_MIN} min steps "
        f"({n_steps} steps), SOC at the start {soc_init:.0%} ===")
    for note in p.notes:
        warn("MPC: " + note)
    log(f"Horizon {p.horizon} steps ({p.horizon * p.dt_min / 60:g} h), {len(HeuristicMPC(p).rules)} battery rules x "
        f"the {DG2_NAME} schedules; forecast: {fc.kind}"
        + (f" from {fc.series['paths'][0]} and {fc.series['paths'][1]}" if fc.series else
           f" from {fc.prof['paths'][0]} and {fc.prof['paths'][1]}" if fc.prof else
           " - THE PROFILES WERE NOT FOUND: the MPC assumes load and PV stay as they are"))
    hard = [c for c in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8") if getattr(p, f"s{c[1]}_mode") == "hard"]
    log(f"Stability: {', '.join(hard)} in m_k, target m* = {p.m_star:g}; tau_eff {p.tau_eff:.3f} s, rho {p.rho:.2f}, "
        f"lambda {p.lambda_up:.2f}/{p.lambda_down:.2f}, kappa {p.kappa:.2f}; PV surge on {p.pv_surge} PV; "
        f"S8: swing at most {p.fault_swing_max:g} deg over a {1000 * p.t_fault:g} ms fault")
    log(f"{DG1_NAME}: {p.dg1_p_min:.1f}-{p.dg1_p_max:.0f} kW, E {p.dg1_e_kws:.0f} kWs ({p.source['dg1_e_kws']});  "
        f"{DG2_NAME}: {p.dg2_p_min:.1f}-{p.dg2_p_max:.0f} kW, E {p.dg2_e_kws:.0f} kWs, "
        f"min run/stop {p.dg2_min_up}/{p.dg2_min_down} steps")
    log(f"Costs: diesel R{p.diesel_price:.2f}/L, start R{p.c_start:g}, wear R{p.c_wear:g}/kWh, curtailment "
        f"R{p.c_curtail:g}/kWh, w {p.w_margin:g}, end of horizon below SOC {p.soc_ref:.0%}: "
        f"R{p.c_terminal_value:.2f}/kWh")
    log(f"CSV log: {csv_path}\n")


# The decisions in worker processes. Inside PowerFactory this script's Python runs the MPC's inner
# loops 25-40x slower than a plain python.exe does, so MpcWorkers hands them to MPC_WORKERS separate
# Python processes: this same file, run with MPC_WORKER_FLAG. Each takes a share of the battery
# rules, all of them evaluate their candidates at the same time, and this script chooses among the
# candidates as HeuristicMPC.decide does, so the decisions are the same, bit for bit. Only plain
# numbers go through the pipes, never this file's classes.
MPC_WORKER_FLAG = "--mpc-worker"
MPC_WORKER_START_S = 60.0   # how long a worker may take to start


def mpc_worker_count(p):
    """How many worker processes MPC_WORKERS asks for: none, or at most one per battery rule."""
    rules = len(p.rule_p1_offsets) * len(p.rule_soc_floors)
    n = max(1, (os.cpu_count() or 2) - 1) if str(MPC_WORKERS).strip().lower() == "auto" else int(MPC_WORKERS or 0)
    return max(0, min(n, rules))


def mpc_worker_python():
    """The python.exe for the workers: MPC_WORKER_PYTHON, else the one this script runs on (inside
    PowerFactory sys.executable is PowerFactory.exe; the Python it embeds lives in sys.base_prefix)."""
    if MPC_WORKER_PYTHON:
        return MPC_WORKER_PYTHON if os.path.isfile(MPC_WORKER_PYTHON) else None
    exe = sys.executable or ""
    if os.path.basename(exe).lower().startswith("python") and os.path.isfile(exe):
        return exe
    for home in (sys.base_exec_prefix, sys.base_prefix, sys.exec_prefix, sys.prefix):
        exe = os.path.join(home or "", "python.exe")
        if home and os.path.isfile(exe):
            return exe
    return None


def _mpc_receive(stream, timeout=None):
    """The next reply from a worker; with a timeout, None if none came in time (or it died)."""
    if timeout is None:
        return pickle.load(stream)
    box = []

    def read():
        try:
            box.append(pickle.load(stream))
        except Exception:                       # it ended without a word
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout)
    return box[0] if box else None


class MpcWorkers:
    """HeuristicMPC.decide with the candidates evaluated in n worker processes (0: here). If they
    cannot start, or one fails, it warns and decides here instead; close() ends them."""

    def __init__(self, p, n):
        self.p, self.mpc, self.procs, self.errors = p, HeuristicMPC(p), [], None
        self.where = "in this Python"
        if n:
            try:
                self._start(n)
            except Exception as e:
                self.close()
                warn(f"MPC: the {n} worker processes did not start ({e}); it decides in this Python, more slowly")

    def _start(self, n):
        script, exe = os.path.abspath(globals().get("__file__") or ""), mpc_worker_python()
        if not os.path.isfile(script):
            raise RuntimeError("this script is embedded in PowerFactory, so there is no file for them to run")
        if not exe:
            raise RuntimeError("no python.exe found: set MPC_WORKER_PYTHON")
        os.makedirs(MPC_DIR, exist_ok=True)
        self.errors = open(os.path.join(MPC_DIR, "mpc_worker_errors.txt"), "w")
        values = {key: getattr(self.p, key) for key in MPC_DEFAULTS}  # finish() derives the rest again
        rules = list(range(len(self.mpc.rules)))
        for j in range(n):
            share = rules[j * len(rules) // n:(j + 1) * len(rules) // n]     # contiguous: replies come rule by rule
            proc = subprocess.Popen([exe, "-I", script, MPC_WORKER_FLAG], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=self.errors,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.procs.append(proc)
            pickle.dump((values, share), proc.stdin, pickle.HIGHEST_PROTOCOL)
            proc.stdin.flush()
        for proc in self.procs:
            if _mpc_receive(proc.stdout, MPC_WORKER_START_S) != "ready":
                raise RuntimeError(f"one did not answer (its messages are in {self.errors.name})")
        self.where = f"in {n} worker processes at once ({exe})"

    def decide(self, st, load, pv):
        if not self.procs:
            return self.mpc.decide(st, load, pv)
        scheds = self.mpc.schedules(st, min(self.p.horizon, len(load), len(pv)))
        request = pickle.dumps((st.soc, st.locked, st.u2, st.t2, load, pv), pickle.HIGHEST_PROTOCOL)
        runs, firsts = [], {}
        try:
            for proc in self.procs:
                proc.stdin.write(request)
                proc.stdin.flush()
            for proc in self.procs:
                status, reply = _mpc_receive(proc.stdout)
                if status != "ok":
                    raise RuntimeError(reply.strip().splitlines()[-1])
                runs += reply[0]
                firsts.update(reply[1])
        except (OSError, EOFError, RuntimeError, pickle.UnpicklingError) as e:
            self.close()
            self.where = "in this Python (the worker processes failed)"
            warn(f"MPC: a worker process failed ({str(e) or type(e).__name__}); from here on it decides in this Python")
            return self.mpc.decide(st, load, pv)
        d = self.mpc.choose(runs, scheds)
        d.op = MpcOp(*firsts[self.mpc.rules.index(d.rule), d.u2])
        return d

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        for proc in self.procs:
            try:
                proc.stdin.close()              # a worker ends when its input closes
            except OSError:
                pass
        deadline = time.time() + 3.0
        for proc in self.procs:
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for proc in self.procs:                 # only now: a reader still blocked on it has returned
            try:
                proc.stdout.close()
            except OSError:
                pass
        self.procs = []
        if self.errors is not None:
            self.errors.close()
            try:
                if os.path.getsize(self.errors.name) == 0:
                    os.remove(self.errors.name)
            except OSError:
                pass
            self.errors = None


def mpc_worker_serve():
    """A worker process (this file run with MPC_WORKER_FLAG): it takes the MPC's parameters and its
    rules, then for each step it is sent (state, forecasts) evaluates its rules' candidates and the
    first step each would dispatch now, until its input closes."""
    import traceback
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr                     # only replies go into the pipe
    try:
        values, rules = pickle.load(inp)
    except EOFError:
        return
    p = MpcParams()
    for key, value in values.items():
        p.set(key, value, "the dispatch")
    mpc = HeuristicMPC(p.finish())
    pickle.dump("ready", out, pickle.HIGHEST_PROTOCOL)
    out.flush()
    while True:
        try:
            soc, locked, u2, t2, load, pv = pickle.load(inp)
        except (EOFError, pickle.UnpicklingError):  # closed, maybe in the middle of a request
            return
        try:
            st = MpcState(soc, locked, u2, t2)
            scheds = mpc.schedules(st, min(p.horizon, len(load), len(pv)))
            firsts = {}
            for ri in rules:
                for u in sorted({s[0] for s in scheds}):
                    op = mpc_operate(p, load[0], pv[0], u, soc, locked, mpc.rules[ri])
                    firsts[ri, u] = (op.load, op.pv_avail, op.curtail, op.bess, op.u2, op.p1, op.p2, op.m,
                                     mpc_margins(p, op)[1])
            reply = ("ok", (mpc.evaluate(st, load, pv, scheds, rules), firsts))
        except Exception:
            reply = ("error", traceback.format_exc())
        pickle.dump(reply, out, pickle.HIGHEST_PROTOCOL)
        out.flush()


MPC_PROGRESS_S = 60.0       # a progress line at least this often (s), besides one each simulated day
_mpc_progress_last = [0.0]


def mpc_progress(i, n_steps, steps_per_day, when, st, wall0, decide_s):
    """A progress line each simulated day and at least every MPC_PROGRESS_S; decide_s: the time
    the decisions took so far."""
    now = time.time()
    if i == 0:
        _mpc_progress_last[0] = wall0
    day_done = (i + 1) % steps_per_day == 0 or i + 1 == n_steps
    if day_done or now - _mpc_progress_last[0] >= MPC_PROGRESS_S:
        _mpc_progress_last[0] = now
        done = (i + 1) / n_steps
        elapsed = now - wall0
        log(f"  {when[:10] + ' done' if day_done else when} ({done:4.0%})  SOC {st.soc:4.0%}  elapsed "
            f"{elapsed / 60:4.1f} min, about {(elapsed / done - elapsed) / 60:4.1f} min left  "
            f"({1000 * elapsed / (i + 1):.0f} ms a step, {1000 * decide_s / (i + 1):.0f} of them deciding)")


def run_mpc(case, ldf, net, changes, csv_path, soc_init):
    """The MPC over the period in _qds, through PowerFactory's load flows. Returns the logged rows."""
    if MPC_ENGINE == "model":
        return run_mpc_model(csv_path, soc_init)
    t0, t1, step_s = _qds["t0"], _qds["t1"], qds_step_s()
    p = mpc_params(net, soc_init=soc_init)
    fc = MpcForecaster(p)
    problem = mpc_forecast_problem(fc.kind, fmt_time(t0), fmt_time(t1))
    if problem:
        raise Stop(problem)
    n_steps = (t1 - t0) // step_s + 1
    steps_per_day = 24 * 60 // QDS_STEP_MIN
    mpc_log_start(p, fc, t0, t1, n_steps, soc_init, csv_path, "PowerFactory load flows")
    log("Order each step: measure load and PV -> decide over the horizon -> set DG_2, BESS and PV -> load flow")
    st = MpcState(soc_init, soc_init <= p.soc_min)
    set_dg2(changes, net, False)            # DG_2 starts the period stopped
    p2_set = None
    rows, starts, checked, wall0, decide_s = [], 0, set(), time.time(), 0.0
    with MpcWorkers(p, mpc_worker_count(p)) as mpc, open(csv_path, "w", newline="") as f:
        log(f"Decisions: {mpc.where}\n")
        writer = csv.writer(f)
        writer.writerow(QDS_COLUMNS + MPC_COLUMNS)
        for i in range(n_steps):
            t = t0 + i * step_s
            when = fmt_time(t)
            set_study_time(case, t)

            # 1) Measure: PV uncurtailed, BESS idle, DG_2 as it was last step.
            set_pv_factor(changes, net, 1.0)
            set_bess(changes, net, 0.0)
            qds_solve(ldf, t)
            load, other_all, p1, p2 = qds_measure(net, st.u2)
            pv_avail, _ = measure_pv_bess(net)
            # what PV, battery and diesels cover together, as the load the MPC's model would see
            seen = (p1 + p2 + pv_avail) / (1.0 + p.losses)

            # 2) Decide over the horizon; the first move is applied.
            started = time.time()
            loads, pvs = fc.horizon(when, p.horizon, seen, pv_avail)
            d = mpc.decide(st, loads, pvs)
            op = d.op
            if op is None:
                op = mpc_operate(p, seen, pv_avail, d.u2, st.soc, st.locked, d.rule)
                op.binding = mpc_margins(p, op)[1]
            decide_s += time.time() - started
            switched = op.u2 != st.u2
            if switched or (op.u2 and (p2_set is None or abs(op.p2 - p2_set) > 0.5)):
                set_dg2(changes, net, bool(op.u2), op.p2 if op.u2 else None)
                p2_set = op.p2 if op.u2 else None
                switched = True
            if op.u2 > st.u2:
                starts += 1
            if op.u2 != st.u2:
                log(f"{when}  m_k {op.m:5.2f} ({op.binding})  SOC {st.soc:4.0%}  ->  "
                    f"{DG2_NAME} {'ON  at %.0f kW' % op.p2 if op.u2 else 'OFF'}")
            bess_kw = 0.0 if abs(op.bess) < 0.01 else op.bess
            curtail = 0.0 if op.curtail < 0.01 or pv_avail <= 0 else min(op.curtail, pv_avail)
            set_bess(changes, net, bess_kw)
            set_pv_factor(changes, net, 1.0 - curtail / pv_avail if curtail else 1.0)

            # 3) Load flow with everything dispatched (only if something changed).
            if switched or bess_kw or curtail:
                qds_solve(ldf, t, "Load flow after dispatch")
                load, other_all, p1, p2 = qds_measure(net, op.u2)
            pv, p_bess = measure_pv_bess(net)
            other = other_all - pv - p_bess
            done = MpcOp(load, pv_avail, max(pv_avail - pv, 0.0), p_bess, op.u2, p1, p2 if op.u2 else 0.0)
            done.m, done.binding = mpc_margins(p, done)
            signal = seen * (1.0 + p.losses) - pv_avail - mpc_discharge_max(p, st.soc, st.locked)
            st = mpc_advance(p, st, done)               # SOC follows what really flowed

            # First time each control acts: confirm PowerFactory really applied it.
            if op.u2 and "dg2" not in checked:
                checked.add("dg2")
                if abs(p2 - op.p2) > 0.02 * op.p2 + 0.5:
                    warn(f"{DG2_NAME} delivers {p2:.1f} kW but was set to {op.p2:.1f} kW. Check its dispatch input "
                         "mode, active power limits and any characteristic on 'pgini'.")
            mode = "discharge" if bess_kw > 1 else "charge" if bess_kw < -1 else None
            if mode and mode not in checked:
                checked.add(mode)
                if abs(p_bess - bess_kw) > 0.02 * abs(bess_kw) + 0.5:
                    warn(f"BESS asked to {mode} at {abs(bess_kw):.1f} kW but gives {p_bess:.1f} kW. Check its input "
                         "mode and active power limits (charging needs a negative minimum P).")
            if curtail > 1 and "curtail" not in checked:
                checked.add("curtail")
                if abs(pv - (pv_avail - curtail)) > 0.02 * pv_avail + 0.5:
                    warn("PV curtailment through the scaling factor (scale0) did not take effect: expected "
                         f"{pv_avail - curtail:.1f} kW, got {pv:.1f} kW.")

            row = mpc_row(p, when, done, st.soc, other, signal, d)
            rows.append(row)
            mpc_write_row(writer, row)
            mpc_progress(i, n_steps, steps_per_day, when, st, wall0, decide_s)
    if fc.missing:
        warn(f"MPC: {fc.missing} steps had no profile values for the forecast: there it assumed no change")
    summarise(rows, net, starts)
    return rows


def run_mpc_model(csv_path, soc_init):
    """The MPC over the period in _qds on its own model of the island: no PowerFactory."""
    t0, t1, step_s = _qds["t0"], _qds["t1"], qds_step_s()
    p = mpc_params(soc_init=soc_init)
    fc = MpcForecaster(p)
    prof = fc.prof
    problem = mpc_forecast_problem(fc.kind, fmt_time(t0), fmt_time(t1))
    if problem:
        raise Stop(problem)
    if prof is None:
        raise Stop("The MPC on its own model needs the profiles (aggregate_total_load.csv and "
                   "pv_ac_profile_measured.csv next to this file, or MPC_PROFILES)")
    first, last = fmt_time(t0), fmt_time(t1)
    if first not in prof["index"] or last not in prof["index"]:
        raise Stop(f"The profiles run from {prof['times'][0]} to {prof['times'][-1]}: {first} -> {last} is not in them")
    i0, i1 = prof["index"][first], prof["index"][last]
    n_steps = i1 - i0 + 1
    steps_per_day = 24 * 60 // QDS_STEP_MIN
    mpc_log_start(p, fc, t0, t1, n_steps, soc_init, csv_path, "its own model, no load flows")
    st = MpcState(soc_init, soc_init <= p.soc_min)
    rows, starts, wall0, decide_s = [], 0, time.time(), 0.0
    with MpcWorkers(p, mpc_worker_count(p)) as mpc, open(csv_path, "w", newline="") as f:
        log(f"Decisions: {mpc.where}\n")
        writer = csv.writer(f)
        writer.writerow(QDS_COLUMNS + MPC_COLUMNS)
        for i in range(i0, i1 + 1):
            when, load, pv = prof["times"][i], prof["load"][i], prof["pv"][i]
            started = time.time()
            loads, pvs = fc.horizon(when, p.horizon, load, pv)
            d = mpc.decide(st, loads, pvs)
            op = d.op
            if op is None:
                op = mpc_operate(p, load, pv, d.u2, st.soc, st.locked, d.rule)
                op.binding = mpc_margins(p, op)[1]
            decide_s += time.time() - started
            if op.u2 != st.u2:
                starts += op.u2
                log(f"{when}  m_k {op.m:5.2f} ({op.binding})  SOC {st.soc:4.0%}  ->  "
                    f"{DG2_NAME} {'ON  at %.0f kW' % op.p2 if op.u2 else 'OFF'}")
            signal = (1.0 + p.losses) * load - pv - mpc_discharge_max(p, st.soc, st.locked)
            st = mpc_advance(p, st, op)
            row = mpc_row(p, when, op, st.soc, 0.0, signal, d)
            rows.append(row)
            mpc_write_row(writer, row)
            mpc_progress(i - i0, n_steps, steps_per_day, when, st, wall0, decide_s)
    summarise(rows, dict(r1=p.dg1_rating_kw, dg1_min=p.dg1_p_min), starts)
    return rows


# ============================================================================
# MPC: ONE YARDSTICK FOR EVERY DISPATCH LOG, HEAD TO HEAD
# ============================================================================
# Every controller's log is scored on the MPC's own model: fuel (from the data-sheet fit of the
# diesels' output in the log), DG_2 starts, curtailment, battery throughput, and the stability
# margin m_k of every step with the events and constraints S1-S8 above. For each operating point
# (or custom period) the controllers that have a log are compared over the period they share.
# Output: a table in the log, OUT_DIR\dispatch_comparison.csv and dispatch_head_to_head_<point>.png.
def mpc_read_log(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        try:
            out.append({**r, **{c: float(r[c]) for c in QDS_COLUMNS[1:]}})
        except (KeyError, ValueError):
            continue
    return out


def mpc_score(p, rows):
    """Cost and stability of a dispatch log (any controller), on the MPC's model."""
    soc0 = mpc_soc_before(p, rows[0]["bess_soc_pct"] / 100.0, rows[0]["bess_kW"])
    s = dict(steps=len(rows), first=rows[0]["time"], last=rows[-1]["time"], fuel_dg1=0.0, fuel_dg2=0.0,
             starts=0, dg2_h=0.0, curtail_kwh=0.0, pv_avail_kwh=0.0, load_kwh=0.0, bess_in=0.0, bess_out=0.0,
             below_min=0, above_prime=0, m_min=MPC_INF, m_neg=0, m_low=0, binding={}, s5_neg=0)
    prev = 0
    for r in rows:
        u2 = int(r["DG2_on"] > 0.5)
        op = MpcOp(r["load_kW"], r["pv_avail_kW"], max(r["pv_avail_kW"] - r["pv_kW"], 0.0), r["bess_kW"], u2,
                   r["DG1_kW"], r["DG2_kW"] if u2 else 0.0)
        op.m, op.binding = mpc_margins(p, op)
        s["fuel_dg1"] += (p.dg1_fuel_a + p.dg1_fuel_b * max(op.p1, 0.0)) * p.dt_h
        if u2:
            s["fuel_dg2"] += (p.dg2_fuel_a + p.dg2_fuel_b * max(op.p2, 0.0)) * p.dt_h
            s["dg2_h"] += p.dt_h
        s["starts"] += u2 > prev
        prev = u2
        s["curtail_kwh"] += op.curtail * p.dt_h
        s["pv_avail_kwh"] += op.pv_avail * p.dt_h
        s["load_kwh"] += op.load * p.dt_h
        s["bess_out" if op.bess > 0 else "bess_in"] += abs(op.bess) * p.dt_h
        # 2 %: the load flow's own losses put DG_1 a few kW off the level it was set to
        s["below_min"] += op.p1 < 0.98 * p.dg1_p_min
        s["above_prime"] += op.p1 > 1.02 * p.dg1_p_max
        s["m_min"] = min(s["m_min"], op.m)
        if op.m < 0:
            s["m_neg"] += 1
        elif op.m < p.m_star:
            s["m_low"] += 1
        if op.m < p.m_star:
            s["binding"][op.binding] = s["binding"].get(op.binding, 0) + 1
        if p.s5_mode == "report":
            s["s5_neg"] += any(c == "S5" and m < 0 for c, _, _, _, m, _ in mpc_margin_table(p, op))
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


def mpc_score_lines(name, s, p):
    days = s["steps"] * p.dt_h / 24
    top = ", ".join(f"{k} {v}" for k, v in sorted(s["binding"].items(), key=lambda x: -x[1])[:4]) or "-"
    return [
        f"{name}: {s['first']} -> {s['last']} ({days:.1f} days, {s['steps']} steps)",
        f"   fuel {s['fuel_l']:8.0f} L ({DG1_NAME} {s['fuel_dg1']:.0f}, {DG2_NAME} {s['fuel_dg2']:.0f})   "
        f"{DG2_NAME} {s['starts']} starts, {s['dg2_h']:.1f} h on",
        f"   PV curtailed {s['curtail_kwh']:7.0f} of {s['pv_avail_kwh']:.0f} kWh   battery in {s['bess_in']:.0f} / "
        f"out {s['bess_out']:.0f} kWh   SOC {s['soc0']:.0%} -> {s['soc_end']:.0%}",
        f"   cost R {s['cost']:9.0f} (fuel {s['cost_fuel']:.0f}, starts {s['cost_start']:.0f}, curtailment "
        f"{s['cost_curtail']:.0f}, wear {s['cost_wear']:.0f});  with the SOC change valued: R {s['cost_adjusted']:.0f}",
        f"   margin m_k: min {s['m_min']:.2f}; steps below 0: {s['m_neg']}, between 0 and m* = {p.m_star:g}: "
        f"{s['m_low']}   {DG1_NAME} below its minimum: {s['below_min']}, above prime: {s['above_prime']}",
        f"   where m_k < m*: {top}" + (f"   (S5 ROCOF, reported only, < 0 at {s['s5_neg']} steps)"
                                        if s["s5_neg"] else ""),
    ]


def dispatch_log_groups():
    """{period: {controller: log path}} of the logs in DISPATCH_DIR: <tag>_<period>.csv, where the
    period is an operating point or a custom period."""
    groups = {}
    if not os.path.isdir(DISPATCH_DIR):
        return groups
    names = list(dict.fromkeys(list(QDS_RUNNERS) + list(DISPATCH)))
    files = sorted(f for f in os.listdir(DISPATCH_DIR) if f.endswith(".csv"))
    for name in names:
        tag = controller_tag(name) + "_"
        for f in files:
            if f.startswith(tag):
                period, label = f[len(tag):-4], name
                for kind in ("perfect", "yesterday") + tuple(MPC_FORECAST_FILES) if name == "MPC" else ():
                    if period.endswith("_" + kind):         # MPC_<start>_<end>_<forecast>.csv
                        period, label = period[:-len(kind) - 1], f"MPC ({kind})"
                groups.setdefault(period, {})[label] = os.path.join(DISPATCH_DIR, f)
    return groups


def compare_dispatch_logs():
    """The tool 'Compare the dispatch logs head to head'."""
    p = mpc_params()
    groups = dispatch_log_groups()
    if COMPARE_INSTANTS is not None:
        groups = {k: v for k, v in groups.items() if k not in POINTS or k in COMPARE_INSTANTS}
    log("\n" + "=" * 100)
    log("DISPATCH HEAD TO HEAD (every log on the MPC's yardstick: fuel, cost and stability margins)")
    log("=" * 100)
    if not groups:
        warn(f"No dispatch logs in {DISPATCH_DIR} to compare: run the dispatch first")
        return
    table = []
    for period in sorted(groups, key=lambda k: (k not in POINTS, k)):
        logs = {name: mpc_read_log(path) for name, path in groups[period].items()}
        logs = {name: rows for name, rows in logs.items() if rows}
        if not logs:
            continue
        first = max(rows[0]["time"] for rows in logs.values())
        last = min(rows[-1]["time"] for rows in logs.values())
        text = POINTS[period]["text"] if period in POINTS else "custom period"
        log(f"\n--- {period} ({text}): {', '.join(logs)}, compared over {first} -> {last}")
        scores = {}
        for name, rows in logs.items():
            part = [r for r in rows if first <= r["time"] <= last]
            if not part:
                warn(f"{name} has no steps in the shared period of {period}")
                continue
            scores[name] = s = mpc_score(p, part)
            for line in mpc_score_lines(name, s, p):
                log("   " + line)
            table.append(dict(period=period, controller=name, first=s["first"], last=s["last"], steps=s["steps"],
                              fuel_l=round(s["fuel_l"], 1), cost_r=round(s["cost"]),
                              cost_soc_valued_r=round(s["cost_adjusted"]), starts=s["starts"],
                              dg2_h=round(s["dg2_h"], 2), pv_curtailed_kwh=round(s["curtail_kwh"]),
                              bess_out_kwh=round(s["bess_out"]), soc_start=round(s["soc0"], 4),
                              soc_end=round(s["soc_end"], 4), m_min=round(s["m_min"], 3), steps_m_below_0=s["m_neg"],
                              steps_m_below_m_star=s["m_neg"] + s["m_low"], dg1_below_min=s["below_min"]))
        if len(scores) > 1:
            best = min(scores, key=lambda n: (scores[n]["m_neg"], scores[n]["cost_adjusted"]))
            log(f"   Fewest steps outside the stability limits, then cheapest: {best}")
        try:
            plot_dispatch_head_to_head(period, text, scores, p)
        except Exception as e:
            warn(f"The head-to-head figure of {period} could not be made ({type(e).__name__}: {e})")
    if table:
        os.makedirs(OUT_DIR, exist_ok=True)
        out = os.path.join(OUT_DIR, "dispatch_comparison.csv")
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(table[0]))
            w.writeheader()
            w.writerows(table)
        log(f"\nTable saved: {out}")


def plot_dispatch_head_to_head(period, text, scores, p):
    """dispatch_head_to_head_<period>.png: the controllers side by side on four measures."""
    if len(scores) < 2:
        return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    names = list(scores)
    colours = [CONTROLLER_COLOUR[i % len(CONTROLLER_COLOUR)] for i in range(len(names))]
    panels = (("Cost, SOC change valued [kR]", lambda s: s["cost_adjusted"] / 1000.0, "{:.0f}"),
              ("Fuel [L]", lambda s: s["fuel_l"], "{:.0f}"),
              ("PV curtailed [MWh]", lambda s: s["curtail_kwh"] / 1000.0, "{:.1f}"),
              (f"Steps with m_k < 0 (of {next(iter(scores.values()))['steps']})", lambda s: s["m_neg"], "{:.0f}"))
    fig, axes = plt.subplots(1, len(panels), figsize=(13, 3.6), layout="constrained")
    fig.suptitle(f"Dispatch head to head, {period} ({text})", x=0.01, ha="left", fontsize=12, color=INK)
    for ax, (title, get, pattern) in zip(axes, panels):
        values = [get(scores[n]) for n in names]
        bars = ax.bar(names, values, color=colours, width=0.6)
        for bar, v in zip(bars, values):
            ax.annotate(pattern.format(v), (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        ha="center", va="bottom", fontsize=9, color=INK, xytext=(0, 2), textcoords="offset points")
        ax.set_title(title, fontsize=10, color=INK2, loc="left")
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(colors=INK2)
        ax.margins(y=0.15)
    out = os.path.join(OUT_DIR, f"dispatch_head_to_head_{period}.png")
    fig.savefig(out, dpi=130)
    plt.close(fig)
    log(f"   Figure saved: {out}")


# ============================================================================
# MPC: SYSTEM DATA FROM POWERFACTORY (the tool 'Read the system data')
# ============================================================================
# Reads everything the network model holds about the gensets, the battery, the PV plant, the
# load and the network, and writes it out in the form the MPC needs (MPC_DIR):
#   mpc_system_model.json        the data in a structure the MPC loads (mpc_from_json)
#   mpc_system_model_report.txt  the same, readable, with the gaps listed
#   attributes\<class>_<name>.csv  every attribute of every object read
# It changes nothing in the model: it only reads, and (POLL_RUN_LOAD_FLOW) runs one load flow at
# the study time already set, so that the operating point and the network losses are known.
# PowerFactory 2024 keeps most of the genset and battery data in its unit commitment and storage
# fields (Pmin_uc, limRampUp, minUpTime, costColdStart, pOpCostCurve, pStorage -> ElmStorage ...).
# Many of them stay at their defaults unless someone fills them in. The report says, for every
# quantity the MPC needs, whether the model holds a value, whether it is still at its default, and
# what this file's settings assume for it, so the gaps are plain.
# How it finds every attribute: PowerFactory's Python interface cannot list the attributes of an
# object. The installation holds them per class in datascheme.db; the tool asks each object for
# every name listed there for its class (and a few result variables), and keeps the ones that exist.
POLL_RUN_LOAD_FLOW = True
POLL_VECTOR_PREVIEW = 8     # values of a long vector shown in the report and the attribute files
# Result variables read after the load flow (they are not in the data scheme)
POLL_RESULTS = {
    "ElmSym": ["m:P:bus1", "m:Q:bus1", "m:i1:bus1", "m:u1", "m:loading", "c:loading"],
    "ElmGenstat": ["m:P:bus1", "m:Q:bus1", "m:i1:bus1", "m:u1", "c:loading"],
    "ElmPvsys": ["m:P:bus1", "m:Q:bus1", "m:i1:bus1", "m:u1", "c:loading"],
    "ElmLod": ["m:P:bus1", "m:Q:bus1", "m:u1"],
    "ElmTerm": ["m:u", "m:u1", "m:phiu", "m:fehz"],
    "ElmLne": ["m:P:bus1", "m:P:bus2", "m:Q:bus1", "c:loading", "c:Losses"],
    "ElmTr2": ["m:P:bus1", "m:P:bus2", "c:loading", "c:Losses"],
}
# Asked of every object as well: names the data scheme of an older build may not list
POLL_ALWAYS = ["loc_name", "outserv", "typ_id", "sgn", "cosn", "ngnum", "pgini", "qgini", "plini", "qlini", "scale0",
               "h", "rstr", "xd", "xq", "xds", "xdss", "uknom", "dline", "nlnum", "sline", "rline", "xline", "c_pmod",
               "pelm", "pblk", "params", "av_mode", "ip_ctrl", "aCategory", "cCategory", "pStorage", "pOpCostCurve",
               "Pmax_uc", "Pmin_uc", "PmaxInv", "PminInv", "limRampUp", "limRampDown", "minUpTime", "minDownTime",
               "cost_up", "costColdStart", "costShutDown", "capacity", "initEnergy", "minEnergyLim", "maxEnergyLim",
               "selfDischarge", "efficiencyCurveGen", "efficiencyCurveConsum"]
# Object references that are followed and read as well (types, storage, cost curves ...)
POLL_FOLLOW = ("typ_id", "pStorage", "pOpCostCurve", "efficiencyCurveGen", "efficiencyCurveConsum", "c_pmod",
               "pCharYrMW", "pCharYrWS")


def poll_is_object(value):
    return hasattr(value, "GetClassName") and hasattr(value, "loc_name")


def poll_short(obj):
    return f"{obj.loc_name}.{obj.GetClassName()}" if poll_is_object(obj) else ""


_POLL_KILO = {"": 1e-3, "k": 1.0, "M": 1e3, "G": 1e6}


def poll_kilo(value, unit):
    """value in kW / kVA / kvar / kWh, from a value in `unit`; None where the unit is not a power or energy."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    u = str(unit or "").strip().strip("[]").strip()
    for base in ("Wh", "var", "VA", "W"):
        if u.endswith(base) and u[: -len(base)] in _POLL_KILO:
            return value * _POLL_KILO[u[: -len(base)]]
    return None


class PollSchema:
    """The attribute names of each class, from the data scheme of the installation."""

    def __init__(self):
        import sqlite3
        self.names, self.source = {}, None
        try:
            path = os.path.join(app.GetInstallationDirectory(), "datascheme.db")
        except Exception:
            path = ""
        if not os.path.isfile(path):
            warn(f"datascheme.db not found ({path or 'no installation directory'}): only the attributes "
                 "the tool names itself are read")
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


def poll_describe(obj, name):
    for get in (lambda: obj.GetAttributeDescription(name), lambda: app.GetAttributeDescription(obj.GetClassName(), name)):
        try:
            text = get()
            if text:
                return str(text)
        except Exception:
            pass
    return ""


def poll_unit_of(obj, name):
    for get in (lambda: obj.GetAttributeUnit(name), lambda: app.GetAttributeUnit(obj.GetClassName(), name)):
        try:
            text = get()
            if text:
                return str(text).strip()
        except Exception:
            pass
    return ""


def poll_plain(value):
    """A value as JSON can hold it: objects by name, vectors as lists."""
    if poll_is_object(value):
        return full_name(value)
    if isinstance(value, (list, tuple)):
        return [poll_plain(v) for v in value]
    if isinstance(value, float) and value != value:
        return None
    return value


def poll_preview(value):
    """A value for the report and the attribute files: long vectors shortened."""
    if isinstance(value, (list, tuple)):
        flat = [v for row in value for v in (row if isinstance(row, (list, tuple)) else [row])]
        nums = [v for v in flat if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(flat) <= POLL_VECTOR_PREVIEW:
            return "[" + ", ".join(str(poll_plain(v)) for v in flat) + "]"
        head = ", ".join(str(poll_plain(v)) for v in flat[:POLL_VECTOR_PREVIEW])
        span = f"; min {min(nums):g}, max {max(nums):g}" if nums else ""
        return f"[{head}, ...] ({len(flat)} values{span})"
    return str(poll_plain(value))


class PollReader:
    """Reads every attribute of an object, once, and the objects it refers to."""

    def __init__(self, schema):
        self.schema, self.dumps = schema, {}

    def read(self, obj, follow=True):
        key = full_name(obj)
        if key in self.dumps:
            return self.dumps[key]
        cls = obj.GetClassName()
        names = self.schema.of(cls) + POLL_ALWAYS + [n for n in POLL_RESULTS.get(cls, []) if POLL_RUN_LOAD_FLOW]
        rows = {}
        for name in dict.fromkeys(names):
            try:
                if not obj.HasAttribute(name):
                    continue
                value = obj.GetAttribute(name)
            except Exception:
                continue
            rows[name] = dict(value=value, unit=poll_unit_of(obj, name), text=poll_describe(obj, name))
        self.dumps[key] = dict(obj=obj, cls=cls, name=obj.loc_name, rows=rows)
        if follow:
            for ref in POLL_FOLLOW:
                target = rows.get(ref, {}).get("value")
                for t in (target if isinstance(target, (list, tuple)) else [target]):
                    if poll_is_object(t):
                        self.read(t, follow=False)
        return self.dumps[key]

    def entry(self, obj, name):
        """{value, unit, text, kw} of one attribute, or None where the object has no such attribute."""
        if obj is None:
            return None
        d = self.read(obj)
        if name not in d["rows"]:
            return None
        row = d["rows"][name]
        return dict(attribute=f"{d['cls']}.{name}", value=poll_plain(row["value"]), unit=row["unit"], text=row["text"],
                    kw=poll_kilo(row["value"], row["unit"]))

    def write_all(self, folder):
        os.makedirs(folder, exist_ok=True)
        for d in self.dumps.values():
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{d['cls']}_{d['name']}")
            with open(os.path.join(folder, safe + ".csv"), "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["attribute", "value", "unit", "description"])
                for name, row in d["rows"].items():
                    w.writerow([name, poll_preview(row["value"]), row["unit"], row["text"]])
        return len(self.dumps)


def poll_calc_objects(cls):
    try:
        return list(app.GetCalcRelevantObjects(f"*.{cls}", 1) or [])
    except Exception:
        return []


def poll_characteristics(obj):
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


def poll_terminal(obj, side="bus1"):
    cub = attr(obj, side)
    return attr(cub, "cterm") if cub is not None else None


def poll_composite_of(gen):
    """The composite model (ElmComp) that holds a generator in one of its slots."""
    linked = attr(gen, "c_pmod")
    if linked is not None:
        return linked
    for comp in poll_calc_objects("ElmComp"):
        if any(e is not None and full_name(e) == full_name(gen) for e in list(attr(comp, "pelm", []) or [])):
            return comp
    return None


def poll_dsl_parameters(dsl):
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


def poll_plant_models(reader, gen):
    """(composite model, {slot: {model, definition, parameters}}) of a generator."""
    comp = poll_composite_of(gen)
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
                         in_service=not attr(elm, "outserv", 0), parameters=poll_dsl_parameters(elm))
    return comp, out


def poll_classify_genstats():
    """(PV elements, battery element) among the static generators and PV systems."""
    pvs, bess = [], None
    for obj in poll_calc_objects("ElmGenstat") + poll_calc_objects("ElmPvsys"):
        name = obj.loc_name.lower()
        chars = " ".join(c.loc_name.lower() for c in poll_characteristics(obj).values())
        if BESS_NAME and obj.loc_name == BESS_NAME or not BESS_NAME and ("bess" in name or "batt" in name):
            bess = bess or obj
        elif (PV_NAMES and obj.loc_name in PV_NAMES or not PV_NAMES and
              (obj.GetClassName() == "ElmPvsys" or name.startswith("pv") or "pv" in chars or "solar" in chars)):
            pvs.append(obj)
    return pvs, bess


# What the MPC needs from each unit: (key, attribute(s) in PowerFactory, what it is, paper reference).
# The first attribute the object has is used.
POLL_DG_FIELDS = [
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
POLL_STORAGE_FIELDS = [
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
POLL_GENSTAT_FIELDS = [
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


def poll_fields(reader, obj, spec):
    out = {}
    for key, names, text, ref in spec:
        found = next((reader.entry(obj, n) for n in names if reader.entry(obj, n) is not None), None)
        out[key] = dict(found or dict(attribute=None, value=None, unit="", text="not in this model"),
                        meaning=text, paper=ref)
    return out


def poll_is_default(value):
    """PowerFactory's 'no limit' (9999 MW, also shown as 9999000 kW) or an empty value."""
    return value in (None, "", 0, 0.0, [], False) or (isinstance(value, (int, float)) and abs(value) >= 9999)


def poll_number(entry, kw=False):
    """The value of an entry, None where it is missing or still at PowerFactory's default."""
    if not entry or poll_is_default(entry.get("value")):
        return None
    value = entry.get("kw") if kw else entry.get("value")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def poll_num(v):
    """A number for the gaps table: no float noise (648.0000115633011 -> 648)."""
    return f"{v:.6g}" if isinstance(v, float) else poll_preview(v)


def poll_operating(reader, obj):
    """Load flow results of an element, in kW / kvar / pu."""
    out = {}
    for key, name in (("p_kw", "m:P:bus1"), ("q_kvar", "m:Q:bus1"), ("u_pu", "m:u1"), ("loading_pct", "c:loading")):
        e = reader.entry(obj, name)
        if e is not None:
            out[key] = e["kw"] if key in ("p_kw", "q_kvar") and e["kw"] is not None else e["value"]
    return out


def poll_setpoint(reader, obj, name):
    e = reader.entry(obj, name)
    return dict(value=e["value"], unit=e["unit"], kw=e["kw"]) if e else None


def poll_characteristic_info(reader, obj):
    """The time characteristics of an element: which parameter, which characteristic, and its data."""
    out = {}
    for par, cha in poll_characteristics(obj).items():
        d = reader.read(cha)
        info = dict(characteristic=cha.loc_name, cls=cha.GetClassName())
        for name, row in d["rows"].items():
            if any(k in name.lower() for k in ("file", "f_name", "source", "usage", "unit", "scale", "timeformat",
                                               "startyear", "datacol", "timecol", "approx", "recurr", "period")):
                info[name] = poll_plain(row["value"])
        vec = next((row["value"] for name, row in d["rows"].items()
                    if name in ("vector", "values", "y", "dat") and isinstance(row["value"], (list, tuple))), None)
        if vec and not info.get("f_name"):         # a file-driven characteristic keeps an unused internal table
            flat = [v for r in vec for v in (r if isinstance(r, (list, tuple)) else [r])
                    if isinstance(v, (int, float))]
            if flat:
                info["values"] = dict(count=len(flat), min=min(flat), max=max(flat), mean=sum(flat) / len(flat))
        out[par] = info
    return out


def poll_rating(reader, obj):
    """(rated kVA of all parallel units, rated power factor, parallel units)."""
    holder = attr(obj, "typ_id") if obj.GetClassName() == "ElmSym" else obj
    units = attr(obj, "ngnum", 1) or 1
    s = reader.entry(holder, "sgn") if holder is not None else None
    cos = attr(holder, "cosn", 1.0) or 1.0
    kva = s["kw"] * units if s and s["kw"] is not None else None
    return kva, cos, units


def poll_genset(reader, gen):
    typ = attr(gen, "typ_id")
    kva, cos, units = poll_rating(reader, gen)
    h = attr(typ, "h")
    comp, models = poll_plant_models(reader, gen)
    role = "dg1" if gen.loc_name == DG1_NAME else "dg2" if gen.loc_name == DG2_NAME else None
    data = GENSET_DATA.get(role, {}) if role else {}
    settings = {}
    if role == "dg1":
        settings = dict(min_level=DG1_MIN_LEVEL, rating_kw=DG1_RATING_KW)
    elif role == "dg2":
        settings = dict(on_level=DG2_ON_LEVEL, off_level=DG2_OFF_LEVEL, p_kw=DG2_P_KW, min_run_min=DG2_MIN_RUN_MIN,
                        min_stop_min=DG2_MIN_STOP_MIN, rating_kw=DG2_RATING_KW)
    if data:
        settings.update(prime_kw=data.get("prime_kw"), standby_kw=data.get("standby_kw"), j_kgm2=data.get("j_kgm2"),
                        j_note=data.get("j_note"), governor_datasheet=data.get("gov"))
    return dict(
        name=gen.loc_name, cls=gen.GetClassName(), in_service=not attr(gen, "outserv", 0),
        terminal=poll_short(poll_terminal(gen)), type=typ.loc_name if typ is not None else None,
        rating_kva=kva, cos_phi=cos, parallel_units=units, rating_kw=kva * cos if kva else None,
        inertia_h_s=h, kinetic_energy_kws=h * kva if h and kva else None,
        machine=dict(rstr=attr(typ, "rstr"), xd=attr(typ, "xd"), xq=attr(typ, "xq"), xds=attr(typ, "xds"),
                     xdss=attr(typ, "xdss"), tds=attr(typ, "tds"), tdss=attr(typ, "tdss")),
        dispatch=dict(pgini=poll_setpoint(reader, gen, "pgini"), qgini=poll_setpoint(reader, gen, "qgini"),
                      control=attr(gen, "av_mode"), reference_machine=attr(gen, "ip_ctrl")),
        unit_commitment=poll_fields(reader, gen, POLL_DG_FIELDS),
        composite_model=comp.loc_name if comp is not None else None, controllers=models,
        load_flow=poll_operating(reader, gen), ctrl_py=settings, characteristics=poll_characteristic_info(reader, gen),
        fuel=mpc_fuel_curve(role))


def poll_battery(reader, bess):
    kva, cos, units = poll_rating(reader, bess)
    f = poll_fields(reader, bess, POLL_GENSTAT_FIELDS)
    storage = attr(bess, "pStorage")
    for obj in poll_calc_objects("ElmStorage"):          # a storage model that points the other way
        if storage is None and any(full_name(x) == full_name(bess) for x in
                                   [attr(obj, n) for n in ("pGen", "pElm", "pgen", "elm")] if poll_is_object(x)):
            storage = obj
    return dict(
        name=bess.loc_name, cls=bess.GetClassName(), in_service=not attr(bess, "outserv", 0),
        terminal=poll_short(poll_terminal(bess)), rating_kva=kva, cos_phi=cos, parallel_units=units,
        dispatch=dict(pgini=poll_setpoint(reader, bess, "pgini"), qgini=poll_setpoint(reader, bess, "qgini")),
        limits=f, storage_model=dict(name=storage.loc_name, **poll_fields(reader, storage, POLL_STORAGE_FIELDS))
        if storage is not None else None,
        load_flow=poll_operating(reader, bess), characteristics=poll_characteristic_info(reader, bess),
        ctrl_py=dict(p_kw=BESS_P_KW, e_kwh=BESS_E_KWH, soc_init=BESS_SOC_INIT, soc_min=BESS_SOC_MIN,
                     soc_max=BESS_SOC_MAX, soc_resume=BESS_SOC_RESUME, round_trip_efficiency=BESS_EFFICIENCY))


def poll_pv_plant(reader, pv):
    kva, cos, units = poll_rating(reader, pv)
    return dict(
        name=pv.loc_name, cls=pv.GetClassName(), in_service=not attr(pv, "outserv", 0),
        terminal=poll_short(poll_terminal(pv)), rating_kva=kva, cos_phi=cos, parallel_units=units,
        dispatch=dict(pgini=poll_setpoint(reader, pv, "pgini"), scale0=attr(pv, "scale0")),
        limits=poll_fields(reader, pv, POLL_GENSTAT_FIELDS), characteristics=poll_characteristic_info(reader, pv),
        load_flow=poll_operating(reader, pv), ctrl_py=dict(curtail_pv=CURTAIL_PV))


def poll_load_model(reader, lod):
    typ = attr(lod, "typ_id")
    dep = {}
    if typ is not None:
        for k in ("aP", "bP", "cP", "kpu0", "kpu1", "kpu", "aQ", "bQ", "cQ", "kqu0", "kqu1", "kqu", "loddy"):
            v = attr(typ, k)
            if v is not None:
                dep[k] = v
    return dict(name=lod.loc_name, cls=lod.GetClassName(), in_service=not attr(lod, "outserv", 0),
                terminal=poll_short(poll_terminal(lod)), type=typ.loc_name if typ is not None else None,
                plini=poll_setpoint(reader, lod, "plini"), qlini=poll_setpoint(reader, lod, "qlini"),
                scale0=attr(lod, "scale0"), voltage_dependence=dep,
                characteristics=poll_characteristic_info(reader, lod), load_flow=poll_operating(reader, lod))


def poll_network(reader):
    out = dict(terminals=[], lines=[], transformers=[], external_grids=[])
    for t in poll_calc_objects("ElmTerm"):
        reader.read(t)
        out["terminals"].append(dict(name=t.loc_name, u_kv=attr(t, "uknom"), in_service=not attr(t, "outserv", 0),
                                     u_pu=attr(t, "m:u") if POLL_RUN_LOAD_FLOW else None))
    for ln in poll_calc_objects("ElmLne"):
        typ = attr(ln, "typ_id")
        reader.read(ln)
        out["lines"].append(dict(name=ln.loc_name, from_=poll_short(poll_terminal(ln, "bus1")),
                                 to=poll_short(poll_terminal(ln, "bus2")),
                                 length_km=attr(ln, "dline"), parallel=attr(ln, "nlnum"),
                                 type=typ.loc_name if typ is not None else None,
                                 i_rated_ka=attr(typ, "sline"), r1_ohm_km=attr(typ, "rline"),
                                 x1_ohm_km=attr(typ, "xline"),
                                 loading_pct=attr(ln, "c:loading") if POLL_RUN_LOAD_FLOW else None))
    for tr in poll_calc_objects("ElmTr2"):
        reader.read(tr)
        out["transformers"].append(dict(name=tr.loc_name,
                                        loading_pct=attr(tr, "c:loading") if POLL_RUN_LOAD_FLOW else None))
    for x in poll_calc_objects("ElmXnet"):
        out["external_grids"].append(dict(name=x.loc_name, in_service=not attr(x, "outserv", 0)))
    return out


def poll_derived(model):
    """Quantities the tool works out from the data above (not read from PowerFactory)."""
    dgs = [d for d in model["gensets"] if d["kinetic_energy_kws"]]
    out = dict(note="Worked out by this tool from the data above, by the swing equation without load "
                    "damping or governor action: an initial-ROCOF estimate, not a simulation result.",
               commitments=[])
    if not dgs:
        return out
    first = dgs[0]
    combos = [[first]] + ([[first] + dgs[1:]] if len(dgs) > 1 else [])
    losses = []
    b = model.get("battery")
    if b:
        p = (poll_number(b["limits"]["p_max_operational"], kw=True) or b["ctrl_py"].get("p_kw")
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


def poll_gaps(model):
    """For every quantity the MPC needs: where the value comes from."""
    rows = []

    def add(unit, what, entry, settings_text, paper):
        v = entry.get("value") if entry else None
        attribute = entry.get("attribute") if entry else None
        if attribute is None:
            state = "not in the model"
        elif poll_is_default(v):
            state = f"{attribute} = {poll_num(v)} (default / not set)"
        else:
            state = f"{attribute} = {poll_num(v)} {entry.get('unit', '')}".rstrip()
        rows.append(dict(unit=unit, quantity=what, powerfactory=state, ctrl_py=settings_text or "-", paper=paper))

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


def poll_fmt(v, pattern="{:.1f}"):
    return pattern.format(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else ("-" if v is None else str(v))


def poll_report(model):
    log("\n" + "=" * 100)
    log("SYSTEM DATA FOR THE MPC (read from PowerFactory)")
    log("=" * 100)
    m = model["meta"]
    log(f"Project {m['project']}, study case {m['study_case']}, study time {m['study_time']}"
        f"{', load flow solved' if m['load_flow'] == 'ok' else ', load flow: ' + str(m['load_flow'])}")
    for d in model["gensets"]:
        log(f"\n{d['name']} ({d['type']}), {'in service' if d['in_service'] else 'OUT OF SERVICE'}, at {d['terminal']}")
        log(f"   rating {poll_fmt(d['rating_kva'])} kVA x cos {poll_fmt(d['cos_phi'], '{:.2f}')} = "
            f"{poll_fmt(d['rating_kw'])} kW ({d['parallel_units']} unit(s));  H = {poll_fmt(d['inertia_h_s'], '{:.3f}')} s, "
            f"stored energy {poll_fmt(d['kinetic_energy_kws'], '{:.0f}')} kWs")
        if d["load_flow"]:
            log(f"   load flow: P {poll_fmt(d['load_flow'].get('p_kw'))} kW, Q {poll_fmt(d['load_flow'].get('q_kvar'))} kvar")
        for key, e in d["unit_commitment"].items():
            if e["attribute"]:
                log(f"   {e['meaning']:<52} {e['attribute']:<26} {poll_preview(e['value'])} {e['unit']}".rstrip())
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
        log(f"   rating {poll_fmt(b['rating_kva'])} kVA, cos {poll_fmt(b['cos_phi'], '{:.2f}')}")
        for key, e in b["limits"].items():
            if e["attribute"]:
                log(f"   {e['meaning']:<52} {e['attribute']:<26} {poll_preview(e['value'])} {e['unit']}".rstrip())
        if b["storage_model"]:
            log(f"   storage model {b['storage_model']['name']}:")
            for key, e in b["storage_model"].items():
                if isinstance(e, dict) and e.get("attribute"):
                    log(f"      {e['meaning']:<49} {e['attribute']:<26} {poll_preview(e['value'])} {e['unit']}".rstrip())
        else:
            log("   no storage model (ElmStorage) linked: energy, SOC limits and efficiencies are not in the model")
    for pv in model.get("pv", []):
        log(f"\n{pv['name']} (PV, {pv['cls']}), at {pv['terminal']}: rating {poll_fmt(pv['rating_kva'])} kVA")
        for par, c in pv["characteristics"].items():
            log(f"   {par} -> {c['characteristic']} ({c['cls']}) "
                + ", ".join(f"{k} {v}" for k, v in c.items() if k not in ("characteristic", "cls", "values")))
    for lod in model.get("loads", []):
        log(f"\n{lod['name']} (load), plini {poll_preview((lod['plini'] or {}).get('value'))} "
            f"{(lod['plini'] or {}).get('unit', '')}, voltage dependence {lod['voltage_dependence']}")
        for par, c in lod["characteristics"].items():
            log(f"   {par} -> {c['characteristic']} ({c['cls']})")
    net = model["network"]
    log(f"\nNetwork: {len(net['terminals'])} terminals, {len(net['lines'])} lines, "
        f"{len(net['transformers'])} transformers, {len(net['external_grids'])} external grids")
    for x in net["external_grids"]:
        log(f"   external grid {x['name']}: {'IN SERVICE' if x['in_service'] else 'out of service'}"
            + (" (the dispatch and the stability study take it out of service, ISOLATED)" if x["in_service"] else ""))
    for ln in net["lines"]:
        log(f"   {ln['name']}: {ln['from_']} -> {ln['to']}, {poll_fmt(ln['length_km'], '{:.3f}')} km, "
            f"rated {poll_fmt(ln['i_rated_ka'], '{:.3f}')} kA, loading {poll_fmt(ln['loading_pct'])} %")
    if model["losses_kw"] is not None:
        log(f"   network losses at this point: {model['losses_kw']:.2f} kW ({model['losses_share']:.2%} of the load)")
    elif model["balance_kw"] is not None:
        warn(f"generation minus load is {model['balance_kw']:.1f} kW at this point: more than losses can be. A "
             "source the tool does not count (or a sign) is off, so no losses are given")
    der = model["derived"]
    if der["commitments"]:
        log(f"\nDerived (by this tool): {der['note']}")
        for c in der["commitments"]:
            log(f"   running {' + '.join(c['running'])}: H = {poll_fmt(c['h_system_s'], '{:.3f}')} s on "
                f"{poll_fmt(c['rated_kw'], '{:.0f}')} kW, {poll_fmt(c['rocof_hz_s_per_100kw'], '{:.2f}')} Hz/s per 100 kW lost")
            for x in c["loss_of"]:
                log(f"      loss of {x['what']} ({poll_fmt(x['p_kw'], '{:.0f}')} kW): initial ROCOF about "
                    f"{poll_fmt(x['initial_rocof_hz_s'], '{:.1f}')} Hz/s")
    log("\n" + "-" * 100)
    log("WHAT THE MPC NEEDS, AND WHERE IT COMES FROM")
    log("-" * 100)
    log(f"{'unit':<10} {'quantity':<28} {'PowerFactory':<56} {'settings / data sheet':<40} paper")
    for r in model["gaps"]:
        log(f"{r['unit']:<10} {r['quantity']:<28} {r['powerfactory'][:56]:<56} {r['ctrl_py'][:40]:<40} {r['paper']}")
    log("'default / not set': the attribute exists but still holds PowerFactory's default; the MPC takes the value "
        "from the data sheets or the settings of this file instead.")


def run_poll():
    """The tool 'Read the system data from PowerFactory'."""
    first_line = len(_log_lines)
    started = time.time()
    log("\n" + "=" * 100)
    log(f"MPC SYSTEM DATA, {datetime.now():%Y-%m-%d %H:%M}")
    log("=" * 100)
    project, case = app.GetActiveProject(), app.GetActiveStudyCase()
    if project is None or project.loc_name != PROJECT_NAME:
        case = open_project()
        project = app.GetActiveProject()
    if case is None:
        raise Stop("No active study case - activate one in the project first")
    t = attr(case, "iStudyTime")
    when = datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if isinstance(t, (int, float)) else str(t)
    log(f"Project {project.loc_name}, study case {case.loc_name}, study time {when} (local)")
    schema = PollSchema()
    if schema.source:
        log(f"Attribute names from {schema.source}")
    reader = PollReader(schema)
    lf = "not run"
    if POLL_RUN_LOAD_FLOW:
        try:
            lf = "ok" if app.GetFromStudyCase("ComLdf").Execute() == 0 else "did not converge"
        except Exception as e:
            lf = f"failed ({type(e).__name__}: {e})"
        log(f"Load flow at the study time: {lf}")
    gens = poll_calc_objects("ElmSym")
    gens.sort(key=lambda g: (g.loc_name != DG1_NAME, g.loc_name))
    pvs, bess = poll_classify_genstats()
    loads = [o for cls in ("ElmLod", "ElmLodlv", "ElmLodmv") for o in poll_calc_objects(cls)]
    for obj in gens + pvs + loads + ([bess] if bess else []) + poll_calc_objects("ElmStorage") + poll_calc_objects("ElmBattery"):
        reader.read(obj)
    log(f"Found: {len(gens)} gensets ({', '.join(g.loc_name for g in gens)}), "
        f"battery {bess.loc_name if bess else 'none'}, PV {', '.join(p.loc_name for p in pvs) or 'none'}, "
        f"{len(loads)} load(s)")
    model = dict(
        meta=dict(project=project.loc_name, study_case=case.loc_name, study_time=when, study_time_epoch=t,
                  load_flow=lf, read_at=f"{datetime.now():%Y-%m-%d %H:%M}", schema=schema.source,
                  script=os.path.basename(globals().get("__file__", "ctrl.py"))),
        gensets=[poll_genset(reader, g) for g in gens],
        battery=poll_battery(reader, bess) if bess else None,
        pv=[poll_pv_plant(reader, p) for p in pvs],
        loads=[poll_load_model(reader, lod) for lod in loads],
        network=poll_network(reader))
    gen = sum((d["load_flow"].get("p_kw") or 0) for d in model["gensets"])
    gen += sum((x["load_flow"].get("p_kw") or 0) for x in model["pv"] + ([model["battery"]] if bess else []))
    demand = sum((x["load_flow"].get("p_kw") or 0) for x in model["loads"])
    # generation minus load is the network loss only where it is small and positive
    model["balance_kw"] = gen - demand if lf == "ok" else None
    plausible = model["balance_kw"] is not None and 0 <= model["balance_kw"] <= 0.1 * max(demand, 1.0)
    model["losses_kw"] = model["balance_kw"] if plausible else None
    model["losses_share"] = model["balance_kw"] / demand if plausible and demand else None
    model["derived"] = poll_derived(model)
    model["gaps"] = poll_gaps(model)

    os.makedirs(MPC_DIR, exist_ok=True)
    with open(os.path.join(MPC_DIR, "mpc_system_model.json"), "w", encoding="utf-8") as fh:
        json.dump(model, fh, indent=2, default=lambda o: full_name(o) if poll_is_object(o) else str(o))
    n = reader.write_all(os.path.join(MPC_DIR, "attributes"))
    poll_report(model)
    log(f"\nSaved in {MPC_DIR}: mpc_system_model.json, mpc_system_model_report.txt, and every attribute of "
        f"{n} objects in attributes\\   [{time.time() - started:.1f} s]")
    with open(os.path.join(MPC_DIR, "mpc_system_model_report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(_log_lines[first_line:]) + "\n")


# The MPC joins the dispatch controllers (QDS_RUNNERS is filled in further up, before it existed)
QDS_RUNNERS["MPC"] = run_mpc


# ============================================================================
# THE DIALOG: what to run
# ============================================================================
def set_fault_duration(seconds):
    """Give every fault of CASES that lasts FAULT_CLEARING_S this duration instead (the dialog's
    'Fault duration'). A fault given a duration of its own in CASES keeps it."""
    global FAULT_CLEARING_S
    old = FAULT_CLEARING_S
    for n, (cid, text, events) in enumerate(CASES):
        CASES[n] = (cid, text, [("fault", e[1], seconds) + tuple(e[3:]) if e[0] == "fault" and abs(e[2] - old) < 1e-9
                                else e for e in events])
    FAULT_CLEARING_S = seconds


SCRIPT_VERSION = "2026-10-17, MPC built in"


def script_identity():
    """Which file runs, and its version: PowerFactory's Python Script object can hold an embedded
    copy of an older script, or point at a file somewhere else."""
    where = os.path.abspath(__file__) if "__file__" in globals() else "embedded in PowerFactory (no file)"
    return f"{where}, version {SCRIPT_VERSION}"


def ask_what_to_do():
    """A small window to choose what to run. It starts from the settings in this file and
    writes the choice back into them. Returns True to go on, False if it was cancelled.
    Raises RuntimeError if no window can be shown (no tkinter): the settings then decide.
    Three tabs: Study (operating points, dispatch, RMS stability), MPC (how the MPC runs, its
    stability constraints, costs, calibration and tools) and MPC parameters (every value the
    MPC uses, with where it comes from)."""
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError as e:
        raise RuntimeError(f"tkinter is not available ({e})")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        raise RuntimeError(f"no window can be opened ({e})")
    root.title(f"Microgrid study - what to run   ({script_identity()})")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)       # in front of PowerFactory
    except tk.TclError:
        pass
    pad = dict(padx=8, pady=3)
    grey = "#52514e"
    book = ttk.Notebook(root)
    book.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 0))
    outer = ttk.Frame(book, padding=10)
    mpc_tab = ttk.Frame(book, padding=10)
    par_tab = ttk.Frame(book, padding=10)
    book.add(outer, text="  Study  ")
    book.add(mpc_tab, text="  MPC  ")
    book.add(par_tab, text="  MPC parameters  ")
    counters = {}

    def section(title, parent=None):
        parent = outer if parent is None else parent
        r = counters.get(parent, 0)
        counters[parent] = r + 1
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.grid(row=r, column=0, sticky="new", pady=(0, 8))
        return frame

    def checks(parent, items, chosen, r, disabled=()):
        """A row of check boxes; returns {key: BooleanVar}."""
        out = {}
        for c, (key, text) in enumerate(items):
            var = tk.BooleanVar(value=(chosen is None or key in chosen) and key not in disabled)
            box = ttk.Checkbutton(parent, text=text, variable=var)
            box.grid(row=r, column=c + 1, sticky="w", **pad)
            if key in disabled:
                box.state(["disabled"])
            out[key] = var
        return out

    # ---- operating points: both tasks use them
    frame = section("Operating points")
    point_vars = {}
    for r, (pid, p) in enumerate(POINTS.items()):
        var = tk.BooleanVar(value=COMPARE_INSTANTS is None or pid in COMPARE_INSTANTS)
        ttk.Checkbutton(frame, text=f"{pid}  {p['text']}", variable=var).grid(row=r, column=0, sticky="w", **pad)
        ttk.Label(frame, text=f"{p['time']}    load {p['load']:.0f} kW, PV {p['pv_avail']:.0f} kW").grid(
            row=r, column=1, sticky="w", **pad)
        point_vars[pid] = var

    # ---- 1. quasi-dynamic dispatch
    frame = section("1.  Quasi-dynamic dispatch (a series of load flows)")
    run_qds = tk.BooleanVar(value=RUN_DISPATCH)
    ttk.Checkbutton(frame, text="Run the dispatch", variable=run_qds).grid(row=0, column=0, columnspan=4, sticky="w", **pad)
    ttk.Label(frame, text="Controller").grid(row=1, column=0, sticky="w", **pad)
    unavailable = [n for n, f in QDS_RUNNERS.items() if f is None]
    qds_vars = checks(frame, [(n, n + (" (not in this file yet)" if n in unavailable else "")) for n in QDS_RUNNERS],
                      QDS_CONTROLLERS, 1, disabled=unavailable)
    period = tk.StringVar(value=QDS_PERIOD)
    ttk.Radiobutton(frame, text="Up to each chosen operating point, after a warm-up of", variable=period,
                    value="points").grid(row=2, column=0, columnspan=3, sticky="w", **pad)
    warmup = tk.StringVar(value=f"{QDS_WARMUP_DAYS:g}")
    days = ttk.Frame(frame)
    days.grid(row=2, column=3, sticky="w")
    ttk.Entry(days, textvariable=warmup, width=5).pack(side="left")
    ttk.Label(days, text=" days").pack(side="left")
    ttk.Radiobutton(frame, text="Over a period of my own, from", variable=period, value="custom").grid(
        row=3, column=0, columnspan=2, sticky="w", **pad)
    start = tk.StringVar(value=datetime(*QDS_START).strftime("%Y-%m-%d %H:%M"))
    end = tk.StringVar(value=datetime(*QDS_END).strftime("%Y-%m-%d %H:%M"))
    span = ttk.Frame(frame)
    span.grid(row=3, column=2, columnspan=2, sticky="w")
    ttk.Entry(span, textvariable=start, width=17).pack(side="left")
    ttk.Label(span, text="  to  ").pack(side="left")
    ttk.Entry(span, textvariable=end, width=17).pack(side="left")
    ttk.Label(frame, text="Battery SOC at the start").grid(row=4, column=0, sticky="w", **pad)
    soc = tk.StringVar(value=f"{100 * BESS_SOC_INIT:g}")
    socs = ttk.Frame(frame)
    socs.grid(row=4, column=1, sticky="w")
    ttk.Entry(socs, textvariable=soc, width=5).pack(side="left")
    ttk.Label(socs, text=" %").pack(side="left")
    ttk.Label(frame, foreground=grey, text="Both controllers = head to head: the same period, the same SOC at the start. "
                                           "How the MPC runs and its parameters: tabs MPC and MPC parameters.").grid(
        row=5, column=0, columnspan=6, sticky="w", **pad)

    # ---- 2. RMS stability
    frame = section("2.  RMS stability")
    run_rms = tk.BooleanVar(value=RUN_STABILITY)
    ttk.Checkbutton(frame, text="Run the stability study", variable=run_rms).grid(
        row=0, column=0, columnspan=6, sticky="w", **pad)
    ttk.Label(frame, text="What").grid(row=1, column=0, sticky="w", **pad)
    mode = tk.StringVar(value=MODE)
    for c, (value, text) in enumerate((("run", "The test cases"), ("controllers", "Governors and AVRs only"),
                                       ("survey", "Survey of the model only"))):
        ttk.Radiobutton(frame, text=text, variable=mode, value=value).grid(row=1, column=c + 1, sticky="w", **pad)
    ttk.Label(frame, text="Controllers").grid(row=2, column=0, sticky="w", **pad)
    names = list(dict.fromkeys(list(DISPATCH) + list(QDS_RUNNERS)))
    rms_vars = checks(frame, [(n, n) for n in names], COMPARE_CONTROLLERS, 2)
    ttk.Label(frame, text="Test cases").grid(row=3, column=0, sticky="nw", **pad)
    table = ttk.Frame(frame)
    table.grid(row=3, column=1, columnspan=5, sticky="w")
    case_vars = {}
    for c, group in enumerate(g for g in GROUPS if any(k[0].startswith(g) for k in CASES)):
        ttk.Label(table, text=GROUPS[group], font=("TkDefaultFont", 9, "bold")).grid(row=0, column=c, sticky="w", **pad)
        for r, (cid, text, _) in enumerate((k for k in CASES if k[0].startswith(group)), start=1):
            short = text.replace("Temporary ", "").replace(" at the main board", "")
            var = tk.BooleanVar(value=CASES_TO_RUN is None or cid in CASES_TO_RUN)
            ttk.Checkbutton(table, text=f"{cid}  {short}", variable=var).grid(row=r, column=c, sticky="w", **pad)
            case_vars[cid] = var
    r = 4
    picks = ttk.Frame(frame)
    picks.grid(row=r, column=1, columnspan=5, sticky="w")
    for text, value in (("All cases", True), ("No cases", False)):
        ttk.Button(picks, text=text, command=lambda v=value: [x.set(v) for x in case_vars.values()]).pack(
            side="left", padx=(8, 0))
    # the duration of the faults: asked only while a fault case is chosen
    fault_ids = [c[0] for c in CASES if any(e[0] == "fault" for e in c[2])]
    fault_ms = tk.StringVar(value=f"{FAULT_CLEARING_S * 1000:g}")
    ttk.Label(picks, text="      Fault duration").pack(side="left")
    fault_entry = ttk.Entry(picks, textvariable=fault_ms, width=6)
    fault_entry.pack(side="left", padx=(6, 0))
    ttk.Label(picks, text="ms").pack(side="left", padx=(4, 0))

    def faults_chosen(*_):
        fault_entry.state(["!disabled"] if any(case_vars[c].get() for c in fault_ids if c in case_vars)
                          else ["disabled"])

    for c in fault_ids:
        if c in case_vars:
            case_vars[c].trace_add("write", faults_chosen)
    faults_chosen()
    ttk.Label(frame, text="Governors and AVRs").grid(row=r + 1, column=0, sticky="w", **pad)
    controllers = tk.StringVar(value=CONTROLLERS)
    ttk.Combobox(frame, textvariable=controllers, values=("keep", "auto", "own", "library"), state="readonly",
                 width=10).grid(row=r + 1, column=1, sticky="w", **pad)
    self_test = tk.BooleanVar(value=SELF_TEST)
    cct = tk.BooleanVar(value=CCT_SEARCH)
    ttk.Checkbutton(frame, text="Self-test of the governors and AVRs", variable=self_test).grid(
        row=r + 1, column=2, columnspan=2, sticky="w", **pad)
    ttk.Checkbutton(frame, text="Critical clearing time search", variable=cct).grid(
        row=r + 1, column=4, columnspan=2, sticky="w", **pad)
    case_pngs = tk.BooleanVar(value=COMPARE_CASE_PNGS)
    ttk.Checkbutton(frame, text="PNG plots of every single run (besides the summaries)", variable=case_pngs).grid(
        row=r + 2, column=1, columnspan=5, sticky="w", **pad)
    ttk.Label(frame, foreground=grey, wraplength=640, justify="left",
              text="Each controller's state at a point is taken from DISPATCH in this file where it is filled in, "
                   f"else from its dispatch log in {DISPATCH_DIR} (part 1 writes it).").grid(
        row=r + 3, column=0, columnspan=6, sticky="w", **pad)

    # ---- the MPC's parameters: one variable per parameter, shared by both MPC tabs. A value that
    # differs from what the defaults, the settings, the system data and the calibration give (plain)
    # is an override: MPC_PARAMS.
    try:
        plain = mpc_params(overrides={})
    except ValueError as e:
        messagebox.showwarning("Microgrid study", f"The MPC's parameters are not valid: {e}", parent=root)
        plain = MpcParams()
        plain.source = {k: "default" for k in plain.source}
    plain_text = {k: mpc_text(getattr(plain, k)) for k in MPC_DEFAULTS}
    plain_src = dict(plain.source)
    pvars, srcs = {}, {}
    for key in MPC_DEFAULTS:
        start_value = MPC_PARAMS.get(key, getattr(plain, key))
        pvars[key] = tk.StringVar(value=mpc_text(start_value))
        srcs[key] = tk.StringVar()

        def mark(*_, key=key):
            srcs[key].set("changed here (MPC_PARAMS)" if pvars[key].get().strip() != plain_text[key]
                          else plain_src[key])
        pvars[key].trace_add("write", mark)
        mark()
    fixed = {"dt_min": "QDS_STEP_MIN (the dispatch's step)", "soc_init": "tab Study: Battery SOC at the start"}

    def field(parent, r, c, key, label, width=8):
        """Label, entry and unit of one parameter, in columns c to c+2."""
        unit = next(u for _, k, _, u, *_ in MPC_PARAMETERS if k == key)
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", **pad)
        box = ttk.Frame(parent)
        box.grid(row=r, column=c + 1, sticky="w")
        ttk.Entry(box, textvariable=pvars[key], width=width).pack(side="left")
        ttk.Label(box, text=f" {unit}" if unit else "").pack(side="left")

    def choice(parent, r, c, key, label, values, width=10):
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", **pad)
        ttk.Combobox(parent, textvariable=pvars[key], values=values, state="readonly", width=width).grid(
            row=r, column=c + 1, sticky="w", **pad)

    # ---- MPC tab: two columns
    mpc_left, mpc_right = ttk.Frame(mpc_tab), ttk.Frame(mpc_tab)
    mpc_left.grid(row=0, column=0, sticky="n")
    mpc_right.grid(row=0, column=1, sticky="n", padx=(8, 0))
    frame = section("How the MPC dispatches", mpc_left)
    engine = tk.StringVar(value=MPC_ENGINE)
    ttk.Label(frame, text="Runs on").grid(row=0, column=0, sticky="w", **pad)
    ttk.Radiobutton(frame, text="PowerFactory's load flows, as the rule-based one",
                    variable=engine, value="powerfactory").grid(row=0, column=1, columnspan=5, sticky="w", **pad)
    ttk.Radiobutton(frame, text="Its own model: no PowerFactory, about 10 s a day",
                    variable=engine, value="model").grid(row=1, column=1, columnspan=5, sticky="w", **pad)
    ttk.Label(frame, text="Forecast").grid(row=2, column=0, sticky="nw", **pad)
    options = [("perfect", "Perfect: the profiles", "the actual load and PV", True)]
    for kind, (label, *files) in MPC_FORECAST_FILES.items():
        found = mpc_forecast_paths(kind)
        options.append((kind, label, " and ".join(os.path.basename(x) for x in found) if found else
                        "NOT FOUND: " + " and ".join(files) + " next to this file", found is not None))
    options.append(("yesterday", "The same time yesterday", "from the profiles", True))
    wanted = list(MPC_FORECAST_RUNS or [MPC_FORECAST])
    ticked = {o[0]: tk.BooleanVar(value=o[0] in wanted and o[3]) for o in options}
    if not any(v.get() for v in ticked.values()):
        ticked["perfect"].set(True)
    choices = ttk.Frame(frame)
    choices.grid(row=2, column=1, columnspan=5, sticky="w")
    for r, (kind, label, note, usable) in enumerate(options):
        ttk.Checkbutton(choices, text=label, variable=ticked[kind],
                        state="normal" if usable else "disabled").grid(row=r, column=0, sticky="w", padx=(4, 12))
        ttk.Label(choices, text=note, foreground=grey if usable else "#b00020").grid(row=r, column=1, sticky="w")
    ttk.Label(choices, foreground=grey, wraplength=440, justify="left",
              text="Tick several to run them one after the other (an MPC dispatch over a period of my own): each "
                   "goes in its own folder, stability_rms/<forecast>.").grid(
        row=len(options), column=0, columnspan=2, sticky="w", padx=(4, 12))
    field(frame, 3, 0, "horizon", "Horizon", width=6)
    ttk.Label(frame, foreground=grey, text=f"of {QDS_STEP_MIN:g} min").grid(row=3, column=2, sticky="w", **pad)
    prof = mpc_profile_paths()
    ttk.Label(frame, text="Actual profiles").grid(row=4, column=0, sticky="w", **pad)
    ttk.Label(frame, foreground=grey, wraplength=440, justify="left",
              text=("\n".join(prof) if prof else "NOT FOUND: aggregate_total_load.csv and pv_ac_profile_measured.csv "
                    "next to this file (or MPC_PROFILES). Without them the MPC assumes nothing changes.")).grid(
        row=4, column=1, columnspan=5, sticky="w", **pad)
    workers = tk.StringVar(value=str(MPC_WORKERS))
    ttk.Label(frame, text="Decides in").grid(row=5, column=0, sticky="w", **pad)
    ttk.Combobox(frame, textvariable=workers, values=("auto",) + tuple(str(n) for n in range(7)), width=6).grid(
        row=5, column=1, sticky="w", **pad)
    ttk.Label(frame, foreground=grey, text=f"worker processes (auto: {max(1, (os.cpu_count() or 2) - 1)} CPUs here, "
              "at most one per rule; 0: in PowerFactory's Python, 25-40x slower)").grid(
        row=5, column=2, columnspan=4, sticky="w", **pad)

    frame = section("Stability constraints   (hard: in m_k;  report: only reported;  off)", mpc_left)
    for i, (key, text) in enumerate((("s1_mode", "S1 spinning reserve"), ("s2_mode", "S2 ISO 8528-5 load acceptance"),
                                     ("s3_mode", "S3 frequency nadir (UF trip)"),
                                     ("s4_mode", "S4 reverse power, over-frequency"),
                                     ("s5_mode", "S5 ROCOF over the relay window"), ("s6_mode", "S6 inverter share"),
                                     ("s7_mode", "S7 reactive power, voltage dip"),
                                     ("s8_mode", "S8 sets in step through a fault"))):
        choice(frame, i, 0, key, text, MPC_MODES, width=7)
    field(frame, 8, 0, "fault_swing_max", "S8: swing over a fault at most")
    field(frame, 9, 0, "m_star", "Target margin m*")
    field(frame, 10, 0, "w_margin", "Weight w of the margin")
    choice(frame, 11, 0, "pv_surge", "PV surge after a cloud: rho x", ("delivered", "curtailed", "none"))
    choice(frame, 12, 0, "iso_class", "ISO 8528-5 class (voltage dip)", tuple(c for c, *_ in ISO_VOLT), width=5)
    field(frame, 13, 0, "f_over_limit", "Over-frequency peak below")
    field(frame, 14, 0, "rocof_window", "ROCOF relay window")

    frame = section("Costs", mpc_right)
    field(frame, 0, 0, "diesel_price", "Diesel")
    field(frame, 0, 2, "c_start", f"{DG2_NAME} start")
    field(frame, 1, 0, "c_wear", "Battery wear")
    field(frame, 1, 2, "c_curtail", "Curtailed PV")
    field(frame, 2, 0, "soc_ref", "End the horizon at SOC")
    field(frame, 2, 2, "c_terminal", "per kWh short (blank: auto)")

    frame = section("Calibration   (profiles and this study's RMS results)", mpc_right)
    for i, (key, text) in enumerate((("tau_eff", "tau_eff, step down"), ("tau_eff_over", "tau_eff, step up"),
                                     ("rho", "rho, PV drop"), ("lambda_up", "lambda, load step"),
                                     ("lambda_down", "lambda, load loss"), ("kappa", "kappa, inverter share"))):
        field(frame, i // 2, (i % 2) * 2, key, text, width=8)
    ttk.Label(frame, textvariable=srcs["tau_eff"], foreground=grey, wraplength=440, justify="left").grid(
        row=3, column=0, columnspan=4, sticky="w", **pad)
    rms = mpc_rms_results()
    ttk.Label(frame, foreground=grey, wraplength=440, justify="left",
              text=f"RMS results: {rms}" if rms else "No RMS results yet: run the stability study, then calibrate").grid(
        row=4, column=0, columnspan=4, sticky="w", **pad)

    def calibrate_now():
        try:
            lines = mpc_calibrate()
            fresh = mpc_params(overrides={})
        except Exception as e:
            messagebox.showerror("Calibration", f"{type(e).__name__}: {e}", parent=root)
            return
        for key in MPC_CALIBRATED:
            plain_text[key], plain_src[key] = mpc_text(getattr(fresh, key)), fresh.source[key]
            pvars[key].set(plain_text[key])
        messagebox.showinfo("Calibration", "\n".join(line.strip() for line in lines[:8]), parent=root)

    ttk.Button(frame, text="Calibrate now", command=calibrate_now).grid(row=5, column=0, sticky="w", **pad)

    frame = section("Tools   (with Run: before the dispatch; the comparison after it)", mpc_right)
    tool_vars = {}
    for r, (name, text) in enumerate((
            ("MPC_POLL", "Read the system data from PowerFactory (JSON + report)"),
            ("MPC_CALIBRATE", "Calibrate from the profiles and the RMS results"),
            ("MPC_REPORT", "Report the MPC's parameters and check a step"),
            ("MPC_COMPARE_LOGS", "Compare the dispatch logs head to head"))):
        tool_vars[name] = tk.BooleanVar(value=globals()[name])
        ttk.Checkbutton(frame, text=text, variable=tool_vars[name]).grid(row=r, column=0, sticky="w", **pad)
    ttk.Label(frame, foreground=grey, wraplength=440, justify="left",
              text=f"System data and calibration: {MPC_DIR}\nComparison (fuel, cost, margins): {OUT_DIR}").grid(
        row=4, column=0, sticky="w", **pad)
    ttk.Button(frame, text="Reset every MPC parameter",
               command=lambda: [pvars[k].set(plain_text[k]) for k in pvars]).grid(row=5, column=0, sticky="w", **pad)

    # ---- MPC parameters tab: every parameter, scrollable
    ttk.Label(par_tab, foreground=grey, wraplength=900, justify="left",
              text="Every value the MPC uses and where it comes from: the defaults, the settings of this file, the "
                   "system data read from PowerFactory, the calibration. A value changed here holds for this run "
                   "(MPC_PARAMS). Ratings, inertia and X''d are read from the model itself while the MPC runs on "
                   "PowerFactory's load flows, unless changed here.").grid(row=0, column=0, columnspan=2, sticky="w",
                                                                           pady=(0, 6))
    canvas = tk.Canvas(par_tab, width=900, height=470, highlightthickness=0)
    bar = ttk.Scrollbar(par_tab, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=bar.set)
    canvas.grid(row=1, column=0, sticky="nsew")
    bar.grid(row=1, column=1, sticky="ns")

    def wheel(e):
        canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")

    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
    r, group = 0, None
    for g, key, _, unit, meaning, _ in MPC_PARAMETERS:
        if g != group:
            group = g
            ttk.Label(inner, text=g, font=("TkDefaultFont", 9, "bold")).grid(row=r, column=0, columnspan=5,
                                                                            sticky="w", pady=(8, 2))
            r += 1
        ttk.Label(inner, text=key).grid(row=r, column=0, sticky="w", padx=(12, 6), pady=1)
        entry = ttk.Entry(inner, textvariable=pvars[key], width=18)
        entry.grid(row=r, column=1, sticky="w", padx=4, pady=1)
        ttk.Label(inner, text=unit, width=6).grid(row=r, column=2, sticky="w", padx=4)
        ttk.Label(inner, text=meaning, wraplength=300, justify="left").grid(row=r, column=3, sticky="w", padx=4)
        if key in fixed:
            entry.state(["disabled"])
            ttk.Label(inner, text=fixed[key], foreground=grey, wraplength=280, justify="left").grid(
                row=r, column=4, sticky="w", padx=4)
        else:
            ttk.Label(inner, textvariable=srcs[key], foreground=grey, wraplength=280, justify="left").grid(
                row=r, column=4, sticky="w", padx=4)
        r += 1

    # ---- buttons
    answer = {"go": False}

    def chosen(variables):
        return [k for k, v in variables.items() if v.get()]

    def go():
        problems = []
        points = chosen(point_vars)
        tools = {name: var.get() for name, var in tool_vars.items()}
        if not run_qds.get() and not run_rms.get() and not any(tools.values()):
            problems.append("Choose the dispatch, the stability study or an MPC tool.")
        if run_qds.get():
            if not chosen(qds_vars):
                problems.append("Choose a controller for the dispatch.")
            if period.get() == "points" and not points:
                problems.append("Choose an operating point to run the dispatch up to.")
            try:
                if not 0 <= float(warmup.get()) <= 366:
                    raise ValueError
            except ValueError:
                problems.append("The warm-up has to be a number of days (0 to 366).")
            try:
                if period.get() == "custom" and epoch_of(end.get()) < epoch_of(start.get()):
                    problems.append("The period ends before it starts.")
            except ValueError:
                problems.append("Write the dates as YYYY-MM-DD HH:MM.")
            try:
                if not 100 * BESS_SOC_MIN <= float(soc.get()) <= 100 * BESS_SOC_MAX:
                    raise ValueError
            except ValueError:
                problems.append(f"The SOC at the start has to lie between {100 * BESS_SOC_MIN:g} and "
                                f"{100 * BESS_SOC_MAX:g} %.")
            if "MPC" in chosen(qds_vars) and engine.get() == "model" and mpc_profile_paths() is None:
                problems.append("The MPC on its own model needs the profiles (tab MPC, Profiles).")
            if "MPC" in chosen(qds_vars):
                picked = [o[0] for o in options if ticked[o[0]].get() and o[3]]
                if not picked:
                    problems.append("Tick at least one forecast for the MPC (tab MPC).")
                elif len(picked) > 1 and period.get() == "points":
                    problems.append("Several forecasts need a period of my own: with the operating points "
                                    "there is one dispatch log per point. Tick one forecast.")
                for kind in picked:
                    if kind not in MPC_FORECAST_FILES:
                        continue
                    if period.get() == "points":
                        series = mpc_forecast_series(kind)
                        problems.append(f"The {MPC_FORECAST_FILES[kind][0]} forecast covers only "
                                        f"{series['times'][0]} to {series['times'][-1]}, not the operating points: "
                                        "choose 'Over a period of my own' inside it.")
                    else:
                        problem = mpc_forecast_problem(kind, start.get().strip(), end.get().strip())
                        if problem:
                            problems.append(problem)
            if not (workers.get().strip().lower() == "auto" or workers.get().strip().isdigit()):
                problems.append("The MPC's worker processes: auto or a whole number (0: none).")
        if run_rms.get() and mode.get() == "run":
            if not points:
                problems.append("Choose an operating point for the stability study.")
            if not chosen(rms_vars):
                problems.append("Choose a controller for the stability study.")
            if not chosen(case_vars) and not cct.get():
                problems.append("Choose a test case (or the critical clearing time search).")
            if any(c in fault_ids for c in chosen(case_vars)):
                try:
                    if not 10 <= float(fault_ms.get()) <= 1000:
                        raise ValueError
                except ValueError:
                    problems.append("The fault duration has to be a number of milliseconds (10 to 1000).")
        overrides = {}
        for key, var in pvars.items():
            text = var.get().strip()
            if key not in fixed and text != plain_text[key]:
                try:
                    overrides[key] = mpc_parse(key, text)
                except ValueError as e:
                    problems.append(f"MPC parameter {key}: {e}")
        if not any(p.startswith("MPC parameter") for p in problems):
            try:
                mpc_params(overrides=overrides)
            except ValueError as e:
                problems.append(f"MPC parameters: {e}")
        if problems:
            messagebox.showwarning("Microgrid study", "\n".join(problems), parent=root)
            return
        forecasts = [o[0] for o in options if ticked[o[0]].get() and o[3]] or [MPC_FORECAST]
        g = globals()
        g.update(RUN_DISPATCH=run_qds.get(), RUN_STABILITY=run_rms.get(), COMPARE_INSTANTS=points,
                 QDS_CONTROLLERS=chosen(qds_vars), QDS_PERIOD=period.get(), QDS_WARMUP_DAYS=float(warmup.get()),
                 MODE=mode.get(), COMPARE_CONTROLLERS=chosen(rms_vars), CASES_TO_RUN=chosen(case_vars),
                 CONTROLLERS=controllers.get(), SELF_TEST=self_test.get(), CCT_SEARCH=cct.get(),
                 COMPARE_CASE_PNGS=case_pngs.get(), MPC_ENGINE=engine.get(), MPC_FORECAST=forecasts[0], MPC_FORECAST_RUNS=forecasts,
                 MPC_PARAMS=overrides, MPC_WORKERS=workers.get().strip().lower(), **tools)
        if run_rms.get() and any(c in fault_ids for c in chosen(case_vars)):
            set_fault_duration(float(fault_ms.get()) / 1000.0)
        if run_qds.get():
            g["BESS_SOC_INIT"] = float(soc.get()) / 100.0
            if period.get() == "custom":
                g["QDS_START"] = datetime.strptime(start.get().strip(), "%Y-%m-%d %H:%M").timetuple()[:5]
                g["QDS_END"] = datetime.strptime(end.get().strip(), "%Y-%m-%d %H:%M").timetuple()[:5]
        answer["go"] = True
        root.destroy()

    buttons = ttk.Frame(root, padding=(10, 8, 10, 10))
    buttons.grid(row=1, column=0, sticky="e")
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text="Run", command=go).pack(side="right")
    root.bind("<Escape>", lambda e: root.destroy())
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return answer["go"]


# ============================================================================
# MAIN
# ============================================================================
def use_output_folder(label):
    """Send the outputs and the dispatch logs to OUT_BASE/<label>. MPC_DIR (the system data and the
    calibration the MPC reads) stays where it is, shared by every forecast."""
    global OUT_DIR, DISPATCH_DIR
    OUT_DIR = os.path.join(OUT_BASE, label)
    DISPATCH_DIR = os.path.join(OUT_DIR, "dispatch")


def reuse_rule_based(source):
    """Copy the rule-based dispatch logs (CSV, plots, PowerFactory files) of this period from the dispatch
    folder `source` into DISPATCH_DIR, instead of running them again: they do not depend on the forecast.
    True if the log is there."""
    names = [os.path.splitext(os.path.basename(path))[0] for _, name, _, _, path in dispatch_runs()
             if name == "Rule-based"]
    if not names or not all(os.path.isfile(os.path.join(source, n + ".csv")) for n in names):
        return False
    os.makedirs(DISPATCH_DIR, exist_ok=True)
    for f in os.listdir(source):
        if any(f.startswith(n) for n in names):
            shutil.copy2(os.path.join(source, f), os.path.join(DISPATCH_DIR, f))
    log(f"The rule-based log does not depend on the forecast: copied from {source}, not run again")
    return True


def write_forecast_comparison(kinds):
    """OUT_BASE/forecast_comparison.csv: each forecast folder's dispatch_comparison.csv, one after the other."""
    rows = []
    for kind in kinds:
        path = os.path.join(OUT_BASE, kind, "dispatch_comparison.csv")
        if os.path.isfile(path):
            with open(path, newline="", encoding="utf-8") as fh:
                rows += [dict(forecast=kind, **r) for r in csv.DictReader(fh)]
    if rows:
        out = os.path.join(OUT_BASE, "forecast_comparison.csv")
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        log(f"\nThe forecasts side by side: {out}")


def run_studies():
    """The dispatch, the head-to-head of the logs and the stability study, in the folder OUT_DIR now is."""
    if RUN_DISPATCH:
        done = run_dispatch_study()
        if RUN_STABILITY and not done:
            warn("No dispatch run finished: the stability study uses DISPATCH and the logs already in "
                 f"{DISPATCH_DIR}")
    if MPC_COMPARE_LOGS:
        try:
            compare_dispatch_logs()
        except Exception:
            import traceback
            warn("The dispatch head to head broke off with an error:")
            for line in traceback.format_exc().rstrip().split("\n"):
                log("   " + line)
    if RUN_STABILITY:
        run_stability()


def main():
    if USE_DIALOG:
        try:
            if not ask_what_to_do():
                print("Cancelled: nothing was run.", flush=True)
                return
        except RuntimeError as why:
            print(f"!! The dialog cannot be shown ({why}): the settings in the file decide what runs.", flush=True)
    tasks = [(what, on) for what, on in (
        ("the MPC tool 'Read the system data'", MPC_POLL),
        ("the MPC calibration", MPC_CALIBRATE),
        ("the MPC parameter report", MPC_REPORT),
        ("1. the quasi-dynamic dispatch (" + ", ".join(QDS_CONTROLLERS) + ")", RUN_DISPATCH),
        ("the dispatch head to head", MPC_COMPARE_LOGS),
        ("2. the RMS stability study", RUN_STABILITY)) if on]
    if not tasks:
        print("Nothing is chosen to run (RUN_DISPATCH, RUN_STABILITY and the MPC tools are off).", flush=True)
        return
    # PowerFactory is needed for the stability study, the system data and every dispatch run that is
    # not the MPC on its own model; inside PowerFactory it is there anyway
    need_pf = (RUN_STABILITY or MPC_POLL or
               (RUN_DISPATCH and any(not mpc_on_model(name) for name in QDS_CONTROLLERS)))
    if need_pf:
        connect()
        if not INSIDE_PF and SHOW_GUI:
            app.Show()
        app.ClearOutputWindow()
        log("Connected to PowerFactory" + (" (running inside PowerFactory)" if INSIDE_PF else ""))
    else:
        try:
            import powerfactory  # type: ignore  (importable as it is only inside PowerFactory)
            inside = powerfactory.GetApplication() is not None
        except Exception:
            inside = False
        if inside:                  # the log then goes to PowerFactory's output window as usual
            connect()
            app.ClearOutputWindow()
        else:
            log("PowerFactory is not needed for this run")
    log(f"Script: {script_identity()}")
    log("To run: " + "; ".join(what for what, _ in tasks))
    for what, step in (("The MPC tool 'Read the system data'", run_poll if MPC_POLL else None),
                       ("The MPC calibration", run_calibration if MPC_CALIBRATE else None),
                       ("The MPC parameter report", mpc_report if MPC_REPORT else None)):
        if step is None:
            continue
        try:
            step()
        except Stop as msg:
            warn(f"{what} stopped: {msg}")
        except Exception:
            import traceback
            warn(f"{what} broke off with an error:")
            for line in traceback.format_exc().rstrip().split("\n"):
                log("   " + line)
    # an MPC dispatch over a period of your own runs once per forecast, each in its own folder
    labelled = RUN_DISPATCH and "MPC" in QDS_CONTROLLERS and QDS_PERIOD == "custom"
    kinds = list(dict.fromkeys(MPC_FORECAST_RUNS or [MPC_FORECAST])) if labelled else [MPC_FORECAST]
    head, rule_dir, g = len(_log_lines), None, globals()
    controllers = list(QDS_CONTROLLERS)
    for n, kind in enumerate(kinds):
        first = len(_log_lines)
        g["MPC_FORECAST"] = kind
        if labelled:
            use_output_folder(kind)
            log(f"\n{'=' * 100}\nForecast {n + 1} of {len(kinds)}: {kind}   (outputs in {OUT_DIR})\n{'=' * 100}")
            if rule_dir and "Rule-based" in controllers and reuse_rule_based(rule_dir):
                g["QDS_CONTROLLERS"] = [c for c in controllers if c != "Rule-based"]
        try:
            run_studies()
        except Exception:
            import traceback
            warn(f"The run of the {kind} forecast broke off with an error:")
            for line in traceback.format_exc().rstrip().split("\n"):
                log("   " + line)
        finally:
            g["QDS_CONTROLLERS"] = controllers
        if labelled and rule_dir is None:
            rule_dir = DISPATCH_DIR
        log(f"\nThis log is saved as {os.path.join(OUT_DIR, 'stability_rms_log.txt')}")
        save_log(head, first if n else 0)
    if labelled and len(kinds) > 1:
        write_forecast_comparison(kinds)

    if need_pf and not INSIDE_PF and SHOW_GUI:
        input("\nPowerFactory window is open - inspect results, then press Enter here to close it...")


if getattr(sys, "argv", [])[1:2] == [MPC_WORKER_FLAG]:     # a worker process of MpcWorkers
    mpc_worker_serve()
elif os.environ.get("STABILITY_RMS_IMPORT_ONLY") != "1":
    main()
