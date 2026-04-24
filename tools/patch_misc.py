from pathlib import Path

# 1) build_default_grid RSI section
p = Path(r"C:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_vec_backtest.py")
t = p.read_text(encoding="utf-8")
old = """    # RSI reversion combos
    for rsi_buy in (8.0, 10.0, 15.0, 20.0):
        for rsi_sell in (40.0, 50.0, 60.0):
            for max_hold in (5, 10, 15, 25):
                for stop_mult in (1.0, 1.5, 2.0):
                    grid.append(ParamSet(
                        mode="rsi_reversion",
                        rsi_buy_threshold=rsi_buy,
                        rsi_sell_threshold=rsi_sell,
                        max_hold_bars=max_hold,
                        atr_stop_mult=stop_mult,
                        fee_pct=fee_pct,
                    ))"""
new = """    # RSI reversion combos (long oversold + short overbought for perps)
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
                        ))"""
if old not in t:
    raise SystemExit("RSI grid not found")
p.write_text(t.replace(old, new, 1), encoding="utf-8")
print("grid OK")

# 2) scalp_wfo
p2 = Path(r"C:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_wfo.py")
t2 = p2.read_text(encoding="utf-8")
t2 = t2.replace(
    '"rsi_sell_threshold": params.rsi_sell_threshold,\n            "ema_scalp_period"',
    '"rsi_sell_threshold": params.rsi_sell_threshold,\n            "rsi_short_threshold": params.rsi_short_threshold,\n            "ema_scalp_period"',
    1,
)
t2 = t2.replace(
    "rsi_sell_threshold=getattr(pair_cfg, \"rsi_sell_threshold\", 50.0),\n        ema_scalp_period=",
    "rsi_sell_threshold=getattr(pair_cfg, \"rsi_sell_threshold\", 50.0),\n        rsi_short_threshold=float(getattr(pair_cfg, \"rsi_short_threshold\", 70.0)),\n        ema_scalp_period=",
    1,
)
p2.write_text(t2, encoding="utf-8")
print("wfo OK")

# 3) scalp_config
p3 = Path(r"C:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_config.py")
t3 = p3.read_text(encoding="utf-8")
t3 = t3.replace(
    "rsi_sell_threshold: float = 50.0    # sell when RSI >= this\n    # EMA scalp params",
    "rsi_sell_threshold: float = 50.0    # sell when RSI >= this (long exit)\n    rsi_short_threshold: float = 70.0   # short entry when RSI >= this (perps)\n    # EMA scalp params",
    1,
)
t3 = t3.replace(
    "rsi_sell_threshold=float(val.get(\"rsi_sell_threshold\", 50.0)),\n            ema_scalp_period=",
    "rsi_sell_threshold=float(val.get(\"rsi_sell_threshold\", 50.0)),\n            rsi_short_threshold=float(val.get(\"rsi_short_threshold\", 70.0)),\n            ema_scalp_period=",
    1,
)
p3.write_text(t3, encoding="utf-8")
print("config OK")

# 4) param_tuner
p4 = Path(r"C:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\param_tuner.py")
t4 = p4.read_text(encoding="utf-8")
t4 = t4.replace(
    "rsi_sell_threshold=pair_cfg.rsi_sell_threshold,\n        ema_scalp_period=",
    "rsi_sell_threshold=pair_cfg.rsi_sell_threshold,\n        rsi_short_threshold=float(getattr(pair_cfg, \"rsi_short_threshold\", 70.0)),\n        ema_scalp_period=",
    1,
)
p4.write_text(t4, encoding="utf-8")
print("tuner OK")
