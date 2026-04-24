from pathlib import Path
p = Path(r"C:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_vec_backtest.py")
t = p.read_text(encoding="utf-8")
old = """def detect_signals_ema_scalp(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    ema_period: int,
    atr_period: int,
    sr_bars: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    \"\"\"Tony's EMA Scalper: buy when price crosses above EMA and candle is bullish.

    Returns (entry_mask, atr_vals, high_n, low_n).
    high_n / low_n are the 8-bar resistance/support used for TP/stop.
    \"\"\"
    ema_vals = ema(close, ema_period)
    atr_vals = atr(high, low, close, atr_period)
    high_n = rolling_highest(close, sr_bars)
    low_n = rolling_lowest(close, sr_bars)

    n = len(close)
    entry_mask = np.zeros(n, dtype=bool)

    for i in range(1, n):
        if np.isnan(ema_vals[i]) or np.isnan(ema_vals[i - 1]):
            continue
        # cross() = price and EMA were on different sides
        prev_above = close[i - 1] > ema_vals[i - 1]
        cur_above = close[i] > ema_vals[i]
        crossed = prev_above != cur_above
        # Bullish: crossed AND close > prev close (uptrend candle)
        bullish = crossed and cur_above and close[i] > close[i - 1]
        if bullish:
            entry_mask[i] = True

    entry_mask &= ~np.isnan(atr_vals) & (atr_vals > 0)
    entry_mask &= ~np.isnan(high_n) & ~np.isnan(low_n)
    warmup = max(ema_period, atr_period, sr_bars)
    entry_mask[:warmup] = False

    return entry_mask, atr_vals, high_n, low_n"""
new = """def detect_signals_ema_scalp(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    ema_period: int,
    atr_period: int,
    sr_bars: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    \"\"\"Tony's EMA Scalper: long on bullish EMA cross; short on bearish cross (perps).

    Returns (long_mask, short_mask, atr_vals, high_n, low_n).
    \"\"\"
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
    warmup = max(ema_period, atr_period, sr_bars)
    long_mask[:warmup] = False
    short_mask[:warmup] = False

    return long_mask, short_mask, atr_vals, high_n, low_n"""
if old not in t:
    raise SystemExit("ema_scalp block not found")
p.write_text(t.replace(old, new, 1), encoding="utf-8")
print("ema_scalp OK")
