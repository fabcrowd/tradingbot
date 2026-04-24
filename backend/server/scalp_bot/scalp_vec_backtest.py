"""Vectorized bar-level scalp backtester — evaluates parameter combos in bulk.

Each ``ParamSet.mode`` is a **named strategy** (full entry/exit simulation), not a loose
indicator pick. WFO / champion selection scores **round-trip trades** only; ``win_rate``
in ``compute_metrics`` is the fraction of ``TradeResult`` with net PnL > 0 after fees
(never "indicator pointed the right way without a trade").

Registered strategy modes (must match ``evaluate_params`` branches and ``SignalEngine``):
  - daviddtech_scalp — T3 + HLC + WAE + ADX bundle (vector long+short)
  - ema_momentum — dual EMA cross + volume confluence + ATR stops/TP
  - rsi_reversion — RSI oversold/overbought mean reversion (perps)
  - ema_scalp — Tony's EMA scalper + S/R bands
  - macd_scalp — Ehlers super-smoother MACD crossover
  - supertrend, hull_suite, … — full set in ``WFO_REGISTERED_STRATEGY_MODES``; default WFO grid
    omits some overlapping/niche modes (see ``build_default_grid`` docstring).

Unknown ``mode`` values **raise** (no silent fallback) so WFO cannot score arbitrary strings.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, replace
from types import SimpleNamespace

import numpy as np

from .indicator_warmup import vec_warmup_prefix_len
from .scalp_mode_resolution import normalize_auto_mode_fallback


# ---------------------------------------------------------------------------
# Indicator helpers (pure numpy)
# ---------------------------------------------------------------------------

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


def wma(close: np.ndarray, period: int) -> np.ndarray:
    """Weighted moving average — linearly weighted, newest bar has highest weight."""
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if period > n or period < 1:
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


def adx_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Wilder ADX. NaN until bar index ``2 * period - 1`` (inclusive) has a value."""
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < 2 * period + 1:
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
    sig_period = max(3, min(21, slow_len // 2))
    sig = ema(macd1, sig_period)
    hist = (macd1 - sig) * float(sensitivity)
    basis = rolling_mean_arr(hist, bb_len)
    std = rolling_std_arr(hist, bb_len)
    upper = basis + bb_mult * std
    lower = basis - bb_mult * std
    return hist, basis, upper, lower


def daviddtech_warmup_bars(
    atr_period: int,
    adx_period: int,
    t3_length: int,
    hlc_close_period: int,
    hlc_low_period: int,
    hlc_high_period: int,
    wae_slow_len: int,
    wae_bb_len: int,
) -> int:
    """Bars to clear at start of ``detect_signals_daviddtech`` (delegates to ``indicator_warmup``)."""
    ns = SimpleNamespace(
        atr_period=atr_period,
        adx_period=adx_period,
        t3_length=t3_length,
        hlc_close_period=hlc_close_period,
        hlc_low_period=hlc_low_period,
        hlc_high_period=hlc_high_period,
        wae_slow_len=wae_slow_len,
        wae_bb_len=wae_bb_len,
    )
    return vec_warmup_prefix_len("daviddtech_scalp", ns)


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
        long_mask[:] = False
        short_mask[:] = False

    return long_mask, short_mask, atr_vals


def daviddtech_live_bundle(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
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
) -> dict[str, float | bool]:
    """Last-bar optimized-strategy values for live UI / incremental engine."""
    n = len(close)
    if n == 0:
        return {
            "t3": 0.0, "hlc_green": 0.0, "hlc_red": 0.0, "wae_up": 0.0, "wae_down": 0.0,
            "adx": 0.0, "optimized_ready": False, "optimized_long_setup": False,
            "optimized_short_setup": False,
        }

    t3 = tillson_t3(close, t3_length, t3_vfactor)
    adx_line = adx_wilder(high, low, close, adx_period)
    hlc_g, hlc_r, hlc_mid = hlc_trend_lines(
        high, low, close, hlc_close_period, hlc_low_period, hlc_high_period,
    )
    hist, _basis, upper, lower = waddah_attar_explosion(
        close, wae_sensitivity, wae_fast_len, wae_slow_len, wae_bb_len, wae_bb_mult,
    )
    long_m, short_m, _atr = detect_signals_daviddtech(
        close=close, high=high, low=low,
        atr_period=atr_period,
        adx_period=adx_period,
        t3_length=t3_length,
        t3_vfactor=t3_vfactor,
        hlc_close_period=hlc_close_period,
        hlc_low_period=hlc_low_period,
        hlc_high_period=hlc_high_period,
        adx_threshold=adx_threshold,
        wae_sensitivity=wae_sensitivity,
        wae_fast_len=wae_fast_len,
        wae_slow_len=wae_slow_len,
        wae_bb_len=wae_bb_len,
        wae_bb_mult=wae_bb_mult,
    )

    i = n - 1
    warm = daviddtech_warmup_bars(
        atr_period, adx_period, t3_length, hlc_close_period, hlc_low_period, hlc_high_period,
        wae_slow_len, wae_bb_len,
    )

    def _fv(arr: np.ndarray) -> float:
        if i >= len(arr):
            return 0.0
        v = arr[i]
        return float(v) if np.isfinite(v) else 0.0

    hi = hist[i] if i < len(hist) else np.nan
    upb = upper[i] if i < len(upper) else np.nan
    lob = lower[i] if i < len(lower) else np.nan
    # Use BB half-bandwidth as explosion threshold (matches detect_signals_daviddtech).
    e_val = (upb - lob) / 2.0 if (np.isfinite(upb) and np.isfinite(lob)) else np.nan
    wae_up = (
        float(max(0.0, hi - e_val))
        if np.isfinite(hi) and np.isfinite(e_val) and hi > 0 and hi > e_val
        else 0.0
    )
    wae_down = (
        float(max(0.0, (-hi) - e_val))
        if np.isfinite(hi) and np.isfinite(e_val) and hi < 0 and (-hi) > e_val
        else 0.0
    )

    ready = n > warm and np.isfinite(t3[i]) and np.isfinite(adx_line[i])

    return {
        "t3": _fv(t3),
        "hlc_green": _fv(hlc_g),
        "hlc_red": _fv(hlc_r),
        "wae_up": wae_up,
        "wae_down": wae_down,
        "adx": _fv(adx_line),
        "optimized_ready": bool(ready),
        "optimized_long_setup": bool(long_m[i]) if i < len(long_m) else False,
        "optimized_short_setup": bool(short_m[i]) if i < len(short_m) else False,
    }


def simulate_trades_bidir(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    long_mask: np.ndarray,
    short_mask: np.ndarray,
    atr_vals: np.ndarray,
    *,
    open_prices: np.ndarray | None = None,
    atr_stop_mult: float = 1.0,
    atr_tp_mult: float = 2.0,
    max_hold_bars: int = 12,
    fee_pct: float = 0.0,
    contract_size: float = 1.0,
    fee_usd_per_contract_per_leg: float = 0.0,
    slippage_pct: float = 0.0001,
    cooldown_bars: int = 1,
    fill_model: str = "close_slip",
    breakeven_atr_trigger: float = 0.0,
    trail_atr_trigger: float = 0.0,
    trail_atr_distance: float = 0.0,
    counter_signal_exit: bool = False,
) -> list[TradeResult]:
    """Long and short trades with intrabar path, break-even, trailing stop, counter exit.

    fill_model="next_open": entries fill at open[i+1] instead of close[i],
    matching realistic live execution where orders are placed after bar close.

    breakeven_atr_trigger: if > 0, move stop to entry when price reaches
      entry ± trigger × ATR (matches live break-even ratchet).
    trail_atr_trigger / trail_atr_distance: if both > 0, after price reaches
      entry ± trail_trigger × ATR, trail stop at close ∓ trail_distance × ATR
      (matches live trailing stop).
    counter_signal_exit: if True, close position on bar where opposite signal fires.
    """
    n = len(close)
    trades: list[TradeResult] = []
    next_allowed = 0
    has_open = open_prices is not None
    use_next_open = fill_model == "next_open" and has_open
    do_be = breakeven_atr_trigger > 0.0
    do_trail = trail_atr_trigger > 0.0 and trail_atr_distance > 0.0

    for i in range(n):
        if i < next_allowed:
            continue

        side = 0
        if long_mask[i] and not short_mask[i]:
            side = 1
        elif short_mask[i] and not long_mask[i]:
            side = -1
        elif long_mask[i] and short_mask[i]:
            continue

        if side == 0:
            continue

        a = atr_vals[i]
        if np.isnan(a) or a <= 0:
            continue

        if use_next_open:
            if i + 1 >= n:
                continue  # no next bar to fill on
            fill_base = open_prices[i + 1]
        else:
            fill_base = close[i]

        if side == 1:
            entry_price = fill_base * (1.0 + slippage_pct)
            stop_price = entry_price - a * atr_stop_mult
            tp_price = entry_price + a * atr_tp_mult
            if stop_price >= entry_price:
                continue
        else:
            entry_price = fill_base * (1.0 - slippage_pct)
            stop_price = entry_price + a * atr_stop_mult
            tp_price = entry_price - a * atr_tp_mult
            if stop_price <= entry_price:
                continue

        exit_bar = min(i + max_hold_bars, n - 1)
        exit_price = close[exit_bar]
        exit_reason = "time_stop"
        be_activated = False
        trail_activated = False

        for j in range(i + 1, min(i + max_hold_bars + 1, n)):
            # -- break-even ratchet: move stop to entry after ATR trigger --
            if do_be and not be_activated:
                if side == 1:
                    if high[j] >= entry_price + a * breakeven_atr_trigger:
                        stop_price = max(stop_price, entry_price)
                        be_activated = True
                else:
                    if low[j] <= entry_price - a * breakeven_atr_trigger:
                        stop_price = min(stop_price, entry_price)
                        be_activated = True

            # -- trailing stop: trail behind close after ATR trigger --
            if do_trail:
                if side == 1:
                    if high[j] >= entry_price + a * trail_atr_trigger:
                        trail_activated = True
                    if trail_activated:
                        trail_stop = close[j] - a * trail_atr_distance
                        stop_price = max(stop_price, trail_stop)
                else:
                    if low[j] <= entry_price - a * trail_atr_trigger:
                        trail_activated = True
                    if trail_activated:
                        trail_stop = close[j] + a * trail_atr_distance
                        stop_price = min(stop_price, trail_stop)

            # -- counter signal exit --
            if counter_signal_exit:
                if side == 1 and short_mask[j]:
                    exit_price = close[j] * (1.0 - slippage_pct)
                    exit_reason = "counter"
                    exit_bar = j
                    break
                elif side == -1 and long_mask[j]:
                    exit_price = close[j] * (1.0 + slippage_pct)
                    exit_reason = "counter"
                    exit_bar = j
                    break

            if side == 1:
                stop_hit = low[j] <= stop_price
                tp_hit = high[j] >= tp_price
            else:
                stop_hit = high[j] >= stop_price
                tp_hit = low[j] <= tp_price

            if stop_hit and tp_hit:
                if has_open:
                    o = open_prices[j]
                    if side == 1:
                        stop_first = _intrabar_stop_first(o, high[j], low[j])
                    else:
                        # Short: stop sits at the high side; infer high-first path.
                        stop_first = (high[j] - o) < (o - low[j])
                else:
                    stop_first = True
                if side == 1:
                    if stop_first:
                        exit_price = stop_price * (1.0 - slippage_pct)
                        exit_reason = "trail_stop" if trail_activated else "be_stop" if be_activated else "stop"
                    else:
                        exit_price = tp_price * (1.0 - slippage_pct)
                        exit_reason = "tp"
                else:
                    if stop_first:
                        exit_price = stop_price * (1.0 + slippage_pct)
                        exit_reason = "trail_stop" if trail_activated else "be_stop" if be_activated else "stop"
                    else:
                        exit_price = tp_price * (1.0 + slippage_pct)
                        exit_reason = "tp"
                exit_bar = j
                break
            elif stop_hit:
                exit_price = stop_price * (1.0 - slippage_pct if side == 1 else 1.0 + slippage_pct)
                exit_reason = "trail_stop" if trail_activated else "be_stop" if be_activated else "stop"
                exit_bar = j
                break
            elif tp_hit:
                exit_price = tp_price * (1.0 - slippage_pct if side == 1 else 1.0 + slippage_pct)
                exit_reason = "tp"
                exit_bar = j
                break

        _g, _fc, net_pnl = _roundtrip_gross_fee_net(
            entry_price,
            exit_price,
            side,
            fee_pct,
            contract_size,
            fee_usd_per_contract_per_leg,
        )

        trades.append(TradeResult(
            entry_bar=i,
            exit_bar=exit_bar,
            entry_price=entry_price,
            exit_price=exit_price,
            stop_price=stop_price,
            tp_price=tp_price,
            pnl=net_pnl,
            is_win=net_pnl > 0,
            exit_reason=exit_reason,
            hold_bars=exit_bar - i,
        ))
        next_allowed = exit_bar + cooldown_bars

    return trades


# ---------------------------------------------------------------------------
# Signal detection (vectorized) — EMA momentum mode
# ---------------------------------------------------------------------------

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
    """EMA momentum mode: fast/slow EMA cross entries only (perps backtests).

    RSI / VWAP / volume / ``min_signals`` stay in the signature for callers
    (WFO / ParamSet) but do not gate entries.
    """
    del volume, timestamp, rsi_period, vol_ma_period, vol_mult, min_signals

    ema_f = ema(close, ema_fast_period)
    ema_s = ema(close, ema_slow_period)
    atr_vals = atr(high, low, close, atr_period)

    ema_bullish = ema_f > ema_s
    ema_cross_up = np.zeros(len(close), dtype=bool)
    ema_cross_up[1:] = ema_bullish[1:] & ~ema_bullish[:-1]

    ema_bearish = ema_f < ema_s
    ema_cross_down = np.zeros(len(close), dtype=bool)
    ema_cross_down[1:] = ema_bearish[1:] & ~ema_bearish[:-1]

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


# ---------------------------------------------------------------------------
# Signal detection (vectorized) — RSI reversion mode
# ---------------------------------------------------------------------------

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


def simulate_trades_rsi(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    long_mask: np.ndarray,
    short_mask: np.ndarray,
    atr_vals: np.ndarray,
    rsi_vals: np.ndarray,
    *,
    rsi_sell_threshold: float = 50.0,
    rsi_short_cover_threshold: float = 30.0,
    atr_stop_mult: float = 1.5,
    atr_tp_mult: float = 1.5,
    max_hold_bars: int = 15,
    fee_pct: float = 0.0,
    contract_size: float = 1.0,
    fee_usd_per_contract_per_leg: float = 0.0,
    slippage_pct: float = 0.0001,
    cooldown_bars: int = 1,
    fill_model: str = "close_slip",
) -> list[TradeResult]:
    """RSI mean-reversion: long and short entries for perps."""
    n = len(close)
    trades: list[TradeResult] = []
    next_allowed = 0
    use_next_open = fill_model == "next_open"

    for i in range(n):
        if i < next_allowed:
            continue

        if long_mask[i] and short_mask[i]:
            continue

        a = atr_vals[i]
        if np.isnan(a) or a <= 0:
            continue

        if long_mask[i]:
            if use_next_open:
                if i + 1 >= n:
                    continue
                entry_price = close[i + 1] * (1.0 + slippage_pct)
            else:
                entry_price = close[i] * (1.0 + slippage_pct)
            stop_price = entry_price - a * atr_stop_mult
            tp_price = entry_price * 1.10

            if stop_price >= entry_price:
                continue

            exit_bar = min(i + max_hold_bars, n - 1)
            exit_price = close[exit_bar]
            exit_reason = "time_stop"

            for j in range(i + 1, min(i + max_hold_bars + 1, n)):
                if low[j] <= stop_price:
                    exit_price = stop_price * (1.0 - slippage_pct)
                    exit_reason = "stop"
                    exit_bar = j
                    break
                if not np.isnan(rsi_vals[j]) and rsi_vals[j] >= rsi_sell_threshold:
                    exit_price = close[j] * (1.0 - slippage_pct)
                    exit_reason = "rsi_exit"
                    exit_bar = j
                    break

            _g, _fc, net_pnl = _roundtrip_gross_fee_net(
                entry_price,
                exit_price,
                1,
                fee_pct,
                contract_size,
                fee_usd_per_contract_per_leg,
            )

            trades.append(TradeResult(
                entry_bar=i,
                exit_bar=exit_bar,
                entry_price=entry_price,
                exit_price=exit_price,
                stop_price=stop_price,
                tp_price=tp_price,
                pnl=net_pnl,
                is_win=net_pnl > 0,
                exit_reason=exit_reason,
                hold_bars=exit_bar - i,
            ))
            next_allowed = exit_bar + cooldown_bars
            continue

        if short_mask[i]:
            if use_next_open:
                if i + 1 >= n:
                    continue
                entry_price = close[i + 1] * (1.0 - slippage_pct)
            else:
                entry_price = close[i] * (1.0 - slippage_pct)
            stop_price = entry_price + a * atr_stop_mult
            tp_price = entry_price - a * atr_tp_mult

            if tp_price >= entry_price:
                continue

            exit_bar = min(i + max_hold_bars, n - 1)
            exit_price = close[exit_bar]
            exit_reason = "time_stop"

            for j in range(i + 1, min(i + max_hold_bars + 1, n)):
                if high[j] >= stop_price:
                    exit_price = stop_price * (1.0 + slippage_pct)
                    exit_reason = "stop"
                    exit_bar = j
                    break
                if low[j] <= tp_price:
                    exit_price = tp_price * (1.0 + slippage_pct)
                    exit_reason = "tp"
                    exit_bar = j
                    break
                if not np.isnan(rsi_vals[j]) and rsi_vals[j] <= rsi_short_cover_threshold:
                    exit_price = close[j] * (1.0 + slippage_pct)
                    exit_reason = "rsi_exit"
                    exit_bar = j
                    break

            _g, _fc, net_pnl = _roundtrip_gross_fee_net(
                entry_price,
                exit_price,
                -1,
                fee_pct,
                contract_size,
                fee_usd_per_contract_per_leg,
            )

            trades.append(TradeResult(
                entry_bar=i,
                exit_bar=exit_bar,
                entry_price=entry_price,
                exit_price=exit_price,
                stop_price=stop_price,
                tp_price=tp_price,
                pnl=net_pnl,
                is_win=net_pnl > 0,
                exit_reason=exit_reason,
                hold_bars=exit_bar - i,
            ))
            next_allowed = exit_bar + cooldown_bars

    return trades


# ---------------------------------------------------------------------------
# Signal detection (vectorized) — EMA scalp mode (Tony's EMA Scalper)
# ---------------------------------------------------------------------------

def rolling_highest(close: np.ndarray, period: int) -> np.ndarray:
    """Rolling highest close over `period` bars."""
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(period - 1, n):
        out[i] = np.max(close[i - period + 1: i + 1])
    return out


def rolling_lowest(close: np.ndarray, period: int) -> np.ndarray:
    """Rolling lowest close over `period` bars."""
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(period - 1, n):
        out[i] = np.min(close[i - period + 1: i + 1])
    return out


def detect_signals_ema_scalp(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    ema_period: int,
    atr_period: int,
    sr_bars: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Tony's EMA Scalper: long on bullish EMA cross; short on bearish cross (perps).

    Returns (long_mask, short_mask, atr_vals, high_n, low_n).
    """
    ema_vals = ema(close, ema_period)
    atr_vals = atr(high, low, close, atr_period)
    high_n = rolling_highest(close, sr_bars)
    low_n = rolling_lowest(close, sr_bars)

    n = len(close)
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)

    for i in range(1, n):
        if np.isnan(ema_vals[i]) or np.isnan(ema_vals[i - 1]):
            continue
        prev_above = close[i - 1] > ema_vals[i - 1]
        cur_above = close[i] > ema_vals[i]
        crossed = prev_above != cur_above
        bullish = crossed and cur_above and close[i] > close[i - 1]
        bearish = crossed and (not cur_above) and close[i] < close[i - 1]
        if bullish:
            long_mask[i] = True
        if bearish:
            short_mask[i] = True

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


def simulate_trades_ema_scalp(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    entry_mask: np.ndarray,
    atr_vals: np.ndarray,
    high_n: np.ndarray,
    low_n: np.ndarray,
    *,
    open_prices: np.ndarray | None = None,
    atr_stop_mult: float = 1.0,
    max_hold_bars: int = 15,
    fee_pct: float = 0.0,
    contract_size: float = 1.0,
    fee_usd_per_contract_per_leg: float = 0.0,
    slippage_pct: float = 0.0001,
    cooldown_bars: int = 1,
) -> list[TradeResult]:
    """EMA scalp trade sim with intrabar path and slippage."""
    n = len(close)
    trades: list[TradeResult] = []
    next_allowed = 0
    has_open = open_prices is not None

    for i in range(n):
        if i < next_allowed:
            continue
        if not entry_mask[i]:
            continue

        a = atr_vals[i]
        if np.isnan(a) or a <= 0:
            continue

        entry_price = close[i] * (1.0 + slippage_pct)
        stop_price = max(low_n[i], entry_price - a * atr_stop_mult) if not np.isnan(low_n[i]) else entry_price - a * atr_stop_mult
        tp_price = high_n[i] if not np.isnan(high_n[i]) and high_n[i] > entry_price else entry_price + a * 1.5

        if stop_price >= entry_price:
            stop_price = entry_price - a * atr_stop_mult
        if stop_price >= entry_price:
            continue

        exit_bar = min(i + max_hold_bars, n - 1)
        exit_price = close[exit_bar]
        exit_reason = "time_stop"

        for j in range(i + 1, min(i + max_hold_bars + 1, n)):
            stop_hit = low[j] <= stop_price
            tp_hit = high[j] >= tp_price

            if stop_hit and tp_hit:
                if has_open:
                    stop_first = _intrabar_stop_first(open_prices[j], high[j], low[j])
                else:
                    stop_first = True
                if stop_first:
                    exit_price = stop_price * (1.0 - slippage_pct)
                    exit_reason = "stop"
                else:
                    exit_price = tp_price * (1.0 - slippage_pct)
                    exit_reason = "tp"
                exit_bar = j
                break
            elif stop_hit:
                exit_price = stop_price * (1.0 - slippage_pct)
                exit_reason = "stop"
                exit_bar = j
                break
            elif tp_hit:
                exit_price = tp_price * (1.0 - slippage_pct)
                exit_reason = "tp"
                exit_bar = j
                break

        _g, _fc, net_pnl = _roundtrip_gross_fee_net(
            entry_price,
            exit_price,
            1,
            fee_pct,
            contract_size,
            fee_usd_per_contract_per_leg,
        )

        trades.append(TradeResult(
            entry_bar=i,
            exit_bar=exit_bar,
            entry_price=entry_price,
            exit_price=exit_price,
            stop_price=stop_price,
            tp_price=tp_price,
            pnl=net_pnl,
            is_win=net_pnl > 0,
            exit_reason=exit_reason,
            hold_bars=exit_bar - i,
        ))
        next_allowed = exit_bar + cooldown_bars

    return trades



# ---------------------------------------------------------------------------
# Signal detection (vectorized) — MACD scalp mode (Scalp Pro)
# ---------------------------------------------------------------------------

def super_smooth(data: np.ndarray, period: int) -> np.ndarray:
    """Ehlers 2-pole super-smoother filter (recursive IIR)."""
    f = (1.4142135623730951 * math.pi) / period
    a = math.exp(-f)
    c2 = 2.0 * a * math.cos(f)
    c3 = -(a * a)
    c1 = 1.0 - c2 - c3
    n = len(data)
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

    Returns (long_mask, short_mask, atr_vals, macd_line, macd_signal_line).
    """
    ss_fast = super_smooth(close, fast_len)
    ss_slow = super_smooth(close, slow_len)
    macd_line = (ss_fast - ss_slow) * 1e7
    macd_signal_line = super_smooth(macd_line, signal_len)
    atr_vals = atr(high, low, close, atr_period)

    n = len(close)
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if macd_line[i - 1] <= macd_signal_line[i - 1] and macd_line[i] > macd_signal_line[i]:
            long_mask[i] = True
        if macd_line[i - 1] >= macd_signal_line[i - 1] and macd_line[i] < macd_signal_line[i]:
            short_mask[i] = True

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


# ---------------------------------------------------------------------------
# New strategy: Supertrend
# ---------------------------------------------------------------------------

def detect_signals_supertrend(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    period: int = 10,
    factor: float = 3.0,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Supertrend ATR-channel trend following.

    Bands: hl2 ± factor×ATR, tightened each bar.
    Long signal: supertrend flips from bearish to bullish (price breaks upper band).
    Short signal: supertrend flips from bullish to bearish (price breaks lower band).
    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    atr_v = atr(high, low, close, atr_period)
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + factor * atr_v
    basic_lower = hl2 - factor * atr_v

    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)  # 1=bull, -1=bear

    loop_warm = max(atr_period, period)
    mask_prefix = vec_warmup_prefix_len(
        "supertrend",
        SimpleNamespace(atr_period=atr_period, supertrend_period=period),
    )
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    if loop_warm >= n:
        return long_mask, short_mask, atr_v

    final_upper[loop_warm] = basic_upper[loop_warm]
    final_lower[loop_warm] = basic_lower[loop_warm]
    direction[loop_warm] = 1

    for i in range(loop_warm + 1, n):
        if np.isnan(atr_v[i]):
            final_upper[i] = final_upper[i - 1]
            final_lower[i] = final_lower[i - 1]
            direction[i] = direction[i - 1]
            continue
        # Tighten bands: upper only drops; lower only rises
        fu = (
            min(basic_upper[i], final_upper[i - 1])
            if close[i - 1] <= final_upper[i - 1]
            else basic_upper[i]
        )
        fl = (
            max(basic_lower[i], final_lower[i - 1])
            if close[i - 1] >= final_lower[i - 1]
            else basic_lower[i]
        )
        final_upper[i] = fu
        final_lower[i] = fl
        # Direction flip logic
        if direction[i - 1] == -1:
            direction[i] = 1 if close[i] > fu else -1
        else:
            direction[i] = -1 if close[i] < fl else 1
        # Signals on direction change
        if direction[i - 1] != 1 and direction[i] == 1:
            long_mask[i] = True
        elif direction[i - 1] != -1 and direction[i] == -1:
            short_mask[i] = True

    ok = ~np.isnan(atr_v) & (atr_v > 0)
    long_mask &= ok
    short_mask &= ok
    long_mask[:mask_prefix] = False
    short_mask[:mask_prefix] = False
    return long_mask, short_mask, atr_v


def supertrend_live_bundle(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int = 10,
    factor: float = 3.0,
    atr_period: int = 14,
) -> dict[str, bool]:
    """Last-bar Supertrend values for the live indicator engine."""
    if len(close) < 2:
        return {"supertrend_long": False, "supertrend_short": False, "supertrend_bull": False}
    long_m, short_m, atr_v = detect_signals_supertrend(
        close, high, low, period=period, factor=factor, atr_period=atr_period,
    )
    i = len(close) - 1
    # Reconstruct direction for current bull/bear state
    n = len(close)
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + factor * atr_v
    basic_lower = hl2 - factor * atr_v
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)
    warmup = max(atr_period, period)
    if warmup < n:
        final_upper[warmup] = basic_upper[warmup]
        final_lower[warmup] = basic_lower[warmup]
        direction[warmup] = 1
        for j in range(warmup + 1, n):
            if np.isnan(atr_v[j]):
                final_upper[j] = final_upper[j - 1]
                final_lower[j] = final_lower[j - 1]
                direction[j] = direction[j - 1]
                continue
            fu = (
                min(basic_upper[j], final_upper[j - 1])
                if close[j - 1] <= final_upper[j - 1]
                else basic_upper[j]
            )
            fl = (
                max(basic_lower[j], final_lower[j - 1])
                if close[j - 1] >= final_lower[j - 1]
                else basic_lower[j]
            )
            final_upper[j] = fu
            final_lower[j] = fl
            if direction[j - 1] == -1:
                direction[j] = 1 if close[j] > fu else -1
            else:
                direction[j] = -1 if close[j] < fl else 1
    return {
        "supertrend_long": bool(long_m[i]),
        "supertrend_short": bool(short_m[i]),
        "supertrend_bull": bool(direction[i] == 1),
    }


# ---------------------------------------------------------------------------
# New strategy: Squeeze Momentum (LazyBear TTM Squeeze)
# ---------------------------------------------------------------------------

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
    """Squeeze Momentum: Bollinger Bands + Keltner Channels + linear-regression momentum.

    Long signal: momentum crosses above zero.
    Short signal: momentum crosses below zero.
    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    atr_v = atr(high, low, close, atr_period)
    kc_mid = ema(close, bb_period)

    # Bollinger Bands (SMA + stdev)
    sma = np.full(n, np.nan)
    stdev = np.full(n, np.nan)
    roll_high = np.full(n, np.nan)
    roll_low = np.full(n, np.nan)
    for i in range(bb_period - 1, n):
        s = close[i - bb_period + 1: i + 1]
        sma[i] = np.mean(s)
        stdev[i] = np.std(s, ddof=0)
        roll_high[i] = np.max(high[i - bb_period + 1: i + 1])
        roll_low[i] = np.min(low[i - bb_period + 1: i + 1])

    # BB/KC envelopes — squeeze is on when BB fits inside KC (low volatility).
    bb_upper = sma + bb_mult * stdev
    bb_lower = sma - bb_mult * stdev
    kc_upper = kc_mid + kc_mult * atr_v
    kc_lower = kc_mid - kc_mult * atr_v

    # Squeeze condition: BB inside KC (volatility compression).
    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    # Momentum: linear-regression of delta over mom_period
    midpoint = (roll_high + roll_low) / 2.0
    val = close - (midpoint + sma) / 2.0

    x = np.arange(mom_period, dtype=np.float64)
    x -= x.mean()
    xdot = np.dot(x, x)
    mom = np.full(n, np.nan)
    for i in range(mom_period - 1, n):
        y = val[i - mom_period + 1: i + 1]
        if np.any(np.isnan(y)) or xdot == 0:
            continue
        slope = np.dot(x, y) / xdot
        mom[i] = slope * (mom_period - 1) / 2.0 + np.mean(y)

    # Entry: momentum crosses zero AND prior bar was in a squeeze (release signal).
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
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


# ---------------------------------------------------------------------------
# New strategy: QQE Mod (simplified RSI-based trailing level)
# ---------------------------------------------------------------------------

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

    Long signal: smooth_rsi crosses above QQE trail AND smooth_rsi > 50.
    Short signal: smooth_rsi crosses below QQE trail AND smooth_rsi < 50.
    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    atr_v = atr(high, low, close, atr_period)
    rsi_v = rsi(close, rsi_period)
    smooth_rsi = ema(rsi_v, qqe_smoothing)

    # Wilder-smoothed ATR of the smoothed RSI
    wilders_period = rsi_period * 2 - 1
    abs_diff = np.abs(np.diff(smooth_rsi, prepend=smooth_rsi[0]))
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

    # Trailing stop
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

    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(smooth_rsi[i]) or np.isnan(trail[i]) or np.isnan(trail[i - 1]):
            continue
        if (smooth_rsi[i - 1] <= trail[i - 1] and smooth_rsi[i] > trail[i]
                and smooth_rsi[i] > 50):
            long_mask[i] = True
        elif (smooth_rsi[i - 1] >= trail[i - 1] and smooth_rsi[i] < trail[i]
              and smooth_rsi[i] < 50):
            short_mask[i] = True

    ok = ~np.isnan(atr_v) & (atr_v > 0)
    long_mask &= ok
    short_mask &= ok
    warmup = vec_warmup_prefix_len(
        "qqe_mod",
        SimpleNamespace(
            qqe_rsi_period=rsi_period,
            qqe_smoothing=qqe_smoothing,
        ),
    )
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
    if len(close) < 2:
        return {"qqe_long": False, "qqe_short": False}
    long_m, short_m, _ = detect_signals_qqe(
        close, high, low,
        rsi_period=rsi_period, qqe_factor=qqe_factor,
        qqe_smoothing=qqe_smoothing, atr_period=atr_period,
    )
    i = len(close) - 1
    return {"qqe_long": bool(long_m[i]), "qqe_short": bool(short_m[i])}


# ---------------------------------------------------------------------------
# New strategy: UT Bot Alert (Chandelier Exit trailing stop)
# ---------------------------------------------------------------------------

def detect_signals_utbot(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    atr_period: int = 10,
    atr_mult: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """UT Bot Alert: ATR-based trailing stop that ratchets with price.

    Long signal: price crosses above the trail (direction flips to bull).
    Short signal: price crosses below the trail (direction flips to bear).
    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    atr_v = atr(high, low, close, atr_period)

    trail = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)  # 1=bull, -1=bear

    loop_warm = atr_period
    mask_prefix = vec_warmup_prefix_len(
        "utbot_alert",
        SimpleNamespace(utbot_atr_period=atr_period),
    )
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    if loop_warm >= n:
        return long_mask, short_mask, atr_v

    trail[loop_warm] = close[loop_warm]
    direction[loop_warm] = 1

    for i in range(loop_warm + 1, n):
        if np.isnan(atr_v[i]):
            trail[i] = trail[i - 1]
            direction[i] = direction[i - 1]
            continue
        loss = atr_mult * atr_v[i]
        c = close[i]
        pc = close[i - 1]
        pt = trail[i - 1]
        # Ratchet logic
        if c > pt and pc > pt:
            trail[i] = max(pt, c - loss)
        elif c < pt and pc < pt:
            trail[i] = min(pt, c + loss)
        elif c > pt:
            trail[i] = c - loss
        else:
            trail[i] = c + loss
        # Direction
        if pc < trail[i - 1] and c > trail[i]:
            direction[i] = 1
        elif pc > trail[i - 1] and c < trail[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        if direction[i - 1] != 1 and direction[i] == 1:
            long_mask[i] = True
        elif direction[i - 1] != -1 and direction[i] == -1:
            short_mask[i] = True

    ok = ~np.isnan(atr_v) & (atr_v > 0)
    long_mask &= ok
    short_mask &= ok
    long_mask[:mask_prefix] = False
    short_mask[:mask_prefix] = False
    return long_mask, short_mask, atr_v


def utbot_live_bundle(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    atr_period: int = 10,
    atr_mult: float = 1.0,
) -> dict[str, bool]:
    """Last-bar UT Bot Alert values for the live indicator engine."""
    if len(close) < 2:
        return {"utbot_long": False, "utbot_short": False, "utbot_bull": False}
    long_m, short_m, atr_v = detect_signals_utbot(
        close, high, low, atr_period=atr_period, atr_mult=atr_mult,
    )
    # Reconstruct direction for current state
    n = len(close)
    trail = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)
    warmup = atr_period
    if warmup < n:
        trail[warmup] = close[warmup]
        direction[warmup] = 1
        for i in range(warmup + 1, n):
            if np.isnan(atr_v[i]):
                trail[i] = trail[i - 1]
                direction[i] = direction[i - 1]
                continue
            loss = atr_mult * atr_v[i]
            c = close[i]
            pc = close[i - 1]
            pt = trail[i - 1]
            if c > pt and pc > pt:
                trail[i] = max(pt, c - loss)
            elif c < pt and pc < pt:
                trail[i] = min(pt, c + loss)
            elif c > pt:
                trail[i] = c - loss
            else:
                trail[i] = c + loss
            if pc < trail[i - 1] and c > trail[i]:
                direction[i] = 1
            elif pc > trail[i - 1] and c < trail[i]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]
    i = len(close) - 1
    return {
        "utbot_long": bool(long_m[i]),
        "utbot_short": bool(short_m[i]),
        "utbot_bull": bool(direction[i] == 1),
    }


# ---------------------------------------------------------------------------
# Hull Suite — TradingView "Hull Suite Strategy" (Hma path) entry semantics
# ---------------------------------------------------------------------------

def detect_signals_hull(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    hull_period: int = 38,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hull Suite (DashTrader / InSilico pack, Hma only).

    HMA = WMA(2*WMA(n/2) − WMA(n), round(sqrt(n))) on ``close`` (Pine ``src`` default).

    Long when ``HMA[i] > HMA[i-2]``, short when ``HMA[i] < HMA[i-2]`` (Pine ``MHULL`` vs ``SHULL``).
    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    atr_v = atr(high, low, close, atr_period)

    half = max(1, hull_period // 2)
    sqrtn = max(1, int(round(np.sqrt(hull_period))))

    wma_half = wma(close, half)
    wma_full = wma(close, hull_period)
    diff = 2.0 * wma_half - wma_full
    hma = wma(diff, sqrtn)

    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    for i in range(2, n):
        if not np.isfinite(hma[i]) or not np.isfinite(hma[i - 2]):
            continue
        if hma[i] > hma[i - 2]:
            long_mask[i] = True
        elif hma[i] < hma[i - 2]:
            short_mask[i] = True

    ok = ~np.isnan(atr_v) & (atr_v > 0)
    long_mask &= ok
    short_mask &= ok
    warmup = vec_warmup_prefix_len(
        "hull_suite",
        SimpleNamespace(hull_period=hull_period, atr_period=atr_period),
    )
    long_mask[:warmup] = False
    short_mask[:warmup] = False
    return long_mask, short_mask, atr_v


def hull_live_bundle(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    hull_period: int = 38,
    atr_period: int = 14,
) -> dict[str, bool]:
    """Last-bar Hull Suite flags (same semantics as ``detect_signals_hull``)."""
    if len(close) < 3:
        return {"hull_long": False, "hull_short": False, "hull_bull": False}
    long_m, short_m, _ = detect_signals_hull(
        close, high, low, hull_period=hull_period, atr_period=atr_period,
    )
    i = len(close) - 1
    hl = bool(long_m[i])
    return {
        "hull_long": hl,
        "hull_short": bool(short_m[i]),
        # TV "Color Hull according to trend": HULL > HULL[2]
        "hull_bull": hl,
    }


# ---------------------------------------------------------------------------
# New strategy: SAR + CHOP (TV "5 min bot scalper" decode)
#   Parabolic SAR (flip trigger) + optional close-based Lucid SAR
#   + MA(200) / MA(50) trend filter + Choppiness Index regime filter
#   + MACD(12,26,9) histogram confirmation + UT Bot ATR-trail exit.
# ---------------------------------------------------------------------------

def _parabolic_sar(
    high: np.ndarray,
    low: np.ndarray,
    *,
    start: float = 0.02,
    step: float = 0.02,
    max_af: float = 0.2,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Classic Wilder Parabolic SAR.

    Returns (sar, bull_dir, long_flip_mask, short_flip_mask) where bull_dir is
    +1 when price is above SAR and -1 when below. ``long_flip_mask[i]`` is True
    on the bar the direction flipped from -1 to +1 (mirror for short_flip).
    """
    n = len(high)
    sar = np.full(n, np.nan, dtype=np.float64)
    bull = np.zeros(n, dtype=np.int8)
    long_flip = np.zeros(n, dtype=bool)
    short_flip = np.zeros(n, dtype=bool)
    if n < 3:
        return sar, bull, long_flip, short_flip
    # Seed with a simple direction heuristic from the first two bars.
    if high[1] >= high[0]:
        direction = 1
        sar[1] = float(low[0])
        ep = float(high[1])
    else:
        direction = -1
        sar[1] = float(high[0])
        ep = float(low[1])
    af = float(start)
    bull[1] = direction
    for i in range(2, n):
        prev_sar = sar[i - 1]
        new_sar = prev_sar + af * (ep - prev_sar)
        if direction == 1:
            # SAR can't exceed the prior two bars' lows.
            new_sar = min(new_sar, float(low[i - 1]), float(low[i - 2]))
            if low[i] < new_sar:
                # Flip down
                direction = -1
                new_sar = ep
                ep = float(low[i])
                af = float(start)
                short_flip[i] = True
            else:
                if high[i] > ep:
                    ep = float(high[i])
                    af = min(af + step, max_af)
        else:
            new_sar = max(new_sar, float(high[i - 1]), float(high[i - 2]))
            if high[i] > new_sar:
                # Flip up
                direction = 1
                new_sar = ep
                ep = float(high[i])
                af = float(start)
                long_flip[i] = True
            else:
                if low[i] < ep:
                    ep = float(low[i])
                    af = min(af + step, max_af)
        sar[i] = new_sar
        bull[i] = direction
    return sar, bull, long_flip, short_flip


def _chop_index(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Choppiness Index — 100 * log10(sum(TR)/range) / log10(period).

    < ~38.2 → trending; > ~61.8 → choppy. First `period` values are NaN.
    """
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if period <= 1 or n <= period:
        return out
    tr = np.empty(n, dtype=np.float64)
    tr[0] = float(high[0] - low[0])
    for i in range(1, n):
        tr[i] = max(
            float(high[i] - low[i]),
            abs(float(high[i] - close[i - 1])),
            abs(float(low[i] - close[i - 1])),
        )
    log_p = math.log10(float(period))
    hi_roll = rolling_max_arr(high, period)
    lo_roll = rolling_min_arr(low, period)
    for i in range(period, n):
        tr_sum = float(np.sum(tr[i - period + 1 : i + 1]))
        rng = float(hi_roll[i] - lo_roll[i])
        if rng <= 0 or tr_sum <= 0 or log_p <= 0:
            continue
        out[i] = 100.0 * math.log10(tr_sum / rng) / log_p
    return out


def _macd_hist(
    close: np.ndarray,
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> np.ndarray:
    """Standard (Appel) MACD histogram = (EMA_fast - EMA_slow) - signal EMA.

    Uses the ``ema`` helper above (first bars NaN until warmup).
    """
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line - sig


def _utbot_trail_flips(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    atr_period: int,
    atr_mult: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run the UT Bot ATR-trail state machine.

    Returns (direction, long_flip_mask, short_flip_mask) — direction is +1 bull,
    -1 bear, same ratchet mechanics as ``detect_signals_utbot``. Used for exits
    in the sar_chop mode without re-detecting entries.
    """
    n = len(close)
    atr_v = atr(high, low, close, atr_period)
    trail = np.full(n, np.nan, dtype=np.float64)
    direction = np.zeros(n, dtype=np.int8)
    long_flip = np.zeros(n, dtype=bool)
    short_flip = np.zeros(n, dtype=bool)
    warmup = atr_period
    if warmup >= n:
        return direction, long_flip, short_flip
    trail[warmup] = float(close[warmup])
    direction[warmup] = 1
    for i in range(warmup + 1, n):
        if np.isnan(atr_v[i]):
            trail[i] = trail[i - 1]
            direction[i] = direction[i - 1]
            continue
        loss = atr_mult * atr_v[i]
        c = float(close[i])
        pc = float(close[i - 1])
        pt = float(trail[i - 1])
        if c > pt and pc > pt:
            trail[i] = max(pt, c - loss)
        elif c < pt and pc < pt:
            trail[i] = min(pt, c + loss)
        elif c > pt:
            trail[i] = c - loss
        else:
            trail[i] = c + loss
        if pc < trail[i - 1] and c > trail[i]:
            direction[i] = 1
        elif pc > trail[i - 1] and c < trail[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        if direction[i - 1] != 1 and direction[i] == 1:
            long_flip[i] = True
        elif direction[i - 1] != -1 and direction[i] == -1:
            short_flip[i] = True
    return direction, long_flip, short_flip


def _sar_chop_common_mats(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    sar_start: float,
    sar_increment: float,
    sar_max: float,
    ma_fast_period: int,
    ma_long_period: int,
    ma_short_period: int,
    chop_period: int,
    macd_fast: int,
    macd_slow: int,
    macd_signal: int,
    use_lucid_sar: bool,
    use_utbot_trail: bool,
    utbot_atr_period: int,
    utbot_atr_mult: float,
    atr_period: int,
) -> tuple[
    int,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
] | None:
    """Shared PSAR/CHOP/MA/MACD/UT arrays for sar_chop. Returns None if ``len(close) < 3``."""
    n = len(close)
    atr_v = atr(high, low, close, atr_period)
    if n < 3:
        return None
    _, _, psar_long_flip, psar_short_flip = _parabolic_sar(
        high, low, start=sar_start, step=sar_increment, max_af=sar_max,
    )
    if use_lucid_sar:
        _, lucid_bull, _, _ = _parabolic_sar(
            close, close, start=sar_start, step=sar_increment, max_af=sar_max,
        )
    else:
        lucid_bull = np.ones(n, dtype=np.int8)
    chop = _chop_index(high, low, close, chop_period)
    ma_fast = ema(close, ma_fast_period)
    ma_long = ema(close, ma_long_period)
    ma_short = ema(close, ma_short_period)
    macd_h = _macd_hist(close, fast=macd_fast, slow=macd_slow, signal=macd_signal)
    if use_utbot_trail:
        ut_dir, _, _ = _utbot_trail_flips(
            close, high, low, atr_period=utbot_atr_period, atr_mult=utbot_atr_mult,
        )
    else:
        ut_dir = np.ones(n, dtype=np.int8)
    warmup = vec_warmup_prefix_len(
        "sar_chop",
        SimpleNamespace(
            sar_chop_ma_long_period=ma_long_period,
            sar_chop_macd_slow=macd_slow,
            sar_chop_macd_signal=macd_signal,
            sar_chop_chop_period=chop_period,
            atr_period=atr_period,
            sar_chop_utbot_atr_period=utbot_atr_period,
        ),
    )
    return (
        warmup,
        atr_v,
        psar_long_flip,
        psar_short_flip,
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    )


def _sar_chop_fill_masks(
    close: np.ndarray,
    warmup: int,
    chop_threshold: float,
    use_lucid_sar: bool,
    use_utbot_trail: bool,
    atr_v: np.ndarray,
    psar_long_flip: np.ndarray,
    psar_short_flip: np.ndarray,
    lucid_bull: np.ndarray,
    chop: np.ndarray,
    ma_fast: np.ndarray,
    ma_long: np.ndarray,
    ma_short: np.ndarray,
    macd_h: np.ndarray,
    ut_dir: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    n = len(close)
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    for i in range(warmup, n):
        if np.isnan(chop[i]) or np.isnan(ma_long[i]) or np.isnan(ma_short[i]) or np.isnan(macd_h[i]):
            continue
        if not (np.isfinite(atr_v[i]) and atr_v[i] > 0):
            continue
        if chop[i] >= chop_threshold:
            continue
        c = float(close[i])
        if psar_long_flip[i]:
            if c > ma_fast[i] and c > ma_long[i] and ma_short[i] >= ma_long[i] and macd_h[i] > 0:
                if (not use_lucid_sar or lucid_bull[i] == 1) and (not use_utbot_trail or ut_dir[i] == 1):
                    long_mask[i] = True
        elif psar_short_flip[i]:
            if c < ma_fast[i] and c < ma_long[i] and ma_short[i] <= ma_long[i] and macd_h[i] < 0:
                if (not use_lucid_sar or lucid_bull[i] == -1) and (not use_utbot_trail or ut_dir[i] == -1):
                    short_mask[i] = True
    return long_mask, short_mask


def detect_signals_sar_chop(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    sar_start: float = 0.02,
    sar_increment: float = 0.02,
    sar_max: float = 0.2,
    ma_fast_period: int = 7,
    ma_long_period: int = 200,
    ma_short_period: int = 50,
    chop_period: int = 14,
    chop_threshold: float = 38.2,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    use_lucid_sar: bool = True,
    use_utbot_trail: bool = True,
    utbot_atr_period: int = 10,
    utbot_atr_mult: float = 2.0,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Combined SAR + CHOP entry, UT Bot ATR-trail exit.

    Entry conditions (long):
      - primary PSAR flips from bear to bull on this bar (``psar_long_flip``)
      - Choppiness Index < ``chop_threshold`` (trending regime)
      - close > MA(7) (fast MA momentum confirmation)
      - close > MA(200) AND MA(50) >= MA(200) (bullish trend stack)
      - MACD histogram > 0
      - if ``use_lucid_sar``: Lucid (close-based) SAR is in bull state
      - if ``use_utbot_trail``: UT Bot trail is bull (prevents entry while exit
        trail still says bear)

    Short is the mirror. Exits via simulate_trades_bidir ATR stops/TPs — the
    ``utbot_atr_mult`` choice is reflected in the caller's atr_stop_mult when
    tuned for this mode (the trail agreement gate keeps entries aligned with
    the same stop that would protect a live fill).

    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    cm = _sar_chop_common_mats(
        close, high, low,
        sar_start=sar_start,
        sar_increment=sar_increment,
        sar_max=sar_max,
        ma_fast_period=ma_fast_period,
        ma_long_period=ma_long_period,
        ma_short_period=ma_short_period,
        chop_period=chop_period,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
        use_lucid_sar=use_lucid_sar,
        use_utbot_trail=use_utbot_trail,
        utbot_atr_period=utbot_atr_period,
        utbot_atr_mult=utbot_atr_mult,
        atr_period=atr_period,
    )
    if cm is None:
        return long_mask, short_mask, atr(high, low, close, atr_period)
    (
        warmup,
        atr_v,
        psar_long_flip,
        psar_short_flip,
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    ) = cm
    long_mask, short_mask = _sar_chop_fill_masks(
        close,
        warmup,
        chop_threshold,
        use_lucid_sar,
        use_utbot_trail,
        atr_v,
        psar_long_flip,
        psar_short_flip,
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    )
    return long_mask, short_mask, atr_v


def sar_chop_diagnostic_frame(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    sar_start: float = 0.02,
    sar_increment: float = 0.02,
    sar_max: float = 0.2,
    ma_fast_period: int = 7,
    ma_long_period: int = 200,
    ma_short_period: int = 50,
    chop_period: int = 14,
    chop_threshold: float = 38.2,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    use_lucid_sar: bool = True,
    use_utbot_trail: bool = True,
    utbot_atr_period: int = 10,
    utbot_atr_mult: float = 2.0,
    atr_period: int = 14,
) -> dict[str, np.ndarray | int | float]:
    """Per-bar arrays for CSV / TradingView parity checks (same math as ``detect_signals_sar_chop``).

    Keys include ``warmup`` (int), ``chop_threshold`` (float), and numpy arrays:
    ``long_mask``, ``short_mask``, ``atr_v``, ``psar_long_flip``, ``psar_short_flip``,
    ``lucid_bull``, ``chop``, ``ma_fast``, ``ma_long``, ``ma_short``, ``macd_hist``, ``ut_dir``.
    """
    n = len(close)
    empty = {
        "warmup": 0,
        "chop_threshold": float(chop_threshold),
        "long_mask": np.zeros(n, dtype=bool),
        "short_mask": np.zeros(n, dtype=bool),
        "atr_v": atr(high, low, close, atr_period),
    }
    cm = _sar_chop_common_mats(
        close, high, low,
        sar_start=sar_start,
        sar_increment=sar_increment,
        sar_max=sar_max,
        ma_fast_period=ma_fast_period,
        ma_long_period=ma_long_period,
        ma_short_period=ma_short_period,
        chop_period=chop_period,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
        use_lucid_sar=use_lucid_sar,
        use_utbot_trail=use_utbot_trail,
        utbot_atr_period=utbot_atr_period,
        utbot_atr_mult=utbot_atr_mult,
        atr_period=atr_period,
    )
    if cm is None:
        return empty
    (
        warmup,
        atr_v,
        psar_long_flip,
        psar_short_flip,
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    ) = cm
    long_mask, short_mask = _sar_chop_fill_masks(
        close,
        warmup,
        chop_threshold,
        use_lucid_sar,
        use_utbot_trail,
        atr_v,
        psar_long_flip,
        psar_short_flip,
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    )
    return {
        "warmup": warmup,
        "chop_threshold": float(chop_threshold),
        "long_mask": long_mask,
        "short_mask": short_mask,
        "atr_v": atr_v,
        "psar_long_flip": psar_long_flip,
        "psar_short_flip": psar_short_flip,
        "lucid_bull": lucid_bull.astype(np.int16),
        "chop": chop,
        "ma_fast": ma_fast,
        "ma_long": ma_long,
        "ma_short": ma_short,
        "macd_hist": macd_h,
        "ut_dir": ut_dir.astype(np.int16),
    }


def sar_chop_live_bundle(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    sar_start: float = 0.02,
    sar_increment: float = 0.02,
    sar_max: float = 0.2,
    ma_fast_period: int = 7,
    ma_long_period: int = 200,
    ma_short_period: int = 50,
    chop_period: int = 14,
    chop_threshold: float = 38.2,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    use_lucid_sar: bool = True,
    use_utbot_trail: bool = True,
    utbot_atr_period: int = 10,
    utbot_atr_mult: float = 2.0,
    atr_period: int = 14,
) -> dict[str, float | bool]:
    """Last-bar SAR+CHOP state for the live indicator engine."""
    defaults: dict[str, float | bool] = {
        "sar_chop_long_setup": False,
        "sar_chop_short_setup": False,
        "sar_value": 0.0,
        "chop_value": 0.0,
        "sar_chop_trail_bull": False,
    }
    n = len(close)
    if n < 3:
        return defaults
    long_m, short_m, _ = detect_signals_sar_chop(
        close, high, low,
        sar_start=sar_start, sar_increment=sar_increment, sar_max=sar_max,
        ma_fast_period=ma_fast_period,
        ma_long_period=ma_long_period, ma_short_period=ma_short_period,
        chop_period=chop_period, chop_threshold=chop_threshold,
        macd_fast=macd_fast, macd_slow=macd_slow, macd_signal=macd_signal,
        use_lucid_sar=use_lucid_sar,
        use_utbot_trail=use_utbot_trail,
        utbot_atr_period=utbot_atr_period, utbot_atr_mult=utbot_atr_mult,
        atr_period=atr_period,
    )
    sar, _bull, _lf, _sf = _parabolic_sar(
        high, low, start=sar_start, step=sar_increment, max_af=sar_max,
    )
    chop = _chop_index(high, low, close, chop_period)
    if use_utbot_trail:
        ut_dir, _, _ = _utbot_trail_flips(
            close, high, low, atr_period=utbot_atr_period, atr_mult=utbot_atr_mult,
        )
        trail_bull = bool(ut_dir[-1] == 1)
    else:
        trail_bull = True
    i = n - 1
    sar_v = float(sar[i]) if np.isfinite(sar[i]) else 0.0
    chop_v = float(chop[i]) if np.isfinite(chop[i]) else 0.0
    return {
        "sar_chop_long_setup": bool(long_m[i]),
        "sar_chop_short_setup": bool(short_m[i]),
        "sar_value": sar_v,
        "chop_value": chop_v,
        "sar_chop_trail_bull": trail_bull,
    }


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

@dataclass
class TradeResult:
    entry_bar: int
    exit_bar: int
    entry_price: float
    exit_price: float
    stop_price: float
    tp_price: float
    pnl: float          # after fees
    is_win: bool
    exit_reason: str     # "stop", "tp", "time_stop"
    hold_bars: int


def _contract_scale(contract_size: float) -> float:
    c = float(contract_size)
    return c if c > 0 else 1.0


def _roundtrip_gross_fee_net(
    entry_price: float,
    exit_price: float,
    side: int,
    fee_pct: float,
    contract_size: float,
    fee_usd_per_contract_per_leg: float,
) -> tuple[float, float, float]:
    """One contract: USD gross PnL, total fees, net. side 1=long, -1=short."""
    cs = _contract_scale(contract_size)
    if side == 1:
        gross = (exit_price - entry_price) * cs
    else:
        gross = (entry_price - exit_price) * cs
    pct = fee_pct * cs
    fee = (
        entry_price * pct
        + exit_price * pct
        + 2.0 * max(0.0, float(fee_usd_per_contract_per_leg))
    )
    return gross, fee, gross - fee


@dataclass
class BacktestMetrics:
    trade_count: int
    win_count: int
    win_rate: float          # 0–1
    total_pnl: float         # net after fees
    avg_pnl: float
    expectancy: float        # avg_win * win_rate - avg_loss * loss_rate
    max_drawdown: float      # peak-to-trough on bar-by-bar equity curve
    max_drawdown_pct: float  # drawdown as % of peak equity
    avg_hold_bars: float
    profit_factor: float     # gross_wins / gross_losses
    sharpe: float            # annualized risk-adjusted return
    sortino: float           # downside-risk-adjusted return
    calmar: float            # annualized return / max drawdown %
    recovery_factor: float   # net profit / max drawdown
    buy_hold_return: float   # simple buy-and-hold % return over same period
    trades: list[TradeResult]


def _intrabar_stop_first(open_price: float, high_price: float, low_price: float) -> bool:
    """TradingView-style intrabar path: infer whether stop (low) was hit before TP (high).

    If open is closer to high -> path is open->high->low->close (TP first).
    If open is closer to low  -> path is open->low->high->close (stop first).

    When ``open_prices`` is absent and both stop and TP print in the same bar, the loop below
    uses ``stop_first = True`` — aligned with paper ``scalp_trader`` same-bar precedence.
    """
    return (open_price - low_price) < (high_price - open_price)


def simulate_trades(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    signal_mask: np.ndarray,
    atr_vals: np.ndarray,
    *,
    open_prices: np.ndarray | None = None,
    atr_stop_mult: float = 1.0,
    atr_tp_mult: float = 2.0,
    max_hold_bars: int = 12,
    fee_pct: float = 0.0,
    contract_size: float = 1.0,
    fee_usd_per_contract_per_leg: float = 0.0,
    slippage_pct: float = 0.0001,
    cooldown_bars: int = 1,
    fill_model: str = "close_slip",
) -> list[TradeResult]:
    """Walk forward through bars with TradingView-style intrabar path simulation.

    Improvements over naive backtester:
      - Intrabar price path: uses bar open to determine stop-before-TP or vice versa
      - Slippage: entry fills worse by slippage_pct, exits degrade toward the losing side
      - Fee per leg applied to entry and exit prices separately
      - fill_model="next_open": entry fills at open of the bar following the signal bar
        (more realistic than close-fill; requires open_prices array)
    """
    n = len(close)
    trades: list[TradeResult] = []
    next_allowed = 0
    has_open = open_prices is not None
    use_next_open = fill_model == "next_open" and has_open

    for i in range(n):
        if i < next_allowed:
            continue
        if not signal_mask[i]:
            continue

        a = atr_vals[i]
        if np.isnan(a) or a <= 0:
            continue

        if use_next_open:
            if i + 1 >= n:
                continue  # no next bar to fill on
            entry_price = open_prices[i + 1] * (1.0 + slippage_pct)
        else:
            entry_price = close[i] * (1.0 + slippage_pct)
        stop_price = entry_price - a * atr_stop_mult
        tp_price = entry_price + a * atr_tp_mult

        if stop_price >= entry_price:
            continue

        exit_bar = min(i + max_hold_bars, n - 1)
        exit_price = close[exit_bar]
        exit_reason = "time_stop"

        for j in range(i + 1, min(i + max_hold_bars + 1, n)):
            stop_hit = low[j] <= stop_price
            tp_hit = high[j] >= tp_price

            if stop_hit and tp_hit:
                if has_open:
                    stop_first = _intrabar_stop_first(open_prices[j], high[j], low[j])
                else:
                    stop_first = True
                if stop_first:
                    exit_price = stop_price * (1.0 - slippage_pct)
                    exit_reason = "stop"
                else:
                    exit_price = tp_price * (1.0 - slippage_pct)
                    exit_reason = "tp"
                exit_bar = j
                break
            elif stop_hit:
                exit_price = stop_price * (1.0 - slippage_pct)
                exit_reason = "stop"
                exit_bar = j
                break
            elif tp_hit:
                exit_price = tp_price * (1.0 - slippage_pct)
                exit_reason = "tp"
                exit_bar = j
                break

        _g, _fc, net_pnl = _roundtrip_gross_fee_net(
            entry_price,
            exit_price,
            1,
            fee_pct,
            contract_size,
            fee_usd_per_contract_per_leg,
        )

        trades.append(TradeResult(
            entry_bar=i,
            exit_bar=exit_bar,
            entry_price=entry_price,
            exit_price=exit_price,
            stop_price=stop_price,
            tp_price=tp_price,
            pnl=net_pnl,
            is_win=net_pnl > 0,
            exit_reason=exit_reason,
            hold_bars=exit_bar - i,
        ))
        next_allowed = exit_bar + cooldown_bars

    return trades


def _trade_is_long(tr: TradeResult) -> bool:
    """Infer direction from protective stop vs entry (same convention as live / portfolio helpers)."""
    return float(tr.stop_price) < float(tr.entry_price)


def _median_bar_interval_hours(timestamps: np.ndarray) -> float:
    """Typical bar spacing in hours (median positive delta), floor ~1s."""
    if timestamps is None or len(timestamps) < 2:
        return 1.0 / 60.0
    d = np.diff(timestamps.astype(np.float64))
    d = d[(d > 0) & np.isfinite(d)]
    if len(d) == 0:
        return 1.0 / 60.0
    sec = float(np.median(d))
    return max(1.0, sec) / 3600.0


def _apply_funding_to_trades(
    trades: list[TradeResult],
    close: np.ndarray,
    *,
    contract_size: float,
    funding_bps_per_hour: float,
    bar_interval_hours: float,
) -> list[TradeResult]:
    """Constant funding accrual per held bar (signed bps/hour on notional; >0 = longs pay)."""
    r = float(funding_bps_per_hour)
    if r == 0.0 or not trades:
        return trades
    cs = _contract_scale(contract_size)
    bh = max(1e-12, float(bar_interval_hours))
    out: list[TradeResult] = []
    n = len(close)
    for t in trades:
        side = 1 if _trade_is_long(t) else -1
        eb = int(t.entry_bar)
        xb = int(t.exit_bar)
        fund = 0.0
        for j in range(eb, min(xb, n)):
            px = float(close[j])
            if not np.isfinite(px):
                continue
            notional = px * cs
            hourly = notional * (r / 10_000.0) * bh
            fund += -hourly if side == 1 else hourly
        new_pnl = float(t.pnl) + fund
        out.append(
            replace(t, pnl=new_pnl, is_win=new_pnl > 0.0),
        )
    return out


def _unrealized_usd(tr: TradeResult, px: float, cs: float) -> float:
    """Mark-to-market one open contract in USD (long vs short)."""
    if _trade_is_long(tr):
        return (float(px) - float(tr.entry_price)) * cs
    return (float(tr.entry_price) - float(px)) * cs


def _build_bar_equity(
    trades: list[TradeResult],
    close: np.ndarray,
    n_bars: int,
    *,
    contract_size: float = 1.0,
) -> np.ndarray:
    """Build bar-by-bar equity curve, marking open positions to market every bar.

    Equity starts at 0. During a position, unrealized PnL is MTM in USD (``contract_size``);
    long: (close − entry)×cs, short: (entry − close)×cs. On exit bar, realized ``t.pnl`` locks in.
    """
    cs = _contract_scale(contract_size)
    equity = np.zeros(n_bars, dtype=np.float64)
    realized = 0.0
    trade_idx = 0
    n_trades = len(trades)

    for bar in range(n_bars):
        unrealized = 0.0
        if trade_idx < n_trades:
            t = trades[trade_idx]
            if bar >= t.entry_bar and bar < t.exit_bar:
                unrealized = _unrealized_usd(t, close[bar], cs)
            elif bar >= t.exit_bar:
                realized += t.pnl
                trade_idx += 1
                if trade_idx < n_trades:
                    t2 = trades[trade_idx]
                    if bar >= t2.entry_bar and bar < t2.exit_bar:
                        unrealized = _unrealized_usd(t2, close[bar], cs)
        equity[bar] = realized + unrealized

    return equity


def _recency_weights(trades: list[TradeResult], n_bars: int, half_life_bars: float) -> np.ndarray:
    """Exponential recency weights: w(t) = 2^((entry_bar - last_bar) / half_life).

    Most recent trade has weight ~1.0; a trade ``half_life_bars`` bars ago
    has weight 0.5; two half-lives ago has weight 0.25, etc.
    Weights are normalized to sum to the number of trades so that
    weighted averages remain in the same scale as unweighted ones.
    """
    if not trades or half_life_bars <= 0:
        return np.ones(len(trades), dtype=np.float64)
    last_bar = max(t.entry_bar for t in trades)
    raw = np.array([
        2.0 ** ((t.entry_bar - last_bar) / half_life_bars)
        for t in trades
    ], dtype=np.float64)
    total = raw.sum()
    if total > 0:
        raw *= len(trades) / total
    return raw


def _apply_same_calendar_day_trade_weights(
    w: np.ndarray,
    trades: list[TradeResult],
    timestamps: np.ndarray,
    boost: float,
) -> np.ndarray:
    """Up-weight trades whose entry bar opens on the same UTC date as the last bar."""
    if boost <= 0 or not trades or len(timestamps) == 0:
        return w
    nts = len(timestamps)
    anchor_ts = int(timestamps[nts - 1])
    anchor_date = datetime.datetime.utcfromtimestamp(anchor_ts).date()
    mult = 1.0 + boost
    out = np.array(w, dtype=np.float64, copy=True)
    for i, t in enumerate(trades):
        bi = min(max(int(t.entry_bar), 0), nts - 1)
        ts_i = int(timestamps[bi])
        if datetime.datetime.utcfromtimestamp(ts_i).date() == anchor_date:
            out[i] *= mult
    tot = float(out.sum())
    if tot > 0:
        out *= len(trades) / tot
    return out


def compute_metrics(
    trades: list[TradeResult],
    close: np.ndarray | None = None,
    bars_per_year: float = 525_600.0,
    recency_half_life_bars: float = 0.0,
    *,
    timestamps: np.ndarray | None = None,
    same_calendar_day_boost: float = 0.0,
    contract_size: float = 1.0,
) -> BacktestMetrics:
    """Compute TradingView-style metrics including bar-by-bar equity curve.

    bars_per_year: annualization factor (525600 for 1-minute bars).
    recency_half_life_bars: if > 0, applies exponential recency weighting
      so trades closer to the end of the data count more.  A trade that
      occurred ``half_life_bars`` bars before the most recent trade gets
      half the weight.  Set to 0 to disable (flat weighting).
    same_calendar_day_boost: if > 0 and ``timestamps`` is set, trades whose entry
      falls on the same UTC calendar day as the final bar get extra weight (after
      half-life weights, if any).
    """
    _empty = BacktestMetrics(
        trade_count=0, win_count=0, win_rate=0.0, total_pnl=0.0,
        avg_pnl=0.0, expectancy=0.0, max_drawdown=0.0, max_drawdown_pct=0.0,
        avg_hold_bars=0.0, profit_factor=0.0,
        sharpe=0.0, sortino=0.0, calmar=0.0, recovery_factor=0.0,
        buy_hold_return=0.0, trades=[],
    )
    if not trades:
        return _empty

    n_bars = len(close) if close is not None else 0
    use_half_life = recency_half_life_bars > 0 and len(trades) > 1
    use_day_boost = (
        same_calendar_day_boost > 0
        and timestamps is not None
        and len(timestamps) > 0
        and len(trades) > 0
    )
    if use_half_life:
        w = _recency_weights(trades, n_bars, recency_half_life_bars)
    else:
        w = np.ones(len(trades), dtype=np.float64)
    if use_day_boost:
        w = _apply_same_calendar_day_trade_weights(
            w, trades, timestamps, same_calendar_day_boost,
        )
    use_weights = use_half_life or use_day_boost

    pnls = np.array([t.pnl for t in trades])
    wins = pnls > 0
    trade_count = len(trades)

    if use_weights:
        win_rate = float((w * wins.astype(np.float64)).sum() / w.sum())
        win_count = int(round(win_rate * trade_count))
    else:
        win_count = int(wins.sum())
        win_rate = win_count / trade_count

    total_pnl = float((pnls * w).sum() / w.sum() * trade_count)
    avg_pnl = total_pnl / trade_count
    total_pnl_unweighted = float(pnls.sum())

    if use_weights:
        w_wins = w[wins]
        w_losses = w[~wins]
        gross_wins = float((pnls[wins] * w_wins).sum() / w_wins.sum() * len(w_wins)) if len(w_wins) > 0 else 0.0
        gross_losses = float((-pnls[~wins] * w_losses).sum() / w_losses.sum() * len(w_losses)) if len(w_losses) > 0 else 0.0
    else:
        gross_wins = float(pnls[wins].sum()) if win_count > 0 else 0.0
        gross_losses = float((-pnls[~wins]).sum()) if win_count < trade_count else 0.0

    avg_win = gross_wins / max(1, int(wins.sum())) if int(wins.sum()) > 0 else 0.0
    avg_loss = gross_losses / max(1, trade_count - int(wins.sum())) if (trade_count - int(wins.sum())) > 0 else 0.0
    expectancy = avg_win * win_rate - avg_loss * (1 - win_rate)

    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf") if gross_wins > 0 else 0.0

    # Bar-by-bar equity for drawdown, Sharpe, Sortino
    if close is not None and len(close) > 0:
        n_bars = len(close)
        equity = _build_bar_equity(trades, close, n_bars, contract_size=contract_size)

        peak = np.maximum.accumulate(equity)
        dd = peak - equity
        max_drawdown = float(dd.max()) if len(dd) > 0 else 0.0
        peak_at_dd = float(peak[np.argmax(dd)]) if len(dd) > 0 and peak[np.argmax(dd)] > 0 else 1.0
        max_drawdown_pct = (max_drawdown / peak_at_dd * 100.0) if peak_at_dd > 0 else 0.0

        # Recency / calendar-day weights apply to total_pnl (growth objective). Bar equity is
        # path-based from raw trade PnL + MTM — Sharpe/Sortino are disabled when weights are on
        # to avoid mixing weighted objectives with unweighted return vol. Calmar / recovery use
        # unweighted sum PnL vs the same drawdown path.

        bar_returns = np.diff(equity)
        if use_weights:
            sharpe = 0.0
            sortino = 0.0
        elif len(bar_returns) > 1 and np.std(bar_returns) > 0:
            mean_r = np.mean(bar_returns)
            std_r = np.std(bar_returns)
            sharpe = (mean_r / std_r) * math.sqrt(bars_per_year)

            neg_returns = bar_returns[bar_returns < 0]
            downside_std = np.std(neg_returns) if len(neg_returns) > 1 else std_r
            sortino = (mean_r / downside_std) * math.sqrt(bars_per_year) if downside_std > 0 else sharpe
        else:
            sharpe = 0.0
            sortino = 0.0

        total_bars = n_bars
        notional_anchor = max(1e-12, trades[0].entry_price * _contract_scale(contract_size))
        pnl_for_path_metrics = total_pnl_unweighted if use_weights else total_pnl
        annualized_return = (pnl_for_path_metrics / notional_anchor) * (
            bars_per_year / max(1, total_bars)
        )
        calmar = annualized_return / (max_drawdown_pct / 100.0) if max_drawdown_pct > 0 else 0.0

        buy_hold_return = ((close[-1] - close[0]) / close[0] * 100.0) if close[0] > 0 else 0.0
    else:
        cum_pnl = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cum_pnl)
        drawdowns = running_max - cum_pnl
        max_drawdown = float(drawdowns.max()) if len(drawdowns) > 0 else 0.0
        max_drawdown_pct = 0.0
        sharpe = 0.0
        sortino = 0.0
        calmar = 0.0
        buy_hold_return = 0.0

    recovery_factor = (
        (total_pnl_unweighted if use_weights else total_pnl) / max_drawdown
        if max_drawdown > 0
        else 0.0
    )
    avg_hold = float(np.mean([t.hold_bars for t in trades]))

    return BacktestMetrics(
        trade_count=trade_count,
        win_count=win_count,
        win_rate=win_rate,
        total_pnl=total_pnl,
        avg_pnl=avg_pnl,
        expectancy=expectancy,
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        avg_hold_bars=avg_hold,
        profit_factor=profit_factor,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        recovery_factor=recovery_factor,
        buy_hold_return=buy_hold_return,
        trades=trades,
    )


# ---------------------------------------------------------------------------
# Single-combo evaluation (convenience)
# ---------------------------------------------------------------------------

# Modes with an explicit ``evaluate_params`` dispatch; champion ``mode`` must be one of these.
WFO_REGISTERED_STRATEGY_MODES: frozenset[str] = frozenset({
    "daviddtech_scalp",
    "ema_momentum",
    "ema_scalp",
    "macd_scalp",
    "rsi_reversion",
    "supertrend",
    "squeeze_momentum",
    "qqe_mod",
    "utbot_alert",
    "hull_suite",
    "sar_chop",
})


@dataclass
class ParamSet:
    mode: str = "ema_momentum"  # includes "daviddtech_scalp"
    # EMA momentum params
    ema_fast: int = 5
    ema_slow: int = 13
    rsi_period: int = 9
    atr_period: int = 14
    vol_ma_period: int = 20
    vol_mult: float = 1.5
    min_signals: int = 2
    atr_stop_mult: float = 1.0
    atr_tp_mult: float = 1.5
    max_hold_bars: int = 15
    fee_pct: float = 0.0              # per-leg fee; 0 = CDE promo / sim, else set from venue
    contract_size: float = 1.0       # underlying per 1 contract (CDE); scales PnL + %-fee notional
    fee_usd_per_contract_per_leg: float = 0.0  # flat NFA/clearing etc. per contract per fill side
    slippage_pct: float = 0.0001     # per-fill slippage (1 bps default for liquid pairs)
    # "close_slip" = fill at signal-bar close + slippage (optimistic, original default).
    # "next_open"  = fill at next bar open + slippage (realistic; recommended for WFO).
    fill_model: str = "close_slip"
    # Constant perps funding (signed bps/hour on notional; >0 = longs pay).
    backtest_funding_enabled: bool = False
    backtest_funding_bps_per_hour: float = 0.0
    # RSI reversion params
    rsi_buy_threshold: float = 10.0
    rsi_sell_threshold: float = 50.0
    rsi_short_threshold: float = 70.0  # overbought short entry (perps)
    # EMA scalp params
    ema_scalp_period: int = 20
    ema_scalp_sr_bars: int = 8
    # MACD scalp params (Scalp Pro — Ehlers super-smoother)
    macd_fast_len: int = 8
    macd_slow_len: int = 10
    macd_signal_len: int = 8
    # Optimized Strategy (DaviddTech-style)
    t3_length: int = 7
    t3_vfactor: float = 0.7
    hlc_close_period: int = 5
    hlc_low_period: int = 13
    hlc_high_period: int = 34
    adx_period: int = 14
    adx_threshold: float = 20.0
    wae_sensitivity: float = 150.0
    wae_fast_len: int = 20
    wae_slow_len: int = 40
    wae_bb_len: int = 20
    wae_bb_mult: float = 2.0
    # Supertrend
    supertrend_period: int = 10
    supertrend_factor: float = 3.0
    # Squeeze Momentum
    squeeze_bb_period: int = 20
    squeeze_bb_mult: float = 2.0
    squeeze_kc_mult: float = 1.5
    squeeze_mom_period: int = 12
    # QQE Mod
    qqe_rsi_period: int = 14
    qqe_factor: float = 4.238
    qqe_smoothing: int = 5
    # UT Bot Alert
    utbot_atr_period: int = 10
    utbot_atr_mult: float = 1.0
    # Hull Suite (TV default length from operator preset)
    hull_period: int = 38
    # SAR + CHOP (TV "5 min bot scalper" decode)
    sar_start: float = 0.02
    sar_increment: float = 0.02
    sar_max: float = 0.2
    sar_chop_ma_fast_period: int = 7
    sar_chop_ma_long_period: int = 200
    sar_chop_ma_short_period: int = 50
    sar_chop_chop_period: int = 14
    sar_chop_chop_threshold: float = 38.2
    sar_chop_macd_fast: int = 12
    sar_chop_macd_slow: int = 26
    sar_chop_macd_signal: int = 9
    sar_chop_use_lucid: bool = True
    sar_chop_use_utbot_trail: bool = True
    sar_chop_utbot_atr_period: int = 10
    sar_chop_utbot_mult: float = 2.0


def apply_param_dict_overrides(base: ParamSet, overrides: dict) -> ParamSet:
    """Merge flat dict values (JSON champion, tuner ``params_changed``) into a ParamSet.

    Coerces scalars to match existing field types on ``base``. Unknown keys are ignored.
    """
    kw: dict[str, object] = {}
    for k, v in overrides.items():
        if not hasattr(base, k):
            continue
        cur = getattr(base, k)
        if isinstance(cur, bool):
            kw[k] = bool(v)
        elif isinstance(cur, int) and not isinstance(cur, bool):
            kw[k] = int(round(float(v)))
        elif isinstance(cur, float):
            kw[k] = float(v)
        elif isinstance(cur, str):
            kw[k] = str(v)
        else:
            kw[k] = v
    return replace(base, **kw)


def min_entry_bar_for_last_hours(bars: dict[str, np.ndarray], lookback_hours: float) -> int:
    """First bar index whose timestamp is >= (last_ts - lookback_hours).

    Used to score only trades that *open* inside the recent window while still
    running signal simulation on the full series (indicator warmup preserved).
    Returns ``len(timestamp)`` if no bar falls in the window (filters all trades).
    """
    ts = bars["timestamp"]
    if len(ts) == 0 or lookback_hours <= 0:
        return 0
    latest = float(ts[-1])
    cutoff_ts = latest - float(lookback_hours) * 3600.0
    idx = int(np.searchsorted(ts, cutoff_ts, side="left"))
    if idx >= len(ts):
        return len(ts)
    return idx


def evaluate_params(
    bars: dict[str, np.ndarray],
    params: ParamSet,
    *,
    recency_half_life_bars: float = 0.0,
    bars_per_year: float | None = None,
    min_entry_bar: int = 0,
    same_calendar_day_boost: float = 0.0,
    breakeven_atr_trigger: float = 0.0,
    trail_atr_trigger: float = 0.0,
    trail_atr_distance: float = 0.0,
    counter_signal_exit: bool = False,
) -> BacktestMetrics:
    """Run full signal detection + trade simulation for one parameter set.

    Dispatches to the correct mode's signal detector and simulator.
    Passes open prices for TradingView-style intrabar path resolution.

    recency_half_life_bars: if > 0, applies exponential recency weighting
      to the resulting metrics so recent trades count more.
    bars_per_year: annualization for Sharpe (default 525600 = 1-minute bars).
      For 15m bars use 35040; for 5m use 105120.
    min_entry_bar: if > 0, only trades with entry_bar >= this index are kept
      before metrics (recent-window scoring; full bars still used for signals).
    same_calendar_day_boost: WFO train scoring — up-weight trades on the UTC day
      of the last bar in ``bars`` (see ``compute_metrics``).
    breakeven_atr_trigger / trail_atr_trigger / trail_atr_distance: forwarded
      to simulate_trades_bidir to model live break-even and trailing stops.
    counter_signal_exit: if True, close position on opposite signal (live behavior).
    """
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    open_prices = bars.get("open")
    fee = params.fee_pct
    slip = params.slippage_pct
    fm = params.fill_model
    cs = float(getattr(params, "contract_size", 1.0) or 1.0)
    fee_u = float(getattr(params, "fee_usd_per_contract_per_leg", 0.0) or 0.0)
    bpy = 525_600.0 if bars_per_year is None else float(bars_per_year)

    # Shared kwargs for live-matching exit mechanics
    _sim_extras: dict = {}
    if breakeven_atr_trigger > 0.0:
        _sim_extras["breakeven_atr_trigger"] = breakeven_atr_trigger
    if trail_atr_trigger > 0.0 and trail_atr_distance > 0.0:
        _sim_extras["trail_atr_trigger"] = trail_atr_trigger
        _sim_extras["trail_atr_distance"] = trail_atr_distance
    if counter_signal_exit:
        _sim_extras["counter_signal_exit"] = True

    if params.mode == "auto":
        # Legacy ParamSet — neutral default when mode was left as "auto".
        params = replace(params, mode=normalize_auto_mode_fallback("ema_momentum"))

    if params.mode == "macd_scalp":
        long_m, short_m, atr_vals, _, _ = detect_signals_macd(
            close=close, high=high, low=low,
            fast_len=params.macd_fast_len,
            slow_len=params.macd_slow_len,
            signal_len=params.macd_signal_len,
            atr_period=params.atr_period,
        )
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip, fill_model=fm,
            **_sim_extras,
        )
    elif params.mode == "ema_scalp":
        long_m, short_m, atr_vals, _high_n, _low_n = detect_signals_ema_scalp(
            close=close, high=high, low=low,
            ema_period=params.ema_scalp_period,
            atr_period=params.atr_period,
            sr_bars=params.ema_scalp_sr_bars,
        )
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip, fill_model=fm,
            **_sim_extras,
        )
    elif params.mode == "rsi_reversion":
        long_m, short_m, atr_vals, rsi_vals = detect_signals_rsi(
            close=close, high=high, low=low,
            rsi_period=params.rsi_period,
            atr_period=params.atr_period,
            rsi_buy_threshold=params.rsi_buy_threshold,
            rsi_sell_threshold=params.rsi_sell_threshold,
            rsi_short_threshold=params.rsi_short_threshold,
        )
        trades = simulate_trades_rsi(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m,
            atr_vals=atr_vals, rsi_vals=rsi_vals,
            rsi_sell_threshold=params.rsi_sell_threshold,
            rsi_short_cover_threshold=params.rsi_buy_threshold,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip,
            # rsi_reversion exits are driven by RSI level, not next-open fill;
            # fill_model still controls entry price for the open
            fill_model=fm,
        )
    elif params.mode == "daviddtech_scalp":
        long_m, short_m, atr_vals = detect_signals_daviddtech(
            close=close, high=high, low=low,
            atr_period=params.atr_period,
            adx_period=params.adx_period,
            t3_length=params.t3_length,
            t3_vfactor=params.t3_vfactor,
            hlc_close_period=params.hlc_close_period,
            hlc_low_period=params.hlc_low_period,
            hlc_high_period=params.hlc_high_period,
            adx_threshold=params.adx_threshold,
            wae_sensitivity=params.wae_sensitivity,
            wae_fast_len=params.wae_fast_len,
            wae_slow_len=params.wae_slow_len,
            wae_bb_len=params.wae_bb_len,
            wae_bb_mult=params.wae_bb_mult,
        )
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip, fill_model=fm,
            **_sim_extras,
        )
    elif params.mode == "supertrend":
        long_m, short_m, atr_vals = detect_signals_supertrend(
            close=close, high=high, low=low,
            period=params.supertrend_period,
            factor=params.supertrend_factor,
            atr_period=params.atr_period,
        )
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip, fill_model=fm,
            **_sim_extras,
        )
    elif params.mode == "squeeze_momentum":
        long_m, short_m, atr_vals = detect_signals_squeeze(
            close=close, high=high, low=low,
            bb_period=params.squeeze_bb_period,
            bb_mult=params.squeeze_bb_mult,
            kc_mult=params.squeeze_kc_mult,
            mom_period=params.squeeze_mom_period,
            atr_period=params.atr_period,
        )
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip, fill_model=fm,
            **_sim_extras,
        )
    elif params.mode == "qqe_mod":
        long_m, short_m, atr_vals = detect_signals_qqe(
            close=close, high=high, low=low,
            rsi_period=params.qqe_rsi_period,
            qqe_factor=params.qqe_factor,
            qqe_smoothing=params.qqe_smoothing,
            atr_period=params.atr_period,
        )
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip, fill_model=fm,
            **_sim_extras,
        )
    elif params.mode == "utbot_alert":
        long_m, short_m, atr_vals = detect_signals_utbot(
            close=close, high=high, low=low,
            atr_period=params.utbot_atr_period,
            atr_mult=params.utbot_atr_mult,
        )
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip, fill_model=fm,
            **_sim_extras,
        )
    elif params.mode == "hull_suite":
        long_m, short_m, atr_vals = detect_signals_hull(
            close=close, high=high, low=low,
            hull_period=params.hull_period,
            atr_period=params.atr_period,
        )
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip, fill_model=fm,
            **_sim_extras,
        )
    elif params.mode == "sar_chop":
        long_m, short_m, atr_vals = detect_signals_sar_chop(
            close=close, high=high, low=low,
            sar_start=params.sar_start,
            sar_increment=params.sar_increment,
            sar_max=params.sar_max,
            ma_fast_period=params.sar_chop_ma_fast_period,
            ma_long_period=params.sar_chop_ma_long_period,
            ma_short_period=params.sar_chop_ma_short_period,
            chop_period=params.sar_chop_chop_period,
            chop_threshold=params.sar_chop_chop_threshold,
            macd_fast=params.sar_chop_macd_fast,
            macd_slow=params.sar_chop_macd_slow,
            macd_signal=params.sar_chop_macd_signal,
            use_lucid_sar=params.sar_chop_use_lucid,
            use_utbot_trail=params.sar_chop_use_utbot_trail,
            utbot_atr_period=params.sar_chop_utbot_atr_period,
            utbot_atr_mult=params.sar_chop_utbot_mult,
            atr_period=params.atr_period,
        )
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip, fill_model=fm,
            **_sim_extras,
        )
    elif params.mode == "ema_momentum":
        long_m, short_m, atr_vals = detect_signals_ema(
            close=close, high=high, low=low,
            volume=bars["volume"], timestamp=bars["timestamp"],
            ema_fast_period=params.ema_fast,
            ema_slow_period=params.ema_slow,
            rsi_period=params.rsi_period,
            atr_period=params.atr_period,
            vol_ma_period=params.vol_ma_period,
            vol_mult=params.vol_mult,
            min_signals=params.min_signals,
        )
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=long_m, short_mask=short_m, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, contract_size=cs, fee_usd_per_contract_per_leg=fee_u, slippage_pct=slip, fill_model=fm,
            **_sim_extras,
        )
    else:
        raise ValueError(
            f"evaluate_params: unknown strategy mode {params.mode!r}. "
            f"Registered modes: {sorted(WFO_REGISTERED_STRATEGY_MODES)}",
        )
    if min_entry_bar > 0:
        trades = [t for t in trades if t.entry_bar >= min_entry_bar]

    ts_arr = bars.get("timestamp")
    bar_h = _median_bar_interval_hours(ts_arr if ts_arr is not None else np.array([], dtype=np.float64))
    fund_bps = float(getattr(params, "backtest_funding_bps_per_hour", 0.0) or 0.0)
    if bool(getattr(params, "backtest_funding_enabled", False)) and abs(fund_bps) > 1e-18:
        trades = _apply_funding_to_trades(
            trades,
            close,
            contract_size=cs,
            funding_bps_per_hour=fund_bps,
            bar_interval_hours=bar_h,
        )

    return compute_metrics(
        trades, close=close,
        bars_per_year=bpy,
        recency_half_life_bars=recency_half_life_bars,
        timestamps=bars.get("timestamp") if same_calendar_day_boost > 0 else None,
        same_calendar_day_boost=same_calendar_day_boost,
        contract_size=cs,
    )


# ---------------------------------------------------------------------------
# Grid evaluation (batch)
# ---------------------------------------------------------------------------

def build_default_grid(fee_pct: float = 0.0, fill_model: str = "close_slip") -> list[ParamSet]:
    """Build default WFO parameter grid (subset of registered modes for CPU/runtime).

    Omitted from this grid (still valid in ``evaluate_params`` / live if champion says so):
    ``ema_scalp``, ``squeeze_momentum``, ``qqe_mod``, ``utbot_alert`` — high overlap or
    niche vs the retained blocks; see AGENTS / operator notes when re-expanding.
    """
    grid: list[ParamSet] = []

    # Optimized Strategy (DaviddTech-style) — compact grid
    for t3_len in (6, 8, 10):
        for adx_th in (18.0, 22.0, 28.0):
            for max_hold in (8, 12, 16):
                for stop_mult in (1.0, 1.5, 2.0):
                    for tp_mult in (2.0, 3.0, 4.0):
                        grid.append(ParamSet(
                            mode="daviddtech_scalp",
                            t3_length=t3_len,
                            adx_threshold=adx_th,
                            max_hold_bars=max_hold,
                            atr_stop_mult=stop_mult,
                            atr_tp_mult=tp_mult,
                            fee_pct=fee_pct,
                            fill_model=fill_model,
                        ))

    # EMA momentum combos — TP range extended to 5x ATR for fee-awareness
    # Note: min_signals is accepted by ParamSet but discarded in detect_signals_ema;
    # a single default value is used to avoid doubling the grid with no effect.
    for ema_fast in (3, 5, 8):
        for ema_slow in (10, 13, 21):
            if ema_fast >= ema_slow:
                continue
            for max_hold in (5, 10, 15, 25):
                for stop_mult in (0.75, 1.0, 1.5):
                    for tp_mult in (1.5, 2.0, 3.0, 4.0, 5.0):
                        grid.append(ParamSet(
                            mode="ema_momentum",
                            ema_fast=ema_fast,
                            ema_slow=ema_slow,
                            max_hold_bars=max_hold,
                            atr_stop_mult=stop_mult,
                            atr_tp_mult=tp_mult,
                            fee_pct=fee_pct,
                            fill_model=fill_model,
                        ))

    # macd_scalp — compact grid (operator: eligible for WFO; holdout gates still apply).
    for macd_fast in (6, 8):
        for macd_slow in (14, 18):
            if macd_fast >= macd_slow:
                continue
            for macd_signal in (7, 9):
                for max_hold in (5, 10, 15):
                    for stop_mult in (1.0, 1.5):
                        for tp_mult in (2.0, 3.0):
                            grid.append(ParamSet(
                                mode="macd_scalp",
                                macd_fast_len=macd_fast,
                                macd_slow_len=macd_slow,
                                macd_signal_len=macd_signal,
                                max_hold_bars=max_hold,
                                atr_stop_mult=stop_mult,
                                atr_tp_mult=tp_mult,
                                fee_pct=fee_pct,
                                fill_model=fill_model,
                            ))

    # Supertrend combos (UT Bot grid omitted — correlated ATR-trail family; faster WFO.)
    for st_period in (7, 10, 14):
        for st_factor in (2.0, 3.0, 4.0):
            for max_hold in (5, 10, 20):
                for stop_mult in (0.75, 1.0, 1.5):
                    for tp_mult in (1.5, 2.5, 4.0):
                        grid.append(ParamSet(
                            mode="supertrend",
                            supertrend_period=st_period,
                            supertrend_factor=st_factor,
                            max_hold_bars=max_hold,
                            atr_stop_mult=stop_mult,
                            atr_tp_mult=tp_mult,
                            fee_pct=fee_pct,
                            fill_model=fill_model,
                        ))

    # Hull Suite — wider ``hull_period`` sweep for WFO (CDE fees/fills ≠ TradingView); TV preset 38 included.
    for h_period in (26, 32, 38, 44, 55, 68, 89):
        for max_hold in (5, 10, 20):
            for stop_mult in (0.75, 1.0, 1.5):
                for tp_mult in (1.5, 2.5, 4.0):
                    grid.append(ParamSet(
                        mode="hull_suite",
                        hull_period=h_period,
                        max_hold_bars=max_hold,
                        atr_stop_mult=stop_mult,
                        atr_tp_mult=tp_mult,
                        fee_pct=fee_pct,
                        fill_model=fill_model,
                    ))

    # RSI reversion combos (long oversold + short overbought for perps)
    for rsi_buy in (8.0, 10.0, 15.0, 20.0):
        for rsi_sell in (40.0, 50.0, 60.0):
            for rsi_short in (65.0, 70.0, 75.0):
                for max_hold in (5, 10, 15, 25):
                    for stop_mult in (1.0, 1.5, 2.0):
                        grid.append(ParamSet(
                            mode="rsi_reversion",
                            rsi_buy_threshold=rsi_buy,
                            rsi_sell_threshold=rsi_sell,
                            rsi_short_threshold=rsi_short,
                            max_hold_bars=max_hold,
                            atr_stop_mult=stop_mult,
                            fee_pct=fee_pct,
                            fill_model=fill_model,
                        ))

    # SAR + CHOP combos (TV "5 min bot scalper" decode)
    # Critical dims: CHOP period (10=TV-native vs 14=default), CHOP threshold (regime),
    # MA length (trend), UT Bot mult (trail gate). Lucid SAR hardcoded True (confirmed improvement).
    # stop_mult floor raised to 1.5 — 1.0×ATR is too tight for CDE fill-lag on low-ATR pairs.
    # Total: 2×2×3×2×2×3×3×2×2 = 864 (same as before).
    for sar_step in (0.02, 0.03):
        for sar_max_af in (0.2, 0.3):
            for chop_th in (38.2, 50.0, 61.8):
                for chop_per in (10, 14):
                    for ma_long in (100, 200):
                        for ut_mult in (1.0, 2.0, 3.0):
                            for max_hold in (8, 16, 24):
                                for stop_mult in (1.5, 2.0):
                                    for tp_mult in (2.0, 3.0):
                                        grid.append(ParamSet(
                                            mode="sar_chop",
                                            sar_start=sar_step,
                                            sar_increment=sar_step,
                                            sar_max=sar_max_af,
                                            sar_chop_ma_long_period=ma_long,
                                            sar_chop_chop_period=chop_per,
                                            sar_chop_chop_threshold=chop_th,
                                            sar_chop_use_lucid=True,
                                            sar_chop_utbot_mult=ut_mult,
                                            max_hold_bars=max_hold,
                                            atr_stop_mult=stop_mult,
                                            atr_tp_mult=tp_mult,
                                            fee_pct=fee_pct,
                                            fill_model=fill_model,
                                        ))

    return grid
