"""Central BotState — shared mutable state across all components."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
    # Quote currency locked in the base position (incl. buy fees); for P&L on sells
    position_cost_quote: float = 0.0
    # Fast threat / market metrics (set by ThreatDetector).
    threat_level: "ThreatLevel" = field(default=None)  # type: ignore[assignment]
    book_imbalance: float = 0.0
    mid_velocity_bps: float = 0.0
    tick_volatility: float = 0.0
    spread_blow_out_ratio: float = 1.0

    # Per-pair realized volatility (set by ThreatDetector).
    realized_vol: float = 0.0
    # Timestamp of last book update (for staleness detection).
    last_book_update_ts: float = 0.0

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


@dataclass
class ActiveOrder:
    cl_ord_id: str
    pair_key: str
    symbol: str
    side: str  # "buy" or "sell"
    price: float
    qty: float
    placed_at: float = field(default_factory=time.time)
    kraken_order_id: str = ""
    filled_qty: float = 0.0
    cancel_retry: bool = False


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


class ThreatLevel(str, Enum):
    CALM = "calm"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


class CancelReason(str, Enum):
    NONE = ""
    PRICE_DRIFT = "price drift > 1.5x spread"
    STALE = "stale order (age)"
    DEPEG = "depeg circuit breaker"
    STOP = "engine stopped"
    SURVIVAL_PNL = "survival P&L floor"


class BotState:
    """Thread-safe-ish shared state. All mutations happen on the asyncio loop."""

    def __init__(self) -> None:
        self.pairs: dict[str, PairState] = {}
        self.active_orders: dict[str, ActiveOrder] = {}  # cl_ord_id -> order
        self.recent_fills: list[TradeRecord] = []
        self.total_pnl: float = 0.0
        # Completed sell legs (round-trip metrics / win rate)
        self.total_trades: int = 0
        self.total_wins: int = 0
        # Every logged fill (buy or sell)
        self.fill_event_count: int = 0
        self.spread_captured: float = 0.0
        self.pnl_curve: list[tuple[float, float]] = []  # (timestamp, cumulative_pnl)
        self.running: bool = False
        self.mode: str = "paper"
        self.active_pair_key: str = ""
        self._lock = asyncio.Lock()
        self.market_signals: dict[str, Any] | None = None
        self.last_cancel_reason: dict[str, str] = {}  # pair_key -> reason text
        self.learner_info: dict[str, Any] = {}  # pair_key -> {spread, rate, direction}
        self.volume_30d: float = 0.0  # rolling 30-day USD volume for fee tier
        # Risk management tracking
        self.peak_pnl: float = 0.0          # highest P&L reached in this session
        self.session_start_pnl: float = 0.0  # P&L at session start (for daily calc)
        self.session_start_ts: float = 0.0   # timestamp when session started
        self.risk_halted: bool = False        # True when auto-stop triggered
        self.risk_halt_reason: str = ""
        self.last_order_reject_ts: float = 0.0  # timestamp of last order rejection (for backoff)
        self.last_fill_ts: dict[str, float] = {}  # pair_key -> timestamp of most recent fill

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_wins / self.total_trades * 100

    def init_pair(self, key: str, symbol: str) -> None:
        if key not in self.pairs:
            self.pairs[key] = PairState(symbol=symbol)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot for the dashboard."""
        pair_data = {}
        for key, ps in self.pairs.items():
            pair_data[key] = {
                "symbol": ps.symbol,
                "best_bid": ps.best_bid,
                "best_ask": ps.best_ask,
                "mid_price": ps.mid_price,
                "spread": ps.spread,
                "bid_levels": [
                    {"price": l.price, "volume": l.volume} for l in ps.bid_levels
                ],
                "ask_levels": [
                    {"price": l.price, "volume": l.volume} for l in ps.ask_levels
                ],
                "inventory_base": ps.inventory_base,
                "inventory_quote": ps.inventory_quote,
                "threat_level": (ps.threat_level.value if isinstance(ps.threat_level, ThreatLevel) else None),
                "book_imbalance": ps.book_imbalance,
                "mid_velocity_bps": ps.mid_velocity_bps,
                "tick_volatility": ps.tick_volatility,
                "spread_blow_out_ratio": ps.spread_blow_out_ratio,
            }

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
            "pairs": pair_data,
            "active_orders": orders,
            "recent_fills": fills,
            "total_pnl": round(self.total_pnl, 6),
            "total_trades": self.total_trades,
            "fill_event_count": self.fill_event_count,
            "win_rate": round(self.win_rate, 1),
            "spread_captured": round(self.spread_captured, 6),
            "pnl_curve": self.pnl_curve[-500:],
            "running": self.running,
            "mode": self.mode,
            "active_pair_key": self.active_pair_key,
            "last_cancel_reason": self.last_cancel_reason,
            "learner_info": self.learner_info,
            "last_fill_ts": {k: round(v, 1) for k, v in self.last_fill_ts.items()},
            "volume_30d": round(self.volume_30d, 2),
            # Risk / projections
            "session_start_pnl": round(self.session_start_pnl, 6),
            "session_start_ts": self.session_start_ts,
            "peak_pnl": round(self.peak_pnl, 6),
            "risk_halted": self.risk_halted,
            "risk_halt_reason": self.risk_halt_reason,
        }
