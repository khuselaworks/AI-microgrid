# Forecasting

Day-ahead forecasting for the cleaned outputs of the preprocessing
pipeline: **24 hours at 5-minute resolution (288 steps)**, rolling
origin, with a hybrid CNN-LSTM encoder-decoder and a TCN alternative.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py --target load  --source ../aggregate_total_load.csv
python run.py --target solar --source ../Stellies_2023.xlsx
python run.py --target load  --source ../aggregate_total_load.csv --model tcn

# feature selection: which covariates the decoder receives
python run.py --target load --source ../aggregate_total_load.csv \
              --features tod dow holiday lag_1w

# probabilistic output: quantile heads trained with the pinball loss
python run.py --target load --source ../aggregate_total_load.csv \
              --quantiles 0.1 0.5 0.9

# device: auto picks Apple Metal on an M-series Mac
python run.py --target load --source ../aggregate_total_load.csv --device cpu
```

Each run writes a self-contained directory under `runs/<name>/`:
`config.json`, `data_summary.txt`, `report.txt`, `metrics.csv`,
`per_step_mae.csv`, `predictions.csv`, `history.csv`, `model.pt`,
`forecast.png`.

---

## Load and solar are two separate models, on purpose

The load record is **2002-02-01 → 2002-09-30** and the irradiance record
is **2023-02-01 → 2023-09-30**. They cover the same 241-day seasonal
window but are 21 years apart and share no real timestamps, so there is
no honest way to learn a weather→load relationship between them. Pairing
them by day-of-year would also misalign the weekly cycle: 2002-02-01 was
a Friday, 2023-02-01 a Wednesday.

So `--target load` and `--target solar` train independent models. Their
outputs can be combined afterwards for net-load scenarios; what cannot be
done is claim the combination was learned.

---

## Modules

| Module | Role |
|---|---|
| `config.py` | One `ForecastConfig` dataclass; a run is reproducible from it alone |
| `solar_geometry.py` | NOAA solar position + clear-sky envelope, no extra dependency |
| `holidays.py` | South African public holidays — a legal future covariate |
| `normalisation.py` | Training-only min-max scaling and serialisation |
| `datasets.py` | Loading, chronological splits, leakage-safe windowing |
| `baselines.py` | Seasonal naive (daily, weekly), similar-day average, clear-sky persistence (1 h, 24 h) |
| `linear_model.py` | Ridge regression on the decoder's own inputs — does the network earn its parameters? |
| `probabilistic.py` | Quantile scoring (coverage, width, interval score, pinball) and the reference interval |
| `models.py` | `CNNLSTM` and `TCNModel`, sharing one encoder-decoder shape |
| `train.py` | Deterministic loop, early stopping on validation, checkpointing |
| `evaluate.py` | Metrics in real units, daylight masking, skill scores |
| `run.py` | CLI entry point |
| `feature_eda.py` | Feature selection step 1 — ACF, periodogram, day-type profiles, MI |
| `feature_ablation.py` | Feature selection step 2 — train each candidate set, rank on validation |
| `compare_runs.py` | Two finished runs → headline error, error vs lead time, a sample day |
| `benchmark.py` | The final benchmark: feature selection and model comparison over 3 folds × 3 seeds |
| `report_assets.py` | Benchmark results → LaTeX tables, PDF figures and number macros in `../report/` |
| `pv_power.py` | GHI + temperature → PV AC power (Erbs, Hay-Davies, NOCT, PVWatts), quantiles too |
| `export_for_powerfactory.py` | Midnight-issued forecasts stitched into continuous series for DIgSILENT |
| `mpc_forecasts.py` | Forecasts at every 5-minute step of the F3 test block, at three quality tiers (perfect, best, reference), as load and PV-power CSVs for the MPC study |
| `mpc_forecast_plots.py` | Error tables and thesis figures for those forecast tiers |

---

## Running it on an Apple Silicon Mac

`--device auto` (the default) picks Metal when it is available. Two
changes make that worth having:

* **Windows are gathered on the device, not in Python.** The whole series
  is three small tensors; a batch is one indexed gather over precomputed
  offsets. The per-item `Dataset` this replaced spent ~110 ms a batch
  slicing and concatenating NumPy arrays 64 times in Python — which
  dwarfed the forward and backward passes of a 100k-parameter model.
* **The model and its data stay on the accelerator**, with one host sync
  per epoch rather than one per batch.

Measured on an M1 Air, load target, default settings:

| Device | s/epoch | 30 epochs |
|---|---|---|
| `cpu` | 29 | ~15 min |
| `mps` | 7.2 | ~3.5 min |

Losses agree to four decimal places across the two devices. Larger
batches buy almost nothing (7.2 → 6.6 s at `--batch-size 512`) and cost
convergence at a fixed epoch budget, so the default stays 64.

---

## The four rules that make the numbers mean something

**1. Windows never cross a split boundary.** Splits are chronological
(70/15/15). An origin is only valid if its *whole* window — lookback,
gap and horizon — lies inside one split. A window whose history sat in
train and whose target sat in validation would hand the model its own
answer.

**2. The scaler is fitted on the training rows only.** It uses
the bundled `normalisation.MinMaxScaler` from the preprocessing pipeline, so the
scaling a model was trained under is written to disk and reproducible
outside this codebase.

**3. No window containing a missing reading is used.** Quietly
interpolating would mean training on invented data and scoring against
invented data. Dropped windows are counted and reported.

**4. The test split is touched exactly once.** The best epoch is chosen
on validation. Selecting on test — even just picking an epoch — turns
the test score into a training score.

The codebase also **screens physically impossible values before scaling**.
This is not paranoia: the raw Stellenbosch export reaches 2,977 W/m², about
twice what can arrive at the surface. Left in, it becomes the scaler's
maximum and squashes every real reading into the bottom 40% of the range.
713 readings are screened.

---

## Baselines are not a formality

Both series are dominated by cycles a trivial rule reproduces, so a
network can post an impressive MAE while adding nothing. The only
meaningful number is the skill score against a *strong* baseline:

- **Seasonal naive (1 day)** — tomorrow repeats today.
- **Seasonal naive (1 week)** — next Tuesday repeats last Tuesday. On
  this load record this is the harder baseline: weekly autocorrelation
  is 0.883 against 0.786 daily, because it gets weekday/weekend right.
- **Clear-sky persistence** — the standard solar yardstick. Carries the
  recent clear-sky index forward onto the deterministic clear-sky curve,
  so it already knows the diurnal shape. Beating plain persistence on
  solar proves nothing; beating this does.

---

## Solar is scored on daylight only

Night-time GHI is exactly zero and every model predicts it perfectly.
Scoring it alongside daylight roughly halves every error and inflates
every skill score by averaging in free correct answers. The daylight
mask comes from the deterministic clear-sky curve — not from the
observations — so it does not depend on what actually happened. About
45% of steps are scored.

MAPE also carries a floor (`mape_floor`), because percentage error is
unbounded as the denominator approaches zero, and the count of excluded
cells is reported.

---

## Solar geometry: the timezone has to be stated, not guessed

`solar_geometry.py` computes solar position from date and time alone.
This is deterministic astronomy, so it is a **legal future covariate at
any lead time** — unlike the weather — and it carries almost all of the
structure in an irradiance series, leaving the model to learn only the
cloud attenuation.

Inferring the UTC offset from longitude is wrong for this site:
`round(18.86/15) = 1`, but South African Standard Time is UTC+2, pinned
to the 30°E meridian. That error put modelled solar noon at 11:45 when
the record's own daily peak is at 12:50. With `SITE_TZ_OFFSET = 2.0` the
modelled noon lands within 5 minutes of the observed one, and the
observed/clear-sky ratio drops from a p95 of 1.90 to 1.17 — physical,
since cloud enhancement can exceed 1 but not by much.

---

## What the models are

Both share an **encoder-decoder** shape, which is what makes the
comparison fair:

- **Encoder** reads the recent history (default 48 h). `CNNLSTM` uses
  two conv blocks — each halving the sequence, so 576 raw steps reach
  the LSTM as 144 — then an LSTM. `TCNModel` uses dilated *causal*
  convolutions instead, left-padded so no step can see its own future.
- **Decoder** maps the encoded state plus that step's **known future
  covariates** onto every horizon step. One small MLP is shared across
  all 288 steps, so the parameter count does not scale with the horizon.

Future covariates are things genuinely known at forecast time: cyclical
time-of-day / day-of-week / day-of-year, a weekend flag, and — for solar
— the clear-sky curve and a daylight flag. Feeding them to the *decoder*
rather than the encoder is the point: the encoder only sees the past, so
that is where they would be wasted.

Output is **direct multi-output**, not autoregressive rollout: a 288-step
rollout compounds its own error and is far slower to train.

`--residual {none,daily,weekly}` switches the model to learning the
*correction* to a seasonal rule instead of the level — the other sense
of "hybrid". The reference value comes from at least one full period
ago, so it is known at forecast time. See the results note below: it
helps early in training and hurts once the model is properly trained.

---

## The benchmark, and the report it feeds

```bash
python benchmark.py --target load  --source ../aggregate_total_load.csv
python benchmark.py --target solar --source ../stellies_mod.csv
python report_assets.py --bench benchmark --out ../report     # tables, figures, macros
cd ../report && latexmk -pdf main.tex
```

`benchmark.py` is resumable (it appends one line per finished unit to
`benchmark/<target>/results.jsonl` and skips what is there) and takes
about 1.5 h per target on an M1 Air. It runs two stages:

1. **Selection.** Each candidate decoder feature set, CNN-LSTM, seed 0,
   on all three folds. The winner has the lowest *mean validation* MAE;
   test is recorded but never consulted.
2. **Comparison.** With the winning features: CNN-LSTM (point), CNN-LSTM
   with seven quantile heads, and a TCN, each on 3 folds × 3 seeds; plus
   every baseline, the linear model and the reference interval once per
   fold.

**Rolling-origin folds** (`cv_folds`, `cv_fold` in `ForecastConfig`)
slide the validation and test blocks back one test-length at a time with
an expanding training window. The last fold *is* the standard 70/15/15
split, so its numbers continue the single-split history. Each fold's
validation block is the previous fold's test block, so only the last
fold's test block (25 Aug – 30 Sep) is untouched by every selection
decision — the report says so.

**The TCN gets a receptive field that covers its input.** Seven blocks of
kernel 5 with dilations 1…64 see 1,017 steps. The two-block TCN this
replaced saw 25 steps (two hours) of a 48-hour history, which measured
the receptive field, not the architecture. `ForecastConfig` now warns
whenever a TCN cannot see its whole lookback, and `run.py --model tcn`
defaults to seven blocks.

**Probabilistic output.** `--quantiles 0.05 0.1 0.25 0.5 0.75 0.9 0.95`
trains the pinball loss; `run.py` then writes one `predictions.csv`
column per level and reports coverage and interval score, and
`export_for_powerfactory.py` and `pv_power.py` carry the levels through
to the DIgSILENT files (`load_kw_q0.1`, `p_ac_kw_q0.9`, …).

## Forecast inputs for the MPC study

```bash
python mpc_forecasts.py --load-source ../aggregate_total_load.csv \
                        --solar-source ../stellies_mod.csv --out mpc_inputs
python mpc_forecast_plots.py --study mpc_inputs
```

The microgrid controller re-solves every 5 minutes over a 24-hour
horizon, so it needs a fresh forecast at every step. `mpc_forecasts.py`
applies the saved F3 benchmark checkpoints at stride 1 (no retraining)
and checks the result against the stored stride-3 test forecasts. It
writes three tiers so the controller's sensitivity to forecast error can
be measured: `perfect` (the measured future), `best` (CNN-LSTM load,
CNN-LSTM quantile median for irradiance) and `reference` (similar-day
average, clear-sky persistence). Irradiance becomes plant AC power
through `pv_power.py`. The MPC itself lives outside this repository;
`mpc_inputs/README.md` documents the file layout it reads.

## Reproducing a run

`config.json` in each run directory is the complete specification.
`model.pt` carries the weights, the input dimensions and the fitted
scaler, so a checkpoint can be applied to new data without re-deriving
anything.

## Repository layout and local outputs

Python modules and CLI entry points stay at the repository root so the
commands above continue to work. `docs/` contains the pipeline diagram
and its LaTeX source. `normalisation.py` bundles the scaler from the
companion preprocessing project, preserving its checkpoint schema;
a sibling preprocessing checkout is not required.

Input datasets are not included. Put private inputs in `data/` (see
[data/README.md](data/README.md)) and pass `--source data/<filename>`.
The `../` paths in the examples refer to datasets from the original
preprocessing workspace; replace them with your own paths.

Generated `runs/`, `benchmark/`, comparison and feature-analysis folders
are ignored by Git. Existing results are retained locally, and historical
console logs live in `logs/`. New commands may create outputs in the
working directory or at an explicit `--out` path. Keep new private inputs
under `data/` and outputs in the ignored directories.

## Validation

The original `smoke_test_forecasting.py` is not included in this workspace.
After installing dependencies, check the CLI imports with:

```bash
python run.py --help
python benchmark.py --help
python feature_eda.py --help
```

Training and numerical validation require the input datasets. Dependency
versions are currently unpinned; this repository does not include a lockfile.
