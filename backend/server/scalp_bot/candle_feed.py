"""OHLC candle feed — seeds from Kraken REST, then streams from WS ohlc channel.

Only fires callbacks on confirmed closed candles (confirm=True from WS).
Live/open candle updates are stored but never trigger signal computation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import aiohttp
from kraken.spot import SpotWSClient

LOG = logging.getLogger(__name__)

KRAKEN_REST_OHLC = "https://api.kraken.com/0/public/OHLC"


@dataclass
class Candle:
    """Normalised OHLCV candle."""
    timestamp: float    # unix epoch of candle open
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float
    trades: int


CandleCallback = Callable[[str, Candle], None]  # (pair_key, candle)
TickCallback = Callable[[str, Candle], None]    # (pair_key, live candle) — each ohlc update


class CandleFeed(SpotWSClient):
    """Public WS client that subscribes to ohlc channel and maintains per-pair buffers."""

    def __init__(
        self,
        pairs: dict[str, str],   # pair_key -> symbol (e.g. "BTC_USD" -> "XBT/USD")
        intervals: dict[str, int],  # pair_key -> interval minutes
        buffer_size: int = 500,
    ) -> None:
        super().__init__()  # no auth — public channel only
        self._pairs = pairs          # key -> symbol
        self._symbol_to_key = {v: k for k, v in pairs.items()}
        self._intervals = intervals  # key -> interval
        self._buffers: dict[str, deque[Candle]] = {
            k: deque(maxlen=buffer_size) for k in pairs
        }
        self._live_candles: dict[str, Candle] = {}
        self._callbacks: list[CandleCallback] = []
        self._tick_callbacks: list[TickCallback] = []
        self._candle_counts: dict[str, int] = {k: 0 for k in pairs}
        self._last_interval_begin: dict[str, str] = {}  # pair_key -> interval_begin for close detection

    def register_callback(self, cb: CandleCallback) -> None:
        self._callbacks.append(cb)

    def register_tick_callback(self, cb: TickCallback) -> None:
        """Fire on each Kraken ohlc message (intra-bar updates), same contract as Coinbase tick cb."""
        self._tick_callbacks.append(cb)

    def get_buffer(self, pair_key: str) -> list[Candle]:
        return list(self._buffers.get(pair_key, deque()))

    def get_live_candle(self, pair_key: str) -> Candle | None:
        return self._live_candles.get(pair_key)

    def candle_count(self, pair_key: str) -> int:
        return self._candle_counts.get(pair_key, 0)

    async def on_message(self, message: dict) -> None:
        channel = message.get("channel", "")
        msg_type = message.get("type", "")
        if channel == "ohlc":
            for entry in message.get("data", []):
                entry["_msg_type"] = msg_type
                await self._handle_ohlc_entry(entry)

    async def _handle_ohlc_entry(self, entry: dict) -> None:
        symbol = entry.get("symbol", "")
        pair_key = self._symbol_to_key.get(symbol)
        if pair_key is None:
            return

        interval_begin = entry.get("interval_begin", "")
        msg_type = entry.get("_msg_type", "")

        try:
            candle = Candle(
                timestamp=_parse_ts(interval_begin),
                open=float(entry.get("open", 0)),
                high=float(entry.get("high", 0)),
                low=float(entry.get("low", 0)),
                close=float(entry.get("close", 0)),
                volume=float(entry.get("volume", 0)),
                vwap=float(entry.get("vwap", 0)),
                trades=int(entry.get("trades", 0)),
            )
        except (ValueError, TypeError):
            LOG.debug("CandleFeed: failed to parse ohlc entry %s", entry)
            return

        prev_begin = self._last_interval_begin.get(pair_key)

        if msg_type == "snapshot":
            self._last_interval_begin[pair_key] = interval_begin
            self._live_candles[pair_key] = candle
            for tcb in self._tick_callbacks:
                try:
                    tcb(pair_key, candle)
                except Exception:
                    LOG.exception("CandleFeed tick callback error (snapshot) for %s", pair_key)
            return

        if prev_begin and interval_begin != prev_begin and pair_key in self._live_candles:
            closed = self._live_candles[pair_key]
            self._buffers[pair_key].append(closed)
            self._candle_counts[pair_key] += 1
            LOG.info(
                "CandleFeed %s: CLOSED candle @ %.6f vol=%.1f trades=%d (n=%d)",
                pair_key, closed.close, closed.volume, closed.trades, self._candle_counts[pair_key],
            )
            for cb in self._callbacks:
                try:
                    cb(pair_key, closed)
                except Exception:
                    LOG.exception("CandleFeed callback error for %s", pair_key)

        self._last_interval_begin[pair_key] = interval_begin
        self._live_candles[pair_key] = candle

        for tcb in self._tick_callbacks:
            try:
                tcb(pair_key, candle)
            except Exception:
                LOG.exception("CandleFeed tick callback error for %s", pair_key)

    async def subscribe_all(self) -> None:
        """Subscribe to ohlc channel for all configured pairs."""
        for pair_key, symbol in self._pairs.items():
            interval = self._intervals.get(pair_key, 5)
            try:
                await self.subscribe(params={
                    "channel": "ohlc",
                    "symbol": [symbol],
                    "interval": interval,
                    "snapshot": True,
                })
                LOG.info("CandleFeed: subscribed to ohlc %s interval=%dm", symbol, interval)
            except Exception:
                LOG.exception("CandleFeed: failed to subscribe to %s", symbol)
            await asyncio.sleep(0.5)  # stagger subscriptions

    async def seed_from_rest(
        self,
        pair_key: str,
        symbol: str,
        interval: int,
        count: int = 100,
    ) -> int:
        """Fetch historical candles from REST and pre-fill the buffer.

        Returns the number of candles loaded.
        """
        # Kraken uses different pair names for REST vs WS
        rest_pair = symbol.replace("/", "")
        url = f"{KRAKEN_REST_OHLC}?pair={rest_pair}&interval={interval}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
        except Exception:
            LOG.warning("CandleFeed: REST seed failed for %s", symbol, exc_info=True)
            return 0

        if data.get("error"):
            LOG.warning("CandleFeed: REST seed error for %s: %s", symbol, data["error"])
            return 0

        result = data.get("result", {})
        # REST result key may differ from the request pair name
        rows = None
        for k, v in result.items():
            if k != "last" and isinstance(v, list):
                rows = v
                break

        if not rows:
            LOG.warning("CandleFeed: no REST candles for %s", symbol)
            return 0

        # REST format: [time, open, high, low, close, vwap, volume, count]
        loaded = 0
        buf = self._buffers[pair_key]
        for row in rows[-count:]:
            try:
                candle = Candle(
                    timestamp=float(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    vwap=float(row[5]),
                    volume=float(row[6]),
                    trades=int(row[7]),
                )
                buf.append(candle)
                loaded += 1
            except (ValueError, TypeError, IndexError):
                continue

        self._candle_counts[pair_key] = loaded
        LOG.info("CandleFeed: seeded %d candles for %s from REST", loaded, symbol)
        return loaded


def _parse_ts(s: str) -> float:
    """Parse RFC3339 or unix-float timestamp to epoch float."""
    if not s:
        return time.time()
    try:
        return float(s)
    except ValueError:
        pass
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return time.time()


async def start_candle_feed(
    pairs: dict[str, str],
    intervals: dict[str, int],
    rest_seed_count: int = 100,
    buffer_size: int = 500,
    *,
    venue: str = "kraken_spot",
):
    """Create, seed from REST, start WS, and return a running feed (Kraken or Coinbase)."""
    v = (venue or "kraken_spot").strip().lower()
    if v == "coinbase_perps":
        from .coinbase_candle_feed import start_coinbase_candle_feed

        return await start_coinbase_candle_feed(
            pairs, intervals, rest_seed_count, buffer_size,
        )

    feed = CandleFeed(pairs=pairs, intervals=intervals, buffer_size=buffer_size)

    # Seed all pairs from REST before WS connects
    for pair_key, symbol in pairs.items():
        interval = intervals.get(pair_key, 5)
        await feed.seed_from_rest(pair_key, symbol, interval, rest_seed_count)
        await asyncio.sleep(1.1)  # respect 1 req/s public rate limit

    await feed.start()
    await feed.subscribe_all()
    return feed
