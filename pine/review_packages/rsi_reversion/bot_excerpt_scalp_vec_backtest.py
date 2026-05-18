# AUTO-GENERATED review excerpt from scalp_vec_backtest.py
# Not intended as a runnable module alone — imports below match typical usage in the full file.
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from indicator_warmup import vec_warmup_prefix_len


# ----- scalp_vec_backtest.py lines 119–159 -----
def rsi(close: np.ndarray, period: int) -> np.ndarray:
    """Wilder RSI. First `period` values are NaN."""
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    out = np.full(len(close), np.nan, dtype=np.float64)
    avg_gain = np.mean(gain[:period])
    avg_loss = np.mean(loss[:period])
    if avg_loss == 0:
        out[period] = 100.0
    else:
        out[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period
        if avg_loss == 0:
            out[i + 1] = 100.0
        else:
            out[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Average True Range. First `period` values are NaN."""
    out = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period:
        return out
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    tr = np.concatenate([[high[0] - low[0]], tr])
    out[period - 1] = np.mean(tr[:period])
    for i in range(period, len(close)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out

# ----- scalp_vec_backtest.py lines 853–886 -----
def detect_signals_rsi(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    rsi_period: int,
    atr_period: int,
    rsi_buy_threshold: float,
    rsi_sell_threshold: float,
    *,
    rsi_short_threshold: float = 70.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """RSI mean-reversion: long on oversold, short on overbought (perps).

    Returns (long_mask, short_mask, atr_vals, rsi_vals).
    rsi_sell_threshold is used by the long-exit simulator (RSI recovery).
    rsi_short_threshold gates short entries (default 70 = overbought).
    """
    rsi_vals = rsi(close, rsi_period)
    atr_vals = atr(high, low, close, atr_period)

    long_mask = (~np.isnan(rsi_vals)) & (rsi_vals <= rsi_buy_threshold)
    long_mask &= ~np.isnan(atr_vals) & (atr_vals > 0)

    short_mask = (~np.isnan(rsi_vals)) & (rsi_vals >= rsi_short_threshold)
    short_mask &= ~np.isnan(atr_vals) & (atr_vals > 0)

    warmup = vec_warmup_prefix_len(
        "rsi_reversion",
        SimpleNamespace(rsi_period=rsi_period, atr_period=atr_period),
    )
    long_mask[:warmup] = False
    short_mask[:warmup] = False

    return long_mask, short_mask, atr_vals, rsi_vals
