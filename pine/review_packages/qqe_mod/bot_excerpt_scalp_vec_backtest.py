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

# ----- scalp_vec_backtest.py lines 1630–1730 -----
def detect_signals_qqe(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    rsi_period: int = 14,
    qqe_factor: float = 4.238,
    qqe_smoothing: int = 5,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """QQE Mod: Wilder-smoothed RSI with a dynamic ATR-derived trailing level.

    Long: ``_touch_crossover(smooth_rsi, trail)`` and smooth_rsi > 50.
    Short: ``_touch_crossunder(smooth_rsi, trail)`` and smooth_rsi < 50.

    Trail seeds at ``wilders_period`` with ``smooth_rsi[start]`` (canonical QQE Mod /
    Pine ``trail := sm`` at first valid bar). ``abs_diff`` uses ``prepend=np.nan`` so
    the Wilder seed is not biased by a synthetic zero at bar 0.

    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    if not (len(high) == n and len(low) == n):
        raise ValueError(
            f"detect_signals_qqe: OHLC length mismatch close={n} high={len(high)} low={len(low)}",
        )
    atr_v = atr(high, low, close, atr_period)
    rsi_v = rsi(close, rsi_period)
    smooth_rsi = ema(rsi_v, qqe_smoothing)

    # Wilder-smoothed ATR of the smoothed RSI
    wilders_period = rsi_period * 2 - 1
    abs_diff = np.abs(np.diff(smooth_rsi, prepend=np.nan))
    atr_rsi = np.full(n, np.nan)
    if wilders_period < n:
        seed = np.nanmean(abs_diff[:wilders_period])
        if np.isfinite(seed):
            atr_rsi[wilders_period - 1] = seed
            for i in range(wilders_period, n):
                if np.isnan(atr_rsi[i - 1]):
                    continue
                atr_rsi[i] = (atr_rsi[i - 1] * (wilders_period - 1) + abs_diff[i]) / wilders_period

    qqe_dn = smooth_rsi - qqe_factor * atr_rsi   # lower band (bull trail)
    qqe_up = smooth_rsi + qqe_factor * atr_rsi   # upper band (bear trail)

    # Trailing stop (seed = smooth RSI at first valid bar — matches Pine qqe_mod block)
    trail = np.full(n, np.nan)
    start = wilders_period
    if start < n and np.isfinite(smooth_rsi[start]):
        trail[start] = smooth_rsi[start]
    for i in range(start + 1, n):
        if np.isnan(trail[i - 1]) or np.isnan(smooth_rsi[i]) or np.isnan(qqe_dn[i]):
            if not np.isnan(trail[i - 1]):
                trail[i] = trail[i - 1]
            continue
        sr = smooth_rsi[i]
        prev_trail = trail[i - 1]
        if sr > prev_trail:
            trail[i] = max(prev_trail, qqe_dn[i])
        else:
            trail[i] = min(prev_trail, qqe_up[i])

    cross_up = _touch_crossover(smooth_rsi, trail)
    cross_dn = _touch_crossunder(smooth_rsi, trail)
    long_mask = cross_up & (smooth_rsi > 50.0)
    short_mask = cross_dn & (smooth_rsi < 50.0)

    ok = ~np.isnan(atr_v) & (atr_v > 0)
    long_mask &= ok
    short_mask &= ok
    warmup = vec_warmup_prefix_len(
        "qqe_mod",
        SimpleNamespace(
            qqe_rsi_period=rsi_period,
            qqe_smoothing=qqe_smoothing,
            atr_period=atr_period,
        ),
    )
    if warmup >= n:
        _scalp_vec_bt_diag_warn(
            f"qqe_mod:warm:{n}:{warmup}",
            f"detect_signals_qqe: len(close)={n} < warmup {warmup}; no signals generated.",
        )
        return long_mask, short_mask, atr_v
    long_mask[:warmup] = False
    short_mask[:warmup] = False
    return long_mask, short_mask, atr_v


def qqe_live_bundle(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    rsi_period: int = 14,
    qqe_factor: float = 4.238,
    qqe_smoothing: int = 5,
    atr_period: int = 14,
) -> dict[str, bool]:
    """Last-bar QQE Mod values for the live indicator engine."""
