"""
Training loop: deterministic, early-stopped, and checkpointed on the
validation split it never trains on.

Two details that decide whether the numbers mean anything:

* **The best checkpoint is chosen on validation, and the test split is
  touched exactly once**, at the end. Selecting on test - even just
  picking the best epoch - turns the test score into a training score.

* **Shuffling happens over WINDOW ORIGINS, not over time.** The windows
  themselves are still built from contiguous history and the splits are
  chronological, so shuffling here only decorrelates gradient batches;
  it does not mix the future into the past.

Seeding covers Python, NumPy and torch, and batch shuffling is seeded
too, so a run is reproducible from the config alone.

* **Windows are gathered on the device, not in Python.** The whole series
  is three small tensors (a few MB); a batch is one indexed gather over
  precomputed offsets. The per-item Dataset this replaced spent ~110 ms a
  batch slicing and concatenating NumPy arrays 64 times in Python, which
  dwarfed the forward and backward passes of a 100k-parameter model.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import torch

from config import ForecastConfig
from models import build_model, count_parameters


def pinball_loss(pred: torch.Tensor, target: torch.Tensor,
                 quantiles: torch.Tensor) -> torch.Tensor:
    """
    Mean pinball (quantile) loss over a set of quantile levels.

        pred      (B, H, Q)  one value per quantile level, per horizon step
        target    (B, H)
        quantiles (Q,)

    For level q the error e = y - y_hat is charged at q when the forecast
    is too low (e > 0) and at 1 - q when it is too high (e < 0).
    Minimising it drives y_hat to the q-th conditional quantile of the
    target. At q = 0.5 the two rates are equal, so the term is exactly
    0.5 * |e| - half the L1 loss.
    """
    err = target.unsqueeze(-1) - pred                       # (B, H, Q)
    q = quantiles.view(1, 1, -1)
    return torch.maximum(q * err, (q - 1.0) * err).mean()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(False)   # CPU convs have no nondeterminism here


def resolve_device(cfg: ForecastConfig) -> torch.device:
    """
    Where to train. "auto" prefers Apple's Metal backend when it is
    present, which is the only accelerator on an M-series Mac.

    MPS is not always the faster choice: this model is small and its LSTM
    kernels are not as well optimised as the convolutions, so `--device
    cpu` can win. The config records whichever was used, so a run always
    says where its numbers came from.
    """
    name = getattr(cfg, "device", "auto")
    if name and name != "auto":
        return torch.device(name)
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


class WindowBatcher:
    """
    Vectorised window gathering: the series lives on the device, a batch
    is one gather.

    Every window is a contiguous run of indices from its origin, so a
    whole batch of them is `origins[:, None] + offsets` - arithmetic that
    costs nothing next to the Python-level slicing and per-item tensor
    construction it replaces. Nothing about the windowing changes; the
    same origins produce the same windows.
    """

    def __init__(self, spec, split: str, cfg: ForecastConfig, device: torch.device):
        self.cfg, self.device = cfg, device
        # torch.tensor copies; the source arrays are a few MB and some are
        # read-only views, which as_tensor would alias with a warning.
        to = lambda a, dt=torch.float32: torch.tensor(np.asarray(a), dtype=dt,
                                                      device=device)
        self.origins = to(spec.origins[split], torch.long)
        self.values = to(spec.values)
        self.past_extra = to(spec.past_extra)
        self.future = to(spec.future)
        self.baseline = to(spec.baseline) if spec.baseline is not None else None
        self.past_off = torch.arange(-cfg.lookback, 0, device=device)
        self.future_off = cfg.gap + torch.arange(cfg.horizon, device=device)
        self.has_past_extra = self.past_extra.ndim == 2 and self.past_extra.shape[1] > 0

    def __len__(self) -> int:
        return int(self.origins.numel())

    def batches(self, shuffle: bool, generator: Optional[torch.Generator] = None):
        n = len(self)
        if shuffle:
            order = torch.randperm(n, generator=generator).to(self.device)
        else:
            order = torch.arange(n, device=self.device)
        for start in range(0, n, self.cfg.batch_size):
            o = self.origins[order[start:start + self.cfg.batch_size]]   # (B,)
            past_idx = o[:, None] + self.past_off                        # (B, L)
            future_idx = o[:, None] + self.future_off                    # (B, H)

            past = self.values[past_idx].unsqueeze(-1)                   # (B, L, 1)
            if self.has_past_extra:
                past = torch.cat([past, self.past_extra[past_idx]], dim=-1)
            future = self.future[future_idx]                             # (B, H, F)
            target = self.values[future_idx]                             # (B, H)
            if self.baseline is not None:
                target = target - self.baseline[future_idx]
            yield past, future, target


@dataclass
class History:
    train_loss: List[float] = field(default_factory=list)
    val_loss: List[float] = field(default_factory=list)
    epoch_seconds: List[float] = field(default_factory=list)
    best_epoch: int = -1
    best_val: float = float("inf")
    stopped_early: bool = False

    device: str = "cpu"

    def describe(self) -> str:
        lines = ["TRAINING", f"  epochs run:      {len(self.train_loss)}",
                 f"  best epoch:      {self.best_epoch + 1} "
                 f"(val loss {self.best_val:.6f})",
                 f"  early stopped:   {'yes' if self.stopped_early else 'no'}",
                 f"  device:          {self.device}",
                 f"  time:            {sum(self.epoch_seconds):.0f} s total, "
                 f"{np.mean(self.epoch_seconds):.1f} s/epoch"]
        return "\n".join(lines)


def train_model(spec, cfg: ForecastConfig, verbose: bool = True):
    set_seed(cfg.seed)
    # The M1/M2 Air has 4 performance cores; asking for more threads than
    # that costs more in contention than it buys.
    torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
    device = resolve_device(cfg)

    model = build_model(cfg, spec.n_past_features, spec.n_future_features).to(device)
    optimiser = torch.optim.Adam(model.parameters(), lr=cfg.lr,
                                 weight_decay=cfg.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimiser, mode="min", factor=0.5, patience=max(1, cfg.patience // 2))
    # L1 rather than MSE: load and irradiance both have heavy-tailed
    # errors, and MSE would let a handful of ramp events dominate the
    # gradient. It also matches MAE, the metric being reported.
    #
    # With more than one quantile the objective becomes the pinball loss,
    # which generalises L1 (it IS L1/2 at q=0.5) and additionally fits the
    # outer quantiles that form the predictive interval.
    if cfg.n_quantiles == 1:
        criterion = torch.nn.L1Loss()
    else:
        _q = torch.tensor(cfg.quantiles, dtype=torch.float32, device=device)
        criterion = lambda pred, target: pinball_loss(pred, target, _q)

    train_batches = WindowBatcher(spec, "train", cfg, device)
    val_batches = WindowBatcher(spec, "val", cfg, device)
    generator = torch.Generator()
    generator.manual_seed(cfg.seed)

    history = History(device=str(device))
    best_state: Optional[Dict[str, torch.Tensor]] = None
    since_improved = 0

    if verbose:
        print(f"  model {cfg.model} · {count_parameters(model):,} parameters · "
              f"{len(train_batches):,} train / {len(val_batches):,} val windows · "
              f"device {device}")

    for epoch in range(cfg.epochs):
        t0 = time.time()
        model.train()
        total, seen = 0.0, 0
        for past, future, target in train_batches.batches(shuffle=True,
                                                          generator=generator):
            optimiser.zero_grad(set_to_none=True)
            loss = criterion(model(past, future), target)
            loss.backward()
            if cfg.grad_clip:
                torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
            optimiser.step()
            # Accumulate on-device; one sync per epoch instead of per batch.
            total += float(loss.detach()) * len(target)
            seen += len(target)
        train_loss = total / max(seen, 1)

        model.eval()
        total, seen = 0.0, 0
        with torch.no_grad():
            for past, future, target in val_batches.batches(shuffle=False):
                total += float(criterion(model(past, future), target)) * len(target)
                seen += len(target)
        val_loss = total / max(seen, 1)

        scheduler.step(val_loss)
        history.train_loss.append(train_loss)
        history.val_loss.append(val_loss)
        history.epoch_seconds.append(time.time() - t0)

        improved = val_loss < history.best_val - 1e-6
        if improved:
            history.best_val = val_loss
            history.best_epoch = epoch
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            since_improved = 0
        else:
            since_improved += 1

        if verbose:
            print(f"  epoch {epoch + 1:>3}/{cfg.epochs}  train {train_loss:.5f}  "
                  f"val {val_loss:.5f}  {history.epoch_seconds[-1]:.1f}s"
                  + ("  *" if improved else ""))

        if since_improved >= cfg.patience:
            history.stopped_early = True
            if verbose:
                print(f"  early stop: no improvement for {cfg.patience} epochs")
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, history


@torch.no_grad()
def predict(model, spec, split: str, cfg: ForecastConfig,
            quantile: Optional[float] = None) -> np.ndarray:
    """
    Scaled predictions, one row per origin.

    When the model predicts several quantiles it emits (N, H, Q); this
    collapses that to (N, H) by selecting one level - the median (the
    point forecast) by default, or `quantile` if given. Use
    `predict_quantiles` to keep every level.
    """
    model.eval()
    device = next(model.parameters()).device
    out = []
    for past, future, _ in WindowBatcher(spec, split, cfg, device).batches(shuffle=False):
        out.append(model(past, future).cpu().numpy())
    pred = np.concatenate(out, axis=0) if out else np.empty((0, cfg.horizon))
    if pred.ndim == 3:
        qi = cfg.median_index if quantile is None else cfg.quantiles.index(round(quantile, 6))
        pred = pred[:, :, qi]
    if spec.baseline is not None and pred.size:
        # The model produced a correction; add the rule back to get a level.
        origins = spec.origins[split]
        h = np.arange(cfg.horizon)
        pred = pred + spec.baseline[origins[:, None] + cfg.gap + h[None, :]]
    return pred


@torch.no_grad()
def predict_quantiles(model, spec, split: str, cfg: ForecastConfig) -> np.ndarray:
    """
    Every quantile level kept: (N, H, Q), columns ordered as cfg.quantiles.

    Quantile crossing is possible in principle (the heads are independent
    linear maps); the columns are sorted along Q here so an interval edge
    is never inside the median. Stage 2 will address why it happens.
    """
    model.eval()
    device = next(model.parameters()).device
    out = []
    for past, future, _ in WindowBatcher(spec, split, cfg, device).batches(shuffle=False):
        pred = model(past, future).cpu().numpy()
        if pred.ndim == 2:                       # single-quantile model
            pred = pred[:, :, None]
        out.append(pred)
    q = cfg.n_quantiles
    pred = np.concatenate(out, axis=0) if out else np.empty((0, cfg.horizon, q))
    if spec.baseline is not None and pred.size:
        origins = spec.origins[split]
        h = np.arange(cfg.horizon)
        ref = spec.baseline[origins[:, None] + cfg.gap + h[None, :]]
        pred = pred + ref[:, :, None]
    return np.sort(pred, axis=2)


def save_checkpoint(model, cfg: ForecastConfig, spec, path: Path) -> Path:
    """Weights plus everything needed to use them on new data."""
    torch.save({
        "state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
        "config": {k: (list(v) if isinstance(v, tuple) else v)
                   for k, v in cfg.__dict__.items()},
        "past_dim": spec.n_past_features,
        "future_dim": spec.n_future_features,
        "scaler": spec.scaler.to_dict(),
    }, path)
    return path
