from __future__ import annotations

from dataclasses import dataclass

from .feeds import MarketSnapshot


@dataclass
class Features:
    symbol: str
    market_id: str
    yes_token_id: str
    no_token_id: str
    mid: float
    spread: float
    spread_bps: float
    ts: float
    end_ts: float = 0.0
    binance_price: float = 0.0
    chainlink_price: float = 0.0


def compute_features(snapshot: MarketSnapshot) -> Features:
    spread = max(0.0, snapshot.best_ask - snapshot.best_bid)
    spread_bps = (spread / max(snapshot.mid, 1e-9)) * 10000.0
    return Features(
        symbol=snapshot.symbol,
        market_id=snapshot.market_id,
        yes_token_id=snapshot.yes_token_id,
        no_token_id=snapshot.no_token_id,
        mid=snapshot.mid,
        spread=spread,
        spread_bps=spread_bps,
        ts=snapshot.ts,
        end_ts=snapshot.end_ts,
        binance_price=snapshot.binance_price,
        chainlink_price=snapshot.chainlink_price,
    )
