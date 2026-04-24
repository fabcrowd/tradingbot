"""Coinbase Advanced Trade — public candle WebSocket + REST seed (INTX perps product ids)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque

from typing import Callable

import aiohttp

from .candle_feed import Candle, CandleCallback

LOG = logging.getLogger(__name__)

TickCallback = Callable[[str, Candle], None]

COINBASE_WS_URL = "wss://advanced-trade-ws.coinbase.com"


def _granularity_for_interval(interval_minutes: int) -> str:
    m = max(1, int(interval_minutes))
    if m <= 1:
        return "ONE_MINUTE"
    if m <= 5:
        return "FIVE_MINUTE"
    if m <= 15:
        return "FIFTEEN_MINUTE"
    if m <= 60:
        return "ONE_HOUR"
    return "SIX_HOUR"


class CoinbaseCandleFeed:
    """Public WS client: `candles` channel + in-memory buffers (confirmed closes)."""

    def __init__(
        self,
        pairs: dict[str, str],
        intervals: dict[str, int],
        buffer_size: int = 500,
    ) -> None:
        self._pairs = pairs
        self._product_to_key = {v: k for k, v in pairs.items()}
        self._intervals = intervals
        self._buffers: dict[str, deque[Candle]] = {k: deque(maxlen=buffer_size) for k in pairs}
        self._live_candles: dict[str, Candle] = {}
        self._callbacks: list[CandleCallback] = []
        self._tick_callbacks: list[TickCallback] = []
        self._candle_counts: dict[str, int] = {k: 0 for k in pairs}
        self._last_start_ts: dict[str, int] = {}
        self._ws_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._session: aiohttp.ClientSession | None = None
        self._orderbooks: dict[str, dict] = {
            k: {"bids": {}, "asks": {}} for k in pairs
        }

    def register_callback(self, cb: CandleCallback) -> None:
        self._callbacks.append(cb)

    def register_tick_callback(self, cb: TickCallback) -> None:
        """Register a callback that fires on every WS candle update (intra-bar)."""
        self._tick_callbacks.append(cb)

    def get_buffer(self, pair_key: str) -> list[Candle]:
        return list(self._buffers.get(pair_key, deque()))

    def get_live_candle(self, pair_key: str) -> Candle | None:
        return self._live_candles.get(pair_key)

    def candle_count(self, pair_key: str) -> int:
        return self._candle_counts.get(pair_key, 0)

    async def seed_from_rest(
        self,
        pair_key: str,
        product_id: str,
        interval: int,
        count: int = 100,
    ) -> int:
        try:
            from .bar_store import coinbase_rest_client_from_env
        except ImportError:
            LOG.error("CoinbaseCandleFeed: bar_store / coinbase client unavailable — cannot seed")
            return 0

        gran = _granularity_for_interval(interval)
        end = int(time.time())
        span = max(count * interval * 60, 3600)
        start = end - span

        def _pull() -> list[dict]:
            # Same as bar_store: CDE product candle REST often 403s without credentials.
            client = coinbase_rest_client_from_env()
            resp = client.get_public_candles(product_id, str(start), str(end), gran, limit=min(350, max(count, 10)))
            if isinstance(resp, dict):
                return resp.get("candles") or []
            if isinstance(resp, list):
                return resp
            # SDK response object
            raw = getattr(resp, "__dict__", {})
            return raw.get("candles") or getattr(resp, "candles", []) or []

        try:
            rows = await asyncio.to_thread(_pull)
        except Exception:
            LOG.warning("CoinbaseCandleFeed: REST seed failed for %s", product_id, exc_info=True)
            return 0

        buf = self._buffers[pair_key]
        loaded = 0
        for c in rows[-count:]:
            try:
                _g = c.get if isinstance(c, dict) else lambda k, d=None: getattr(c, k, d)
                ts = float(_g("start", 0) or 0.0)
                if ts > 1e12:
                    ts = ts / 1000.0
                candle = Candle(
                    timestamp=float(int(ts)),
                    open=float(_g("open", 0)),
                    high=float(_g("high", 0)),
                    low=float(_g("low", 0)),
                    close=float(_g("close", 0)),
                    volume=float(_g("volume", 0)),
                    vwap=float(_g("close", 0)),
                    trades=int(_g("trade_count", 0) or 0),
                )
                buf.append(candle)
                loaded += 1
            except (TypeError, ValueError, KeyError):
                continue

        # Fall back to bar_store if REST returned nothing
        if loaded == 0:
            try:
                from . import bar_store as _bs
                stored = await asyncio.to_thread(_bs.load_bars, product_id, interval, 14.0)
                if stored is not None:
                    ts_arr = stored["timestamp"]
                    n = len(ts_arr)
                    start_idx = max(0, n - count)
                    for i in range(start_idx, n):
                        candle = Candle(
                            timestamp=float(ts_arr[i]),
                            open=float(stored["open"][i]),
                            high=float(stored["high"][i]),
                            low=float(stored["low"][i]),
                            close=float(stored["close"][i]),
                            volume=float(stored["volume"][i]),
                            vwap=float(stored["vwap"][i]),
                            trades=int(stored["trades"][i]),
                        )
                        buf.append(candle)
                        loaded += 1
                    if loaded:
                        LOG.info("CoinbaseCandleFeed: seeded %d candles for %s from bar_store", loaded, product_id)
            except Exception:
                LOG.debug("CoinbaseCandleFeed: bar_store fallback seed failed for %s", product_id, exc_info=True)

        self._candle_counts[pair_key] = loaded
        LOG.info("CoinbaseCandleFeed: seeded %d candles for %s", loaded, product_id)
        return loaded

    async def _ws_loop(self) -> None:
        assert self._session is not None
        while not self._stop.is_set():
            try:
                async with self._session.ws_connect(COINBASE_WS_URL, heartbeat=20.0) as ws:
                    # Subscribe each product with its granularity
                    seen: set[tuple[str, str]] = set()
                    for pair_key, product_id in self._pairs.items():
                        interval = self._intervals.get(pair_key, 5)
                        gran = _granularity_for_interval(interval)
                        key = (product_id, gran)
                        if key in seen:
                            continue
                        seen.add(key)
                        msg = {
                            "type": "subscribe",
                            "product_ids": [product_id],
                            "channel": "candles",
                            "granularity": gran,
                        }
                        await ws.send_str(json.dumps(msg))
                        LOG.info("CoinbaseCandleFeed: subscribed candles %s %s", product_id, gran)
                        await asyncio.sleep(0.2)

                    # Subscribe to ticker for real-time price updates on live candle
                    all_products = list({pid for pid in self._pairs.values()})
                    if all_products:
                        await ws.send_str(json.dumps({
                            "type": "subscribe",
                            "product_ids": all_products,
                            "channel": "ticker",
                        }))
                        LOG.info("CoinbaseCandleFeed: subscribed ticker %s", all_products)
                        await asyncio.sleep(0.2)
                        await ws.send_str(json.dumps({
                            "type": "subscribe",
                            "product_ids": all_products,
                            "channel": "level2",
                        }))
                        LOG.info("CoinbaseCandleFeed: subscribed level2 %s", all_products)

                    async for msg in ws:
                        if self._stop.is_set():
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_ws_text(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                break
            except Exception:
                LOG.warning("CoinbaseCandleFeed: WS disconnected — reconnecting", exc_info=True)
                await asyncio.sleep(3.0)
                # NM-010: backfill bars missed during the disconnect via REST before resuming WS
                for pk, pid in self._pairs.items():
                    interval = self._intervals.get(pk, 5)
                    try:
                        loaded = await self.seed_from_rest(pk, pid, interval, count=10)
                        if loaded:
                            LOG.info(
                                "CoinbaseCandleFeed: reconnect backfill — loaded %d bars for %s",
                                loaded, pid,
                            )
                    except Exception:
                        LOG.debug(
                            "CoinbaseCandleFeed: reconnect backfill failed for %s", pid, exc_info=True
                        )

    async def _handle_ws_text(self, data: str) -> None:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return

        ch = payload.get("channel")
        if ch == "ticker":
            self._handle_ticker(payload)
            return
        if ch == "l2_data":
            self._handle_level2(payload)
            return
        if ch != "candles":
            return

        events = payload.get("events") or []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            candles = ev.get("candles") or []
            for c in candles:
                if not isinstance(c, dict):
                    continue
                product_id = c.get("product_id") or c.get("productId") or ""
                pair_key = self._product_to_key.get(product_id)
                if not pair_key:
                    continue
                try:
                    start_s = c.get("start") or c.get("start_time") or c.get("time")
                    ts = float(start_s) if start_s is not None else 0.0
                    if ts > 1e12:
                        ts = ts / 1000.0
                    candle = Candle(
                        timestamp=ts,
                        open=float(c.get("open", 0)),
                        high=float(c.get("high", 0)),
                        low=float(c.get("low", 0)),
                        close=float(c.get("close", 0)),
                        volume=float(c.get("volume", 0)),
                        vwap=float(c.get("close", 0)),
                        trades=int(c.get("trade_count", 0) or 0),
                    )
                except (TypeError, ValueError):
                    continue

                prev_ts = self._last_start_ts.get(pair_key)
                cur_ts = int(candle.timestamp)

                if prev_ts is not None and cur_ts != prev_ts and pair_key in self._live_candles:
                    closed = self._live_candles[pair_key]
                    self._buffers[pair_key].append(closed)
                    self._candle_counts[pair_key] += 1
                    LOG.info(
                        "CoinbaseCandleFeed %s: CLOSED @ %.6f vol=%.4f (n=%d)",
                        pair_key, closed.close, closed.volume, self._candle_counts[pair_key],
                    )
                    for cb in self._callbacks:
                        try:
                            cb(pair_key, closed)
                        except Exception:
                            LOG.exception("CoinbaseCandleFeed callback error for %s", pair_key)

                self._last_start_ts[pair_key] = cur_ts
                self._live_candles[pair_key] = candle

                for tcb in self._tick_callbacks:
                    try:
                        tcb(pair_key, candle)
                    except Exception:
                        LOG.exception("CoinbaseCandleFeed tick callback error for %s", pair_key)

    def get_orderbook(self, pair_key: str, depth: int = 15) -> dict:
        """Return top N bids/asks sorted by price for the given pair."""
        book = self._orderbooks.get(pair_key, {"bids": {}, "asks": {}})
        bids_sorted = sorted(book["bids"].items(), key=lambda x: float(x[0]), reverse=True)[:depth]
        asks_sorted = sorted(book["asks"].items(), key=lambda x: float(x[0]))[:depth]
        return {
            "bids": [[float(p), float(q)] for p, q in bids_sorted],
            "asks": [[float(p), float(q)] for p, q in asks_sorted],
        }

    def _handle_level2(self, payload: dict) -> None:
        """Process level2 (order book) updates from Coinbase WS."""
        events = payload.get("events") or []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            product_id = ev.get("product_id", "")
            pair_key = self._product_to_key.get(product_id)
            if not pair_key:
                continue
            book = self._orderbooks.get(pair_key)
            if book is None:
                continue
            ev_type = ev.get("type", "")
            updates = ev.get("updates") or []
            if ev_type == "snapshot":
                book["bids"].clear()
                book["asks"].clear()
            for u in updates:
                side = u.get("side", "").lower()
                price = u.get("price_level") or u.get("price", "0")
                qty = float(u.get("new_quantity", 0))
                target = book["bids"] if side == "bid" else book["asks"]
                if qty <= 0:
                    target.pop(price, None)
                else:
                    target[price] = qty

    def _handle_ticker(self, payload: dict) -> None:
        """Update live candle with real-time trade price from ticker channel."""
        events = payload.get("events") or []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            tickers = ev.get("tickers") or []
            for t in tickers:
                product_id = t.get("product_id", "")
                pair_key = self._product_to_key.get(product_id)
                if not pair_key:
                    continue
                try:
                    price = float(t.get("price", 0))
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                live = self._live_candles.get(pair_key)
                if live is None:
                    continue
                changed = False
                if price > live.high:
                    live.high = price
                    changed = True
                if price < live.low:
                    live.low = price
                    changed = True
                if price != live.close:
                    live.close = price
                    changed = True
                if changed:
                    for tcb in self._tick_callbacks:
                        try:
                            tcb(pair_key, live)
                        except Exception:
                            LOG.exception("CoinbaseCandleFeed tick callback error (ticker) for %s", pair_key)

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        self._stop.clear()
        self._ws_task = asyncio.create_task(self._ws_loop(), name="coinbase_candle_ws")

    async def close(self) -> None:
        self._stop.set()
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
        if self._session:
            await self._session.close()
            self._session = None


async def start_coinbase_candle_feed(
    pairs: dict[str, str],
    intervals: dict[str, int],
    rest_seed_count: int = 100,
    buffer_size: int = 500,
) -> CoinbaseCandleFeed:
    feed = CoinbaseCandleFeed(pairs=pairs, intervals=intervals, buffer_size=buffer_size)
    for pair_key, product_id in pairs.items():
        interval = intervals.get(pair_key, 5)
        await feed.seed_from_rest(pair_key, product_id, interval, rest_seed_count)
        await asyncio.sleep(0.15)
    await feed.start()
    return feed
