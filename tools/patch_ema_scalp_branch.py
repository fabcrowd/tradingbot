from pathlib import Path
p = Path(r"C:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_vec_backtest.py")
t = p.read_text(encoding="utf-8")
old = """    elif params.mode == \"ema_scalp\":
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
        trades = _merge_trades_by_entry(trades_long, trades_short)"""
new = """    elif params.mode == \"ema_scalp\":
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
            fee_pct=fee, slippage_pct=slip,
        )"""
if old not in t:
    raise SystemExit("ema_scalp branch not found")
p.write_text(t.replace(old, new, 1), encoding="utf-8")
print("ema_scalp branch fixed")
