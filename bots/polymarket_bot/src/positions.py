"""Position lifecycle for binary prediction markets.

Tracks open positions through to contract resolution ($0 or $1 settlement).
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

LOG = logging.getLogger("polymarket_bot.positions")

TAKER_FEE_RATE = 0.072


def taker_fee(shares: float, price: float) -> float:
    p = min(max(price, 1e-6), 1.0 - 1e-6)
    return shares * TAKER_FEE_RATE * p * (1.0 - p)


@dataclass
class Position:
    id: str
    market_id: str
    token_id: str
    side: str
    entry_price: float
    size: float
    fee_paid: float
    entry_ts: float
    market_end_ts: float
    status: str = "open"
    pnl: float = 0.0
    resolved_ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PositionManager:
    """Manages the full lifecycle: open -> resolve -> realized PnL."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}

    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.status == "open"]

    @property
    def all_positions(self) -> list[Position]:
        return list(self._positions.values())

    def open_position(
        self,
        market_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        market_end_ts: float = 0.0,
        is_maker: bool = True,
    ) -> Position:
        fee = 0.0 if is_maker else taker_fee(size, price)
        pos = Position(
            id=uuid.uuid4().hex[:12],
            market_id=market_id,
            token_id=token_id,
            side=side,
            entry_price=price,
            size=size,
            fee_paid=fee,
            entry_ts=time.time(),
            market_end_ts=market_end_ts,
        )
        self._positions[pos.id] = pos
        LOG.info(
            "Opened %s %s %.0f shares @ %.4f (fee=%.4f) market=%s",
            pos.side, pos.id, pos.size, pos.entry_price, pos.fee_paid, pos.market_id[:16],
        )
        return pos

    def resolve_position(self, pos_id: str, winning_outcome: str) -> float:
        pos = self._positions.get(pos_id)
        if pos is None or pos.status != "open":
            return 0.0
        won = (pos.side == "yes" and winning_outcome == "yes") or \
              (pos.side == "no" and winning_outcome == "no")
        if won:
            pos.pnl = (1.0 - pos.entry_price) * pos.size - pos.fee_paid
            pos.status = "won"
        else:
            pos.pnl = -(pos.entry_price * pos.size) - pos.fee_paid
            pos.status = "lost"
        pos.resolved_ts = time.time()
        LOG.info(
            "Resolved %s %s -> %s  pnl=%.4f USD",
            pos.side, pos.id, pos.status, pos.pnl,
        )
        return pos.pnl

    def resolve_market(self, market_id: str, winning_outcome: str) -> list[dict[str, Any]]:
        """Resolve all open positions for a market. Returns trade records."""
        records: list[dict[str, Any]] = []
        for pos in list(self._positions.values()):
            if pos.market_id == market_id and pos.status == "open":
                pnl = self.resolve_position(pos.id, winning_outcome)
                records.append({
                    "ts": time.time(),
                    "symbol": f"market:{market_id[:16]}",
                    "side": pos.side,
                    "price": pos.entry_price,
                    "size": pos.size,
                    "pnl_delta": pnl,
                    "fee_paid": pos.fee_paid,
                    "status": pos.status,
                    "position_id": pos.id,
                    "market_id": market_id,
                    "is_maker": pos.fee_paid == 0.0,
                })
        return records

    def get_exposure_usd(self) -> float:
        return sum(p.entry_price * p.size for p in self.open_positions)

    def count_for_market(self, market_id: str) -> int:
        return sum(1 for p in self.open_positions if p.market_id == market_id)

    def inventory_ratio(self, market_id: str, max_size: float) -> float:
        """Net YES share count for a market, normalized to [-1, 1]."""
        net = 0.0
        for p in self.open_positions:
            if p.market_id == market_id:
                net += p.size if p.side == "yes" else -p.size
        if max_size <= 0:
            return 0.0
        return max(-1.0, min(1.0, net / max_size))

    def realized_pnl(self) -> float:
        return sum(p.pnl for p in self._positions.values() if p.status in ("won", "lost"))

    def load_from_records(self, records: list[dict[str, Any]]) -> None:
        """Rebuild positions from persisted trade records (for restart recovery)."""
        for r in records:
            pid = r.get("position_id", "")
            if not pid:
                continue
            if pid in self._positions:
                continue
            pos = Position(
                id=pid,
                market_id=r.get("market_id", ""),
                token_id=r.get("token_id", ""),
                side=r.get("side", ""),
                entry_price=float(r.get("price", 0)),
                size=float(r.get("size", 0)),
                fee_paid=float(r.get("fee_paid", 0)),
                entry_ts=float(r.get("ts", 0)),
                market_end_ts=0.0,
                status=r.get("status", "open"),
                pnl=float(r.get("pnl_delta", 0)),
            )
            self._positions[pid] = pos
