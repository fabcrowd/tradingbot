"""Position lifecycle manager for the scalp bot.

Opens positions on signals, tracks them, handles stop/tp fills via OCO pattern.
Capital-aware: reserves funds before entry, releases on close.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .signal_engine import ScalpSignal, SignalEngine

if TYPE_CHECKING:
    from ..live_order_manager import LiveOrderManager
    from ..state import BotState
    from .scalp_config import ScalpBotConfig, ScalpPairConfig

LOG = logging.getLogger(__name__)


@dataclass
class ScalpPosition:
    pair_key: str
    symbol: str
    direction: str          # "long"
    entry_price: float
    stop_price: float
    tp_price: float
    qty: float
    entry_cl_ord_id: str
    stop_cl_ord_id: str = ""
    tp_cl_ord_id: str = ""
    status: str = "pending"     # pending | open | closed
    opened_at: float = field(default_factory=time.time)
    closed_at: float = 0.0
    pnl: float = 0.0
    close_reason: str = ""      # "stop", "tp", "manual", "error"


class ScalpTrader:
    """Manages open scalp positions and interfaces with LiveOrderManager for fills."""

    def __init__(
        self,
        state: "BotState",
        cfg: "ScalpBotConfig",
        signal_engine: SignalEngine,
        live_mgr: "LiveOrderManager | None",
    ) -> None:
        self._state = state
        self._cfg = cfg
        self._signal_engine = signal_engine
        self._live_mgr = live_mgr
        self._positions: dict[str, ScalpPosition] = {}   # pair_key -> open position
        self._daily_pnl: float = 0.0
        self._daily_reset_day: int = 0
        self._reserved_capital: float = 0.0

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def open_position_count(self) -> int:
        return sum(1 for p in self._positions.values() if p.status in ("pending", "open"))

    @property
    def daily_pnl(self) -> float:
        self._maybe_reset_daily()
        return self._daily_pnl

    def has_position(self, pair_key: str) -> bool:
        p = self._positions.get(pair_key)
        return p is not None and p.status in ("pending", "open")

    def get_position(self, pair_key: str) -> ScalpPosition | None:
        return self._positions.get(pair_key)

    async def try_open(
        self,
        signal: ScalpSignal,
        pair_cfg: "ScalpPairConfig",
        available_capital: float,
    ) -> bool:
        """Attempt to open a position for the given signal. Returns True if placed."""
        self._maybe_reset_daily()

        if self._state.risk_halted:
            LOG.debug("ScalpTrader: risk_halted — skipping %s", signal.pair_key)
            return False

        if not self._state.running:
            return False

        if self.has_position(signal.pair_key):
            LOG.debug("ScalpTrader: already has position for %s", signal.pair_key)
            return False

        if self.open_position_count >= self._cfg.max_concurrent_positions:
            LOG.info("ScalpTrader: max concurrent positions reached (%d)", self._cfg.max_concurrent_positions)
            return False

        # Daily loss check
        daily_loss_limit = self._cfg.allocated_capital_usd * (self._cfg.daily_loss_limit_pct / 100.0)
        if self._daily_pnl < -daily_loss_limit:
            LOG.warning(
                "ScalpTrader: daily loss limit hit (%.2f / -%.2f) — halting for the day",
                self._daily_pnl, daily_loss_limit,
            )
            return False

        if self._live_mgr is None:
            LOG.warning("ScalpTrader: no live_mgr — cannot place orders (paper scalping not yet supported)")
            return False

        # Position sizing: risk_pct of allocated capital / stop distance
        stop_distance = signal.entry_price - signal.stop_price
        if stop_distance <= 0:
            return False
        dollar_risk = self._cfg.allocated_capital_usd * pair_cfg.risk_pct
        qty = dollar_risk / stop_distance

        # Check available capital
        notional = qty * signal.entry_price
        if notional > available_capital - self._reserved_capital:
            LOG.info(
                "ScalpTrader: insufficient capital for %s (need=%.2f available=%.2f reserved=%.2f)",
                signal.pair_key, notional, available_capital, self._reserved_capital,
            )
            return False

        # Place entry order
        entry_id = f"scalp_entry_{uuid.uuid4().hex[:8]}"
        stop_id = f"scalp_stop_{uuid.uuid4().hex[:8]}"
        tp_id = f"scalp_tp_{uuid.uuid4().hex[:8]}"

        order_type = self._cfg.order_type  # "limit" or "market"
        try:
            await self._live_mgr.add_order(
                params={
                    "symbol": signal.symbol,
                    "side": "buy",
                    "order_type": order_type,
                    "limit_price": str(round(signal.entry_price, 5)) if order_type == "limit" else None,
                    "quantity": str(round(qty, 8)),
                    "cl_ord_id": entry_id,
                    "reduce_only": False,
                }
            )
        except Exception:
            LOG.exception("ScalpTrader: failed to place entry order for %s", signal.pair_key)
            return False

        position = ScalpPosition(
            pair_key=signal.pair_key,
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            tp_price=signal.tp_price,
            qty=qty,
            entry_cl_ord_id=entry_id,
            stop_cl_ord_id=stop_id,
            tp_cl_ord_id=tp_id,
            status="pending",
        )
        self._positions[signal.pair_key] = position
        self._reserved_capital += notional

        LOG.info(
            "ScalpTrader %s: entry placed | qty=%.6f @ %.5f | stop=%.5f | tp=%.5f | "
            "notional=%.2f | id=%s",
            signal.pair_key, qty, signal.entry_price,
            signal.stop_price, signal.tp_price, notional, entry_id,
        )
        return True

    async def on_entry_filled(self, pair_key: str, fill_price: float, fill_qty: float) -> None:
        """Called when the entry order is confirmed filled. Place stop + tp."""
        pos = self._positions.get(pair_key)
        if pos is None or pos.status != "pending":
            return
        pos.status = "open"
        pos.entry_price = fill_price
        pos.qty = fill_qty

        if self._live_mgr is None:
            return

        # Place stop-loss
        try:
            await self._live_mgr.add_order(params={
                "symbol": pos.symbol,
                "side": "sell",
                "order_type": "stop-loss-limit",
                "trigger_price": str(round(pos.stop_price, 5)),
                "limit_price": str(round(pos.stop_price * 0.9995, 5)),  # 0.05% below trigger
                "quantity": str(round(pos.qty, 8)),
                "cl_ord_id": pos.stop_cl_ord_id,
                "reduce_only": False,
            })
        except Exception:
            LOG.exception("ScalpTrader: failed to place stop for %s", pair_key)

        # Place take-profit
        try:
            await self._live_mgr.add_order(params={
                "symbol": pos.symbol,
                "side": "sell",
                "order_type": "take-profit-limit",
                "trigger_price": str(round(pos.tp_price, 5)),
                "limit_price": str(round(pos.tp_price * 0.9998, 5)),  # slight slippage buffer
                "quantity": str(round(pos.qty, 8)),
                "cl_ord_id": pos.tp_cl_ord_id,
                "reduce_only": False,
            })
        except Exception:
            LOG.exception("ScalpTrader: failed to place tp for %s", pair_key)

        LOG.info(
            "ScalpTrader %s: entry filled @ %.5f | stop=%s placed | tp=%s placed",
            pair_key, fill_price, pos.stop_cl_ord_id, pos.tp_cl_ord_id,
        )

    async def on_exit_filled(
        self,
        pair_key: str,
        filled_cl_ord_id: str,
        fill_price: float,
    ) -> None:
        """Called when stop or tp is filled. Cancels the sibling, closes position."""
        pos = self._positions.get(pair_key)
        if pos is None or pos.status != "open":
            return

        pnl = (fill_price - pos.entry_price) * pos.qty
        is_stop = filled_cl_ord_id == pos.stop_cl_ord_id
        close_reason = "stop" if is_stop else "tp"

        # Cancel the sibling order
        sibling_id = pos.tp_cl_ord_id if is_stop else pos.stop_cl_ord_id
        if sibling_id and self._live_mgr is not None:
            try:
                await self._live_mgr.cancel_order(
                    params={"cl_ord_id": sibling_id, "symbol": pos.symbol}
                )
            except Exception:
                LOG.debug("ScalpTrader: sibling cancel failed for %s", sibling_id, exc_info=True)

        self._close_position(pos, pnl, close_reason, fill_price)

        if is_stop:
            self._signal_engine.record_loss(pair_key)

        LOG.info(
            "ScalpTrader %s: closed via %s @ %.5f | pnl=%.4f | daily_pnl=%.4f",
            pair_key, close_reason, fill_price, pnl, self._daily_pnl,
        )

    def _close_position(
        self,
        pos: ScalpPosition,
        pnl: float,
        reason: str,
        close_price: float,
    ) -> None:
        pos.pnl = pnl
        pos.close_reason = reason
        pos.status = "closed"
        pos.closed_at = time.time()
        self._daily_pnl += pnl
        notional = pos.qty * pos.entry_price
        self._reserved_capital = max(0.0, self._reserved_capital - notional)
        self._state.push_alert(
            "success" if pnl > 0 else "warning",
            f"Scalp {reason.upper()}: {pos.pair_key}",
            f"{'Profit' if pnl > 0 else 'Loss'}: ${pnl:+.4f} | "
            f"entry={pos.entry_price:.5f} exit={close_price:.5f}",
            "scalp",
        )

    def _maybe_reset_daily(self) -> None:
        day = int(time.time() // 86400)
        if day != self._daily_reset_day:
            self._daily_reset_day = day
            self._daily_pnl = 0.0

    def snapshot(self) -> dict:
        """Summary for dashboard display."""
        open_pos = {
            k: {
                "symbol": p.symbol,
                "entry": p.entry_price,
                "stop": p.stop_price,
                "tp": p.tp_price,
                "qty": p.qty,
                "status": p.status,
                "age_sec": round(time.time() - p.opened_at, 0),
            }
            for k, p in self._positions.items()
            if p.status in ("pending", "open")
        }
        return {
            "open_positions": open_pos,
            "open_count": len(open_pos),
            "daily_pnl": round(self._daily_pnl, 4),
            "reserved_capital": round(self._reserved_capital, 2),
        }
