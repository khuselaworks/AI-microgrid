"""
Turning a cleaned series into supervised windows, without leaking.

Three rules are enforced here rather than left to the caller, because
each one is a silent, results-invalidating mistake if it is forgotten:

  1. **Splits are chronological, and windows never straddle them.** A
     window whose history sits in train and whose target sits in
     validation would let the model see its own answer. Origins are
     therefore only valid where the WHOLE window - lookback, gap and
     horizon - falls inside one split.

  2. **The scaler is fitted on train rows only**, reusing the same
     min-max scaler the preprocessing GUI writes out, so the scaling a
     model is trained under is reproducible outside this codebase.

  3. **No window containing a missing value is used.** The alternative -
     quietly interpolating - would train the model on invented data and
     score it against invented data. Dropped windows are counted and
     reported instead.

Windows are addressed by index and materialised on demand. Storing them
would mean holding roughly (lookback + horizon) floats per origin: at the
default settings that is 864 floats x ~20k origins, which is wasteful
when the underlying series is 69,696 floats in total.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


from config import (SITE_LAT, SITE_LON, SITE_TZ_OFFSET, STEPS_PER_DAY,  # noqa: E402
                    STEPS_PER_WEEK, ForecastConfig, pd_minutes)
from holidays import is_holiday  # noqa: E402
from normalisation import MinMaxScaler, fit as fit_scaler, transform  # noqa: E402
from solar_geometry import clear_sky_ghi, daytime_mask  # noqa: E402

# Seasonal lags available as future covariates, and how far back each reads.
LAG_PERIODS = {"lag_1d": STEPS_PER_DAY, "lag_1w": STEPS_PER_WEEK}


# --------------------------------------------------------------- loading
def load_target(cfg: ForecastConfig) -> pd.Series:
    """Read the source file, pick the target column, put it on the grid."""
    path = Path(cfg.source)
    if not path.exists():
        raise FileNotFoundError(f"Source not found: {path}")

    if path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path, engine="openpyxl")
    else:
        raw = pd.read_csv(path)

    time_col = raw.columns[0]
    index = pd.DatetimeIndex(pd.to_datetime(raw[time_col]))
    body = raw.drop(columns=[time_col])
    body.index = index

    if cfg.column and cfg.column in body.columns:
        col = cfg.column
    elif cfg.target == "solar":
        matches = [c for c in body.columns if "ghi" in str(c).lower()]
        col = matches[0] if matches else body.columns[0]
    else:
        col = body.columns[0]

    series = pd.to_numeric(body[col], errors="coerce").sort_index()
    series = series[~series.index.duplicated(keep="first")]

    # Physical screening BEFORE resampling, so one impossible reading
    # cannot contaminate the 5-minute mean it falls in.
    if cfg.valid_range is not None:
        lo, hi = cfg.valid_range
        impossible = (series < lo) | (series > hi)
        n = int(impossible.sum())
        if n:
            series = series.mask(impossible)
            load_target.last_screened = n  # type: ignore[attr-defined]
        else:
            load_target.last_screened = 0  # type: ignore[attr-defined]
    else:
        load_target.last_screened = 0  # type: ignore[attr-defined]

    # Mean over the bin, so a 1-minute record becomes a true 5-minute
    # average rather than a sample of one minute in five.
    series = series.resample(cfg.resample).mean()
    series.name = str(col)
    return series


# ---------------------------------------------------------------- splits
@dataclass
class Splits:
    train: slice
    val: slice
    test: slice
    index: pd.DatetimeIndex

    def bounds(self) -> Dict[str, Tuple[pd.Timestamp, pd.Timestamp]]:
        return {name: (self.index[sl.start], self.index[sl.stop - 1])
                for name, sl in (("train", self.train), ("val", self.val),
                                 ("test", self.test))}

    def describe(self) -> str:
        out = []
        for name, (a, b) in self.bounds().items():
            sl = getattr(self, name)
            out.append(f"  {name:<6} {a:%Y-%m-%d} → {b:%Y-%m-%d}  "
                       f"({sl.stop - sl.start:,} steps)")
        return "\n".join(out)


def chronological_splits(index: pd.DatetimeIndex, cfg: ForecastConfig) -> Splits:
    """
    Train / val / test as contiguous blocks, in time order.

    With rolling-origin folds the val and test blocks keep their standard
    lengths but end `shift` steps before the end of the record, and train
    is everything before them. The last fold has shift 0 and is the
    standard split exactly, so its numbers are the single-split numbers.
    """
    n = len(index)
    n_train = int(round(n * cfg.train_frac))
    n_val = int(round(n * cfg.val_frac))
    n_test = n - n_train - n_val
    shift = (cfg.cv_folds - 1 - cfg.cv_fold) * n_test
    end = n - shift
    test_start = end - n_test
    val_start = test_start - n_val
    if val_start <= cfg.input_span or n_val <= cfg.input_span:
        raise ValueError(
            f"Not enough data: a window spans {cfg.input_span} steps but fold "
            f"{cfg.cv_fold} leaves train/val of {val_start}/{n_val} steps.")
    return Splits(slice(0, val_start), slice(val_start, test_start),
                  slice(test_start, end), index)


# -------------------------------------------------------------- features
def _calendar_frame(index: pd.DatetimeIndex, groups) -> pd.DataFrame:
    """
    Only the requested cyclical groups, as named columns.

    Sine/cosine pairs rather than raw hour/weekday integers so that 23:55
    and 00:00 are adjacent to the model instead of maximally distant.
    """
    groups = set(groups)
    minute_of_day = index.hour.to_numpy() * 60 + index.minute.to_numpy()
    dow = index.dayofweek.to_numpy()
    doy = index.dayofyear.to_numpy()
    feats = {}
    if "tod" in groups:
        feats["tod_sin"] = np.sin(2 * np.pi * minute_of_day / 1440.0)
        feats["tod_cos"] = np.cos(2 * np.pi * minute_of_day / 1440.0)
    if "dow" in groups:
        feats["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
        feats["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    if "weekend" in groups:
        feats["is_weekend"] = (dow >= 5).astype(float)
    if "doy" in groups:
        feats["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
        feats["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    return pd.DataFrame(feats, index=index)


def calendar_features(index: pd.DatetimeIndex, use_doy: bool = True) -> pd.DataFrame:
    """The full default calendar set, for callers outside the config path."""
    return _calendar_frame(index, ["tod", "dow", "weekend"] + (["doy"] if use_doy else []))


def _selected_groups(cfg: ForecastConfig):
    groups = [g for g in ("tod", "dow", "weekend") if g in cfg.future_features]
    if cfg.use_doy or "doy" in cfg.future_features:
        groups.append("doy")
    return groups


def future_covariates(index: pd.DatetimeIndex, cfg: ForecastConfig) -> pd.DataFrame:
    """
    Everything genuinely known in advance for every step of the horizon.

    Calendar and public holidays always; for solar, the sun's position
    too. Solar geometry is deterministic astronomy, so it is legal at any
    lead time - and it carries almost all of the structure in an
    irradiance series, leaving the model to learn only the cloud
    attenuation on top.

    Which columns appear is `cfg.future_features`. Everything here is a
    function of the TIMESTAMP alone, so it is known for any future step;
    the seasonal lags, which are functions of past observations, are
    added in `prepare` where the scaled series is available.
    """
    feats = _calendar_frame(index, _selected_groups(cfg))
    if "holiday" in cfg.future_features:
        feats["is_holiday"] = is_holiday(index)
    if cfg.target == "solar":
        cs = clear_sky_ghi(index, SITE_LAT, SITE_LON, SITE_TZ_OFFSET)
        feats["clear_sky"] = (cs / 1200.0).to_numpy()      # scaled to ~[0, 1]
        feats["is_daylight"] = (cs > cfg.daytime_threshold).astype(float).to_numpy()
    return feats


def past_covariates(index: pd.DatetimeIndex, cfg: ForecastConfig) -> pd.DataFrame:
    """
    Calendar over the HISTORY window, fed to the encoder beside the target.

    The encoder only ever sees the past, so it can read the shape of the
    last two days directly; these columns mostly tell it *where in the
    week* that history sat. `cfg.encoder_calendar` turns them off so the
    ablation can measure whether they earn their parameters.
    """
    if not cfg.encoder_calendar:
        return pd.DataFrame(index=index)
    return _calendar_frame(index, _selected_groups(cfg))


# --------------------------------------------------------------- windows
@dataclass
class WindowSpec:
    """Everything a model needs to consume one prepared dataset."""

    values: np.ndarray            # scaled target, full length
    future: np.ndarray            # (n, n_future_features)
    past_extra: np.ndarray        # (n, n_past_extra) — calendar over the history
    origins: Dict[str, np.ndarray]
    scaler: MinMaxScaler
    index: pd.DatetimeIndex
    raw: np.ndarray               # unscaled target, for reporting in real units
    dropped: Dict[str, int]
    cfg: ForecastConfig
    baseline: Optional[np.ndarray] = None   # scaled seasonal baseline, for residual mode
    future_names: Tuple[str, ...] = ()      # column names for `future`, looked up by NAME

    @property
    def n_future_features(self) -> int:
        return self.future.shape[1]

    @property
    def n_past_features(self) -> int:
        return 1 + self.past_extra.shape[1]

    def target_slice(self, origin: int) -> slice:
        start = origin + self.cfg.gap
        return slice(start, start + self.cfg.horizon)

    def past_slice(self, origin: int) -> slice:
        return slice(origin - self.cfg.lookback, origin)


def _valid_origins(finite: np.ndarray, sl: slice, cfg: ForecastConfig) -> Tuple[np.ndarray, int]:
    """
    Origins whose entire window lies inside `sl` and contains no NaN.

    Returns the origins and how many were rejected for missing data, so a
    run can report that rather than silently training on less than it
    claims.
    """
    first = sl.start + cfg.lookback
    last = sl.stop - cfg.gap - cfg.horizon
    if last <= first:
        return np.empty(0, dtype=int), 0
    candidates = np.arange(first, last, cfg.stride, dtype=int)
    if candidates.size == 0:
        return candidates, 0

    # Cumulative count of finite values makes the "is this whole window
    # clean?" test O(1) per candidate instead of O(window).
    csum = np.concatenate([[0], np.cumsum(finite.astype(np.int64))])
    span_start = candidates - cfg.lookback
    span_end = candidates + cfg.gap + cfg.horizon
    clean = (csum[span_end] - csum[span_start]) == (span_end - span_start)
    return candidates[clean], int((~clean).sum())


def prepare(cfg: ForecastConfig) -> WindowSpec:
    """Load, split, scale on train only, and enumerate valid origins."""
    series = load_target(cfg)
    n_screened = int(getattr(load_target, "last_screened", 0))
    index = series.index
    splits = chronological_splits(index, cfg)

    raw = series.to_numpy(dtype=float)
    finite = np.isfinite(raw)

    # Rule 2: the scaler sees the training rows and nothing else.
    train_frame = series.iloc[splits.train].to_frame(series.name)
    scaler = fit_scaler(train_frame, fit_fraction=1.0, feature_range=(0.0, 1.0),
                        source=cfg.source, layer=f"{cfg.target}-train",
                        units={series.name: "kW" if cfg.target == "load" else "W/m²"})
    scaled = transform(series.to_frame(series.name), scaler)[series.name].to_numpy(dtype=float)

    future_frame = future_covariates(index, cfg)

    # Seasonal lags are future covariates too: the value one day or one
    # week before each TARGET step. Because gap + horizon never exceeds
    # the lag period (checked in ForecastConfig.__post_init__), every one
    # of them was already observed at the forecast origin, so this reads
    # nothing the model would not have. They use the SCALED series, so
    # they arrive on the same footing as the target.
    for name, period in LAG_PERIODS.items():
        if name in cfg.future_features:
            col = np.full(len(scaled), np.nan, dtype=np.float32)
            col[period:] = scaled[:-period]
            future_frame[name] = col

    future = future_frame.to_numpy(dtype=np.float32)
    past_extra = past_covariates(index, cfg).to_numpy(dtype=np.float32)

    # A window is only usable if every future covariate it reads is
    # finite as well: the seasonal lags are NaN over the first period of
    # the record, exactly as the residual baseline is below.
    usable = finite
    if future.shape[1]:
        usable = usable & np.isfinite(future).all(axis=1)

    # Residual mode: the model learns the CORRECTION to a seasonal rule
    # rather than the level. The rule's value comes from at least one
    # full period ago, so it is known at forecast time; the first period
    # of the record has no such reference and its windows are dropped.
    baseline = None
    if cfg.residual != "none":
        period = {"daily": STEPS_PER_DAY, "weekly": STEPS_PER_WEEK}[cfg.residual]
        baseline = np.full_like(scaled, np.nan, dtype=np.float32)
        baseline[period:] = scaled[:-period]
        usable = usable & np.isfinite(baseline)

    origins, dropped = {}, {}
    for name in ("train", "val", "test"):
        origins[name], dropped[name] = _valid_origins(usable, getattr(splits, name), cfg)

    if cfg.max_train_windows and len(origins["train"]) > cfg.max_train_windows:
        rng = np.random.default_rng(cfg.seed)
        keep = np.sort(rng.choice(len(origins["train"]), cfg.max_train_windows, replace=False))
        origins["train"] = origins["train"][keep]

    spec = WindowSpec(values=scaled.astype(np.float32), future=future,
                      past_extra=past_extra, origins=origins, scaler=scaler,
                      index=index, raw=raw, dropped=dropped, cfg=cfg,
                      baseline=baseline,
                      future_names=tuple(str(c) for c in future_frame.columns))
    spec.splits = splits  # type: ignore[attr-defined]
    spec.n_screened = n_screened  # type: ignore[attr-defined]
    return spec


def summarise(spec: WindowSpec) -> str:
    cfg = spec.cfg
    minutes = pd_minutes(cfg.resample)
    lines = [
        f"Target:        {cfg.target}  ({spec.index.name or 'series'})",
        f"Source:        {cfg.source}",
        f"Grid:          {minutes:g}-minute, {len(spec.index):,} steps, "
        f"{spec.index[0]:%Y-%m-%d} → {spec.index[-1]:%Y-%m-%d}",
        f"Missing:       {int((~np.isfinite(spec.raw)).sum()):,} readings",
        (f"Screened:      {getattr(spec, 'n_screened', 0):,} readings outside "
         f"{cfg.valid_range} — physically impossible, removed before scaling"
         if getattr(spec, "n_screened", 0) else
         "Screened:      none outside the physical range"),
        "",
        "SPLITS  (chronological; no window crosses a boundary)",
        spec.splits.describe(),  # type: ignore[attr-defined]
        "",
        "WINDOWS",
        f"  lookback {cfg.lookback} + gap {cfg.gap} + horizon {cfg.horizon} "
        f"= {cfg.input_span} steps per window, origins every {cfg.stride}",
        (f"  residual: the model predicts the CORRECTION to the {cfg.residual} "
         f"seasonal rule, not the level" if cfg.residual != "none" else
         "  residual: off — the model predicts the level directly"),
    ]
    for name in ("train", "val", "test"):
        n, d = len(spec.origins[name]), spec.dropped[name]
        lines.append(f"  {name:<6} {n:,} windows"
                     + (f"   ({d:,} rejected for missing data)" if d else ""))
    cs = spec.scaler.channels[list(spec.scaler.channels)[0]]
    lines += [
        "",
        "SCALING  (fitted on the training split only)",
        f"  min {cs.data_min:,.3f}   max {cs.data_max:,.3f}   → [0, 1]",
    ]
    return "\n".join(lines)
