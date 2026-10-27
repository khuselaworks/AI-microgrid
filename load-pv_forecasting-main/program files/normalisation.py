"""
Min-max normalisation, fitted without leaking the test period.

    x_scaled = (x - min) / (max - min) * (hi - lo) + lo

The formula is trivial. Everything that matters here is about WHERE the
min and max come from, and about being able to get back.

**The bounds are fitted on the training split only.** If min and max are
computed over the whole record, the largest value your model will ever
be tested on has already influenced the scaling of the data it trained
on. The model never sees a value above 1.0 in training, so it never
learns that the series can exceed what it has seen, and the validation
error comes out optimistically biased. Fitting on the first `fit_fraction`
of the record and applying those same numbers to the rest is the
standard defence, and it is what this module does.

A consequence worth stating rather than hiding: held-out values CAN fall
outside the target range, because a later peak may exceed the training
maximum. That is not a bug - it is the honest signal that the training
period did not contain the full dynamic range. The report counts those
excursions and says how large they are; nothing is clipped unless you
ask for it.

**The parameters are the deliverable, not just the scaled data.** A
forecast produced on normalised data comes back in normalised units and
is meaningless until it is mapped back to kW. `save_params()` writes the
exact min/max per channel to JSON, and `inverse_transform()` reverses
the scaling with them, so the model's output can be reported in real
units and compared against real measurements.

Bundled from the companion preprocessing project so forecasting can run
without a sibling checkout. The scaler schema is preserved for checkpoints.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

DEFAULT_FIT_FRACTION = 0.70   # first 70% of the record fits the scaler
DEFAULT_RANGE = (0.0, 1.0)

# A channel whose fitted min and max are this close is treated as
# constant. Dividing by that range would explode any numerical noise
# into full-scale swings, so the channel is mapped to the bottom of the
# target range and flagged loudly - a flat channel is almost always a
# dead sensor rather than a real measurement.
CONSTANT_TOLERANCE = 1e-12

SCHEMA_VERSION = 1


@dataclass
class ChannelScale:
    """The fitted parameters for one channel. This is what gets saved."""

    name: str
    data_min: float
    data_max: float
    feature_min: float
    feature_max: float
    n_fit: int
    n_missing_fit: int
    is_constant: bool = False
    unit: str = ""

    @property
    def data_range(self) -> float:
        return self.data_max - self.data_min

    @property
    def scale(self) -> float:
        """Multiplier applied after centring on data_min."""
        if self.is_constant or abs(self.data_range) < CONSTANT_TOLERANCE:
            return 0.0
        return (self.feature_max - self.feature_min) / self.data_range


@dataclass
class MinMaxScaler:
    """Fitted scaler for a whole frame, plus the provenance to defend it."""

    channels: Dict[str, ChannelScale]
    feature_range: Tuple[float, float]
    fit_fraction: float
    fit_start: Optional[pd.Timestamp] = None
    fit_end: Optional[pd.Timestamp] = None
    n_rows_total: int = 0
    n_rows_fit: int = 0
    source: str = ""
    layer: str = ""
    fitted_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    # -- persistence ---------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "method": "min-max",
            "formula": "x_scaled = (x - data_min) / (data_max - data_min) "
                       "* (feature_max - feature_min) + feature_min",
            "inverse": "x = (x_scaled - feature_min) / (feature_max - feature_min) "
                       "* (data_max - data_min) + data_min",
            "feature_range": list(self.feature_range),
            "fit_fraction": self.fit_fraction,
            "fit_start": str(self.fit_start) if self.fit_start is not None else None,
            "fit_end": str(self.fit_end) if self.fit_end is not None else None,
            "n_rows_total": self.n_rows_total,
            "n_rows_fit": self.n_rows_fit,
            "source": self.source,
            "layer": self.layer,
            "fitted_at": self.fitted_at,
            "channels": {k: asdict(v) for k, v in self.channels.items()},
        }

    def save_params(self, path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load_params(cls, path) -> "MinMaxScaler":
        raw = json.loads(Path(path).read_text())
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"Scaler file schema {raw.get('schema_version')!r} does not match "
                f"this version ({SCHEMA_VERSION}). Refit rather than guessing.")
        channels = {k: ChannelScale(**v) for k, v in raw["channels"].items()}
        return cls(
            channels=channels,
            feature_range=tuple(raw["feature_range"]),
            fit_fraction=raw["fit_fraction"],
            fit_start=pd.Timestamp(raw["fit_start"]) if raw["fit_start"] else None,
            fit_end=pd.Timestamp(raw["fit_end"]) if raw["fit_end"] else None,
            n_rows_total=raw["n_rows_total"],
            n_rows_fit=raw["n_rows_fit"],
            source=raw.get("source", ""),
            layer=raw.get("layer", ""),
            fitted_at=raw.get("fitted_at", ""),
        )


def fit(df: pd.DataFrame, fit_fraction: float = DEFAULT_FIT_FRACTION,
        feature_range: Tuple[float, float] = DEFAULT_RANGE,
        source: str = "", layer: str = "",
        units: Optional[Dict[str, str]] = None) -> MinMaxScaler:
    """
    Fit min/max on the FIRST `fit_fraction` of the rows, chronologically.

    Chronological, not random: this is a time series, and a random split
    would put future samples in the training set, which leaks in exactly
    the way this whole module exists to avoid.
    """
    lo, hi = feature_range
    if hi <= lo:
        raise ValueError(f"Target range must have max > min, got {feature_range}.")
    if not 0 < fit_fraction <= 1:
        raise ValueError(f"Fit fraction must be in (0, 1], got {fit_fraction}.")
    if df.empty:
        raise ValueError("Nothing to fit: the frame is empty.")

    n_total = len(df)
    n_fit = max(1, int(round(n_total * fit_fraction)))
    train = df.iloc[:n_fit]
    units = units or {}

    channels: Dict[str, ChannelScale] = {}
    for name in df.columns:
        col = pd.to_numeric(train[name], errors="coerce")
        valid = col.dropna()
        if valid.empty:
            # No evidence at all. Identity-scale it and say so, rather
            # than emitting NaN parameters that fail silently later.
            channels[str(name)] = ChannelScale(
                name=str(name), data_min=0.0, data_max=1.0,
                feature_min=lo, feature_max=hi, n_fit=0,
                n_missing_fit=int(col.isna().sum()), is_constant=True,
                unit=units.get(str(name), ""))
            continue
        cmin, cmax = float(valid.min()), float(valid.max())
        channels[str(name)] = ChannelScale(
            name=str(name), data_min=cmin, data_max=cmax,
            feature_min=lo, feature_max=hi,
            n_fit=int(valid.size), n_missing_fit=int(col.isna().sum()),
            is_constant=(cmax - cmin) < CONSTANT_TOLERANCE,
            unit=units.get(str(name), ""))

    return MinMaxScaler(
        channels=channels, feature_range=(float(lo), float(hi)),
        fit_fraction=float(fit_fraction),
        fit_start=train.index.min(), fit_end=train.index.max(),
        n_rows_total=n_total, n_rows_fit=n_fit, source=source, layer=layer)


def transform(df: pd.DataFrame, scaler: MinMaxScaler,
              clip: bool = False) -> pd.DataFrame:
    """
    Apply the fitted scaling. NaN stays NaN.

    `clip` is off by default on purpose: a held-out value above the
    training maximum SHOULD come out above the target range, because
    that is the fact of the matter. Clipping hides it.
    """
    out = pd.DataFrame(index=df.index)
    lo, hi = scaler.feature_range
    for name in df.columns:
        key = str(name)
        cs = scaler.channels.get(key)
        col = pd.to_numeric(df[name], errors="coerce")
        if cs is None:
            out[name] = col          # unknown channel: pass through untouched
            continue
        if cs.is_constant:
            out[name] = col.where(col.isna(), lo)
            continue
        scaled = (col - cs.data_min) * cs.scale + cs.feature_min
        out[name] = scaled.clip(lo, hi) if clip else scaled
    return out


def inverse_transform(df: pd.DataFrame, scaler: MinMaxScaler) -> pd.DataFrame:
    """
    Map normalised values back to real units.

    This is what turns a model's output back into kW. Without it a
    forecast is a number between 0 and 1 that cannot be compared with a
    measurement or a tariff.
    """
    out = pd.DataFrame(index=df.index)
    for name in df.columns:
        key = str(name)
        cs = scaler.channels.get(key)
        col = pd.to_numeric(df[name], errors="coerce")
        if cs is None:
            out[name] = col
            continue
        if cs.is_constant:
            out[name] = col.where(col.isna(), cs.data_min)
            continue
        out[name] = (col - cs.feature_min) / cs.scale + cs.data_min
    return out
