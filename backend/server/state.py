"""Central BotState — shared mutable state across all components."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger(__name__)


@dataclass
class OrderBookLevel:
    price: float
    volume: float


@dataclass
class PairState:
    symbol: str
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_levels: list[OrderBookLevel] = field(default_factory=list)
    ask_levels: list[OrderBookLevel] = field(default_factory=list)
    inventory_base: float = 0.0
    inventory_quote: float = 0.0
    position_cost_quote: float = 0.0

    @property
    def mid_price(self) -> float:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return 0.0

    @property
    def microprice(self) -> float:
        """Volume-weighted mid — better fair-value estimate than raw mid."""
        if not (self.best_bid and self.best_ask):
            return 0.0
        bid_vol = self.bid_levels[0].volume if self.bid_levels else 0.0
        ask_vol = self.ask_levels[0].volume if self.ask_levels else 0.0
        total = bid_vol + ask_vol
        if total <= 0:
            return self.mid_price
        return (self.best_bid * ask_vol + self.best_ask * bid_vol) / total

    @property
    def spread(self) -> float:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": self.mid_price,
            "spread": self.spread,
            "microprice": self.microprice,
            "bid_levels": [{"price": lvl.price, "volume": lvl.volume} for lvl in self.bid_levels],
            "ask_levels": [{"price": lvl.price, "volume": lvl.volume} for lvl in self.ask_levels],
            "inventory_base": self.inventory_base,
            "inventory_quote": self.inventory_quote,
            "position_cost_quote": self.position_cost_quote,
        }


@dataclass
class ActiveOrder:
    cl_ord_id: str
    pair_key: str
    symbol: str
    side: str  # "buy" or "sell"
    price: float
    qty: float
    placed_at: float = field(default_factory=time.time)
    exchange_order_id: str = ""
    filled_qty: float = 0.0
    cancel_retry: bool = False
    cancel_attempt_count: int = 0  # NM-008: cap cancel retries to avoid rate-limiter starvation


@dataclass
class TradeRecord:
    timestamp: float
    pair_key: str
    symbol: str
    side: str
    price: float
    qty: float
    fee: float
    pnl_delta: float
    spread_bps: int | None = None


@dataclass
class OCOPair:
    pair_key: str
    stop_cl_ord_id: str
    take_cl_ord_id: str
    created_at: float = field(default_factory=time.time)


class BotState:
    """Thread-safe-ish shared state. All mutations happen on the asyncio loop."""

    def __init__(self) -> None:
        self.pairs: dict[str, PairState] = {}
        self.active_orders: dict[str, ActiveOrder] = {}
        self.recent_fills: list[TradeRecord] = []
        self.total_pnl: float = 0.0
        self.total_trades: int = 0
        self.total_wins: int = 0
        self.fill_event_count: int = 0
        self.pnl_curve: list[tuple[float, float]] = []
        self.running: bool = False
        self.mode: str = "paper"
        self.mm_spread_bot_enabled: bool = False
        self.active_pair_key: str = ""
        self._lock = asyncio.Lock()
        self.last_cancel_reason: dict[str, str] = {}
        self.peak_pnl: float = 0.0
        self.session_start_pnl: float = 0.0
        self.session_start_ts: float = 0.0
        self.risk_halted: bool = False
        self.risk_halt_reason: str = ""
        # Scalp-native portfolio halt (independent of spread-MM bot / portfolio risk_halted).
        self.scalp_risk_halted: bool = False
        self.scalp_risk_halt_reason: str = ""
        self.scalp_risk_halted_ts: float = 0.0
        self.last_order_reject_ts: float = 0.0
        self.last_order_reject_reason: str = ""
        self.order_reject_count: int = 0
        self.insufficient_funds_until: float = 0.0
        self.order_reject_pause_until: float = 0.0
        # Set from ScalpRuntime / config — when False, venue rejects do not time-block new entries.
        self.exchange_entry_cooldown_enabled: bool = True
        self.last_fill_ts: dict[str, float] = {}
        self.oco_pairs: dict[str, OCOPair] = {}
        self._alert_fn: Any = None
        # Dashboard asyncio loop — set when DashboardServer starts; used to deliver
        # alerts from worker threads (e.g. bar_store Parquet append via asyncio.to_thread).
        self._alert_loop: Any = None  # asyncio.AbstractEventLoop | None
        self._exchange_errors: list[dict[str, Any]] = []
        self._request_snapshot_bump: Any = None

    def push_alert(
        self,
        level: str,
        title: str,
        detail: str = "",
        source: str = "",
        *,
        persistent: bool = False,
        exchange_error_id: str | None = None,
    ) -> None:
        if level == "error":
            LOG.error("ALERT [%s] %s — %s", source, title, detail)
        elif level == "warning":
            LOG.warning("ALERT [%s] %s — %s", source, title, detail)
        else:
            LOG.info("ALERT [%s] %s — %s", source, title, detail)
        if self._alert_fn is None:
            return
        coro = self._alert_fn(level, title, detail, source, persistent, exchange_error_id)
        main_loop = getattr(self, "_alert_loop", None)
        try:
            cur = asyncio.get_running_loop()
        except RuntimeError:
            cur = None

        def _fail(fut: asyncio.Future | asyncio.Task) -> None:
            try:
                exc = fut.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                return
            if exc is not None:
                LOG.warning("alert broadcast task failed: %s", exc)

        if main_loop is not None and main_loop.is_running() and cur is not main_loop:
            try:
                fut = asyncio.run_coroutine_threadsafe(coro, main_loop)
                fut.add_done_callback(_fail)
            except Exception:
                LOG.warning("ALERT thread-dispatch failed", exc_info=True)
        elif cur is not None:
            try:
                t = cur.create_task(coro)
                t.add_done_callback(_fail)
            except Exception:
                LOG.warning("ALERT create_task failed", exc_info=True)
        elif main_loop is not None and main_loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(coro, main_loop)
                fut.add_done_callback(_fail)
            except Exception:
                LOG.warning("ALERT thread-dispatch failed", exc_info=True)
        else:
            LOG.warning("ALERT dropped (no dashboard loop): [%s] %s — %s", source, title, detail)

    def record_exchange_error(self, level: str, title: str, detail: str = "", source: str = "") -> str:
        lv = level if level in ("error", "warning") else "warning"
        err_id = f"ex-{uuid.uuid4().hex[:12]}"
        entry: dict[str, Any] = {
            "id": err_id,
            "ts": time.time(),
            "level": lv,
            "title": title,
            "detail": detail,
            "source": source,
            "acknowledged": False,
        }
        self._exchange_errors.append(entry)
        if len(self._exchange_errors) > 100:
            self._exchange_errors = self._exchange_errors[-100:]
        self.push_alert(lv, title, detail, source, persistent=True, exchange_error_id=err_id)
        bump = getattr(self, "_request_snapshot_bump", None)
        if callable(bump):
            try:
                bump()
            except Exception:
                LOG.debug("exchange error snapshot bump failed", exc_info=True)
        return err_id

    def acknowledge_exchange_errors(self, error_ids: list[str] | None) -> None:
        if error_ids is None:
            for e in self._exchange_errors:
                e["acknowledged"] = True
            return
        want = set(error_ids)
        for e in self._exchange_errors:
            if e["id"] in want:
                e["acknowledged"] = True

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_wins / self.total_trades * 100

    def trim_lists(self) -> None:
        if len(self.recent_fills) > 1000:
            self.recent_fills = self.recent_fills[-500:]
        if len(self.pnl_curve) > 2000:
            self.pnl_curve = self.pnl_curve[-1000:]

    def init_pair(self, key: str, symbol: str) -> None:
        if key not in self.pairs:
            self.pairs[key] = PairState(symbol=symbol)

    def exchange_entries_throttled(self) -> bool:
        """True during cooldown after consecutive venue rejects or insufficient-funds errors."""
        if not getattr(self, "exchange_entry_cooldown_enabled", True):
            return False
        now = time.time()
        return now < self.order_reject_pause_until or now < self.insufficient_funds_until

    def note_order_reject(
        self,
        reason: str,
        *,
        source: str = "coinbase",
        max_consecutive: int = 3,
        consecutive_pause_sec: float = 120.0,
        insufficient_funds_cooldown_sec: float = 300.0,
    ) -> None:
        self.last_order_reject_ts = time.time()
        self.last_order_reject_reason = (reason or "")[:500]
        self.order_reject_count += 1
        if not getattr(self, "exchange_entry_cooldown_enabled", True):
            return
        ur = self.last_order_reject_reason.upper()
        if "INSUFFICIENT" in ur and "FUND" in ur:
            self.insufficient_funds_until = max(
                self.insufficient_funds_until,
                time.time() + max(1.0, float(insufficient_funds_cooldown_sec)),
            )
        mc = max(1, int(max_consecutive))
        if self.order_reject_count >= mc:
            self.order_reject_pause_until = max(
                self.order_reject_pause_until,
                time.time() + max(1.0, float(consecutive_pause_sec)),
            )
            self.order_reject_count = 0

    def note_order_success(self) -> None:
        self.order_reject_count = 0

    def scalp_entries_blocked(self) -> bool:
        """True when new scalp entries must not fire (scalp halt, MM+spread halt, or venue cooldown)."""
        if self.scalp_risk_halted:
            return True
        if self.mm_spread_bot_enabled and self.risk_halted:
            return True
        if self.exchange_entries_throttled():
            return True
        return False

    def scalp_exchange_throttle_diag(self) -> dict[str, Any]:
        """Venue cooldown fields for ``portfolio_risk`` when entries are exchange-throttled."""
        now = time.time()
        pause_rem = max(0.0, float(self.order_reject_pause_until) - now)
        funds_rem = max(0.0, float(self.insufficient_funds_until) - now)
        return {
            "exchange_entry_cooldown_enabled": bool(
                getattr(self, "exchange_entry_cooldown_enabled", True),
            ),
            "exchange_entries_throttled": self.exchange_entries_throttled(),
            "order_reject_pause_until": float(self.order_reject_pause_until),
            "insufficient_funds_until": float(self.insufficient_funds_until),
            "exchange_throttle_reject_remain_sec": round(pause_rem, 1),
            "exchange_throttle_insufficient_remain_sec": round(funds_rem, 1),
            "last_order_reject_reason": (self.last_order_reject_reason or "")[:300],
        }

    def snapshot(self) -> dict[str, Any]:
        orders = [
            {
                "cl_ord_id": o.cl_ord_id,
                "pair_key": o.pair_key,
                "side": o.side,
                "price": o.price,
                "qty": o.qty,
                "filled_qty": o.filled_qty,
            }
            for o in self.active_orders.values()
        ]
        fills = [
            {
                "timestamp": f.timestamp,
                "pair_key": f.pair_key,
                "side": f.side,
                "price": f.price,
                "qty": f.qty,
                "fee": f.fee,
                "pnl_delta": f.pnl_delta,
            }
            for f in self.recent_fills[-50:]
        ]
        return {
            "pairs": {key: ps.to_dict() for key, ps in self.pairs.items()},
            "active_orders": orders,
            "recent_fills": fills,
            "total_pnl": round(self.total_pnl, 6),
            "total_trades": self.total_trades,
            "fill_event_count": self.fill_event_count,
            "win_rate": round(self.win_rate, 1),
            "pnl_curve": self.pnl_curve[-500:],
            "running": self.running,
            "mode": self.mode,
            "spread_bot_enabled": False,
            "active_pair_key": self.active_pair_key,
            "last_cancel_reason": self.last_cancel_reason,
            "last_fill_ts": {k: round(v, 1) for k, v in self.last_fill_ts.items()},
            "session_start_pnl": round(self.session_start_pnl, 6),
            "session_start_ts": self.session_start_ts,
            "peak_pnl": round(self.peak_pnl, 6),
            "risk_halted": self.risk_halted,
            "risk_halt_reason": self.risk_halt_reason,
            "scalp_risk_halted": self.scalp_risk_halted,
            "scalp_risk_halt_reason": self.scalp_risk_halt_reason,
            "scalp_risk_halted_ts": self.scalp_risk_halted_ts,
            "scalp_entries_blocked": self.scalp_entries_blocked(),
            "oco_pairs": {
                k: {"pair_key": v.pair_key, "stop": v.stop_cl_ord_id, "take": v.take_cl_ord_id}
                for k, v in self.oco_pairs.items()
            },
            "last_order_reject_reason": self.last_order_reject_reason,
            "order_reject_count": self.order_reject_count,
            "order_reject_pause_until": self.order_reject_pause_until,
            "insufficient_funds_until": self.insufficient_funds_until,
            "exchange_entries_throttled": self.exchange_entries_throttled(),
            "exchange_errors": list(self._exchange_errors),
            "exchange_errors_unacked": sum(
                1 for e in self._exchange_errors if not e.get("acknowledged")
            ),
        }
