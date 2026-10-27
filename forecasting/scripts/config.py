"""
One configuration object for the whole forecasting codebase.

Everything that decides what a run means lives here, so a result can be
reproduced from a single serialised config rather than from a memory of
which flags were passed.

The defaults describe the task actually chosen for this project:
day-ahead, 24 hours at 5-minute resolution (288 steps), rolling origin,
with load and solar handled by two separate models because the two
records are 21 years apart and share no real timestamps.
"""
from __future__ import annotations

import json
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Tuple

# Steps per day at 5-minute resolution. The horizon for a day-ahead
# forecast, and the period of the strongest seasonality in both series.
STEPS_PER_DAY = 288
STEPS_PER_WEEK = STEPS_PER_DAY * 7

# Every covariate name `future_features` accepts. See ForecastConfig.
FEATURE_NAMES = ("tod", "dow", "weekend", "doy", "holiday", "lag_1d", "lag_1w")

# Stellenbosch, where the irradiance record was measured. Used only to
# compute solar geometry, which is deterministic and therefore a legal
# future covariate - it is known for any future timestamp.
SITE_LAT = -33.9321
SITE_LON = 18.8602
# South African Standard Time is UTC+2, pinned to the 30 deg E meridian,
# while Stellenbosch sits at 18.9 deg E. Inferring the offset from
# longitude (round(lon/15) = 1) puts modelled solar noon at 11:45 when
# the record's own daily peak is at 12:50 - an hour of error that would
# corrupt every solar-geometry feature. It has to be stated, not guessed.
SITE_TZ_OFFSET = 2.0


@dataclass
class ForecastConfig:
    # -- what is being forecast ----------------------------------------
    target: str = "load"                 # "load" | "solar"
    source: str = ""                     # path to the input file
    column: Optional[str] = None         # target column; None = first/auto
    resample: str = "5min"               # both series are put on this grid
    # Physical screening applied before anything else. Not optional
    # paranoia: the raw Stellenbosch export reaches 2,977 W/m^2, which is
    # roughly twice the solar constant at the surface and cannot happen.
    # Left in, it becomes the scaler's maximum and squashes every real
    # reading into the bottom 40% of the range. Screened readings become
    # NaN, and any window containing one is dropped.
    valid_range: Optional[Tuple[float, float]] = None
    screen_defaults = {"solar": (0.0, 1400.0), "load": None}

    # -- the task ------------------------------------------------------
    horizon: int = STEPS_PER_DAY         # steps to predict (288 = 24 h)
    lookback: int = STEPS_PER_DAY * 2    # steps of history fed to the model
    gap: int = 0                         # gate closure: steps skipped after the origin
    stride: int = 3                      # origins are every `stride` steps

    # -- splits (chronological, never random) --------------------------
    train_frac: float = 0.70
    val_frac: float = 0.15               # test gets the remainder
    # Rolling-origin evaluation. A single chronological split puts val in
    # winter and test in spring, so one split cannot say whether a result
    # holds across seasons. With cv_folds = K the val/test pair is slid
    # back by one test-block per fold, the training window expanding behind
    # it: fold K-1 is exactly the train_frac/val_frac split above, fold
    # K-2 ends one test-block earlier, and so on. Data after a fold's test
    # block is never read by that fold.
    cv_folds: int = 1
    cv_fold: int = 0                     # 0 .. cv_folds-1; the last is the standard split

    # -- model ---------------------------------------------------------
    model: str = "cnn_lstm"              # "cnn_lstm" | "tcn"
    # Learn the CORRECTION to a seasonal baseline instead of the level
    # itself. This is the other sense in which the model is hybrid: a
    # trivial rule supplies the periodic structure, and the network only
    # has to learn what that rule gets wrong. The baseline value is drawn
    # from at least one period ago, so it is known at forecast time and
    # introduces no leakage.
    residual: str = "none"               # "none" | "daily" | "weekly"
    # Day-of-year encoding. Harmful on a record shorter than a full year:
    # with train in Feb-Jul and test in Aug-Sep the model cannot learn an
    # annual cycle from two thirds of one, so it fits a trend and
    # extrapolates it off a cliff. Measured cost on this load record: 5.2 kW
    # of MAE, which is the difference between losing and beating the
    # seasonal-naive baseline. Turn it on only with 2+ years of data.
    use_doy: bool = False
    # -- feature selection ---------------------------------------------
    # Which covariates the decoder receives. Measured on the training
    # split by feature_eda.py:
    #   tod      time-of-day sin/cos    dominant - 12 h and 24 h carry the
    #                                   series; MI ~0.35 against <0.08 for
    #                                   everything else
    #   dow      day-of-week sin/cos    matters through SHAPE, not level:
    #                                   weekday and weekend means differ by
    #                                   under 2%, but weekdays have a sharp
    #                                   06:30 peak weekends lack entirely.
    #                                   A univariate score misses this - it
    #                                   is a tod x dow interaction
    #   weekend  weekend flag           largely redundant with dow
    #   holiday  public-holiday flag    holidays follow the WEEKEND shape;
    #                                   without it the model predicts a
    #                                   phantom morning peak on each one
    #   lag_1d   the value 24 h back    ACF 0.78
    #   lag_1w   the value 7 days back  ACF 0.84 - higher than the daily
    #                                   lag, and outside the reach of a
    #                                   48 h encoder lookback
    # Day-of-year stays governed by `use_doy` above.
    future_features: Tuple[str, ...] = ("tod", "dow", "weekend")
    # Feed the same calendar over the history to the encoder as well.
    encoder_calendar: bool = True
    conv_channels: Tuple[int, ...] = (32, 32)
    kernel_size: int = 5
    hidden: int = 64
    decoder_hidden: int = 64
    dropout: float = 0.1
    # Quantile levels the decoder predicts, each trained with the pinball
    # (quantile) loss. The default (0.5,) is a plain point forecast and
    # trains under the exact L1 objective the existing results were
    # measured with. Add levels - e.g. (0.1, 0.5, 0.9) for an 80% central
    # interval - to get a predictive interval for reserve sizing. 0.5 must
    # stay in the list: it is the point forecast the skill scores and the
    # sample-day plot use.
    quantiles: Tuple[float, ...] = (0.5,)

    # -- training ------------------------------------------------------
    epochs: int = 30
    batch_size: int = 64
    lr: float = 1e-3
    weight_decay: float = 1e-5
    patience: int = 5                    # early-stopping patience, in epochs
    grad_clip: float = 1.0
    seed: int = 0
    num_workers: int = 0
    max_train_windows: Optional[int] = None   # cap for quick runs
    # "auto" picks Apple Metal (mps) when present, else cuda, else cpu.
    # Recorded in the run's config.json so a result says where it ran.
    device: str = "auto"

    # -- evaluation ----------------------------------------------------
    # Solar at night is exactly zero and trivially predictable. Scoring it
    # alongside daytime inflates every metric and makes a model look far
    # better than it is, so solar is scored on daylight only.
    daytime_only: bool = False
    daytime_threshold: float = 20.0      # W/m^2 of clear-sky irradiance
    mape_floor: float = 1.0              # values below this are excluded from MAPE

    # -- output --------------------------------------------------------
    run_name: str = "run"
    out_dir: str = "runs"

    def __post_init__(self):
        if self.target == "solar":
            self.daytime_only = True
        if self.valid_range is None:
            self.valid_range = self.screen_defaults.get(self.target)
        elif self.valid_range is not None:
            self.valid_range = tuple(self.valid_range)

        self.future_features = tuple(dict.fromkeys(self.future_features))
        unknown = set(self.future_features) - set(FEATURE_NAMES)
        if unknown:
            raise ValueError(f"unknown future_features {sorted(unknown)}; "
                             f"known names are {sorted(FEATURE_NAMES)}")
        # A seasonal lag is only a legal covariate while the whole horizon
        # sits inside it. At gap + horizon > period the last horizon steps
        # would read values not yet observed at the origin - a leak that
        # would silently flatter every metric.
        for name, period in (("lag_1d", STEPS_PER_DAY), ("lag_1w", STEPS_PER_WEEK)):
            if name in self.future_features and self.gap + self.horizon > period:
                raise ValueError(
                    f"{name} would leak: gap + horizon = {self.gap + self.horizon} "
                    f"steps exceeds its {period}-step lag, so the far end of the "
                    f"horizon would read unobserved values.")

        if self.model == "tcn" and self.tcn_receptive_field < self.lookback:
            # A TCN that cannot see its own history is not a fair comparison
            # with an LSTM that can - the two-block default once saw 25 of
            # 576 steps. Warn rather than fail: tiny test models trip this.
            warnings.warn(
                f"TCN receptive field {self.tcn_receptive_field} steps is shorter "
                f"than the {self.lookback}-step lookback; most of the history is "
                f"invisible to it. Use more conv blocks (dilation doubles per block).")

        if self.cv_folds < 1 or not 0 <= self.cv_fold < self.cv_folds:
            raise ValueError(f"cv_fold must lie in [0, {self.cv_folds}); got {self.cv_fold}")

        self.quantiles = tuple(sorted(round(float(q), 6) for q in self.quantiles))
        if not self.quantiles or any(not 0.0 < q < 1.0 for q in self.quantiles):
            raise ValueError(f"quantiles must lie in (0, 1); got {self.quantiles}")
        if 0.5 not in self.quantiles:
            raise ValueError(
                f"quantiles must include 0.5 (the point forecast); got {self.quantiles}")

    @property
    def input_span(self) -> int:
        """Total steps one training window consumes."""
        return self.lookback + self.gap + self.horizon

    @property
    def tcn_receptive_field(self) -> int:
        """Steps a TCN output can see: two convs per block, dilation 2^b."""
        return 1 + 2 * (self.kernel_size - 1) * (2 ** len(self.conv_channels) - 1)

    @property
    def n_quantiles(self) -> int:
        return len(self.quantiles)

    @property
    def median_index(self) -> int:
        """Row of `quantiles` holding 0.5 — the point-forecast output."""
        return self.quantiles.index(0.5)

    def save(self, path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(asdict(self), indent=2, default=str))
        return path

    @classmethod
    def load(cls, path) -> "ForecastConfig":
        raw = json.loads(Path(path).read_text())
        raw["conv_channels"] = tuple(raw.get("conv_channels", (32, 32)))
        raw["quantiles"] = tuple(raw.get("quantiles", (0.5,)))
        raw["future_features"] = tuple(raw.get("future_features",
                                               ("tod", "dow", "weekend")))
        return cls(**raw)

    def describe(self) -> str:
        minutes = int(pd_minutes(self.resample))
        return (f"{self.target} · horizon {self.horizon} steps "
                f"({self.horizon * minutes / 60:.0f} h) · lookback {self.lookback} "
                f"({self.lookback * minutes / 60:.0f} h) · stride {self.stride} · "
                f"{self.model}"
                + (f" · quantiles {self.quantiles}" if self.n_quantiles > 1 else ""))


def pd_minutes(freq: str) -> float:
    import pandas as pd
    return pd.Timedelta(freq).total_seconds() / 60.0
