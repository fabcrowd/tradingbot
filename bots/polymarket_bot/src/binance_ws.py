"""Direct Binance Futures WebSocket feed for BTC/ETH prices.

Bypasses Polymarket RTDS (1 update/sec, ~1.4s lag) by connecting directly
to Binance Futures WS (10+ updates/sec, ~650ms network latency).

Only activates during scheduled volatile windows:
  - US equity market open (9:30-10:00 AM ET)
  - CPI/PPI/NFP release windows
  - FOMC announcement windows
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from aiohttp import ClientSession, ClientTimeout, WSMsgType

LOG = logging.getLogger("polymarket_bot.binance_ws")

BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws"


@dataclass
class BinanceTick:
    price: float
    ts: float
    volume: float = 0.0


class BinanceDirectFeed:
    """Direct Binance Futures WebSocket feed.

    Streams aggTrade data for BTC/USDT and ETH/USDT. Computes rolling
    momentum at configurable lookback windows. Replaces RTDS for the
    crypto taker strategy.
    """

    def __init__(
        self,
        symbols: tuple[str, ...] = ("btcusdt", "ethusdt"),
        max_ticks: int = 600,
    ) -> None:
        self._symbols = symbols
        self._max_ticks = max_ticks
        self._ticks: dict[str, deque[BinanceTick]] = {
            s: deque(maxlen=max_ticks) for s in symbols
        }
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._connected = False
        self._last_ts: float = 0.0
        self._tick_count = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def latest(self, symbol: str) -> BinanceTick | None:
        buf = self._ticks.get(symbol.lower())
        if not buf:
            return None
        return buf[-1]

    def momentum_pct(self, symbol: str, lookback_sec: float = 30.0) -> float:
        buf = self._ticks.get(symbol.lower())
        if not buf or len(buf) < 2:
            return 0.0
        now_tick = buf[-1]
        cutoff = now_tick.ts - lookback_sec
        oldest = now_tick
        for t in buf:
            if t.ts >= cutoff:
                oldest = t
                break
        if oldest.price <= 0:
            return 0.0
        return (now_tick.price - oldest.price) / oldest.price

    def volatility_1m(self, symbol: str) -> float:
        """1-minute realized volatility as stdev of tick returns."""
        buf = self._ticks.get(symbol.lower())
        if not buf or len(buf) < 10:
            return 0.0
        now = buf[-1].ts
        recent = [t.price for t in buf if t.ts > now - 60]
        if len(recent) < 2:
            return 0.0
        mean = sum(recent) / len(recent)
        var = sum((p - mean) ** 2 for p in recent) / len(recent)
        return (var ** 0.5) / mean if mean > 0 else 0.0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._ws_loop())
        LOG.info("BinanceDirectFeed started for %s", self._symbols)

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=None))
        return self._session

    async def _ws_loop(self) -> None:
        while True:
            try:
                await self._connect()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                LOG.warning("Binance WS lost: %s — reconnecting in 3s", exc)
                self._connected = False
                await asyncio.sleep(3)

    async def _connect(self) -> None:
        streams = "/".join(f"{s}@aggTrade" for s in self._symbols)
        url = f"{BINANCE_FUTURES_WS}/{streams}"
        sess = await self._get_session()

        async with sess.ws_connect(url, heartbeat=10) as ws:
            self._connected = True
            LOG.info("Binance Futures WS connected: %s", url)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    self._handle(msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSED):
                    break
        self._connected = False

    def _handle(self, raw: str) -> None:
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Combined stream wraps in {"stream": "...", "data": {...}}
        if "data" in d:
            d = d["data"]

        sym = str(d.get("s", "")).lower()
        price = float(d.get("p", 0))
        ts_ms = int(d.get("T", d.get("E", 0)))
        qty = float(d.get("q", 0))

        if sym not in self._ticks or price <= 0:
            return

        self._ticks[sym].append(BinanceTick(
            price=price,
            ts=ts_ms / 1000.0 if ts_ms > 1e12 else ts_ms,
            volume=qty,
        ))
        self._last_ts = time.time()
        self._tick_count += 1


class VolatilityScheduler:
    """Determines whether the crypto taker strategy should be active.

    Checks if the current time falls within known high-volatility windows.
    """

    # All times in UTC
    WINDOWS: list[tuple[int, int, int, int, str]] = [
        # US equity market open: 13:30-14:30 UTC (weekdays)
        (13, 30, 14, 30, "us_market_open"),
        # US equity market close: 19:30-20:30 UTC (weekdays)
        (19, 30, 20, 30, "us_market_close"),
        # London open overlap: 08:00-09:00 UTC (weekdays)
        (8, 0, 9, 0, "london_open"),
        # CPI/PPI typically at 12:30 UTC on release days
        (12, 25, 12, 45, "macro_release"),
        # FOMC typically at 18:00 UTC
        (17, 55, 18, 15, "fomc_window"),
    ]

    def __init__(self, always_active: bool = False) -> None:
        self._always_active = always_active
        self._force_active_until: float = 0.0

    def force_active(self, duration_sec: float = 300.0) -> None:
        self._force_active_until = time.time() + duration_sec

    def is_active(self) -> tuple[bool, str]:
        if self._always_active:
            return True, "always_active"

        if time.time() < self._force_active_until:
            return True, "force_active"

        now = datetime.now(timezone.utc)
        if now.weekday() >= 5:  # Saturday/Sunday
            return False, "weekend"

        h, m = now.hour, now.minute
        current_mins = h * 60 + m
        for start_h, start_m, end_h, end_m, label in self.WINDOWS:
            start = start_h * 60 + start_m
            end = end_h * 60 + end_m
            if start <= current_mins <= end:
                return True, label

        return False, "outside_volatile_window"
