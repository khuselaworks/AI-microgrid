# Microgrid forecasting and control — final year project

This repository contains the data preparation results, electricity demand and solar forecasts, and microgrid simulation work for my final year project. The microgrid combines solar photovoltaic (PV) generation, battery storage and diesel generators.

The project follows three stages: prepare the data, forecast demand and solar generation, then use those forecasts to study how the microgrid is operated. The accompanying technical report will be added shortly; it will explain the methods and discuss the results in detail.

## Where to start

| Folder | What it contains |
|---|---|
| [data_preprocessing](data_preprocessing/README.md) | Reports, tables and figures showing how the load and solar data were prepared. |
| [forecasting](forecasting/README.md) | Forecasting programs, saved experiments, model comparisons and forecasts prepared for the controller. |
| [powerfactory](powerfactory/README.md) | The PowerFactory project, control and simulation programs, input data and saved operating-point tables. |

To review the work, start with the preparation figures, then the [forecast comparisons](forecasting/comparison/README.md) and [controller forecast inputs](forecasting/mpc_inputs/README.md). Each content folder has a README explaining its files. CSV tables, text reports and PNG/PDF figures can be viewed without running the programs.

## Running the work

Follow the [forecasting usage guide](forecasting/scripts/README.md) for Python setup and a load-forecast example. The [PowerFactory guide](powerfactory/scripts/README.md) describes the separate simulation setup, which requires a licensed DIgSILENT PowerFactory installation.

The saved outputs are included for review. Full reproduction also needs the original solar input and preprocessing programs, which are not included here. Historical configuration files retain the paths used when the experiments were run; those paths may need changing for a new run. Use a new output folder when experimenting so that the submitted results remain available.

## Naming and organisation

Folder and new file names use lowercase words separated by underscores. `README.md` is the guide in each folder. Units belong in descriptive data filenames where helpful, such as `ghi_w_m2` and `temperature_c`; existing data columns retain their original units and names.

The former folders `data-preprocessing-main`, `load-pv_forecasting-main` and `powerfactory-main` are now `data_preprocessing`, `forecasting` and `powerfactory`. Both `program files` folders are now `scripts`. Solar preparation filenames beginning with `GHI (W_m^2)` or `Temp (℃)` now begin with `ghi_w_m2` or `temperature_c`.

Saved experiment names, model filenames and the PowerFactory project filename are retained to keep their connection to the recorded results. Some controller files and data appear in both forecasting and PowerFactory; use the PowerFactory section as the starting point for simulation work.
