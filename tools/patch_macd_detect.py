from pathlib import Path
p = Path(r"C:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_vec_backtest.py")
t = p.read_text(encoding="utf-8")
old = """def detect_signals_macd(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    fast_len: int,
    slow_len: int,
    signal_len: int,
    atr_period: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    \"\"\"Scalp Pro MACD: super-smoothed fast/slow difference, crossover for buy.

    Returns (entry_mask, atr_vals, macd_line, macd_signal_line).
    \"\"\"
    ss_fast = super_smooth(close, fast_len)
    ss_slow = super_smooth(close, slow_len)
    macd_line = (ss_fast - ss_slow) * 1e7
    macd_signal_line = super_smooth(macd_line, signal_len)
    atr_vals = atr(high, low, close, atr_period)

    n = len(close)
    entry_mask = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if macd_line[i - 1] <= macd_signal_line[i - 1] and macd_line[i] > macd_signal_line[i]:
            entry_mask[i] = True

    entry_mask &= ~np.isnan(atr_vals) & (atr_vals > 0)
    warmup = max(fast_len, slow_len, signal_len, atr_period)
    entry_mask[:warmup] = False

    return entry_mask, atr_vals, macd_line, macd_signal_line"""
new = """def detect_signals_macd(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    fast_len: int,
    slow_len: int,
    signal_len: int,
    atr_period: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    \"\"\"Scalp Pro MACD: bull and bear super-smoother crossovers (perps).

    Returns (long_mask, short_mask, atr_vals, macd_line, macd_signal_line).
    \"\"\"
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
    warmup = max(fast_len, slow_len, signal_len, atr_period)
    long_mask[:warmup] = False
    short_mask[:warmup] = False

    return long_mask, short_mask, atr_vals, macd_line, macd_signal_line"""
if old not in t:
    raise SystemExit("macd block not found")
p.write_text(t.replace(old, new, 1), encoding="utf-8")
print("macd OK")
