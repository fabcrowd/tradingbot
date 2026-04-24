"""Shared candle types and feed entrypoint — Coinbase Advanced Trade (perps) only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Candle:
    """Normalised OHLCV candle."""

    timestamp: float  # unix epoch of candle open
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float
    trades: int


CandleCallback = Callable[[str, Candle], None]  # (pair_key, candle)
TickCallback = Callable[[str, Candle], None]  # (pair_key, live candle)


async def start_candle_feed(
    pairs: dict[str, str],
    intervals: dict[str, int],
    rest_seed_count: int = 100,
    buffer_size: int = 500,
    *,
    venue: str = "coinbase_perps",
):
    """Start the Coinbase candle / book WebSocket feed (CDE perps)."""
    from .coinbase_candle_feed import start_coinbase_candle_feed

    return await start_coinbase_candle_feed(
        pairs, intervals, rest_seed_count, buffer_size,
    )
