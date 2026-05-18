"""RSI simulator: long ATR TP and same-bar ambiguity (intrabar helper)."""

import numpy as np

from scalp_bot.scalp_vec_backtest import simulate_trades_rsi


def test_rsi_long_exits_take_profit_before_rsi_recovery() -> None:
    n = 4
    close = np.array([100.0, 125.0, 125.0, 125.0], dtype=np.float64)
    high = np.array([100.0, 126.0, 126.0, 126.0], dtype=np.float64)
    low = np.array([99.0, 119.0, 119.0, 119.0], dtype=np.float64)
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    long_mask[0] = True

    atr_vals = np.full(n, 10.0, dtype=np.float64)
    rsi_vals = np.array([20.0, 40.0, 40.0, 55.0], dtype=np.float64)

    trades = simulate_trades_rsi(
        close,
        high,
        low,
        long_mask,
        short_mask,
        atr_vals,
        rsi_vals,
        open_prices=None,
        atr_stop_mult=1.5,
        atr_tp_mult=2.0,
        rsi_sell_threshold=50.0,
        max_hold_bars=10,
        slippage_pct=0.0,
        cooldown_bars=1,
        fill_model="close_slip",
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_bar == 0
    assert t.exit_bar == 1
    assert t.exit_reason == "tp"


def test_rsi_long_same_bar_stop_before_tp_when_open_near_low() -> None:
    n = 5
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    long_mask[0] = True
    atr_vals = np.full(n, 4.0, dtype=np.float64)
    rsi_vals = np.full(n, 35.0, dtype=np.float64)

    entry = 100.0
    close = np.array([entry, entry, entry, entry, entry], dtype=np.float64)
    stop_price = entry - 4.0 * 2.0
    tp_price = entry + 4.0 * 3.0
    hi = tp_price + 10.0
    lo = stop_price - 1.0
    high = np.array([entry, hi, hi, hi, hi], dtype=np.float64)
    low = np.array([entry, lo, lo, lo, lo], dtype=np.float64)

    opens = np.array([entry + 100.0, lo + 2.0] + [entry] * (n - 2), dtype=np.float64)

    trades = simulate_trades_rsi(
        close,
        high,
        low,
        long_mask,
        short_mask,
        atr_vals,
        rsi_vals,
        open_prices=opens,
        atr_stop_mult=2.0,
        atr_tp_mult=3.0,
        rsi_sell_threshold=50.0,
        max_hold_bars=10,
        slippage_pct=0.0,
        cooldown_bars=1,
        fill_model="close_slip",
    )
    assert len(trades) == 1
    assert trades[0].exit_bar == 1
    assert trades[0].exit_reason == "stop"
