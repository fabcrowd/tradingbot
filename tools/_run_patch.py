from pathlib import Path
ROOT = Path(r"C:\Users\daroo\Desktop\Repos\tradingbot-1")
p = ROOT / "backend" / "server" / "scalp_bot" / "scalp_vec_backtest.py"
t = p.read_text(encoding="utf-8")
start = t.index("def simulate_trades_rsi(")
end = t.index("\n\n# ---------------------------------------------------------------------------\n# Signal detection (vectorized) — EMA scalp mode", start)
exec(open(r"C:\Users\daroo\Desktop\Repos\tradingbot-1\tools\_new_rsi_fn.py", encoding="utf-8").read())
