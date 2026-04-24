"""TradingView strategy shootout — test 3 Pine-derived scalp strategies vs existing modes.

Strategies tested (all from open-source TradingView Pine scripts):
  1. tv_ema50_macd_rsi  — ionut081's "Scalping 15min: EMA+MACD+RSI+ATR SL/TP"
     Entry: price > EMA(50), MACD histogram positive, RSI 50-70
     Exit: 1x ATR stop, 2x ATR TP

  2. tv_vwap_rsi        — michaelriggs' "VWAP-RSI Scalper FINAL v1"
     Entry: RSI(3) oversold (<25) AND price > VWAP AND price > EMA(50)
     Exit: 1x ATR stop, 2x ATR TP

  3. tv_meta_confluence  — salvadorvelasco2009's "META Scalp 5m" (simplified)
     Scores: EMA trend, RSI bias, ADX strength, volume, MACD direction
     Entry: score >= threshold (60/100)
     Exit: 1.2x ATR stop, 1.5x ATR TP

Run:  python tools/tv_strategy_shootout.py
"""

from __future__ import annotations
import math, sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "server"))

import tomllib
from scalp_bot import bar_store
from scalp_bot.scalp_config import load_scalp_config
from scalp_bot.scalp_vec_backtest import (
    TradeResult, BacktestMetrics, ParamSet,
    ema, rsi, atr, session_vwap, volume_ma,
    simulate_trades, compute_metrics, evaluate_params,
    _intrabar_stop_first,
)
from scalp_bot.scalp_wfo import _params_from_config
from scalp_bot.strategy_lookback import STRATEGY_MODES, _slice_bars_to_hours


# ═══════════════════════════════════════════════════════════════════════════
# Indicator helpers
# ═══════════════════════════════════════════════════════════════════════════

def stoch_rsi(close: np.ndarray, rsi_len: int = 14, stoch_len: int = 14,
              smooth_k: int = 3) -> np.ndarray:
    """Stochastic RSI %K (smoothed)."""
    r = rsi(close, rsi_len)
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(stoch_len - 1 + rsi_len, n):
        window = r[i - stoch_len + 1: i + 1]
        lo = np.nanmin(window)
        hi = np.nanmax(window)
        out[i] = (r[i] - lo) / (hi - lo) * 100.0 if hi > lo else 50.0
    # smooth
    kernel = np.ones(smooth_k, dtype=np.float64) / smooth_k
    smoothed = np.convolve(np.nan_to_num(out, nan=50.0), kernel, mode="full")[:n]
    smoothed[:rsi_len + stoch_len + smooth_k] = np.nan
    return smoothed


def macd_histogram(close: np.ndarray, fast: int = 12, slow: int = 26,
                   signal: int = 9) -> np.ndarray:
    """Standard MACD histogram (MACD line - signal line)."""
    ema_f = ema(close, fast)
    ema_s = ema(close, slow)
    macd_line = ema_f - ema_s
    sig = ema(np.nan_to_num(macd_line, nan=0.0), signal)
    return macd_line - sig


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ADX, +DI, -DI."""
    n = len(close)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i-1]
        down = low[i-1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    # Smoothed (Wilder)
    atr_s = np.full(n, np.nan)
    pdm_s = np.full(n, np.nan)
    mdm_s = np.full(n, np.nan)
    atr_s[period] = np.sum(tr[1:period+1])
    pdm_s[period] = np.sum(plus_dm[1:period+1])
    mdm_s[period] = np.sum(minus_dm[1:period+1])
    for i in range(period+1, n):
        atr_s[i] = atr_s[i-1] - atr_s[i-1]/period + tr[i]
        pdm_s[i] = pdm_s[i-1] - pdm_s[i-1]/period + plus_dm[i]
        mdm_s[i] = mdm_s[i-1] - mdm_s[i-1]/period + minus_dm[i]
    plus_di = np.where(atr_s > 0, 100.0 * pdm_s / atr_s, 0.0)
    minus_di = np.where(atr_s > 0, 100.0 * mdm_s / atr_s, 0.0)
    dx = np.where((plus_di + minus_di) > 0, 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di), 0.0)
    adx_out = np.full(n, np.nan)
    start = 2 * period
    if start < n:
        adx_out[start] = np.nanmean(dx[period:start+1])
        for i in range(start+1, n):
            adx_out[i] = (adx_out[i-1] * (period - 1) + dx[i]) / period
    return adx_out, plus_di, minus_di


# ═══════════════════════════════════════════════════════════════════════════
# Strategy 1: EMA50 + MACD + RSI (ionut081, 15min optimized)
# ═══════════════════════════════════════════════════════════════════════════

def detect_tv_ema50_macd_rsi(
    bars: dict[str, np.ndarray],
    ema_len: int = 50,
    rsi_len: int = 14,
    rsi_lo: float = 50.0,
    rsi_hi: float = 70.0,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray]:
    close, high, low = bars["close"], bars["high"], bars["low"]
    ema_50 = ema(close, ema_len)
    macd_hist = macd_histogram(close)
    rsi_vals = rsi(close, rsi_len)
    atr_vals = atr(high, low, close, atr_period)

    entry = (
        (close > ema_50) &
        (macd_hist > 0) &
        (rsi_vals > rsi_lo) & (rsi_vals < rsi_hi) &
        ~np.isnan(atr_vals) & (atr_vals > 0)
    )
    warmup = max(ema_len, 26, rsi_len, atr_period)
    entry[:warmup] = False
    return entry, atr_vals


# ═══════════════════════════════════════════════════════════════════════════
# Strategy 2: VWAP-RSI Scalper (michaelriggs, prop-firm style)
# ═══════════════════════════════════════════════════════════════════════════

def detect_tv_vwap_rsi(
    bars: dict[str, np.ndarray],
    rsi_len: int = 3,
    rsi_threshold: float = 25.0,
    ema_len: int = 50,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray]:
    close, high, low = bars["close"], bars["high"], bars["low"]
    volume, ts = bars["volume"], bars["timestamp"]
    rsi_vals = rsi(close, rsi_len)
    vwap_vals = session_vwap(ts, high, low, close, volume)
    ema_vals = ema(close, ema_len)
    atr_vals = atr(high, low, close, atr_period)

    entry = (
        (rsi_vals <= rsi_threshold) &
        (close > vwap_vals) &
        (close > ema_vals) &
        ~np.isnan(atr_vals) & (atr_vals > 0)
    )
    warmup = max(ema_len, rsi_len, atr_period)
    entry[:warmup] = False
    return entry, atr_vals


# ═══════════════════════════════════════════════════════════════════════════
# Strategy 3: META Scalp confluence score (simplified)
# ═══════════════════════════════════════════════════════════════════════════

def detect_tv_meta_confluence(
    bars: dict[str, np.ndarray],
    ema_fast_len: int = 21,
    ema_slow_len: int = 55,
    rsi_len: int = 14,
    adx_period: int = 14,
    adx_min: float = 18.0,
    vol_mult: float = 1.1,
    score_threshold: int = 60,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray]:
    close, high, low = bars["close"], bars["high"], bars["low"]
    volume = bars["volume"]
    n = len(close)

    ema_f = ema(close, ema_fast_len)
    ema_s = ema(close, ema_slow_len)
    rsi_vals = rsi(close, rsi_len)
    adx_vals, plus_di, minus_di = adx(high, low, close, adx_period)
    macd_hist = macd_histogram(close)
    vol_sma = volume_ma(volume, 20)
    atr_vals = atr(high, low, close, atr_period)

    # Score components (each 0-20, total 0-100)
    score = np.zeros(n, dtype=np.float64)

    # EMA trend (0-25): fast > slow and rising
    ema_bull = (ema_f > ema_s).astype(np.float64)
    ema_rising = np.zeros(n)
    ema_rising[1:] = ((ema_f[1:] > ema_f[:-1])).astype(np.float64)
    score += ema_bull * 15.0 + ema_rising * 10.0

    # RSI bias (0-20): distance from 50, bullish side
    rsi_component = np.clip((np.nan_to_num(rsi_vals, nan=50.0) - 50.0) / 30.0, 0.0, 1.0) * 20.0
    score += rsi_component

    # ADX strength + direction (0-25)
    adx_clean = np.nan_to_num(adx_vals, nan=0.0)
    adx_strong = (adx_clean >= adx_min).astype(np.float64) * 15.0
    di_bull = (np.nan_to_num(plus_di, nan=0.0) > np.nan_to_num(minus_di, nan=0.0)).astype(np.float64) * 10.0
    score += adx_strong + di_bull

    # Volume (0-15)
    vol_clean = np.nan_to_num(vol_sma, nan=1.0)
    vol_ok = (volume > vol_clean * vol_mult).astype(np.float64) * 15.0
    score += vol_ok

    # MACD direction (0-15)
    macd_pos = (np.nan_to_num(macd_hist, nan=0.0) > 0).astype(np.float64) * 15.0
    score += macd_pos

    entry = (
        (score >= score_threshold) &
        (adx_clean >= adx_min) &
        ~np.isnan(atr_vals) & (atr_vals > 0)
    )
    warmup = max(ema_slow_len, rsi_len, 2 * adx_period, 26, atr_period)
    entry[:warmup] = False
    return entry, atr_vals


# ═══════════════════════════════════════════════════════════════════════════
# Strategy 4: DaviddTech 15m Scalper
# T3(7) baseline + HLC Trend confirmation + Waddah Attar + ADX > 20
# SL: ATR bands (mult 3), TP: 1:1 R:R
# Supports both LONG and SHORT
# ═══════════════════════════════════════════════════════════════════════════

def tillson_t3(close: np.ndarray, length: int = 7, vfactor: float = 0.7) -> np.ndarray:
    """Tillson T3 moving average — 6 nested EMAs with volume-factor weighting."""
    a = vfactor
    c1 = -(a ** 3)
    c2 = 3 * a ** 2 + 3 * a ** 3
    c3 = -6 * a ** 2 - 3 * a - 3 * a ** 3
    c4 = 1 + 3 * a + a ** 3 + 3 * a ** 2

    e1 = ema(close, length)
    e2 = ema(np.nan_to_num(e1, nan=close[0]), length)
    e3 = ema(np.nan_to_num(e2, nan=close[0]), length)
    e4 = ema(np.nan_to_num(e3, nan=close[0]), length)
    e5 = ema(np.nan_to_num(e4, nan=close[0]), length)
    e6 = ema(np.nan_to_num(e5, nan=close[0]), length)

    t3_val = c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3
    warmup = 6 * (length - 1)
    t3_val[:warmup] = np.nan
    return t3_val


def hlc_trend(high: np.ndarray, low: np.ndarray, close: np.ndarray,
              close_period: int = 5, low_period: int = 13, high_period: int = 34,
              ) -> tuple[np.ndarray, np.ndarray]:
    """HLC Trend Identifier — returns (green_line, red_line).

    green_line = EMA(close, close_period) - EMA(high, high_period)
    red_line   = EMA(low, low_period) - EMA(close, close_period)
    Bullish: green > 0 and red < 0 and green > red
    Bearish: red > 0 and green < 0 and red > green
    """
    ema_close = ema(close, close_period)
    ema_high = ema(high, high_period)
    ema_low = ema(low, low_period)

    green_line = ema_close - ema_high
    red_line = ema_low - ema_close
    return green_line, red_line


def waddah_attar_explosion(close: np.ndarray,
                           sensitivity: int = 150,
                           fast_len: int = 20,
                           slow_len: int = 40,
                           bb_len: int = 20,
                           bb_mult: float = 2.0,
                           ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Waddah Attar Explosion — returns (trend_up, trend_down, explosion_line).

    trend = (MACD_now - MACD_prev) * sensitivity
    trend_up = max(trend, 0)
    trend_down = max(-trend, 0)
    explosion_line = BB_upper - BB_lower (volatility threshold)
    """
    macd_fast = ema(close, fast_len)
    macd_slow = ema(close, slow_len)
    macd_line = macd_fast - macd_slow

    n = len(close)
    trend = np.zeros(n)
    trend[1:] = (macd_line[1:] - macd_line[:-1]) * sensitivity

    trend_up = np.maximum(trend, 0.0)
    trend_down = np.maximum(-trend, 0.0)

    sma_bb = np.full(n, np.nan)
    for i in range(bb_len - 1, n):
        sma_bb[i] = np.mean(close[i - bb_len + 1: i + 1])
    std_bb = np.full(n, np.nan)
    for i in range(bb_len - 1, n):
        std_bb[i] = np.std(close[i - bb_len + 1: i + 1], ddof=0)

    bb_upper = sma_bb + bb_mult * np.nan_to_num(std_bb, nan=0.0)
    bb_lower = sma_bb - bb_mult * np.nan_to_num(std_bb, nan=0.0)
    explosion_line = bb_upper - bb_lower

    return trend_up, trend_down, explosion_line


def detect_daviddtech_scalp(
    bars: dict[str, np.ndarray],
    t3_len: int = 7,
    t3_vfactor: float = 0.7,
    hlc_close_p: int = 5,
    hlc_low_p: int = 13,
    hlc_high_p: int = 34,
    adx_period: int = 14,
    adx_threshold: float = 20.0,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """DaviddTech 15m scalper — returns (long_mask, short_mask, atr_vals)."""
    close, high, low = bars["close"], bars["high"], bars["low"]
    n = len(close)

    t3_val = tillson_t3(close, t3_len, t3_vfactor)
    green_line, red_line = hlc_trend(high, low, close, hlc_close_p, hlc_low_p, hlc_high_p)
    wae_up, wae_down, wae_explosion = waddah_attar_explosion(close)
    adx_vals, _, _ = adx(high, low, close, adx_period)
    atr_vals = atr(high, low, close, atr_period)

    adx_clean = np.nan_to_num(adx_vals, nan=0.0)
    t3_clean = np.nan_to_num(t3_val, nan=close[0])
    green_clean = np.nan_to_num(green_line, nan=0.0)
    red_clean = np.nan_to_num(red_line, nan=0.0)

    warmup = max(6 * (t3_len - 1), hlc_high_p, 2 * adx_period, 40, atr_period)

    # LONG: T3 below price (green), HLC green>0 & red<0 & green>red,
    #       Waddah Attar bullish (trend_up > 0), ADX > 20
    long_mask = (
        (close > t3_clean) &
        (green_clean > 0) & (red_clean < 0) & (green_clean > red_clean) &
        (wae_up > 0) &
        (adx_clean > adx_threshold) &
        ~np.isnan(atr_vals) & (atr_vals > 0)
    )

    # SHORT: T3 above price (red), HLC red>0 & green<0 & red>green,
    #        Waddah Attar bearish (trend_down > 0), ADX > 20
    short_mask = (
        (close < t3_clean) &
        (red_clean > 0) & (green_clean < 0) & (red_clean > green_clean) &
        (wae_down > 0) &
        (adx_clean > adx_threshold) &
        ~np.isnan(atr_vals) & (atr_vals > 0)
    )

    long_mask[:warmup] = False
    short_mask[:warmup] = False
    return long_mask, short_mask, atr_vals


def simulate_trades_bidir(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    long_mask: np.ndarray,
    short_mask: np.ndarray,
    atr_vals: np.ndarray,
    *,
    open_prices: np.ndarray | None = None,
    atr_stop_mult: float = 3.0,
    atr_tp_mult: float = 3.0,
    max_hold_bars: int = 12,
    fee_pct: float = 0.0002,
    slippage_pct: float = 0.0001,
    cooldown_bars: int = 1,
) -> list[TradeResult]:
    """Bidirectional trade simulator — handles both long and short entries."""
    n = len(close)
    trades: list[TradeResult] = []
    next_allowed = 0
    has_open = open_prices is not None

    for i in range(n):
        if i < next_allowed:
            continue
        a = atr_vals[i]
        if np.isnan(a) or a <= 0:
            continue

        is_long = bool(long_mask[i])
        is_short = bool(short_mask[i])
        if not is_long and not is_short:
            continue
        if is_long and is_short:
            continue  # conflicting signals, skip

        direction = 1.0 if is_long else -1.0
        entry_price = close[i] * (1.0 + direction * slippage_pct)
        stop_price = entry_price - direction * a * atr_stop_mult
        tp_price = entry_price + direction * a * atr_tp_mult

        if is_long and stop_price >= entry_price:
            continue
        if is_short and stop_price <= entry_price:
            continue

        exit_bar = min(i + max_hold_bars, n - 1)
        exit_price = close[exit_bar]
        exit_reason = "time_stop"

        for j in range(i + 1, min(i + max_hold_bars + 1, n)):
            if is_long:
                stop_hit = low[j] <= stop_price
                tp_hit = high[j] >= tp_price
            else:
                stop_hit = high[j] >= stop_price
                tp_hit = low[j] <= tp_price

            if stop_hit and tp_hit:
                if has_open:
                    stop_first = _intrabar_stop_first(open_prices[j], high[j], low[j])
                    if not is_long:
                        stop_first = not stop_first
                else:
                    stop_first = True
                if stop_first:
                    exit_price = stop_price * (1.0 - direction * slippage_pct)
                    exit_reason = "stop"
                else:
                    exit_price = tp_price * (1.0 - direction * slippage_pct)
                    exit_reason = "tp"
                exit_bar = j
                break
            elif stop_hit:
                exit_price = stop_price * (1.0 - direction * slippage_pct)
                exit_reason = "stop"
                exit_bar = j
                break
            elif tp_hit:
                exit_price = tp_price * (1.0 - direction * slippage_pct)
                exit_reason = "tp"
                exit_bar = j
                break

        raw_pnl = direction * (exit_price - entry_price)
        fee_cost = entry_price * fee_pct + exit_price * fee_pct
        net_pnl = raw_pnl - fee_cost

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


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════

TV_STRATEGIES = {
    "tv_ema50_macd_rsi": {
        "detect": detect_tv_ema50_macd_rsi,
        "stop_mult": 1.0,
        "tp_mult": 2.0,
    },
    "tv_vwap_rsi": {
        "detect": detect_tv_vwap_rsi,
        "stop_mult": 1.0,
        "tp_mult": 2.0,
    },
    "tv_meta_confluence": {
        "detect": detect_tv_meta_confluence,
        "stop_mult": 1.2,
        "tp_mult": 1.5,
    },
}

BIDIR_STRATEGIES = {
    "daviddtech_scalp": {
        "detect": detect_daviddtech_scalp,
        "stop_mult": 3.0,
        "tp_mult": 3.0,
    },
}


def run_tv_strategy(
    bars: dict[str, np.ndarray],
    name: str,
    fee_pct: float,
    slippage_pct: float,
    max_hold: int = 12,
    recency_hl: float = 0.0,
) -> BacktestMetrics:
    cfg = TV_STRATEGIES[name]
    entry_mask, atr_vals = cfg["detect"](bars)
    trades = simulate_trades(
        close=bars["close"], high=bars["high"], low=bars["low"],
        signal_mask=entry_mask, atr_vals=atr_vals,
        open_prices=bars.get("open"),
        atr_stop_mult=cfg["stop_mult"],
        atr_tp_mult=cfg["tp_mult"],
        max_hold_bars=max_hold,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
    )
    return compute_metrics(trades, close=bars["close"], recency_half_life_bars=recency_hl)


def run_bidir_strategy(
    bars: dict[str, np.ndarray],
    name: str,
    fee_pct: float,
    slippage_pct: float,
    max_hold: int = 12,
    recency_hl: float = 0.0,
) -> tuple[BacktestMetrics, int, int]:
    """Run a bidirectional (long+short) strategy. Returns (metrics, n_longs, n_shorts)."""
    cfg = BIDIR_STRATEGIES[name]
    long_mask, short_mask, atr_vals = cfg["detect"](bars)
    trades = simulate_trades_bidir(
        close=bars["close"], high=bars["high"], low=bars["low"],
        long_mask=long_mask, short_mask=short_mask, atr_vals=atr_vals,
        open_prices=bars.get("open"),
        atr_stop_mult=cfg["stop_mult"],
        atr_tp_mult=cfg["tp_mult"],
        max_hold_bars=max_hold,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
    )
    n_longs = sum(1 for t in trades if t.exit_price >= t.entry_price or t.stop_price < t.entry_price)
    n_shorts = len(trades) - n_longs
    return compute_metrics(trades, close=bars["close"], recency_half_life_bars=recency_hl), n_longs, n_shorts


def run_at_fee(bot_cfg, look_h, load_days, fee_bps_label: str, fee_pct: float, slip_pct: float) -> None:
    entry_price_approx = {}
    for pk, pc in bot_cfg.pairs.items():
        b15 = bar_store.load_bars(pc.symbol, 15, last_n_days=1)
        if b15 is not None and len(b15["close"]) > 0:
            entry_price_approx[pk] = float(b15["close"][-1])
        else:
            entry_price_approx[pk] = 1.0

    print(f"\n{'#'*100}")
    print(f"  FEE TIER: {fee_bps_label} ({fee_pct*10000:.0f} bps maker per leg)")
    print(f"  Lookback: {look_h}h | Slippage: {slip_pct*10000:.1f} bps | Capital: ${bot_cfg.allocated_capital_usd}")
    print(f"{'#'*100}")

    for pk, pc in bot_cfg.pairs.items():
        sym = pc.symbol
        base = _params_from_config(pc, bot_cfg)
        price = entry_price_approx[pk]
        dollar_risk = bot_cfg.allocated_capital_usd * pc.risk_pct
        print(f"\n{'='*100}")
        print(f"  {pk} ({sym}) | price ~${price:.2f} | risk/trade ~${dollar_risk:.2f}")
        print(f"{'='*100}")

        for interval in (5, 15):
            bars = bar_store.load_bars(sym, interval, last_n_days=load_days)
            if bars is None or len(bars.get("timestamp", [])) < 50:
                print(f"  {interval}m: insufficient data")
                continue
            sl = _slice_bars_to_hours(bars, look_h)
            n = len(sl["close"])
            hl = max(10.0, n / 3.0)
            avg_atr = float(np.nanmean(atr(sl["high"], sl["low"], sl["close"], 14)))
            fee_per_rt = price * fee_pct * 2
            atr_to_fee = avg_atr / fee_per_rt if fee_per_rt > 0 else 0

            print(f"\n  --- {interval}m chart ({n} bars) | avg ATR=${avg_atr:.6f} | RT fee=${fee_per_rt:.6f} | ATR/fee={atr_to_fee:.2f}x ---")
            print(f"  {'Strategy':25} {'PnL':>10} {'Expect':>8} {'WR':>6} {'Trades':>6} {'PF':>6} {'Avg W':>9} {'Avg L':>9} {'RR':>5}")
            print(f"  {'-'*90}")

            results = []

            # Existing modes (override fee_pct from the tier we're testing)
            for mode in STRATEGY_MODES:
                p = replace(base, mode=mode, fee_pct=fee_pct, slippage_pct=slip_pct)
                m = evaluate_params(sl, p, recency_half_life_bars=0.0)
                pf = m.profit_factor if m.profit_factor != float("inf") else 999.0
                wins = [t.pnl for t in m.trades if t.pnl > 0]
                losses = [t.pnl for t in m.trades if t.pnl <= 0]
                avg_w = sum(wins)/len(wins) if wins else 0.0
                avg_l = sum(losses)/len(losses) if losses else 0.0
                rr = abs(avg_w / avg_l) if avg_l != 0 else 0.0
                results.append((mode, float(m.total_pnl), float(m.expectancy),
                                float(m.win_rate), int(m.trade_count), pf,
                                avg_w, avg_l, rr))

            # New TV strategies (long-only)
            for tv_name in TV_STRATEGIES:
                m = run_tv_strategy(sl, tv_name, fee_pct, slip_pct, max_hold=pc.max_hold_bars)
                pf = m.profit_factor if m.profit_factor != float("inf") else 999.0
                wins = [t.pnl for t in m.trades if t.pnl > 0]
                losses = [t.pnl for t in m.trades if t.pnl <= 0]
                avg_w = sum(wins)/len(wins) if wins else 0.0
                avg_l = sum(losses)/len(losses) if losses else 0.0
                rr = abs(avg_w / avg_l) if avg_l != 0 else 0.0
                results.append((tv_name, float(m.total_pnl), float(m.expectancy),
                                float(m.win_rate), int(m.trade_count), pf,
                                avg_w, avg_l, rr))

            # Bidirectional strategies (long + short)
            for bidir_name in BIDIR_STRATEGIES:
                m, n_l, n_s = run_bidir_strategy(sl, bidir_name, fee_pct, slip_pct, max_hold=pc.max_hold_bars)
                pf = m.profit_factor if m.profit_factor != float("inf") else 999.0
                wins = [t.pnl for t in m.trades if t.pnl > 0]
                losses = [t.pnl for t in m.trades if t.pnl <= 0]
                avg_w = sum(wins)/len(wins) if wins else 0.0
                avg_l = sum(losses)/len(losses) if losses else 0.0
                rr = abs(avg_w / avg_l) if avg_l != 0 else 0.0
                label = f"{bidir_name} (L+S)"
                results.append((label, float(m.total_pnl), float(m.expectancy),
                                float(m.win_rate), int(m.trade_count), pf,
                                avg_w, avg_l, rr))

            # Sort by PnL desc (most profitable first)
            results.sort(key=lambda r: r[1], reverse=True)
            for name, pnl, exp, wr, tc, pf, aw, al, rr in results:
                marker = " <-- BEST" if pnl == results[0][1] else ""
                pnl_pct = (pnl / price) * 100 if price > 0 else 0
                print(f"  {name:25} {pnl:+10.6f} {exp:+8.6f} {wr:5.1%} {tc:6} {pf:6.2f} {aw:+9.6f} {al:+9.6f} {rr:5.2f}{marker}")

            if results:
                best = results[0]
                if best[1] > 0:
                    print(f"\n  >> PROFITABLE ({interval}m): {best[0]} net +{best[1]:.6f} ({best[4]} trades, {best[3]:.0%} WR, {best[8]:.1f}:1 RR)")
                else:
                    least_bad = best
                    print(f"\n  >> LEAST LOSS ({interval}m): {least_bad[0]} net {least_bad[1]:+.6f} | {least_bad[4]} trades | expect={least_bad[2]:+.6f}")
                    if atr_to_fee < 2.0:
                        print(f"     ATR/fee={atr_to_fee:.2f}x — ATR doesn't cover round-trip fees. Structural problem, not strategy.")

    print()


def main() -> None:
    raw = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    bot_cfg = load_scalp_config(raw)
    look_h = float(bot_cfg.wfo_train_hours) + float(bot_cfg.wfo_holdout_hours)
    load_days = look_h / 24.0 + 0.25
    slip_pct = bot_cfg.slippage_bps / 10_000.0

    FEE_TIERS = [
        ("SPOT $0+ (current)",    25),
        ("SPOT $10K+ (imminent)", 20),
        ("FUTURES $0+ maker",      2),
        ("FUTURES $0+ taker",      5),
        ("FUTURES 0% maker",       0),
    ]

    for label, bps in FEE_TIERS:
        fee_pct = bps / 10_000.0
        run_at_fee(bot_cfg, look_h, load_days, label, fee_pct, slip_pct)

    print(f"\n{'='*100}")
    print("Compare the SAME strategy across fee tiers to find where profitability flips positive.")
    print("KEY: ATR/fee >= 2x is the minimum for a strategy to have structural edge.")


if __name__ == "__main__":
    main()
