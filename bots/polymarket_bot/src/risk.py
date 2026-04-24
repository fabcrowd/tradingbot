from __future__ import annotations

from dataclasses import dataclass

from .models import RiskConfig


@dataclass
class RiskDecision:
    allow: bool
    reason: str


class RiskEngine:
    def __init__(self, risk: RiskConfig) -> None:
        self._risk = risk
        self._session_high_water: float = 0.0

    def check(
        self,
        pnl_total_usd: float,
        open_positions: int,
        projected_exposure_usd: float,
        max_concurrent: int = 10,
        per_market_count: int = 0,
        per_market_limit: int = 1,
    ) -> RiskDecision:
        if pnl_total_usd <= -abs(self._risk.daily_loss_limit_usd):
            return RiskDecision(False, "daily_loss_limit_hit")
        if projected_exposure_usd > self._risk.max_portfolio_exposure_usd:
            return RiskDecision(False, "max_portfolio_exposure_hit")
        if open_positions >= max_concurrent:
            return RiskDecision(False, f"max_concurrent_positions ({max_concurrent})")
        if per_market_count >= per_market_limit:
            return RiskDecision(False, f"per_market_limit ({per_market_limit})")

        self._session_high_water = max(self._session_high_water, pnl_total_usd)
        drawdown = self._session_high_water - pnl_total_usd
        if self._session_high_water > 0 and drawdown > self._session_high_water * 0.4:
            return RiskDecision(False, f"drawdown_40pct (hw={self._session_high_water:.2f} dd={drawdown:.2f})")

        return RiskDecision(True, "ok")
