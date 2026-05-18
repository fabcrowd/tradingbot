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

# ----- scalp_vec_backtest.py lines 146–193 -----
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


def wma(close: np.ndarray, period: int) -> np.ndarray:
    """Weighted moving average — linearly weighted, newest bar has highest weight."""
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if period < 1:
        return out
    if period > n:
        _scalp_vec_bt_diag_warn(
            f"wma:{period}:{n}",
            f"wma: period={period} > len(close)={n}; returning all-NaN.",
        )
        return out
    weights = np.arange(1, period + 1, dtype=np.float64)
    wsum = weights.sum()
    for i in range(period - 1, n):
        out[i] = np.dot(close[i - period + 1: i + 1], weights) / wsum
    return out


def session_vwap(timestamp: np.ndarray, high: np.ndarray, low: np.ndarray,
                 close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """Cumulative session VWAP, resetting at midnight UTC each day."""
    typical = (high + low + close) / 3.0
    out = np.empty_like(close, dtype=np.float64)
    cum_pv = 0.0
    cum_v = 0.0
    prev_day = -1
    for i in range(len(close)):
        day = int(timestamp[i]) // 86400
        if day != prev_day:
            cum_pv = 0.0
            cum_v = 0.0

# ----- scalp_vec_backtest.py lines 1507–1623 -----
def detect_signals_squeeze(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    bb_period: int = 20,
    bb_mult: float = 2.0,
    kc_mult: float = 1.5,
    mom_period: int = 12,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Squeeze Momentum (TTM-style): BB inside KC + linear-regression momentum.

    Entry (intentional semantics — matches ``strategies.md`` and Pine export):
      - Prior bar ``squeeze_on`` (BB strictly inside KC).
      - This bar: momentum crosses zero (touch-and-cross on ``mom``).
      - **Does not** require ``not squeeze_on[i]`` — signals may fire while squeeze
        is still on if momentum crosses during compression (broader than strict
        "release bar only").

    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    if not (len(high) == n and len(low) == n):
        raise ValueError(
            f"detect_signals_squeeze: OHLC length mismatch close={n} high={len(high)} low={len(low)}",
        )
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    atr_v = atr(high, low, close, atr_period)
    if mom_period < 2:
        return long_mask, short_mask, atr_v

    kc_mid = ema(close, bb_period)
    sma = rolling_mean_arr(close, bb_period)
    stdev = rolling_std_arr(close, bb_period)
    roll_high = rolling_max_arr(high, bb_period)
    roll_low = rolling_min_arr(low, bb_period)

    # BB/KC envelopes — squeeze is on when BB fits inside KC (low volatility).
    bb_upper = sma + bb_mult * stdev
    bb_lower = sma - bb_mult * stdev
    kc_upper = kc_mid + kc_mult * atr_v
    kc_lower = kc_mid - kc_mult * atr_v

    # Squeeze condition: BB inside KC (strict inequalities; equality is zero-measure).
    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    # Momentum: linear-regression projection to the right edge of the window (TTM formula).
    midpoint = (roll_high + roll_low) / 2.0
    val = close - (midpoint + sma) / 2.0

    x = np.arange(mom_period, dtype=np.float64)
    x -= x.mean()
    xdot = float(np.dot(x, x))  # > 0 for mom_period >= 2
    mom = np.full(n, np.nan)
    for i in range(mom_period - 1, n):
        y = val[i - mom_period + 1: i + 1]
        if np.any(np.isnan(y)):
            continue
        slope = np.dot(x, y) / xdot
        mom[i] = slope * (mom_period - 1) / 2.0 + float(np.mean(y))

    # Entry: momentum cross gated by prior-bar squeeze (see docstring).
    for i in range(1, n):
        if np.isnan(mom[i]) or np.isnan(mom[i - 1]):
            continue
        if not squeeze_on[i - 1]:
            continue
        if mom[i - 1] <= 0 and mom[i] > 0:
            long_mask[i] = True
        elif mom[i - 1] >= 0 and mom[i] < 0:
            short_mask[i] = True

    ok = ~np.isnan(atr_v) & (atr_v > 0)
    long_mask &= ok
    short_mask &= ok
    warmup = vec_warmup_prefix_len(
        "squeeze_momentum",
        SimpleNamespace(
            squeeze_bb_period=bb_period,
            atr_period=atr_period,
            squeeze_mom_period=mom_period,
        ),
    )
    if warmup >= n:
        _scalp_vec_bt_diag_warn(
            f"squeeze:warm:{n}:{warmup}",
            f"detect_signals_squeeze: len(close)={n} < warmup {warmup}; no signals generated.",
        )
        return long_mask, short_mask, atr_v
    long_mask[:warmup] = False
    short_mask[:warmup] = False
    return long_mask, short_mask, atr_v


def squeeze_live_bundle(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    bb_period: int = 20,
    bb_mult: float = 2.0,
    kc_mult: float = 1.5,
    mom_period: int = 12,
    atr_period: int = 14,
) -> dict[str, bool]:
    """Last-bar Squeeze Momentum values for the live indicator engine."""
    if len(close) < 2:
        return {"squeeze_long": False, "squeeze_short": False}
    long_m, short_m, _ = detect_signals_squeeze(
        close, high, low,
        bb_period=bb_period, bb_mult=bb_mult, kc_mult=kc_mult,
        mom_period=mom_period, atr_period=atr_period,
    )
    i = len(close) - 1
    return {"squeeze_long": bool(long_m[i]), "squeeze_short": bool(short_m[i])}
