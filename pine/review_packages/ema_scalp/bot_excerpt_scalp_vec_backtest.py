# AUTO-GENERATED review excerpt from scalp_vec_backtest.py
# Not intended as a runnable module alone — imports below match typical usage in the full file.
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from indicator_warmup import vec_warmup_prefix_len


# ----- scalp_vec_backtest.py lines 61–83 -----
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

# ----- scalp_vec_backtest.py lines 86–96 -----
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

# ----- scalp_vec_backtest.py lines 123–239 -----
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
            prev_day = day
        cum_pv += typical[i] * volume[i]
        cum_v += volume[i]
        out[i] = cum_pv / cum_v if cum_v > 0 else close[i]
    return out


def volume_ma(volume: np.ndarray, period: int) -> np.ndarray:
    """Simple rolling mean of volume."""
    kernel = np.ones(period, dtype=np.float64) / period
    out = np.convolve(volume, kernel, mode="full")[:len(volume)]
    out[:period - 1] = np.nan
    return out


def rolling_mean_arr(x: np.ndarray, period: int) -> np.ndarray:
    """Rolling mean over each window using finite samples only (no all-NaN nanmean warnings)."""
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if period <= 0 or n < period:
        return out
    for i in range(period - 1, n):
        sl = x[i - period + 1 : i + 1]
        fin = sl[np.isfinite(sl)]
        if fin.size == 0:
            continue
        out[i] = float(np.mean(fin))
    return out


def rolling_std_arr(x: np.ndarray, period: int) -> np.ndarray:
    """Rolling population std; finite samples only (avoids nanstd DOF / empty-slice warnings)."""
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if period <= 1 or n < period:
        return out
    for i in range(period - 1, n):
        sl = x[i - period + 1 : i + 1]
        fin = sl[np.isfinite(sl)]
        if fin.size < 2:
            continue
        out[i] = float(np.std(fin, ddof=0))
    return out


def rolling_max_arr(x: np.ndarray, period: int) -> np.ndarray:

# ----- scalp_vec_backtest.py lines 1108–1168 -----
def detect_signals_ema_scalp(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    ema_period: int,
    atr_period: int,
    sr_bars: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Tony's EMA Scalper (**registered mode:** ``ema_scalp``).

    Entries: price crosses the EMA with bar-direction confirmation (strict ``>`` / ``<`` vs
    prior close — matches Pine / live ``SignalEngine``). ``high_n`` / ``low_n`` are rolling
    S/R levels from close; they **do not** veto longs near resistance or shorts near support
    (same as Pine ``hh``/``ll`` validity checks). They gate entries only once the S/R window is
    finite and are returned for live stop/TP (``high_8`` / ``low_8``) and legacy sim paths.

    WFO ``evaluate_params`` uses ``simulate_trades_bidir`` (generic ATR stop/TP), not S/R exits.
    """
    n = len(close)
    if not (len(high) == n and len(low) == n):
        raise ValueError(
            f"detect_signals_ema_scalp: OHLC length mismatch close={n} high={len(high)} low={len(low)}",
        )

    ema_vals = ema(close, ema_period)
    atr_vals = atr(high, low, close, atr_period)
    high_n = rolling_highest(close, sr_bars)
    low_n = rolling_lowest(close, sr_bars)

    ema_ok = ~np.isnan(ema_vals)
    pair_ok = np.zeros(n, dtype=bool)
    pair_ok[1:] = ema_ok[1:] & ema_ok[:-1]

    above = (close > ema_vals) & ema_ok
    below = (close < ema_vals) & ema_ok
    # Strict bar-direction filter (equal close vs prior bar = no confirmation).
    close_up = np.zeros(n, dtype=bool)
    close_up[1:] = close[1:] > close[:-1]
    close_dn = np.zeros(n, dtype=bool)
    close_dn[1:] = close[1:] < close[:-1]

    long_mask = _rising_edge(above) & pair_ok & close_up
    short_mask = _rising_edge(below) & pair_ok & close_dn

    ok = ~np.isnan(atr_vals) & (atr_vals > 0) & ~np.isnan(high_n) & ~np.isnan(low_n)
    long_mask &= ok
    short_mask &= ok
    warmup = vec_warmup_prefix_len(
        "ema_scalp",
        SimpleNamespace(
            ema_scalp_period=ema_period,
            atr_period=atr_period,
            ema_scalp_sr_bars=sr_bars,
        ),
    )
    long_mask[:warmup] = False
    short_mask[:warmup] = False

    return long_mask, short_mask, atr_vals, high_n, low_n


