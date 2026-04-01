"""Order manager — abstracts paper vs live order placement."""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING

from .state import ActiveOrder

if TYPE_CHECKING:
    from .config import AppConfig
    from .state import BotState

LOG = logging.getLogger(__name__)


class OrderManager:
    """Paper-mode order manager. Live mode overrides in Phase 8."""

    def __init__(self, state: BotState, config: AppConfig) -> None:
        self._state = state
        self._config = config

    async def place_order(
        self, pair_key: str, symbol: str, side: str, price: float, qty: float,
    ) -> str:
        cl_ord_id = f"mitch-{uuid.uuid4().hex[:12]}"
        order = ActiveOrder(
            cl_ord_id=cl_ord_id,
            pair_key=pair_key,
            symbol=symbol,
            side=side,
            price=price,
            qty=qty,
            placed_at=time.time(),
        )
        self._state.active_orders[cl_ord_id] = order
        LOG.debug(
            "PAPER place %s %s %.6f @ %.8f [%s]",
            side, symbol, qty, price, cl_ord_id,
        )
        return cl_ord_id

    async def cancel_order(self, cl_ord_id: str) -> bool:
        order = self._state.active_orders.pop(cl_ord_id, None)
        if order:
            LOG.debug("PAPER cancel %s", cl_ord_id)
            return True
        return False

    async def cancel_all(self, pair_key: str | None = None) -> int:
        to_remove = [
            oid for oid, o in self._state.active_orders.items()
            if pair_key is None or o.pair_key == pair_key
        ]
        for oid in to_remove:
            del self._state.active_orders[oid]
        if to_remove:
            LOG.debug("PAPER cancelled %d orders", len(to_remove))
        return len(to_remove)

    async def close(self) -> None:
        pass
