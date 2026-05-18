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

# ----- scalp_vec_backtest.py lines 196–391 -----
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
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(period - 1, n):
        v = np.nanmax(x[i - period + 1: i + 1])
        if np.isfinite(v):
            out[i] = float(v)
    return out


def rolling_min_arr(x: np.ndarray, period: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(period - 1, n):
        v = np.nanmin(x[i - period + 1: i + 1])
        if np.isfinite(v):
            out[i] = float(v)
    return out


def rolling_highest(close: np.ndarray, period: int) -> np.ndarray:
    """Rolling highest close (Pine ``ta.highest``) — ``rolling_max_arr`` with NaN-safe windows."""
    return rolling_max_arr(close, period)


def rolling_lowest(close: np.ndarray, period: int) -> np.ndarray:
    """Rolling lowest close (Pine ``ta.lowest``) — ``rolling_min_arr`` with NaN-safe windows."""
    return rolling_min_arr(close, period)


def adx_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Wilder ADX. NaN until bar index ``2 * period - 1`` (inclusive) has a value."""
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < 2 * period + 1:
        need_n = 2 * period + 1
        _scalp_vec_bt_diag_warn(
            f"adx_wilder:{n}:{period}",
            f"adx_wilder: series length={n} < minimum {need_n} for ADX period={period} — returning all NaN.",
        )
        return out

    tr = np.zeros(n, dtype=np.float64)
    plus_dm = np.zeros(n, dtype=np.float64)
    minus_dm = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0.0
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    def _wilder_smooth(x: np.ndarray) -> np.ndarray:
        s = np.zeros(n, dtype=np.float64)
        s[period] = float(np.sum(x[1: period + 1]))
        for i in range(period + 1, n):
            s[i] = s[i - 1] - (s[i - 1] / period) + x[i]
        return s

    tr_s = _wilder_smooth(tr)
    p_s = _wilder_smooth(plus_dm)
    m_s = _wilder_smooth(minus_dm)

    pdi = np.zeros(n, dtype=np.float64)
    mdi = np.zeros(n, dtype=np.float64)
    dx = np.zeros(n, dtype=np.float64)
    for i in range(period, n):
        if tr_s[i] > 0:
            pdi[i] = 100.0 * (p_s[i] / tr_s[i])
            mdi[i] = 100.0 * (m_s[i] / tr_s[i])
        denom = pdi[i] + mdi[i]
        if denom > 0:
            dx[i] = 100.0 * abs(pdi[i] - mdi[i]) / denom

    first_adx = 2 * period - 1
    if first_adx >= n:
        return out
    out[first_adx] = float(np.mean(dx[period: 2 * period]))
    for i in range(first_adx + 1, n):
        out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
    return out


def tillson_t3(close: np.ndarray, length: int, vfactor: float) -> np.ndarray:
    """Tillson T3 (triple generalized dema)."""
    e1 = ema(close, length)
    e2 = ema(e1, length)
    e3 = ema(e2, length)
    e4 = ema(e3, length)
    e5 = ema(e4, length)
    e6 = ema(e5, length)
    v = float(vfactor)
    c1 = -(v ** 3)
    c2 = 3 * v * v + 3 * v ** 3
    c3 = -6 * v * v - 3 * v - 3 * v ** 3
    c4 = 1 + 3 * v + v ** 3 + 3 * v * v
    return c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3


def hlc_trend_lines(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    close_period: int,
    low_period: int,
    high_period: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """HLC3-style trend bands: green = rolling max of typical, red = rolling min, mid = rolling mean."""
    hlc3 = (high + low + close) / 3.0
    green = rolling_max_arr(hlc3, max(1, high_period))
    red = rolling_min_arr(hlc3, max(1, low_period))
    mid = rolling_mean_arr(hlc3, max(1, close_period))
    return green, red, mid


def waddah_attar_explosion(
    close: np.ndarray,
    sensitivity: float,
    fast_len: int,
    slow_len: int,
    bb_len: int,
    bb_mult: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Simplified WAE: MACD-style trend × sensitivity + Bollinger on histogram.

    Returns (hist, basis, upper, lower) all length n.
    """
    macd1 = ema(close, fast_len) - ema(close, slow_len)
    # Histogram signal smoothing: empirical clamp (floor 3, cap 21). For ``wae_slow_len > 41``,
    # ``slow_len // 2`` would exceed 21 but ``sig_period`` plateaus — high ``wae_slow_len`` overrides
    # from config/champion do not change WAE behavior past this ceiling unless relaxed in code.
    sig_period = max(3, min(21, slow_len // 2))
    if slow_len > 41:
        _scalp_vec_bt_diag_warn(
            f"wae_sig_plateau:{slow_len}",
            "waddah_attar_explosion: wae_slow_len=%d exceeds plateau band "
            "(sig_period=max(3,min(21,slow_len//2)) sticks at 21 — see strategies.md)."
            % slow_len,
        )
    sig = ema(macd1, sig_period)
    hist = (macd1 - sig) * float(sensitivity)
    basis = rolling_mean_arr(hist, bb_len)
    std = rolling_std_arr(hist, bb_len)
    upper = basis + bb_mult * std
    lower = basis - bb_mult * std
    return hist, basis, upper, lower


def daviddtech_warmup_bars(

# ----- scalp_vec_backtest.py lines 415–491 -----
def detect_signals_daviddtech(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    atr_period: int,
    adx_period: int,
    t3_length: int,
    t3_vfactor: float,
    hlc_close_period: int,
    hlc_low_period: int,
    hlc_high_period: int,
    adx_threshold: float,
    wae_sensitivity: float,
    wae_fast_len: int,
    wae_slow_len: int,
    wae_bb_len: int,
    wae_bb_mult: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """DaviddTech-style confluence. Long and short masks for bidirectional backtest."""
    n = len(close)
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)

    t3 = tillson_t3(close, t3_length, t3_vfactor)
    adx_line = adx_wilder(high, low, close, adx_period)
    hlc_g, hlc_r, hlc_mid = hlc_trend_lines(
        high, low, close, hlc_close_period, hlc_low_period, hlc_high_period,
    )
    hist, _basis, upper, lower = waddah_attar_explosion(
        close, wae_sensitivity, wae_fast_len, wae_slow_len, wae_bb_len, wae_bb_mult,
    )
    atr_vals = atr(high, low, close, atr_period)

    # WAE explosion: histogram must exceed the BB half-bandwidth (bb_mult × std),
    # i.e. momentum is significant relative to the noise floor — not compared to its
    # own BB upper band (which rises with the trend, making it nearly impossible to
    # sustain in a real trend). This matches the standard WAE explosion logic where
    # e1 = (upper - lower) / 2 = bb_mult * std.
    e_band = (upper - lower) / 2.0
    wae_long = (~np.isnan(hist)) & (~np.isnan(e_band)) & (hist > 0) & (hist > e_band)
    wae_short = (~np.isnan(hist)) & (~np.isnan(e_band)) & (hist < 0) & (hist < -e_band)

    base_ok = (
        ~np.isnan(atr_vals)
        & (atr_vals > 0)
        & ~np.isnan(t3)
        & ~np.isnan(adx_line)
        & (adx_line > adx_threshold)
        & ~np.isnan(hlc_g)
        & ~np.isnan(hlc_r)
        & ~np.isnan(hlc_mid)
    )

    long_mask = base_ok & (close > t3) & (hlc_g > hlc_r) & (close > hlc_mid) & wae_long
    short_mask = base_ok & (close < t3) & (hlc_g < hlc_r) & (close < hlc_mid) & wae_short

    warm = daviddtech_warmup_bars(
        atr_period, adx_period, t3_length, hlc_close_period, hlc_low_period, hlc_high_period,
        wae_slow_len, wae_bb_len,
    )
    if warm < n:
        long_mask[:warm] = False
        short_mask[:warm] = False
    else:
        _scalp_vec_bt_diag_warn(
            f"daviddtech_warm_coverage:{warm}:{n}",
            (
                "detect_signals_daviddtech: bar count=%d is less than or equal to warmup prefix=%d "
                "— masks are cleared for the full window."
            )
            % (n, warm),
        )
        long_mask[:] = False
        short_mask[:] = False

    return long_mask, short_mask, atr_vals
