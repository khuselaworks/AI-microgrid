"""
The hybrid architectures.

Both models share the same shape, which is what makes the comparison in
`evaluate.py` fair: an **encoder** that reads the recent history, and a
**decoder** that maps the encoded state plus the known future covariates
onto every step of the horizon at once.

Why an encoder-decoder rather than a plain sequence-to-vector net: a
day-ahead forecast has information available about the future that is
not in the past. Time of day, day of week and - for solar - the sun's
position at every step of tomorrow are all known exactly at forecast
time. Feeding them into the decoder lets the model say "this step is
03:00 on a Sunday in winter" rather than having to infer it from the
history. Bolting them onto the encoder input instead wastes them, since
the encoder only sees the past.

CNNLSTM  - convolutions extract local shape (ramps, peaks, the morning
           rise) at a stride the recurrence then reads at lower rate.
           This is the "hybrid" of the hybrid CNN-LSTM literature.
TCNModel - dilated causal convolutions cover the same receptive field
           without recurrence. Trains faster and parallelises; included
           so the comparison is against something other than itself.

Direct multi-output, not autoregressive rollout: a 288-step rollout
compounds its own error and is ~288x slower to train. Predicting the
whole horizon in one shot is standard for day-ahead work.
"""
from __future__ import annotations

from typing import Sequence

import torch
import torch.nn as nn


class HorizonDecoder(nn.Module):
    """
    Shared decoder: (context, future covariates) -> one value per step.

    The same small MLP is applied at every horizon step, with that step's
    own covariates. Weight sharing keeps the parameter count independent
    of the horizon - a dense 288-way head over a 64-unit context would be
    most of the model's parameters and would learn each step in isolation.

    `n_outputs` is the number of quantile levels the model predicts. With
    one output the head returns (B, H) as before; with several it returns
    (B, H, n_outputs), one column per quantile.
    """

    def __init__(self, context_dim: int, future_dim: int, hidden: int, dropout: float,
                 n_outputs: int = 1):
        super().__init__()
        self.n_outputs = n_outputs
        self.net = nn.Sequential(
            nn.Linear(context_dim + future_dim, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Linear(hidden // 2, n_outputs),
        )

    def forward(self, context: torch.Tensor, future: torch.Tensor) -> torch.Tensor:
        # context (B, C) -> (B, H, C) so every step sees the same summary
        horizon = future.shape[1]
        ctx = context.unsqueeze(1).expand(-1, horizon, -1)
        out = self.net(torch.cat([ctx, future], dim=-1))     # (B, H, n_outputs)
        return out.squeeze(-1) if self.n_outputs == 1 else out


class CNNLSTM(nn.Module):
    """Convolutional feature extraction, then an LSTM, then the decoder."""

    def __init__(self, past_dim: int, future_dim: int,
                 conv_channels: Sequence[int] = (32, 32), kernel_size: int = 5,
                 hidden: int = 64, decoder_hidden: int = 64, dropout: float = 0.1,
                 n_outputs: int = 1):
        super().__init__()
        layers = []
        in_ch = past_dim
        for out_ch in conv_channels:
            layers += [
                nn.Conv1d(in_ch, out_ch, kernel_size, padding=kernel_size // 2),
                nn.BatchNorm1d(out_ch),
                nn.ReLU(),
                # Halving the sequence each block keeps the LSTM's job
                # short: 576 raw steps become 144, which is far easier to
                # carry state across than the full sequence.
                nn.MaxPool1d(2),
            ]
            in_ch = out_ch
        self.conv = nn.Sequential(*layers)
        self.lstm = nn.LSTM(in_ch, hidden, batch_first=True)
        self.dropout = nn.Dropout(dropout)
        self.decoder = HorizonDecoder(hidden, future_dim, decoder_hidden, dropout,
                                      n_outputs=n_outputs)

    def forward(self, past: torch.Tensor, future: torch.Tensor) -> torch.Tensor:
        # past (B, L, C) -> Conv1d wants (B, C, L)
        h = self.conv(past.transpose(1, 2)).transpose(1, 2)
        _, (hn, _) = self.lstm(h)
        return self.decoder(self.dropout(hn[-1]), future)


class _TemporalBlock(nn.Module):
    """One dilated causal residual block."""

    def __init__(self, in_ch: int, out_ch: int, kernel_size: int, dilation: int,
                 dropout: float):
        super().__init__()
        self.pad = (kernel_size - 1) * dilation
        self.conv1 = nn.Conv1d(in_ch, out_ch, kernel_size, dilation=dilation)
        self.conv2 = nn.Conv1d(out_ch, out_ch, kernel_size, dilation=dilation)
        self.relu = nn.ReLU()
        self.drop = nn.Dropout(dropout)
        self.down = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else None

    def _causal(self, x, conv):
        # Left-pad only: a step must never see its own future, which is
        # what "causal" buys and what symmetric padding would destroy.
        return conv(nn.functional.pad(x, (self.pad, 0)))

    def forward(self, x):
        y = self.drop(self.relu(self._causal(x, self.conv1)))
        y = self.drop(self.relu(self._causal(y, self.conv2)))
        res = x if self.down is None else self.down(x)
        return self.relu(y + res)


class TCNModel(nn.Module):
    """Dilated causal convolutions instead of recurrence."""

    def __init__(self, past_dim: int, future_dim: int,
                 conv_channels: Sequence[int] = (32, 32), kernel_size: int = 5,
                 hidden: int = 64, decoder_hidden: int = 64, dropout: float = 0.1,
                 n_outputs: int = 1):
        super().__init__()
        blocks, in_ch = [], past_dim
        for i, out_ch in enumerate(conv_channels):
            blocks.append(_TemporalBlock(in_ch, out_ch, kernel_size, 2 ** i, dropout))
            in_ch = out_ch
        self.tcn = nn.Sequential(*blocks)
        self.project = nn.Linear(in_ch, hidden)
        self.decoder = HorizonDecoder(hidden, future_dim, decoder_hidden, dropout,
                                      n_outputs=n_outputs)

    def forward(self, past: torch.Tensor, future: torch.Tensor) -> torch.Tensor:
        h = self.tcn(past.transpose(1, 2))
        context = self.project(h[:, :, -1])     # the most recent step's state
        return self.decoder(context, future)


def build_model(cfg, past_dim: int, future_dim: int) -> nn.Module:
    kinds = {"cnn_lstm": CNNLSTM, "tcn": TCNModel}
    if cfg.model not in kinds:
        raise ValueError(f"Unknown model {cfg.model!r}; expected one of {sorted(kinds)}")
    return kinds[cfg.model](
        past_dim=past_dim, future_dim=future_dim,
        conv_channels=tuple(cfg.conv_channels), kernel_size=cfg.kernel_size,
        hidden=cfg.hidden, decoder_hidden=cfg.decoder_hidden, dropout=cfg.dropout,
        n_outputs=cfg.n_quantiles)


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)
