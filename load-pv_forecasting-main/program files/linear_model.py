"""
A linear model on the inputs the network gets - the benchmark that asks
whether the network earns its parameters.

The seasonal baselines show that the network beats rules nobody fitted.
They cannot show that it beats a *fitted* model given the same
information, and that is the first question an examiner asks of any deep
forecaster: would least squares on the same covariates do as well?

So this regresses every horizon step on

  * that step's future covariates, exactly as the decoder receives them
    (calendar, holiday flag, seasonal lags; clear-sky curve for solar),
  * the last observed value and the mean of the last 24 h - the two
    summaries of the history a linear model can use, in place of the
    encoder,
  * the lead time, so the fit can shade towards the lags as the horizon
    lengthens.

One coefficient vector is shared across all horizon steps, mirroring the
network's shared decoder. It is fitted by ridge-regularised least squares
on the training origins only, so it obeys the same leakage rules as the
network: it never sees validation or test rows.
"""
from __future__ import annotations

import numpy as np

from config import STEPS_PER_DAY


class LinearDirect:
    name = "linear regression"

    def __init__(self, ridge: float = 1e-3, chunk: int = 512):
        self.ridge = ridge
        self.chunk = chunk
        self.coef: np.ndarray | None = None

    # ---------------------------------------------------------- design
    @staticmethod
    def _history_means(spec) -> np.ndarray:
        """Cumulative sum, so a 24 h mean ending at any origin is O(1)."""
        return np.concatenate([[0.0], np.cumsum(np.nan_to_num(spec.values,
                                                              nan=0.0).astype(np.float64))])

    def _design(self, spec, origins: np.ndarray, csum: np.ndarray) -> np.ndarray:
        cfg = spec.cfg
        h = np.arange(cfg.horizon)
        idx = origins[:, None] + cfg.gap + h[None, :]                 # (N, H)
        n = len(origins)

        future = spec.future[idx].astype(np.float64)                 # (N, H, F)
        last = spec.values[origins - 1].astype(np.float64)           # (N,)
        day_mean = (csum[origins] - csum[origins - STEPS_PER_DAY]) / STEPS_PER_DAY
        lead = (h + 1) / cfg.horizon

        cols = [future,
                np.broadcast_to(last[:, None, None], (n, cfg.horizon, 1)),
                np.broadcast_to(day_mean[:, None, None], (n, cfg.horizon, 1)),
                np.broadcast_to(lead[None, :, None], (n, cfg.horizon, 1)),
                np.ones((n, cfg.horizon, 1))]
        return np.concatenate(cols, axis=2)                           # (N, H, D)

    # ------------------------------------------------------------- fit
    def fit(self, spec, split: str = "train") -> "LinearDirect":
        """Accumulate the normal equations chunk by chunk, then solve."""
        cfg = spec.cfg
        origins = spec.origins[split]
        csum = self._history_means(spec)
        h = np.arange(cfg.horizon)
        xtx = xty = None
        for start in range(0, len(origins), self.chunk):
            o = origins[start:start + self.chunk]
            x = self._design(spec, o, csum).reshape(-1, self._n_cols(spec))
            y = spec.values[o[:, None] + cfg.gap + h[None, :]].astype(np.float64).ravel()
            if xtx is None:
                xtx, xty = x.T @ x, x.T @ y
            else:
                xtx += x.T @ x
                xty += x.T @ y
        reg = self.ridge * np.eye(xtx.shape[0])
        reg[-1, -1] = 0.0                     # the intercept is not shrunk
        self.coef = np.linalg.solve(xtx + reg, xty)
        return self

    def _n_cols(self, spec) -> int:
        return spec.future.shape[1] + 4

    # --------------------------------------------------------- predict
    def available(self, spec, origins: np.ndarray) -> bool:
        return self.coef is not None and origins.size > 0

    def predict(self, spec, origins: np.ndarray) -> np.ndarray:
        if self.coef is None:
            raise RuntimeError("LinearDirect.predict called before fit")
        csum = self._history_means(spec)
        out = []
        for start in range(0, len(origins), self.chunk):
            o = origins[start:start + self.chunk]
            out.append(self._design(spec, o, csum) @ self.coef)
        return np.concatenate(out, axis=0) if out else np.empty((0, spec.cfg.horizon))
