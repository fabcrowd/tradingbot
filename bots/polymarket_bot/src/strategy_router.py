"""Multi-strategy router.

Orchestrates three concurrent strategies:
  1. Sports Odds Arbitrage — active during live games
  2. Maker Spread Capture — always active on wide-spread markets
  3. Crypto Taker — active only during scheduled volatile windows

Allocates capital across strategies and manages shared risk limits.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger("polymarket_bot.router")


@dataclass
class StrategyAllocation:
    sports_pct: float = 50.0
    maker_pct: float = 30.0
    crypto_pct: float = 20.0

    def for_strategy(self, strategy: str, total_usd: float) -> float:
        pct_map = {
            "sports": self.sports_pct,
            "maker": self.maker_pct,
            "crypto": self.crypto_pct,
        }
        return total_usd * pct_map.get(strategy, 0.0) / 100.0


@dataclass
class StrategyState:
    name: str
    active: bool = False
    reason: str = ""
    signals_fired: int = 0
    trades_executed: int = 0
    pnl_usd: float = 0.0
    last_tick_ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "active": self.active,
            "reason": self.reason,
            "signals_fired": self.signals_fired,
            "trades_executed": self.trades_executed,
            "pnl_usd": self.pnl_usd,
            "last_tick_ts": self.last_tick_ts,
        }


class StrategyRouter:
    """Routes market data to the appropriate strategy based on conditions.

    Each strategy runs independently but shares a global risk budget.
    The router decides which strategies are active and allocates capital.
    """

    def __init__(
        self,
        allocation: StrategyAllocation | None = None,
        total_budget_usd: float = 200.0,
    ) -> None:
        self._allocation = allocation or StrategyAllocation()
        self._total_budget = total_budget_usd
        self._strategies: dict[str, StrategyState] = {
            "sports": StrategyState(name="Sports Odds Arb"),
            "maker": StrategyState(name="Maker Spread Capture"),
            "crypto": StrategyState(name="Crypto Taker"),
        }

    @property
    def strategies(self) -> dict[str, StrategyState]:
        return self._strategies

    def budget_for(self, strategy: str) -> float:
        return self._allocation.for_strategy(strategy, self._total_budget)

    def activate(self, strategy: str, reason: str = "") -> None:
        s = self._strategies.get(strategy)
        if s and not s.active:
            s.active = True
            s.reason = reason
            LOG.info("Strategy activated: %s (%s)", strategy, reason)

    def deactivate(self, strategy: str, reason: str = "") -> None:
        s = self._strategies.get(strategy)
        if s and s.active:
            s.active = False
            s.reason = reason
            LOG.info("Strategy deactivated: %s (%s)", strategy, reason)

    def record_signal(self, strategy: str) -> None:
        s = self._strategies.get(strategy)
        if s:
            s.signals_fired += 1

    def record_trade(self, strategy: str, pnl: float = 0.0) -> None:
        s = self._strategies.get(strategy)
        if s:
            s.trades_executed += 1
            s.pnl_usd += pnl

    def mark_tick(self, strategy: str) -> None:
        s = self._strategies.get(strategy)
        if s:
            s.last_tick_ts = time.time()

    def status(self) -> dict[str, Any]:
        return {
            "allocation": {
                "sports_pct": self._allocation.sports_pct,
                "maker_pct": self._allocation.maker_pct,
                "crypto_pct": self._allocation.crypto_pct,
                "total_budget_usd": self._total_budget,
            },
            "strategies": {k: v.to_dict() for k, v in self._strategies.items()},
        }
