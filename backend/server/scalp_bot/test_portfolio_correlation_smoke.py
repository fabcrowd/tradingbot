"""Smoke test for portfolio_correlation_backtest (P4)."""

from __future__ import annotations

from scalp_bot.portfolio_correlation_backtest import (
    compare_sizing_functions,
    sizing_baseline,
    sizing_live_mirror,
)
from scalp_bot.scalp_vec_backtest import TradeResult


def test_portfolio_sizing_live_mirror_reduces_pnl_vs_baseline() -> None:
    tr = TradeResult(
        entry_bar=0,
        exit_bar=1,
        entry_price=100.0,
        exit_price=101.0,
        stop_price=99.0,
        tp_price=102.0,
        pnl=10.0,
        is_win=True,
        exit_reason="tp",
        hold_bars=1,
    )
    trades = {"a": [tr], "b": [tr]}
    groups = {"a": "g", "b": "g"}
    cmp = compare_sizing_functions(
        trades,
        ["a", "b"],
        groups,
        sizing_baseline,
        sizing_live_mirror,
        label_a="baseline",
        label_b="live_mirror",
    )
    assert cmp.trade_count == 2
    assert cmp.total_b < cmp.total_a
