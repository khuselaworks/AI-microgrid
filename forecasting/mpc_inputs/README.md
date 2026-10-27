# MPC forecast inputs

Forecasts of load and PV power for the MPC study, at three levels of
forecast quality. A new 24-hour forecast is issued at **every 5-minute
step** from 2002-08-28 00:00 to 2002-09-29 23:55: 9,504 forecasts of
288 steps each.

To inspect these results, open the [figures](figures/README.md) or [error tables](metrics/README.md). The [CSV guide](csv/README.md) and [PowerFactory export guide](powerfactory/README.md) explain the two output layouts.

Regenerate from the `forecasting` folder after installing the forecasting dependencies and supplying the original solar input (not included):

```bash
python scripts/mpc_forecasts.py --load-source data/aggregate_total_load.csv \
                        --solar-source /path/to/stellies_mod.csv --out mpc_inputs
python scripts/mpc_forecast_plots.py --study mpc_inputs
```

## The three tiers

| Tier | Load | PV power | Load MAE | PV MAE, all hours (daylight) | Net-load MAE |
|---|---|---|---|---|---|
| `perfect` | measured | measured | 0 | 0 | 0 |
| `best` | CNN-LSTM | CNN-LSTM quantile model, median | 27.2 kW | 56.2 kW (102.2) | 74.0 kW |
| `reference` | similar-day average (4 wk) | clear-sky persistence (24 h) | 31.8 kW (+17 %) | 66.9 kW (+19 %) (121.6) | 85.1 kW (+15 %) |

- **`perfect`** is the MPC lower bound. Whatever operating cost remains is
  the controller's own; the cost of forecast error is the difference to
  this tier.
- **`best`** applies the saved F3 benchmark checkpoints unchanged. At
  every origin shared with the benchmark (one every 15 min) the forecasts
  match the stored test forecasts exactly; see `forecast_meta.json`.
- **`reference`** uses the standard rules the networks were benchmarked
  against. They raise the error of load and PV by a similar amount.

The `best` forecast tier was chosen using the F3 test errors, as recorded in `scripts/mpc_forecasts.py`. These results therefore describe the selected forecasts on this study period; they are not an independent test of that selection.
The irradiance forecasts are converted to the AC output of the
726 kWp / 600 kW plant through the PVWatts chain (`pv_power.py`), using
the **measured** ambient temperature, which is not forecast. The
measured series reproduce the sizing chapter's load and PV profiles
(the original sizing files, not included here) to within 5 × 10⁻⁵ kW.

## Files

### `csv/`: inputs for the controller

| File | Layout |
|---|---|
| `{tier}_load_kw.csv`, `{tier}_pv_kw.csv` | One row per forecast origin. `origin` is the issue time; `h001` is the 5-minute interval **starting at** the origin, and `h288` is the interval starting 23 h 55 min later. kW, 2 decimals. |
| `measured_series.csv` | The whole 2002-02-01 to 2002-09-30 record: load, GHI, ambient temperature and PV power. This is what the plant sees. |
| `day_ahead_stitched.csv` | The forecasts issued at 00:00, concatenated into one continuous series per tier (a once-daily day-ahead schedule). |

Timestamps are South African Standard Time (UTC+2), labelling the start
of each 5-minute interval. Each value is the mean over that interval.

A forecast issued at `t` uses observations up to the interval ending at
`t` and nothing later. So at step `t` a controller may read row `t` of the
forecast file, together with the measured values strictly before `t`.

### `powerfactory/`: single series for a DIgSILENT time characteristic

`{tier}_load.csv` and `{tier}_pv.csv` hold one value per timestamp, from
2002-08-28 00:00 to 2002-09-29 23:55 (9,504 rows, no gaps). Each is the
`day_ahead_stitched.csv` series: each day's values come from the forecast
issued at 00:00 that day. Columns: `timestamp`, `time_h` (hours from the
first row), `p_kw`, `p_mw`, and for PV `factor` = `p_kw` / 600 kW (the AC
rating). `perfect_*` is the measured series on the same timestamps.

### `metrics/`: forecast error

| File | Content |
|---|---|
| `forecast_error_summary.csv` | MAE, RMSE, bias (forecast − measured), daylight MAE and first-hour MAE per tier and signal |
| `forecast_mae_by_lead.csv` | MAE at each of the 288 lead steps |
| `mae_by_hour_of_day.csv` | MAE by clock hour of the target step |
| `daily_mae.csv` | MAE of all forecasts issued on each day |
| `sample_days.csv` | The days shown in `fig_sample_days`, chosen by daily clear-sky index |

### `figures/`: PDF (thesis) and PNG (slides)

| Figure | Shows |
|---|---|
| `fig_sample_days` | The clearest, median and cloudiest day: forecasts issued at 00:00 against the measured values |
| `fig_week_day_ahead` | The first week of stitched day-ahead forecasts |
| `fig_mae_by_lead` | Error against lead time |
| `fig_mae_by_hour` | Error against time of day: when the controller is most exposed |
| `fig_daily_mae` | Error day by day across the test block |
| `fig_error_distribution` | Distribution and bias of the errors |

### Other

- `forecasts.npz`: every matrix above as float32, plus GHI and net load,
  keyed `{tier}__{signal}`.
- `forecast_meta.json`: models, checkpoints, sources and the consistency
  checks against the benchmark.

## Points to carry into the results

- The load forecasts are biased in opposite directions: `best` is
  −5.3 kW (under-forecast) and `reference` is +11.1 kW (over-forecast). A
  controller fed the reference forecasts will tend to keep more
  dispatchable capacity on than it needs, so part of its extra cost comes
  from bias rather than random error.
- PV error dominates net-load error and peaks at midday (about
  150–200 kW MAE between 10:00 and 14:00). The battery has to absorb
  most of the mismatch at that time.
- The error grows only mildly with lead time beyond the first few
  hours, because both forecasters are day-ahead models without a nowcast
  component. Re-solving every 5 minutes therefore corrects the state of
  charge, but it does not sharpen the near-term forecast much.

[Back to forecasting](../README.md)
