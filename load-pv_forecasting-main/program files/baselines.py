"""
The baselines the deep model has to beat to be worth anything.

This module is not a formality. Load and irradiance are both dominated
by cycles a trivial rule reproduces, so a network can post an
impressive-looking MAE while adding nothing. The only number that means
anything is the **skill score** against a strong baseline:

    skill = 1 - MAE_model / MAE_baseline

and the baseline has to be strong, or the skill is flattering. These are
implemented, each a standard yardstick in its own literature (plus
SimilarDayAverage for load and a 24-hour ClearSkyPersistence for solar,
described on their classes):

* **SeasonalNaive** - tomorrow repeats today. On this load record the
  daily autocorrelation is 0.786.
* **WeeklyNaive** - next Monday repeats last Monday. Usually the harder
  one to beat for load, because it gets the weekday/weekend pattern
  right: on this record the weekly autocorrelation is 0.883, higher than
  the daily one.
* **ClearSkyPersistence** - the standard solar baseline, and much harder
  to beat than plain persistence. It carries the recent *clear-sky index*
  forward and multiplies it by the deterministic clear-sky curve, so it
  already knows the diurnal shape and only assumes cloudiness persists.
  Beating plain persistence on solar proves nothing; beating this does.

All baselines take the same inputs as the model and are evaluated on the
same windows, so the comparison is like for like.
"""
from __future__ import annotations

from typing import Dict

import numpy as np

from config import STEPS_PER_DAY, STEPS_PER_WEEK


class Baseline:
    name = "baseline"

    def predict(self, spec, origins: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def available(self, spec, origins: np.ndarray) -> bool:
        return True


class SeasonalNaive(Baseline):
    """Repeat the same clock time from `period` steps ago."""

    def __init__(self, period: int = STEPS_PER_DAY, name: str = "seasonal naive (1 day)"):
        self.period = period
        self.name = name

    def available(self, spec, origins: np.ndarray) -> bool:
        if origins.size == 0:
            return False
        cfg = spec.cfg
        # The furthest-back reference the horizon needs must still exist.
        return int(origins.min()) + cfg.gap + 1 - self.period >= 0

    def predict(self, spec, origins: np.ndarray) -> np.ndarray:
        cfg = spec.cfg
        h = np.arange(cfg.horizon)
        # value at (origin + gap + h) taken from `period` steps earlier
        idx = origins[:, None] + cfg.gap + h[None, :] - self.period
        return spec.values[idx]


class WeeklyNaive(SeasonalNaive):
    def __init__(self):
        super().__init__(period=STEPS_PER_WEEK, name="seasonal naive (1 week)")


class SimilarDayAverage(Baseline):
    """
    The mean of the same clock time on the same weekday over the last
    `weeks` weeks - the "similar day" method utilities have used for
    decades, and a harder target than weekly naive because averaging
    removes most of one week's idiosyncratic noise.

    Every reference is at least one full week old, and gap + horizon never
    exceeds a week, so all of them were observed at the forecast origin.
    Load only: irradiance has no weekly cycle to average over.
    """

    def __init__(self, weeks: int = 4):
        self.weeks = weeks
        self.name = f"similar-day average ({weeks} wk)"

    def available(self, spec, origins: np.ndarray) -> bool:
        if origins.size == 0 or spec.cfg.target != "load":
            return False
        return int(origins.min()) + spec.cfg.gap - self.weeks * STEPS_PER_WEEK >= 0

    def predict(self, spec, origins: np.ndarray) -> np.ndarray:
        cfg = spec.cfg
        h = np.arange(cfg.horizon)
        target = origins[:, None] + cfg.gap + h[None, :]
        stack = np.stack([spec.values[target - j * STEPS_PER_WEEK]
                          for j in range(1, self.weeks + 1)])
        with np.errstate(invalid="ignore"):
            return np.nanmean(stack, axis=0)


class ClearSkyPersistence(Baseline):
    """
    Smart persistence for irradiance.

    Takes the mean clear-sky index over the last `window` steps of
    history and applies it to the clear-sky curve across the horizon.
    Equivalent to assuming the cloud state persists while the geometry
    evolves exactly - which it does.

    The default 1-hour window is the intraday form. From an origin at
    night that window holds no daylight, so the index falls back to 1 and
    the forecast is the clear-sky curve itself. The 24-hour window is the
    day-ahead form: yesterday's mean cloudiness, carried onto tomorrow's
    geometry, and always defined.
    """

    def __init__(self, window: int = 12, name: str = "clear-sky persistence"):
        self.window = window
        self.name = name

    def _column(self, spec) -> int:
        """
        Locate the clear-sky curve by NAME, never by a fixed index.

        It was a fixed index once. Turning off the day-of-year features
        dropped the calendar block from 7 columns to 5, the clear-sky
        curve moved from index 7 to index 5, `available()` went False,
        and the strongest solar baseline silently vanished from the
        report - leaving the model's skill quoted only against the weak
        naive baselines. A missing baseline flatters a model, so this
        must never fail quietly.
        """
        names = getattr(spec, "future_names", ())
        if "clear_sky" not in names:
            raise KeyError(
                "clear-sky persistence needs a 'clear_sky' future covariate; "
                f"got {list(names)}. This baseline must not be skipped silently.")
        return names.index("clear_sky")

    def available(self, spec, origins: np.ndarray) -> bool:
        return (spec.cfg.target == "solar"
                and "clear_sky" in getattr(spec, "future_names", ()))

    def predict(self, spec, origins: np.ndarray) -> np.ndarray:
        cfg = spec.cfg
        cs = spec.future[:, self._column(spec)].astype(np.float64)   # scaled clear sky
        # The scaled target and the scaled clear-sky curve are on
        # different scales, so the ratio is computed then re-applied
        # consistently - the constant cancels.
        h = np.arange(cfg.horizon)
        w = np.arange(-self.window, 0)

        recent_idx = origins[:, None] + w[None, :]
        recent_obs = spec.values[recent_idx]
        recent_cs = cs[recent_idx]

        usable = recent_cs > 0.02
        num = np.where(usable, recent_obs, 0.0).sum(axis=1)
        den = np.where(usable, recent_cs, 0.0).sum(axis=1)
        # No usable daylight in the recent window (i.e. it is night):
        # fall back to a clear-sky index of 1, which the zero clear-sky
        # curve then multiplies to zero anyway.
        k = np.where(den > 1e-9, num / np.maximum(den, 1e-9), 1.0)

        future_idx = origins[:, None] + cfg.gap + h[None, :]
        return np.clip(k[:, None] * cs[future_idx], 0.0, None)


def default_baselines(spec) -> Dict[str, Baseline]:
    """Whichever baselines make sense for this target and this history."""
    candidates = [SeasonalNaive(), WeeklyNaive(), SimilarDayAverage(),
                  ClearSkyPersistence(),
                  ClearSkyPersistence(window=STEPS_PER_DAY,
                                      name="clear-sky persistence (24 h)")]
    out = {}
    for b in candidates:
        for split in ("test",):
            if b.available(spec, spec.origins[split]):
                out[b.name] = b
    return out
