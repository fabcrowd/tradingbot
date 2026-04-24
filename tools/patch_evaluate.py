from pathlib import Path
p = Path(r"C:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_vec_backtest.py")
t = p.read_text(encoding="utf-8")

# Remove backward-compat alias
t = t.replace("\n\n# Backward-compat alias\ndetect_signals = detect_signals_ema\n", "\n")

# ParamSet: add rsi_short_threshold
old_ps = "    rsi_sell_threshold: float = 50.0\n    # EMA scalp params"
new_ps = "    rsi_sell_threshold: float = 50.0\n    rsi_short_threshold: float = 70.0  # overbought short entry (perps)\n    # EMA scalp params"
if old_ps not in t:
    raise SystemExit("ParamSet rsi block not found")
t = t.replace(old_ps, new_ps, 1)

old_ev = '''def evaluate_params(
    bars: dict[str, np.ndarray],
    params: ParamSet,
    *,
    recency_half_life_bars: float = 0.0,
) -> BacktestMetrics:
    """Run full signal detection + trade simulation for one parameter set.

    Dispatches to the correct mode's signal detector and simulator.
    Passes open prices for TradingView-style intrabar path resolution.

    recency_half_life_bars: if > 0, applies exponential recency weighting
      to the resulting metrics so recent trades count more.
    """
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    open_prices = bars.get("open")
    fee = params.fee_pct
    slip = params.slippage_pct

    if params.mode == "auto":
        params = replace(params, mode="ema_momentum")

    if params.mode == "macd_scalp":
        entry_mask, atr_vals, _, _ = detect_signals_macd(
            close=close, high=high, low=low,
            fast_len=params.macd_fast_len,
            slow_len=params.macd_slow_len,
            signal_len=params.macd_signal_len,
            atr_period=params.atr_period,
        )
        trades = simulate_trades(
            close=close, high=high, low=low,
            signal_mask=entry_mask, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, slippage_pct=slip,
        )
    elif params.mode == "ema_scalp":
        entry_mask, atr_vals, high_n, low_n = detect_signals_ema_scalp(
            close=close, high=high, low=low,
            ema_period=params.ema_scalp_period,
            atr_period=params.atr_period,
            sr_bars=params.ema_scalp_sr_bars,
        )
        trades = simulate_trades_ema_scalp(
            close=close, high=high, low=low,
            entry_mask=entry_mask, atr_vals=atr_vals,
            high_n=high_n, low_n=low_n,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, slippage_pct=slip,
        )
    elif params.mode == "rsi_reversion":
        entry_mask, atr_vals, rsi_vals = detect_signals_rsi(
            close=close, high=high, low=low,
            rsi_period=params.rsi_period,
            atr_period=params.atr_period,
            rsi_buy_threshold=params.rsi_buy_threshold,
            rsi_sell_threshold=params.rsi_sell_threshold,
        )
        trades = simulate_trades_rsi(
            close=close, high=high, low=low,
            entry_mask=entry_mask, atr_vals=atr_vals, rsi_vals=rsi_vals,
            rsi_sell_threshold=params.rsi_sell_threshold,
            atr_stop_mult=params.atr_stop_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, slippage_pct=slip,
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
            fee_pct=fee, slippage_pct=slip,
        )
    else:
        signal_mask, atr_vals, _ = detect_signals_ema(
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
        trades = simulate_trades(
            close=close, high=high, low=low,
            signal_mask=signal_mask, atr_vals=atr_vals,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, slippage_pct=slip,
        )
    return compute_metrics(trades, close=close, recency_half_life_bars=recency_half_life_bars)'''

new_ev = '''def evaluate_params(
    bars: dict[str, np.ndarray],
    params: ParamSet,
    *,
    recency_half_life_bars: float = 0.0,
    bars_per_year: float | None = None,
) -> BacktestMetrics:
    """Run full signal detection + trade simulation for one parameter set.

    Dispatches to the correct mode's signal detector and simulator.
    Passes open prices for TradingView-style intrabar path resolution.

    recency_half_life_bars: if > 0, applies exponential recency weighting
      to the resulting metrics so recent trades count more.
    bars_per_year: annualization for Sharpe (default 525600 = 1-minute bars).
      For 15m bars use 35040; for 5m use 105120.
    """
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    open_prices = bars.get("open")
    fee = params.fee_pct
    slip = params.slippage_pct
    bpy = 525_600.0 if bars_per_year is None else float(bars_per_year)

    if params.mode == "auto":
        params = replace(params, mode="ema_momentum")

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
            fee_pct=fee, slippage_pct=slip,
        )
    elif params.mode == "ema_scalp":
        long_m, short_m, atr_vals, high_n, low_n = detect_signals_ema_scalp(
            close=close, high=high, low=low,
            ema_period=params.ema_scalp_period,
            atr_period=params.atr_period,
            sr_bars=params.ema_scalp_sr_bars,
        )
        trades_long = simulate_trades_ema_scalp(
            close=close, high=high, low=low,
            entry_mask=long_m, atr_vals=atr_vals,
            high_n=high_n, low_n=low_n,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, slippage_pct=slip,
        )
        trades_short = simulate_trades_ema_scalp_short(
            close=close, high=high, low=low,
            entry_mask=short_m, atr_vals=atr_vals,
            high_n=high_n, low_n=low_n,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee, slippage_pct=slip,
        )
        trades = _merge_trades_by_entry(trades_long, trades_short)
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
            fee_pct=fee, slippage_pct=slip,
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
            fee_pct=fee, slippage_pct=slip,
        )
    else:
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
            fee_pct=fee, slippage_pct=slip,
        )
    return compute_metrics(
        trades, close=close,
        bars_per_year=bpy,
        recency_half_life_bars=recency_half_life_bars,
    )'''

if old_ev not in t:
    raise SystemExit("evaluate_params block not found")
t = t.replace(old_ev, new_ev, 1)
p.write_text(t, encoding="utf-8")
print("evaluate_params OK")
