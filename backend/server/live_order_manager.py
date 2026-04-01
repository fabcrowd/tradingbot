"""Live order manager — places real orders on Kraken via WebSocket v2."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from kraken.spot import SpotWSClient

from .state import ActiveOrder

if TYPE_CHECKING:
    from .config import AppConfig
    from .inventory import InventoryManager
    from .pnl import PnLTracker
    from .state import BotState

LOG = logging.getLogger(__name__)


class LiveOrderManager(SpotWSClient):
    """Authenticated Kraken WS v2 client for order management."""

    def __init__(
        self,
        state: BotState,
        config: AppConfig,
        inventory: InventoryManager,
        pnl: PnLTracker,
    ) -> None:
        super().__init__(key=config.api_key, secret=config.api_secret)
        self._state = state
        self._config = config
        self._inventory = inventory
        self._pnl = pnl
        self._pending_acks: dict[int, asyncio.Future] = {}
        self._req_counter = 0
        self._symbol_to_key: dict[str, str] = {
            pc.symbol: key for key, pc in config.pairs.items()
        }

    async def on_message(self, message: dict) -> None:
        """Handle all messages from the authenticated WS connection."""
        if message.get("method") == "pong" or message.get("channel") == "heartbeat":
            return

        if "error" in message:
            LOG.error("Kraken WS error: %s", message)
            self._handle_order_response(message)
            return

        channel = message.get("channel")

        if channel == "executions":
            await self._handle_execution(message)
        elif message.get("method") in ("add_order", "cancel_order"):
            self._handle_order_response(message)

    async def _handle_execution(self, message: dict) -> None:
        """Process execution/fill messages from Kraken WS v2.

        exec_type values: pending_new, new, trade, filled, canceled, expired, ...
        - "trade": an incremental fill event (use last_qty / last_price).
        - "filled": order fully complete — remove from active orders, no fill data.
        - "canceled"/"expired": terminal — remove from active orders.
        """
        data = message.get("data", [])
        for exec_data in data:
            exec_type = exec_data.get("exec_type")
            cl_ord_id = exec_data.get("cl_ord_id", "")
            if exec_type == "trade":
                await self._process_fill(exec_data)
            elif exec_type in {"filled", "canceled", "expired"}:
                self._state.active_orders.pop(cl_ord_id, None)
                if exec_type != "filled":
                    LOG.info("Order %s: %s", exec_type, cl_ord_id[:16])

    async def _process_fill(self, data: dict) -> None:
        symbol = data.get("symbol", "")
        pair_key = self._symbol_to_key.get(symbol)
        if pair_key is None:
            return

        side = data.get("side", "")
        price = float(data.get("last_price", data.get("avg_price", 0)))
        qty = float(data.get("last_qty", 0))
        if qty <= 0 or price <= 0:
            return

        fees = data.get("fees", [])
        if isinstance(fees, list) and fees:
            fee_paid = sum(float(f.get("qty", 0)) for f in fees if isinstance(f, dict))
        else:
            fee_paid = float(data.get("fee_paid", 0))

        pnl_log = 0.0
        if side == "buy":
            self._inventory.record_buy(pair_key, qty, price, fee_paid)
            self._pnl.record_fill(
                pair_key=pair_key,
                symbol=symbol,
                side=side,
                price=price,
                qty=qty,
                fee=fee_paid,
                pnl_delta=0.0,
            )
        else:
            gross = self._inventory.gross_spread_on_sell(pair_key, qty, price, fee_paid)
            pnl_log = self._inventory.record_sell(pair_key, qty, price, fee_paid)
            self._pnl.record_fill(
                pair_key=pair_key,
                symbol=symbol,
                side=side,
                price=price,
                qty=qty,
                fee=fee_paid,
                pnl_delta=pnl_log,
                gross_spread=gross,
            )

        LOG.info(
            "FILL %s %s %.6f @ %.8f | fee=%.6f pnl=%.6f",
            side, symbol, qty, price, fee_paid, pnl_log,
        )

    def _handle_order_response(self, message: dict) -> None:
        req_id = message.get("req_id")
        if req_id and req_id in self._pending_acks:
            fut = self._pending_acks.pop(req_id)
            if not fut.done():
                if message.get("success"):
                    fut.set_result(message)
                else:
                    fut.set_exception(
                        RuntimeError(message.get("error", "Order failed")),
                    )

    async def initialize(self) -> None:
        """Start the WS connection and subscribe to execution feed."""
        await self.start()
        await self.subscribe(params={"channel": "executions"})
        LOG.info("Live order manager initialized, subscribed to executions")

    async def place_order(
        self, pair_key: str, symbol: str, side: str, price: float, qty: float,
    ) -> str:
        cl_ord_id = f"mitch-{uuid.uuid4().hex[:12]}"
        self._req_counter += 1
        req_id = self._req_counter

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

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_acks[req_id] = fut

        await self.send_message(
            message={
                "method": "add_order",
                "req_id": req_id,
                "params": {
                    "order_type": "limit",
                    "side": side,
                    "order_qty": qty,
                    "limit_price": price,
                    "symbol": symbol,
                    "cl_ord_id": cl_ord_id,
                    "post_only": True,
                },
            },
        )

        try:
            await asyncio.wait_for(fut, timeout=5.0)
            LOG.info(
                "ORDER %s %s %.4f x %.4f @ %s [%s]",
                side.upper(), symbol, qty, price, pair_key, cl_ord_id[:16],
            )
        except asyncio.TimeoutError:
            LOG.warning("Order ack timeout for %s", cl_ord_id)
        except RuntimeError as e:
            LOG.error("Order rejected: %s", e)
            self._state.active_orders.pop(cl_ord_id, None)
            # Back off briefly so the engine doesn't immediately retry on the next 500ms cycle
            self._state.last_order_reject_ts = time.time()

        return cl_ord_id

    async def cancel_order(self, cl_ord_id: str) -> bool:
        order = self._state.active_orders.get(cl_ord_id)
        if order is None:
            return False

        self._req_counter += 1
        req_id = self._req_counter
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_acks[req_id] = fut

        await self.send_message(
            message={
                "method": "cancel_order",
                "req_id": req_id,
                "params": {"cl_ord_id": [cl_ord_id]},
            },
        )

        try:
            await asyncio.wait_for(fut, timeout=5.0)
            self._state.active_orders.pop(cl_ord_id, None)
            return True
        except (asyncio.TimeoutError, RuntimeError) as e:
            LOG.warning("Cancel failed for %s: %s", cl_ord_id, e)
            self._state.active_orders.pop(cl_ord_id, None)
            return False

    async def place_aggressive_sell(
        self, pair_key: str, symbol: str, price: float, qty: float,
    ) -> str:
        """Aggressive limit sell at best_bid (no post_only) to fill immediately."""
        cl_ord_id = f"mitch-emrg-{uuid.uuid4().hex[:8]}"
        self._req_counter += 1
        req_id = self._req_counter

        order = ActiveOrder(
            cl_ord_id=cl_ord_id,
            pair_key=pair_key,
            symbol=symbol,
            side="sell",
            price=price,
            qty=qty,
            placed_at=time.time(),
        )
        self._state.active_orders[cl_ord_id] = order

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_acks[req_id] = fut

        await self.send_message(
            message={
                "method": "add_order",
                "req_id": req_id,
                "params": {
                    "order_type": "limit",
                    "side": "sell",
                    "order_qty": qty,
                    "limit_price": price,
                    "symbol": symbol,
                    "cl_ord_id": cl_ord_id,
                },
            },
        )

        try:
            await asyncio.wait_for(fut, timeout=5.0)
        except asyncio.TimeoutError:
            LOG.warning("Emergency sell ack timeout for %s", cl_ord_id)
        except RuntimeError as e:
            LOG.error("Emergency sell rejected: %s", e)
            self._state.active_orders.pop(cl_ord_id, None)

        return cl_ord_id

    async def cancel_all(self, pair_key: str | None = None) -> int:
        to_cancel = [
            oid for oid, o in self._state.active_orders.items()
            if pair_key is None or o.pair_key == pair_key
        ]
        count = 0
        for oid in to_cancel:
            if await self.cancel_order(oid):
                count += 1
        return count
