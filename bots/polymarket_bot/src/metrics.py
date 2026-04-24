from __future__ import annotations

from .models import Metrics
from .positions import PositionManager


def recompute_metrics(
    metrics: Metrics,
    trades: list[dict],
    positions: PositionManager | None = None,
) -> None:
    metrics.orders_placed = len(trades)
    if not trades:
        metrics.win_rate = 0.0
        metrics.pnl_total_usd = 0.0
        metrics.pnl_realized_usd = 0.0
        metrics.resolved_positions = 0
        return

    pnl = 0.0
    wins = 0
    resolved = 0
    for t in trades:
        d = float(t.get("pnl_delta", 0.0))
        pnl += d
        status = t.get("status", "")
        if status in ("won", "lost"):
            resolved += 1
            if status == "won":
                wins += 1

    metrics.pnl_total_usd = pnl
    metrics.pnl_realized_usd = pnl
    metrics.resolved_positions = resolved
    metrics.win_rate = wins / resolved if resolved > 0 else 0.0
    metrics.session_high_water = max(metrics.session_high_water, pnl)

    if positions is not None:
        metrics.open_positions = len(positions.open_positions)
