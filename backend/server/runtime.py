"""Shared runtime handles (mode switching, lazy live client)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import AppConfig
from .inventory import InventoryManager
from .live_order_manager import LiveOrderManager
from .order_manager import OrderManager
from .pnl import PnLTracker
from .spread_engine import SpreadEngine
from .state import BotState
from .strategy_learner import StrategyLearner


@dataclass
class BotRuntime:
    state: BotState
    config: AppConfig
    pnl: PnLTracker
    inventory: InventoryManager
    paper_mgr: OrderManager
    live_mgr: LiveOrderManager | None = None
    engine: SpreadEngine | None = None
    learner: StrategyLearner | None = None
    book_client: object | None = field(default=None, repr=False)

    async def ensure_live(self) -> LiveOrderManager | None:
        """Create and start the authenticated Kraken client when switching to LIVE."""
        if not self.config.api_key or not self.config.api_secret:
            return None
        if self.live_mgr is None:
            self.live_mgr = LiveOrderManager(
                self.state, self.config, self.inventory, self.pnl,
            )
            await self.live_mgr.initialize()
            if self.engine is not None:
                self.engine.set_live_order_mgr(self.live_mgr)
            self.inventory.sync_from_kraken()
        return self.live_mgr
