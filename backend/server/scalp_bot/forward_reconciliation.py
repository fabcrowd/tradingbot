"""Live forward PnL vs WFO holdout expectancy reconciliation (P0 telemetry)."""

from __future__ import annotations

import math
from typing import Any


def compute_forward_reconciliation(
    *,
    forward_pnl: float,
    forward_trades: int,
    holdout_expectancy: float,
    alert_pct: float = 0.30,
    period_start: float = 0.0,
) -> dict[str, Any]:
    """Compare live forward PnL to holdout-implied expectation.

    ``forward_ratio`` = ``forward_pnl / (holdout_expectancy * forward_trades)`` when
    holdout expectancy is positive and trades > 0 (same spirit as Gate 1 in
    ``ScalpRuntime._check_champion_forward_validation``).

    ``divergence_pct`` = ``|1 - forward_ratio|`` when ratio is finite.
    ``alert`` when divergence exceeds ``alert_pct`` and enough trades exist.
    """
    out: dict[str, Any] = {
        "forward_pnl": round(float(forward_pnl), 6),
        "forward_trades": int(forward_trades),
        "holdout_expectancy": round(float(holdout_expectancy), 6),
        "expected_pnl": 0.0,
        "forward_ratio": None,
        "divergence_pct": None,
        "alert": False,
        "reason": "",
        "period_start": float(period_start),
    }
    if forward_trades <= 0:
        out["reason"] = "insufficient_forward_trades"
        return out
    exp = float(holdout_expectancy)
    if exp <= 0:
        out["reason"] = "non_positive_holdout_expectancy"
        out["expected_pnl"] = exp * forward_trades
        return out
    expected_pnl = exp * forward_trades
    ratio = forward_pnl / expected_pnl if expected_pnl != 0 else float("nan")
    out["expected_pnl"] = round(expected_pnl, 6)
    if not math.isfinite(ratio):
        out["reason"] = "non_finite_ratio"
        return out
    out["forward_ratio"] = round(ratio, 4)
    div = abs(1.0 - ratio)
    out["divergence_pct"] = round(div, 4)
    if div > float(alert_pct):
        out["alert"] = True
        out["reason"] = "divergence_above_threshold"
    else:
        out["reason"] = "ok"
    return out


def reconciliation_from_champion_row(
    champ_row: dict[str, Any] | None,
    *,
    forward_pnl: float,
    forward_trades: int,
    period_start: float,
    alert_pct: float,
) -> dict[str, Any]:
    """Build reconciliation dict from a champion JSON row."""
    if not champ_row:
        return compute_forward_reconciliation(
            forward_pnl=forward_pnl,
            forward_trades=forward_trades,
            holdout_expectancy=0.0,
            alert_pct=alert_pct,
            period_start=period_start,
        )
    hm = champ_row.get("holdout_metrics") or {}
    exp = float(hm.get("expectancy", 0.0) or 0.0)
    rec = compute_forward_reconciliation(
        forward_pnl=forward_pnl,
        forward_trades=forward_trades,
        holdout_expectancy=exp,
        alert_pct=alert_pct,
        period_start=period_start,
    )
    rec["symbol"] = str(champ_row.get("symbol", ""))
    rec["mode"] = str(champ_row.get("mode", ""))
    rec["holdout_total_pnl"] = round(float(hm.get("total_pnl", 0.0) or 0.0), 6)
    rec["holdout_trade_count"] = int(hm.get("trade_count", 0) or 0)
    return rec
