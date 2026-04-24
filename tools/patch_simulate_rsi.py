from pathlib import Path
import numpy as np  # noqa: F401 — doc only; file uses np from module

ROOT = Path(r"C:\Users\daroo\Desktop\Repos\tradingbot-1")
p = ROOT / "backend" / "server" / "scalp_bot" / "scalp_vec_backtest.py"
t = p.read_text(encoding="utf-8")
start = t.index("def simulate_trades_rsi(")
end = t.index("\n\n# ---------------------------------------------------------------------------\n# Signal detection (vectorized) — EMA scalp mode", start)

new_fn = """def simulate_trades_rsi(
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
    fee_pct: float = 0.0026,
    slippage_pct: float = 0.0001,
    cooldown_bars: int = 1,
) -> list[TradeResult]:
    \"\"\"RSI mean-reversion: long and short entries for perps.\"\"\"
    n = len(close)
    trades: list[TradeResult] = []
    next_allowed = 0

    for i in range(n):
        if i < next_allowed:
            continue

        if long_mask[i] and short_mask[i]:
            continue

        a = atr_vals[i]
        if np.isnan(a) or a <= 0:
            continue

        if long_mask[i]:
            entry_price = close[i] * (1.0 + slippage_pct)
            stop_price = entry_price - a * atr_stop_mult
            tp_price = entry_price * 1.10

            if stop_price >= entry_price:
                continue

            exit_bar = min(i + max_hold_bars, n - 1)
            exit_price = close[exit_bar]
            exit_reason = \"time_stop\"

            for j in range(i + 1, min(i + max_hold_bars + 1, n)):
                if low[j] <= stop_price:
                    exit_price = stop_price * (1.0 - slippage_pct)
                    exit_reason = \"stop\"
                    exit_bar = j
                    break
                if not np.isnan(rsi_vals[j]) and rsi_vals[j] >= rsi_sell_threshold:
                    exit_price = close[j] * (1.0 - slippage_pct)
                    exit_reason = \"rsi_exit\"
                    exit_bar = j
                    break

            raw_pnl = exit_price - entry_price
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
            continue

        if short_mask[i]:
            entry_price = close[i] * (1.0 - slippage_pct)
            stop_price = entry_price + a * atr_stop_mult
            tp_price = entry_price - a * atr_tp_mult

            if tp_price >= entry_price:
                continue

            exit_bar = min(i + max_hold_bars, n - 1)
            exit_price = close[exit_bar]
            exit_reason = \"time_stop\"

            for j in range(i + 1, min(i + max_hold_bars + 1, n)):
                if high[j] >= stop_price:
                    exit_price = stop_price * (1.0 + slippage_pct)
                    exit_reason = \"stop\"
                    exit_bar = j
                    break
                if low[j] <= tp_price:
                    exit_price = tp_price * (1.0 + slippage_pct)
                    exit_reason = \"tp\"
                    exit_bar = j
                    break
                if not np.isnan(rsi_vals[j]) and rsi_vals[j] <= rsi_short_cover_threshold:
                    exit_price = close[j] * (1.0 + slippage_pct)
                    exit_reason = \"rsi_exit\"
                    exit_bar = j
                    break

            raw_pnl = entry_price - exit_price
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
"""

p.write_text(t[:start] + new_fn + t[end:], encoding="utf-8")
print("simulate_trades_rsi OK")
