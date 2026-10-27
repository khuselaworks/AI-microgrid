# Load and solar forecasting

This section forecasts electricity demand and solar irradiance one day ahead, in five-minute steps. Solar irradiance is then converted to PV power for the microgrid study. Load and solar are modelled separately: the underlying records come from different years.

| Folder | Purpose |
|---|---|
| [scripts](scripts/README.md) | Programs and instructions for running a forecast. |
| [data](data/README.md) | Prepared load, measured PV power and exported forecast series. |
| [runs](runs/README.md) | Saved individual forecasting experiments. |
| [benchmark](benchmark/README.md) | Comparisons between forecasting methods and saved models. |
| [comparison](comparison/README.md) | Summary figures and a table comparing saved runs. |
| [feature_eda](feature_eda/README.md) | Exploration of patterns in the input data. |
| [feature_ablation](feature_ablation/README.md) | Load results for different choices of model inputs. |
| [solar_ablation](solar_ablation/README.md) | Solar results for different choices of model inputs. |
| [mpc_inputs](mpc_inputs/README.md) | Forecasts supplied to model predictive control (MPC), which plans operation ahead and updates it as new measurements arrive. |
| [mpc_tube](mpc_tube/README.md) | Saved controller uncertainty tables and their plotting program. |
| [logs](logs/README.md) | Benchmark progress and timing records. |
| [docs](docs/README.md) | Pipeline diagram and supplementary notes. |

For review, the figures and reports can be opened directly. To run an experiment, follow the [scripts guide](scripts/README.md). The raw solar source is not bundled, so the included PV power series should not be used as a replacement for the solar irradiance training input.

[Back to the repository](../README.md)
