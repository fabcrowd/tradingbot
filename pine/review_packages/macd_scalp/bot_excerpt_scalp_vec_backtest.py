# AUTO-GENERATED review excerpt from scalp_vec_backtest.py
# Not intended as a runnable module alone — imports below match typical usage in the full file.
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from indicator_warmup import vec_warmup_prefix_len


# ----- scalp_vec_backtest.py lines 61–159 -----
def ema(close: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average. NaN-safe: skips leading NaNs before seeding."""
    out = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period:
        return out
    alpha = 2.0 / (period + 1)
    start = 0
    while start < len(close) and np.isnan(close[start]):
        start += 1
    if start + period > len(close):
        return out
    seed = np.nanmean(close[start : start + period])
    if np.isnan(seed):
        return out
    idx = start + period - 1
    out[idx] = seed
    for i in range(idx + 1, len(close)):
        v = close[i]
        if np.isnan(v):
            out[i] = out[i - 1]
        else:
            out[i] = alpha * v + (1 - alpha) * out[i - 1]
    return out


def _rising_edge(mask: np.ndarray) -> np.ndarray:
    """True at ``i`` when ``mask[i]`` is True and ``mask[i-1]`` was False.

    ``mask`` must be **boolean**. Integer 0/1 arrays are unsafe — ``~`` breaks for ints.
    """
    n = int(mask.shape[0])
    out = np.zeros(n, dtype=bool)
    if n <= 1:
        return out
    out[1:] = mask[1:] & (~mask[:-1])
    return out


def _touch_crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossover(a, b)``: ``a[i-1] <= b[i-1]`` and ``a[i] > b[i]``."""
    n = len(a)
    out = np.zeros(n, dtype=bool)
    if n <= 1:
        return out
    out[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
    return out


def _touch_crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossunder(a, b)``: ``a[i-1] >= b[i-1]`` and ``a[i] < b[i]``."""
    n = len(a)
    out = np.zeros(n, dtype=bool)
    if n <= 1:
        return out
    out[1:] = (a[:-1] >= b[:-1]) & (a[1:] < b[1:])
    return out


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

# ----- scalp_vec_backtest.py lines 99–116 -----
def _touch_crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossover(a, b)``: ``a[i-1] <= b[i-1]`` and ``a[i] > b[i]``."""
    n = len(a)
    out = np.zeros(n, dtype=bool)
    if n <= 1:
        return out
    out[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
    return out


def _touch_crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossunder(a, b)``: ``a[i-1] >= b[i-1]`` and ``a[i] < b[i]``."""
    n = len(a)
    out = np.zeros(n, dtype=bool)
    if n <= 1:
        return out
    out[1:] = (a[:-1] >= b[:-1]) & (a[1:] < b[1:])
    return out

# ----- scalp_vec_backtest.py lines 1279–1346 -----
def super_smooth(data: np.ndarray, period: int) -> np.ndarray:
    """Ehlers 2-pole super-smoother filter (recursive IIR)."""
    n = len(data)
    if period < 1 or n < 1:
        return np.full(n, np.nan, dtype=np.float64)
    f = (1.4142135623730951 * math.pi) / period
    a = math.exp(-f)
    c2 = 2.0 * a * math.cos(f)
    c3 = -(a * a)
    c1 = 1.0 - c2 - c3
    out = np.empty(n, dtype=np.float64)
    out[0] = data[0]
    if n > 1:
        out[1] = c1 * (data[1] + data[0]) * 0.5 + c2 * out[0]
    for i in range(2, n):
        out[i] = c1 * (data[i] + data[i - 1]) * 0.5 + c2 * out[i - 1] + c3 * out[i - 2]
    return out


def detect_signals_macd(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    fast_len: int,
    slow_len: int,
    signal_len: int,
    atr_period: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Scalp Pro MACD: bull and bear super-smoother crossovers (perps).

    ``* 1e7`` on the MACD line matches Pine ``TradingBotScalp``; inert for crossover
    bars (sign-only) but keeps plotted / returned magnitudes aligned with TV.

    Returns ``(long_mask, short_mask, atr_vals, macd_line, macd_signal_line)``.
    ``evaluate_params`` discards the MACD series (masks only); live paths use ATR brackets.
    """
    n = len(close)
    if not (len(high) == n and len(low) == n):
        raise ValueError(
            f"detect_signals_macd: OHLC length mismatch close={n} high={len(high)} low={len(low)}",
        )

    ss_fast = super_smooth(close, fast_len)
    ss_slow = super_smooth(close, slow_len)
    macd_line = (ss_fast - ss_slow) * 1e7
    macd_signal_line = super_smooth(macd_line, signal_len)
    atr_vals = atr(high, low, close, atr_period)

    # Touch-and-cross (Pine ta.crossover / ta.crossunder); mutual exclusion is mathematical.
    long_mask = _touch_crossover(macd_line, macd_signal_line)
    short_mask = _touch_crossunder(macd_line, macd_signal_line)

    ok = ~np.isnan(atr_vals) & (atr_vals > 0)
    long_mask &= ok
    short_mask &= ok
    warmup = vec_warmup_prefix_len(
        "macd_scalp",
        SimpleNamespace(
            macd_fast_len=fast_len,
            macd_slow_len=slow_len,
            macd_signal_len=signal_len,
            atr_period=atr_period,
        ),
    )
    long_mask[:warmup] = False
    short_mask[:warmup] = False

    return long_mask, short_mask, atr_vals, macd_line, macd_signal_line
