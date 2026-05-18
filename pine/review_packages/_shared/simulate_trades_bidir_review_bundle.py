# AUTO-GENERATED review excerpt from scalp_vec_backtest.py
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ----- Trade sim helpers + simulate_trades_bidir -----

# ----- scalp_vec_backtest.py lines 2316–2327 -----
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Combined SAR + CHOP entry, UT Bot ATR-trail exit.

    Entry conditions (long):
      - primary PSAR flips from bear to bull on this bar (``psar_long_flip``)
      - Choppiness Index < ``chop_threshold`` (regime gate; default ``68`` is looser than strict fib caps — see `_chop_index` doc.)
      - close > MA(7) (fast MA momentum confirmation)
      - close > MA(200) AND MA(50) >= MA(200) (bullish trend stack)
      - MACD histogram > 0
      - if ``use_lucid_sar``: Lucid (close-based) SAR is in bull state
      - if ``use_utbot_trail``: UT Bot trail is bull (prevents entry while exit
        trail still says bear)

# ----- scalp_vec_backtest.py lines 2330–2332 -----
      - PSAR bull→bear flip; same CHOP regime gate
      - close < MA(7), close < MA(50), close < MA(200); MACD hist < 0
      - optional Lucid bear / UT bear gates

# ----- scalp_vec_backtest.py lines 2335–2355 -----
    ``utbot_atr_mult`` choice is reflected in the caller's atr_stop_mult when
    tuned for this mode (the trail agreement gate keeps entries aligned with
    the same stop that would protect a live fill).

    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    if not (len(high) == n and len(low) == n):
        raise ValueError(
            f"detect_signals_sar_chop: OHLC length mismatch close={n} high={len(high)} low={len(low)}",
        )
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

# ----- scalp_vec_backtest.py lines 2378–2387 -----
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    ) = cm
    if warmup >= n:
        _scalp_vec_bt_diag_warn(

# ----- scalp_vec_backtest.py lines 494–689 -----
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

