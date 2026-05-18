"""Tests for forward_reconciliation (P0 live vs holdout telemetry)."""

from __future__ import annotations

from scalp_bot.forward_reconciliation import (
    compute_forward_reconciliation,
    reconciliation_from_champion_row,
)


def test_ratio_ok_within_threshold() -> None:
    rec = compute_forward_reconciliation(
        forward_pnl=100.0,
        forward_trades=10,
        holdout_expectancy=10.0,
        alert_pct=0.30,
    )
    assert rec["forward_ratio"] == 1.0
    assert rec["divergence_pct"] == 0.0
    assert rec["alert"] is False
    assert rec["reason"] == "ok"


def test_alert_when_divergence_above_threshold() -> None:
    rec = compute_forward_reconciliation(
        forward_pnl=50.0,
        forward_trades=10,
        holdout_expectancy=10.0,
        alert_pct=0.30,
    )
    assert rec["forward_ratio"] == 0.5
    assert rec["divergence_pct"] == 0.5
    assert rec["alert"] is True


def test_insufficient_trades() -> None:
    rec = compute_forward_reconciliation(
        forward_pnl=0.0,
        forward_trades=0,
        holdout_expectancy=5.0,
    )
    assert rec["reason"] == "insufficient_forward_trades"
    assert rec["alert"] is False


def test_champion_row_integration() -> None:
    row = {
        "symbol": "BIP-TEST",
        "mode": "ema_momentum",
        "holdout_metrics": {"expectancy": 2.0, "total_pnl": 20.0, "trade_count": 10},
    }
    rec = reconciliation_from_champion_row(
        row,
        forward_pnl=16.0,
        forward_trades=10,
        period_start=1.0,
        alert_pct=0.30,
    )
    assert rec["symbol"] == "BIP-TEST"
    assert rec["forward_ratio"] == 0.8
    assert rec["alert"] is False
