"""Real-time WebSocket feeds for Polymarket.

Three concurrent connections:
  1. RTDS  — Binance + Chainlink crypto prices (external signal source)
  2. Market WS — best bid/ask and trade data per token
  3. Gamma REST (on timer) — market discovery + resolution polling
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientSession, ClientTimeout, WSMsgType

from .config import BotConfig
from .feeds import MarketSnapshot

LOG = logging.getLogger("polymarket_bot.ws_feeds")

RTDS_URL = "wss://ws-live-data.polymarket.com"
MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"


@dataclass
class PricePoint:
    value: float
    ts: float


@dataclass
class ActiveMarket:
    market_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    end_date_iso: str
    end_ts: float
    category: str


@dataclass
class BookState:
    best_bid: float = 0.0
    best_ask: float = 0.0
    spread: float = 0.0
    last_trade_price: float = 0.0
    last_update_ts: float = 0.0


class RtdsBuffer:
    """Rolling price buffer for RTDS price streams."""

    def __init__(self, max_len: int = 120) -> None:
        self._data: dict[str, deque[PricePoint]] = {}
        self._max_len = max_len

    def push(self, symbol: str, value: float, ts: float) -> None:
        if symbol not in self._data:
            self._data[symbol] = deque(maxlen=self._max_len)
        self._data[symbol].append(PricePoint(value, ts))

    def latest(self, symbol: str) -> PricePoint | None:
        buf = self._data.get(symbol)
        if not buf:
            return None
        return buf[-1]

    def momentum_pct(self, symbol: str, lookback_sec: float = 60.0) -> float:
        """Return percentage price change over the last lookback_sec seconds."""
        buf = self._data.get(symbol)
        if not buf or len(buf) < 2:
            return 0.0
        now = buf[-1].ts
        cutoff = now - lookback_sec
        oldest = buf[-1]
        for p in buf:
            if p.ts >= cutoff:
                oldest = p
                break
        if oldest.value <= 0:
            return 0.0
        return (buf[-1].value - oldest.value) / oldest.value

    @property
    def symbols(self) -> list[str]:
        return list(self._data.keys())


class LiveFeedManager:
    """Manages all three Polymarket data connections."""

    def __init__(self, cfg: BotConfig) -> None:
        self._cfg = cfg
        self._session: ClientSession | None = None
        self._rtds_buf = RtdsBuffer()
        self._books: dict[str, BookState] = {}
        self._markets: dict[str, ActiveMarket] = {}
        self._current_market: ActiveMarket | None = None
        self._rtds_task: asyncio.Task | None = None
        self._market_ws_task: asyncio.Task | None = None
        self._discovery_task: asyncio.Task | None = None
        self._rtds_last_ts: float = 0.0
        self._market_ws_last_ts: float = 0.0

    @property
    def rtds(self) -> RtdsBuffer:
        return self._rtds_buf

    @property
    def current_market(self) -> ActiveMarket | None:
        return self._current_market

    @property
    def markets(self) -> dict[str, ActiveMarket]:
        return self._markets

    @property
    def books(self) -> dict[str, BookState]:
        return self._books

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=10))
        return self._session

    # ── RTDS connection ─────────────────────────────────────────────

    async def _rtds_loop(self) -> None:
        while True:
            try:
                await self._rtds_connect()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                LOG.warning("RTDS connection lost: %s — reconnecting in 3s", exc)
                await asyncio.sleep(3)

    async def _rtds_connect(self) -> None:
        sess = await self._get_session()
        async with sess.ws_connect(RTDS_URL, heartbeat=5) as ws:
            LOG.info("RTDS connected")
            await ws.send_json({
                "action": "subscribe",
                "subscriptions": [
                    {"topic": "crypto_prices", "type": "*"},
                    {"topic": "crypto_prices_chainlink", "type": "*"},
                ],
            })
            async for msg in ws:
                if msg.type in (WSMsgType.TEXT,):
                    self._handle_rtds_message(msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSED):
                    break

    def _handle_rtds_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        topic = data.get("topic", "")
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return

        if topic == "crypto_prices":
            sym = str(payload.get("symbol", "")).lower()
            val = float(payload.get("value", 0))
            ts = float(payload.get("timestamp", time.time() * 1000)) / 1000.0
            if val > 0:
                self._rtds_buf.push(f"binance_{sym}", val, ts)
                self._rtds_last_ts = time.time()
        elif topic == "crypto_prices_chainlink":
            sym = str(payload.get("symbol", "")).lower().replace("/", "_")
            val = float(payload.get("value", 0))
            ts = float(payload.get("timestamp", time.time() * 1000)) / 1000.0
            if val > 0:
                self._rtds_buf.push(f"chainlink_{sym}", val, ts)
                self._rtds_last_ts = time.time()

    # ── Market WS connection ────────────────────────────────────────

    async def _market_ws_loop(self) -> None:
        while True:
            try:
                await self._market_ws_connect()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                LOG.warning("Market WS lost: %s — reconnecting in 3s", exc)
                await asyncio.sleep(3)

    async def _market_ws_connect(self) -> None:
        sess = await self._get_session()
        token_ids = self._collect_token_ids()
        if not token_ids:
            LOG.debug("No token IDs for Market WS — waiting for discovery")
            await asyncio.sleep(5)
            return
        async with sess.ws_connect(MARKET_WS_URL, heartbeat=10) as ws:
            LOG.info("Market WS connected with %d tokens", len(token_ids))
            await ws.send_json({
                "assets_ids": token_ids,
                "type": "market",
                "custom_feature_enabled": True,
            })
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    self._handle_market_message(msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSED):
                    break

    def _collect_token_ids(self) -> list[str]:
        ids: list[str] = []
        for m in self._markets.values():
            if m.yes_token_id:
                ids.append(m.yes_token_id)
            if m.no_token_id:
                ids.append(m.no_token_id)
        return ids

    def _handle_market_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._process_market_event(item)
        elif isinstance(data, dict):
            self._process_market_event(data)

    def _process_market_event(self, data: dict) -> None:
        evt = data.get("event_type", "")
        asset_id = str(data.get("asset_id", ""))
        if not asset_id:
            return

        if evt == "best_bid_ask":
            book = self._books.setdefault(asset_id, BookState())
            book.best_bid = float(data.get("best_bid", 0))
            book.best_ask = float(data.get("best_ask", 0))
            book.spread = float(data.get("spread", 0))
            book.last_update_ts = time.time()
            self._market_ws_last_ts = time.time()
        elif evt == "last_trade_price":
            book = self._books.setdefault(asset_id, BookState())
            book.last_trade_price = float(data.get("price", 0))
            book.last_update_ts = time.time()
            self._market_ws_last_ts = time.time()
        elif evt == "book":
            asset_id = str(data.get("asset_id", ""))
            if not asset_id:
                return
            book = self._books.setdefault(asset_id, BookState())
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if bids and isinstance(bids[0], dict):
                book.best_bid = float(bids[0].get("price", 0))
            if asks and isinstance(asks[0], dict):
                book.best_ask = float(asks[0].get("price", 0))
            if book.best_bid > 0 and book.best_ask > 0:
                book.spread = book.best_ask - book.best_bid
            book.last_update_ts = time.time()
            self._market_ws_last_ts = time.time()

    # ── Market discovery via Gamma REST ─────────────────────────────

    async def _discovery_loop(self) -> None:
        while True:
            try:
                await self._discover_markets()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                LOG.warning("Discovery error: %s", exc)
            await asyncio.sleep(60)

    async def _discover_markets(self) -> None:
        sess = await self._get_session()
        base = self._cfg.gamma_api_url.rstrip("/")

        # Discover crypto markets (primary for taker strategy)
        await self._discover_crypto_markets(sess, base)

        # Discover all-category markets for maker strategy
        if self._cfg.maker_scan_all_categories:
            await self._discover_wide_spread_markets(sess, base)

    async def _discover_crypto_markets(self, sess: ClientSession, base: str) -> None:
        params: dict[str, str] = {
            "limit": "100", "active": "true", "closed": "false",
            "order": "startDate", "ascending": "false",
        }
        try:
            async with sess.get(f"{base}/markets", params=params) as resp:
                if resp.status != 200:
                    LOG.warning("Gamma discovery HTTP %s", resp.status)
                    return
                raw_markets = await resp.json()
        except Exception as exc:
            LOG.warning("Gamma discovery error: %s", exc)
            return

        if not isinstance(raw_markets, list):
            return

        best: dict | None = None
        best_score = -1
        for m in raw_markets:
            q = str(m.get("question") or "").upper()
            score = 0

            has_symbol = False
            for sym in self._cfg.preferred_symbols:
                word_boundary = f" {sym} " in f" {q} " or q.startswith(f"{sym} ") or q.endswith(f" {sym}")
                if sym == "BTC" and "BITCOIN" in q:
                    word_boundary = True
                if sym == "ETH" and "ETHEREUM" in q:
                    word_boundary = True
                if word_boundary:
                    score += 5
                    has_symbol = True
                    if sym in ("BTC", "BITCOIN"):
                        score += 2

            is_updown = "UP OR DOWN" in q
            is_above = "ABOVE" in q
            if is_updown:
                score += 5
            if is_above:
                score += 3
            if "5" in q and "MIN" in q:
                score += 3
            if "15" in q and "MIN" in q:
                score += 3
            if "HOUR" in q:
                score += 2
            if m.get("active") and not m.get("closed"):
                score += 1

            if not has_symbol:
                continue
            if not (is_updown or is_above):
                continue

            token_ids = self._parse_json_field(m.get("clobTokenIds"))
            if not token_ids or len(token_ids) < 2:
                continue

            end_iso = str(m.get("endDate") or "")
            if end_iso:
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
                    hours_until_end = (dt.timestamp() - time.time()) / 3600.0
                    if 0 < hours_until_end <= 2:
                        score += 8
                    elif 0 < hours_until_end <= 6:
                        score += 4
                    elif hours_until_end <= 0:
                        score -= 5
                except Exception:
                    pass

            if score > best_score:
                best = m
                best_score = score

        if best is None:
            return

        am = self._market_dict_to_active(best)
        prev = self._current_market
        self._current_market = am
        self._markets[am.market_id] = am

        if prev is None or prev.market_id != am.market_id:
            LOG.info("Active crypto market: %s [%s]", am.question[:80], am.market_id[:16])

    async def _discover_wide_spread_markets(self, sess: ClientSession, base: str) -> None:
        """Discover markets across all categories with wide spreads for maker strategy."""
        params: dict[str, str] = {
            "limit": "200", "active": "true", "closed": "false",
            "order": "volume24hr", "ascending": "false",
        }
        try:
            async with sess.get(f"{base}/markets", params=params) as resp:
                if resp.status != 200:
                    return
                raw_markets = await resp.json()
        except Exception:
            return

        if not isinstance(raw_markets, list):
            return

        added = 0
        for m in raw_markets:
            mid = str(m.get("id", ""))
            if not mid or mid in self._markets:
                continue

            token_ids = self._parse_json_field(m.get("clobTokenIds"))
            if not token_ids or len(token_ids) < 2:
                continue

            if m.get("closed"):
                continue

            # Volume filter
            vol = float(m.get("volume24hr", 0) or 0)
            min_vol = self._cfg.maker_min_volume_usd
            if vol < min_vol:
                continue

            # Check spread via outcome prices
            raw_prices = self._parse_json_field(m.get("outcomePrices"))
            if raw_prices and len(raw_prices) >= 2:
                try:
                    yes_p = float(raw_prices[0])
                    no_p = float(raw_prices[1])
                    spread_pct = abs(1.0 - yes_p - no_p) * 100
                    if spread_pct < self._cfg.maker_min_spread_pct:
                        continue
                except (ValueError, IndexError):
                    continue

            am = self._market_dict_to_active(m)
            self._markets[am.market_id] = am
            added += 1

            if added >= 10:
                break

        if added > 0:
            LOG.info("Discovered %d wide-spread markets for maker strategy", added)

    def _market_dict_to_active(self, m: dict) -> ActiveMarket:
        token_ids = self._parse_json_field(m.get("clobTokenIds"))
        mid = m.get("id", "")
        end_iso = str(m.get("endDate") or m.get("endDateIso") or "")
        end_ts = 0.0
        try:
            from datetime import datetime, timezone
            if end_iso:
                dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
                end_ts = dt.timestamp()
        except Exception:
            pass

        return ActiveMarket(
            market_id=str(mid),
            question=str(m.get("question") or ""),
            yes_token_id=str(token_ids[0]) if token_ids else "",
            no_token_id=str(token_ids[1]) if len(token_ids) > 1 else "",
            end_date_iso=end_iso,
            end_ts=end_ts,
            category=str(m.get("category") or ""),
        )

    def _parse_json_field(self, raw: Any) -> list[str]:
        if raw is None:
            return []
        try:
            if isinstance(raw, str):
                arr = json.loads(raw)
            elif isinstance(raw, list):
                arr = raw
            else:
                return []
            return [str(x) for x in arr]
        except Exception:
            return []

    # ── Snapshot assembly ───────────────────────────────────────────

    async def next_snapshot(self) -> MarketSnapshot:
        m = self._current_market
        if m is None:
            return _synthetic_snapshot()

        yes_book = self._books.get(m.yes_token_id, BookState())
        bb = yes_book.best_bid
        ba = yes_book.best_ask

        if bb <= 0 or ba <= 0:
            try:
                bb, ba = await self._fetch_rest_book(m.yes_token_id)
            except Exception:
                pass

        if bb <= 0 or ba <= 0:
            bb, ba = 0.49, 0.51

        mid = (bb + ba) / 2.0
        binance_price = 0.0
        chainlink_price = 0.0
        for sym in self._cfg.preferred_symbols:
            bp = self._rtds_buf.latest(f"binance_{sym.lower()}usdt")
            if bp:
                binance_price = bp.value
                break
        for sym in self._cfg.preferred_symbols:
            cp = self._rtds_buf.latest(f"chainlink_{sym.lower()}_usd")
            if cp:
                chainlink_price = cp.value
                break

        return MarketSnapshot(
            symbol=m.question,
            best_bid=bb,
            best_ask=ba,
            mid=mid,
            ts=time.time(),
            market_id=m.market_id,
            yes_token_id=m.yes_token_id,
            no_token_id=m.no_token_id,
            end_ts=m.end_ts,
            binance_price=binance_price,
            chainlink_price=chainlink_price,
        )

    async def _fetch_rest_book(self, token_id: str) -> tuple[float, float]:
        if not token_id:
            return 0.0, 0.0
        sess = await self._get_session()
        async with sess.get(CLOB_BOOK_URL, params={"token_id": token_id}) as resp:
            if resp.status != 200:
                return 0.0, 0.0
            data = await resp.json()
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        bb = float(bids[0]["price"]) if bids else 0.0
        ba = float(asks[0]["price"]) if asks else 0.0
        return bb, ba

    # ── Resolution check ────────────────────────────────────────────

    async def check_resolution(self, market_id: str) -> dict[str, Any] | None:
        """Poll Gamma for a market's resolution status. Returns dict with
        'resolved', 'winning_outcome' if resolved, else None."""
        if not market_id:
            return None
        sess = await self._get_session()
        base = self._cfg.gamma_api_url.rstrip("/")
        try:
            async with sess.get(f"{base}/markets/{market_id}") as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except Exception:
            return None
        if not data.get("resolved"):
            return None
        outcomes = self._parse_json_field(data.get("outcomePrices"))
        winning = "unknown"
        if outcomes:
            try:
                yes_price = float(outcomes[0])
                winning = "yes" if yes_price > 0.5 else "no"
            except (ValueError, IndexError):
                pass
        return {"resolved": True, "winning_outcome": winning, "market_id": market_id}

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        self._discovery_task = asyncio.create_task(self._discovery_loop())
        await asyncio.sleep(2)
        self._rtds_task = asyncio.create_task(self._rtds_loop())
        self._market_ws_task = asyncio.create_task(self._market_ws_loop())
        LOG.info("LiveFeedManager started")

    async def close(self) -> None:
        for task in (self._rtds_task, self._market_ws_task, self._discovery_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self._session and not self._session.closed:
            await self._session.close()

    def staleness_report(self) -> dict[str, float]:
        now = time.time()
        return {
            "rtds_age_sec": now - self._rtds_last_ts if self._rtds_last_ts else -1,
            "market_ws_age_sec": now - self._market_ws_last_ts if self._market_ws_last_ts else -1,
            "active_market": self._current_market.market_id if self._current_market else "",
            "tracked_tokens": len(self._books),
        }


def _synthetic_snapshot() -> MarketSnapshot:
    import random
    mid = 0.50 + random.uniform(-0.05, 0.05)
    hs = random.uniform(0.005, 0.02)
    return MarketSnapshot(
        symbol="AWAITING_DISCOVERY",
        best_bid=max(0.01, mid - hs),
        best_ask=min(0.99, mid + hs),
        mid=mid,
        ts=time.time(),
    )
