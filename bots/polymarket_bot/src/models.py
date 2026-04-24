from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class RiskConfig:
    daily_loss_limit_usd: float
    max_position_pct: float
    max_portfolio_exposure_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Metrics:
    pnl_total_usd: float = 0.0
    pnl_realized_usd: float = 0.0
    win_rate: float = 0.0
    open_positions: int = 0
    resolved_positions: int = 0
    orders_placed: int = 0
    mode: str = "paper"
    session_high_water: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
