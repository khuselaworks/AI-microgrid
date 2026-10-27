"""
Scoring quantile forecasts, and the interval they have to beat.

A point forecast says what is most likely; reserve sizing needs to know
how bad it can get. The quantile heads (`--quantiles`) give that, but an
interval is only useful if it is *calibrated* - an 80% interval that
contains the truth 60% of the time is worse than none, because it is
trusted. So intervals are judged on two axes, in this order:

  reliability  does the q-quantile sit above the truth a fraction q of
               the time? Equivalently, does a central (1 - alpha)
               interval cover (1 - alpha) of outcomes (PICP)?
  sharpness    among calibrated intervals, narrower is better (mean width).

and by two proper scores that reward both at once, so neither can be
gamed alone:

  pinball loss     mean over levels of max(q e, (q - 1) e), e = y - y_hat_q
  interval score   (u - l) + (2 / alpha)(l - y)[y < l] + (2 / alpha)(y - u)[y > u]
                   (Gneiting and Raftery, 2007)

THE REFERENCE INTERVAL
----------------------
A probabilistic model needs a probabilistic baseline, just as the point
model needs seasonal naive. `EmpiricalResidualIntervals` takes a point
baseline and wraps it in the empirical quantiles of that baseline's own
errors, grouped by hour of day (errors are larger at the load peaks and
zero at night for solar). The quantiles are estimated on the validation
block and applied to test, so the reference never sees what it is scored
on.
"""
from __future__ import annotations

from typing import Dict, Optional, Sequence

import numpy as np

# The quantile levels the probabilistic model is trained at. Symmetric
# pairs give the 50%, 80% and 90% central intervals.
QUANTILE_LEVELS = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)


def central_pairs(levels: Sequence[float]):
    """(lower, upper, nominal coverage) for every symmetric pair of levels."""
    levels = [round(float(q), 6) for q in levels]
    out = []
    for q in levels:
        hi = round(1.0 - q, 6)
        if q < 0.5 and hi in levels:
            out.append((q, hi, round(hi - q, 6)))
    return out


def quantile_metrics(truth: np.ndarray, qpred: np.ndarray, levels: Sequence[float],
                     mask: Optional[np.ndarray] = None) -> Dict[str, float]:
    """
    Every reliability, sharpness and score figure for one set of quantile
    forecasts, in the units of `truth`.

        truth  (N, H)       qpred  (N, H, Q), columns ordered as `levels`
    """
    levels = [round(float(q), 6) for q in levels]
    if mask is None:
        mask = np.ones_like(truth, dtype=bool)
    valid = mask & np.isfinite(truth) & np.isfinite(qpred).all(axis=2)
    y = truth[valid]                                    # (n,)
    p = qpred[valid]                                    # (n, Q)

    out: Dict[str, float] = {"n": int(y.size)}
    err = y[:, None] - p
    q = np.asarray(levels)[None, :]
    out["pinball"] = float(np.maximum(q * err, (q - 1.0) * err).mean())

    for j, lv in enumerate(levels):
        out[f"below_{lv:g}"] = float((y <= p[:, j]).mean())   # reliability

    for lo, hi, nominal in central_pairs(levels):
        lo_v, hi_v = p[:, levels.index(lo)], p[:, levels.index(hi)]
        alpha = 1.0 - nominal
        width = hi_v - lo_v
        score = (width
                 + (2.0 / alpha) * np.clip(lo_v - y, 0.0, None)
                 + (2.0 / alpha) * np.clip(y - hi_v, 0.0, None))
        tag = f"{round(nominal * 100):d}"
        out[f"picp_{tag}"] = float(((y >= lo_v) & (y <= hi_v)).mean())
        out[f"width_{tag}"] = float(width.mean())
        out[f"interval_score_{tag}"] = float(score.mean())
    return out


class EmpiricalResidualIntervals:
    """
    A point baseline plus the empirical quantiles of its own errors, by
    hour of day. Fitted on one split, applied to another.
    """

    def __init__(self, levels: Sequence[float] = QUANTILE_LEVELS, floor: Optional[float] = None):
        self.levels = tuple(round(float(q), 6) for q in levels)
        self.floor = floor                       # 0.0 for irradiance
        self.table: Optional[np.ndarray] = None  # (24, Q) residual quantiles

    def fit(self, truth: np.ndarray, point: np.ndarray, hours: np.ndarray,
            mask: Optional[np.ndarray] = None) -> "EmpiricalResidualIntervals":
        """truth, point and hours are (N, H); hours is the TARGET step's hour."""
        if mask is None:
            mask = np.ones_like(truth, dtype=bool)
        valid = mask & np.isfinite(truth) & np.isfinite(point)
        resid = (truth - point)[valid]
        hrs = hours[valid]
        pooled = np.quantile(resid, self.levels)
        table = np.empty((24, len(self.levels)))
        for hr in range(24):
            sel = resid[hrs == hr]
            # Hours with almost no scored cells (dawn and dusk on the
            # daylight mask) fall back to the pooled quantiles.
            table[hr] = np.quantile(sel, self.levels) if sel.size >= 50 else pooled
        self.table = table
        return self

    def predict(self, point: np.ndarray, hours: np.ndarray) -> np.ndarray:
        """(N, H) point forecast -> (N, H, Q) quantile forecast."""
        if self.table is None:
            raise RuntimeError("EmpiricalResidualIntervals.predict called before fit")
        out = point[:, :, None] + self.table[hours]
        if self.floor is not None:
            out = np.clip(out, self.floor, None)
        return np.sort(out, axis=2)
