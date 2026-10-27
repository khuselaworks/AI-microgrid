# Forecasting programs

`run.py` trains and evaluates one load or solar forecasting model. The other programs compare models, explore the data and prepare forecasts for the controller.

## Run a load forecast

From the repository root, create a Python environment and install the listed packages:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r forecasting/scripts/requirements.txt
cd forecasting
python scripts/run.py --target load --source data/aggregate_total_load.csv --run-name submission_check --out-dir local_runs
```

On Windows, create the environment with `python -m venv .venv` and activate it with `.venv\Scripts\Activate.ps1` in PowerShell. The remaining commands are the same.

Run forecasting commands from the `forecasting` folder so relative paths resolve consistently. This example trains a new model and writes results under `local_runs/submission_check`. It may take some time. Use a fresh run name for another attempt. `python scripts/run.py --help` lists the options.

For solar forecasting, use `--target solar --source /path/to/your/solar_input.csv` with the original irradiance input. That input is not included. The measured PV power CSV is a different quantity and is not a substitute.

## Main programs

| Program | Purpose |
|---|---|
| `run.py` | Train and evaluate an individual forecast. |
| `benchmark.py` | Compare models and baseline forecasting rules. |
| `compare_runs.py` | Compare saved runs. |
| `feature_eda.py`, `feature_ablation.py` | Explore data patterns and compare model inputs. |
| `pv_power.py` | Convert irradiance and temperature into PV power. |
| `mpc_forecasts.py` | Prepare forecast sets for the controller. |
| `mpc_forecast_plots.py` | Produce forecast error tables and figures. |
| `export_for_powerfactory.py` | Export continuous series for PowerFactory. |
| `report_assets.py` | Generate tables and figures for a report. |

The remaining forecasting modules support data loading, model training and evaluation. Saved results live in [runs](../runs/README.md) and [benchmark](../benchmark/README.md). Earlier detailed explanations are preserved in [forecasting notes](../docs/forecasting_notes.md).

This folder also retains copies of the microgrid project and controller scripts. Use the [PowerFactory guide](../../powerfactory/scripts/README.md) for that part of the work.

[Back to forecasting](../README.md)
