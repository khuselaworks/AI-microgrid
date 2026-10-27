# PowerFactory project and programs

| File | Purpose |
|---|---|
| `Iso_microgrid_DG_PV_BESS.pfd` | Microgrid project to import into PowerFactory. |
| `main.py` | Main dispatch and stability study. |
| `mpc_model.py` | Standalone controller model and its parameters. |
| `mpc_run.py` | Run or score the standalone controller. |
| `mpc_model_poll.py` | Read model information from PowerFactory. |
| `mpc_calibrate.py` | Fit controller parameters from simulation results. |
| `mpc_calibration.json` | Saved calibration settings. |

## Using the project

1. Import the `.pfd` project into a licensed DIgSILENT PowerFactory installation.
2. Check `PF_PYTHON_PATH` and `PROJECT_NAME` near the beginning of `main.py`. The saved path targets PowerFactory 2024 SP5A with Python 3.12 on Windows; match it to your installation.
3. Review the study settings and output location in `main.py`, then run it using the Python environment connected to PowerFactory. Its opening comments explain the available study modes.
4. Use the resulting tables and figures to review dispatch and stability outcomes.

The input profiles are stored in [data](../data/README.md). Several scripts still expect profiles beside the script: set their `LOAD_CSV` and `PV_CSV` paths to the files in `../data`. For `main.py`, set `MPC_PROFILES` to the full load and PV profile paths and check the forecast file paths for the selected study.

## Standalone model

From this folder, `python mpc_model.py` prints the model parameters and available checks. `python mpc_run.py --help` describes standalone dispatch options. Configure the input paths above before running a dispatch simulation, and choose a new output folder.

Some helpers refer to an earlier `ctrl.py`, which is not included under that name. They may fall back to saved settings or lack controller integration. The tube plotting script also needs this scripts folder on its Python import path. These setup issues remain to be resolved before claiming full reproduction. Existing calibration files do not include the complete simulation evidence needed to refit them.

[Back to PowerFactory](../README.md)
