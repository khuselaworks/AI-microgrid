"""
Scoring, in real units, on the same windows, with the same mask.

Design rules, each of which exists because the alternative flatters the
model:

* **Metrics are computed in original units**, after inverting the
  scaler. An MAE of 0.03 in scaled space is uninterpretable and cannot
  be compared with anything in the literature; 12.4 kW can.

* **Solar is scored on daylight only.** Night-time GHI is exactly zero
  and every model predicts it perfectly. Including it roughly halves
  every error and inflates every skill score, purely by averaging in
  free correct answers. The daylight mask comes from the deterministic
  clear-sky curve, not from the observations, so it does not depend on
  what actually happened.

* **MAPE has a floor.** Percentage error explodes as the denominator
  approaches zero, so readings below `mape_floor` are excluded and the
  count of what was excluded is reported. An unqualified MAPE on a
  series that touches zero is meaningless.

* **Skill is quoted against the best baseline**, not the weakest. Skill
  against a baseline nobody would use is not evidence.

* **Error is reported per horizon step** as well as pooled. A day-ahead
  model that is excellent at +5 minutes and useless at +24 hours has the
  same pooled MAE as one that is mediocre throughout, and they are not
  the same model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from config import SITE_LAT, SITE_LON, SITE_TZ_OFFSET
from solar_geometry import daytime_mask


def inverse_scale(scaled: np.ndarray, spec) -> np.ndarray:
    """Undo the min-max scaling so errors are in kW or W/m^2."""
    cs = spec.scaler.channels[list(spec.scaler.channels)[0]]
    if cs.is_constant:
        return np.full_like(scaled, cs.data_min, dtype=float)
    lo, hi = cs.feature_min, cs.feature_max
    return (scaled - lo) / ((hi - lo) / (cs.data_max - cs.data_min)) + cs.data_min


def horizon_matrix(spec, origins: np.ndarray, values: np.ndarray) -> np.ndarray:
    """(n_origins, horizon) view of any full-length series."""
    cfg = spec.cfg
    h = np.arange(cfg.horizon)
    return values[origins[:, None] + cfg.gap + h[None, :]]


def evaluation_mask(spec, origins: np.ndarray) -> Optional[np.ndarray]:
    """Which (origin, step) cells count. None means all of them."""
    cfg = spec.cfg
    if not cfg.daytime_only:
        return None
    day = daytime_mask(spec.index, SITE_LAT, SITE_LON, SITE_TZ_OFFSET,
                       cfg.daytime_threshold)
    return horizon_matrix(spec, origins, day.astype(bool))


@dataclass
class Metrics:
    name: str
    mae: float
    rmse: float
    mape: Optional[float]
    nmae: float                       # MAE / mean of the truth
    r2: float
    n: int
    n_mape_excluded: int = 0
    per_step_mae: np.ndarray = field(default_factory=lambda: np.empty(0))
    skill: Dict[str, float] = field(default_factory=dict)

    def row(self, unit: str) -> str:
        mape = f"{self.mape:6.2f}%" if self.mape is not None else "     —"
        return (f"  {self.name:<26} {self.mae:9.3f} {self.rmse:9.3f} {mape} "
                f"{self.nmae:8.3f} {self.r2:7.3f}")


def score(truth: np.ndarray, pred: np.ndarray, name: str, cfg,
          mask: Optional[np.ndarray] = None) -> Metrics:
    """Compute every metric on the masked cells, in original units."""
    if mask is None:
        mask = np.ones_like(truth, dtype=bool)
    valid = mask & np.isfinite(truth) & np.isfinite(pred)

    t, p = truth[valid], pred[valid]
    if t.size == 0:
        return Metrics(name, np.nan, np.nan, None, np.nan, np.nan, 0)

    err = p - t
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    denom = float(np.mean(t))
    nmae = mae / denom if denom else np.nan

    big = np.abs(t) >= cfg.mape_floor
    mape = float(np.mean(np.abs(err[big] / t[big])) * 100) if big.any() else None

    ss_res = float(np.sum(err ** 2))
    ss_tot = float(np.sum((t - t.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan

    # Per-step MAE over the horizon, masked the same way.
    step_err = np.where(valid, np.abs(pred - truth), np.nan)
    with np.errstate(invalid="ignore"):
        per_step = np.nanmean(step_err, axis=0)

    return Metrics(name=name, mae=mae, rmse=rmse, mape=mape, nmae=nmae, r2=r2,
                   n=int(t.size), n_mape_excluded=int((~big).sum()),
                   per_step_mae=per_step)


def add_skill(results: List[Metrics], baseline_names: List[str]) -> None:
    """Attach skill scores against every baseline present."""
    lookup = {m.name: m for m in results}
    for m in results:
        for b in baseline_names:
            ref = lookup.get(b)
            if ref is None or not np.isfinite(ref.mae) or ref.mae == 0:
                continue
            m.skill[b] = 1.0 - m.mae / ref.mae


def report(results: List[Metrics], spec, split: str = "test") -> str:
    cfg = spec.cfg
    unit = "kW" if cfg.target == "load" else "W/m²"
    lines = [
        f"FORECAST EVALUATION — {cfg.target}, {split} split",
        "=" * 78,
        f"Horizon:   {cfg.horizon} steps ({cfg.horizon * 5 / 60:.0f} h at 5-minute resolution)",
        f"Windows:   {len(spec.origins[split]):,} rolling origins, every {cfg.stride} steps",
        f"Period:    {spec.index[spec.origins[split][0]]:%Y-%m-%d} → "
        f"{spec.index[spec.origins[split][-1]]:%Y-%m-%d}",
        f"Units:     {unit}  (metrics computed after inverting the scaler)",
    ]
    if cfg.daytime_only:
        m = evaluation_mask(spec, spec.origins[split])
        lines.append(
            f"Mask:      DAYLIGHT ONLY — {m.mean():.1%} of horizon steps scored. "
            "Night GHI is exactly zero and every model gets it right; including it "
            "would roughly halve every error shown below.")
    lines += [
        "",
        f"  {'model':<26} {'MAE':>9} {'RMSE':>9} {'MAPE':>7} {'nMAE':>8} {'R²':>7}",
        "  " + "-" * 70,
    ]
    for m in results:
        lines.append(m.row(unit))

    if any(m.skill for m in results):
        refs = sorted({k for m in results for k in m.skill})
        lines += ["", "SKILL SCORE  (1 - MAE_model / MAE_baseline; positive means better)", ""]
        header = f"  {'model':<26}" + "".join(f"{r:>28}" for r in refs)
        lines += [header, "  " + "-" * (26 + 28 * len(refs))]
        for m in results:
            if m.name in refs:
                continue
            cells = "".join(f"{m.skill.get(r, float('nan')):>28.3f}" for r in refs)
            lines.append(f"  {m.name:<26}{cells}")

    # Skill decay across the horizon is the single most informative plot
    # for a day-ahead model, so its numbers are in the report too.
    best = next((m for m in results if m.per_step_mae.size), None)
    if best is not None and best.per_step_mae.size:
        lines += ["", "ERROR BY LEAD TIME  (MAE, first listed model)", ""]
        marks = [0, 11, 35, 71, 143, 215, 287]
        lines.append("  " + "".join(f"{'+%dh' % round((i + 1) * 5 / 60):>10}"
                                    for i in marks if i < best.per_step_mae.size))
        lines.append("  " + "".join(f"{best.per_step_mae[i]:>10.3f}"
                                    for i in marks if i < best.per_step_mae.size))

    excluded = [m for m in results if m.n_mape_excluded]
    if excluded:
        lines += ["", f"MAPE excludes readings below {cfg.mape_floor:g} {unit} "
                      f"({excluded[0].n_mape_excluded:,} cells) — percentage error is "
                      "unbounded as the denominator approaches zero."]
    return "\n".join(lines)


def per_step_frame(results: List[Metrics], cfg) -> pd.DataFrame:
    out = {"lead_minutes": (np.arange(cfg.horizon) + 1) * 5}
    for m in results:
        if m.per_step_mae.size == cfg.horizon:
            out[m.name] = m.per_step_mae
    return pd.DataFrame(out)
