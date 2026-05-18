# AUTO-GENERATED review excerpt from scalp_vec_backtest.py
# Not intended as a runnable module alone — imports below match typical usage in the full file.
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from indicator_warmup import vec_warmup_prefix_len


# ----- scalp_vec_backtest.py lines 37–54 -----
_SCALP_VEC_BT_DIAG_MAX_KEYS = 64
_scalp_vec_bt_diag_keys: set[str] = set()


def _scalp_vec_bt_diag_enabled() -> bool:
    return os.environ.get("SCALP_VEC_BT_DIAG", "").strip().lower() in ("1", "true", "yes")


def _scalp_vec_bt_diag_warn(fingerprint: str, msg: str) -> None:
    """Throttle LOG.warning behind SCALP_VEC_BT_DIAG — avoids WFO spam."""
    if not _scalp_vec_bt_diag_enabled():
        return
    if fingerprint in _scalp_vec_bt_diag_keys:
        return
    if len(_scalp_vec_bt_diag_keys) >= _SCALP_VEC_BT_DIAG_MAX_KEYS:
        return
    _scalp_vec_bt_diag_keys.add(fingerprint)
    LOG.warning("%s", msg)

# ----- scalp_vec_backtest.py lines 61–139 -----
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

# ----- scalp_vec_backtest.py lines 788–848 -----
def detect_signals_ema(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    timestamp: np.ndarray,
    ema_fast_period: int,
    ema_slow_period: int,
    rsi_period: int,
    atr_period: int,
    vol_ma_period: int,
    vol_mult: float,
    min_signals: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """EMA momentum (**registered mode:** ``ema_momentum`` — kept short historical name).

    Fast/slow EMA cross entries with ATR gate. RSI / VWAP / volume /
    ``min_signals`` remain in the signature for WFO ``ParamSet`` compatibility but **do not**
    gate entries; default ``build_default_grid`` does not sweep them for this mode.
    """
    n = len(close)
    if not (len(high) == n and len(low) == n):
        raise ValueError(
            f"detect_signals_ema: OHLC length mismatch close={n} high={len(high)} low={len(low)}",
        )
    if not (len(volume) == n and len(timestamp) == n):
        raise ValueError(
            f"detect_signals_ema: volume/timestamp length mismatch (expected {n}, "
            f"volume={len(volume)}, timestamp={len(timestamp)})",
        )
    del volume, timestamp, rsi_period, vol_ma_period, vol_mult, min_signals

    ema_f = ema(close, ema_fast_period)
    ema_s = ema(close, ema_slow_period)
    atr_vals = atr(high, low, close, atr_period)

    valid_pair = ~np.isnan(ema_f) & ~np.isnan(ema_s)
    prev_valid = np.zeros(n, dtype=bool)
    prev_valid[1:] = valid_pair[:-1]

    ema_bullish = (ema_f > ema_s) & valid_pair
    ema_bearish = (ema_f < ema_s) & valid_pair
    ema_cross_up = _rising_edge(ema_bullish)
    ema_cross_down = _rising_edge(ema_bearish)
    ema_cross_up &= prev_valid
    ema_cross_down &= prev_valid

    ok_atr = ~np.isnan(atr_vals) & (atr_vals > 0)
    long_mask = ema_cross_up & ok_atr
    short_mask = ema_cross_down & ok_atr

    warmup = vec_warmup_prefix_len(
        "ema_momentum",
        SimpleNamespace(ema_slow=ema_slow_period, atr_period=atr_period),
    )
    long_mask[:warmup] = False
    short_mask[:warmup] = False

    return long_mask, short_mask, atr_vals


