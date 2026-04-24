from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass

from aiohttp import ClientSession, ClientTimeout

from .config import BotConfig


@dataclass
class MarketSnapshot:
    symbol: str
    best_bid: float
    best_ask: float
    mid: float
    ts: float
    market_id: str = ""
    yes_token_id: str = ""
    no_token_id: str = ""
    end_ts: float = 0.0
    binance_price: float = 0.0
    chainlink_price: float = 0.0


class FeedAdapter:
    """Stub feed adapter.

    This is intentionally synthetic for the scaffold phase and is replaced with
    Gamma/CLOB/RTDS ingestion in the next implementation slice.
    """

    def __init__(self, symbol: str = "BTC-15M-UPDOWN") -> None:
        self._symbol = symbol
        self._mid = 0.50

    async def next_snapshot(self) -> MarketSnapshot:
        drift = random.uniform(-0.01, 0.01)
        self._mid = min(0.95, max(0.05, self._mid + drift))
        half_spread = max(0.001, random.uniform(0.002, 0.008))
        best_bid = max(0.01, self._mid - half_spread)
        best_ask = min(0.99, self._mid + half_spread)
        return MarketSnapshot(
            symbol=self._symbol,
            best_bid=best_bid,
            best_ask=best_ask,
            mid=(best_bid + best_ask) / 2.0,
            ts=time.time(),
        )


class PublicPolymarketFeedAdapter:
    """Read-only market feed via Gamma API.

    This adapter uses public endpoints and does not require API credentials.
    """

    def __init__(self, cfg: BotConfig) -> None:
        self._cfg = cfg
        self._base = cfg.gamma_api_url.rstrip("/")
        self._fallback = FeedAdapter()
        self._session: ClientSession | None = None

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=5))
        return self._session

    def _extract_symbol_score(self, question: str) -> int:
        q = question.upper()
        score = 0
        for sym in self._cfg.preferred_symbols:
            if sym in q:
                score += 2
        if "5" in q and "MIN" in q:
            score += 2
        if "15" in q and "MIN" in q:
            score += 2
        return score

    def _parse_price_fields(self, market: dict) -> tuple[float, float, float] | None:
        # Gamma fields are often strings containing JSON arrays.
        raw = market.get("outcomePrices")
        if raw is None:
            return None
        try:
            if isinstance(raw, str):
                arr = json.loads(raw)
            elif isinstance(raw, list):
                arr = raw
            else:
                return None
            yes = float(arr[0])
            no = float(arr[1]) if len(arr) > 1 else max(0.0, 1.0 - yes)
        except Exception:
            return None
        mid = (yes + (1.0 - no)) / 2.0
        # synthetic bid/ask proxy around mid when orderbook prices are not directly returned
        best_bid = max(0.01, mid - 0.01)
        best_ask = min(0.99, mid + 0.01)
        return best_bid, best_ask, mid

    def _parse_token_ids(self, market: dict) -> tuple[str, str]:
        raw = market.get("clobTokenIds")
        if raw is None:
            return "", ""
        try:
            if isinstance(raw, str):
                arr = json.loads(raw)
            elif isinstance(raw, list):
                arr = raw
            else:
                return "", ""
            yes = str(arr[0]) if len(arr) > 0 else ""
            no = str(arr[1]) if len(arr) > 1 else ""
            return yes, no
        except Exception:
            return "", ""

    async def next_snapshot(self) -> MarketSnapshot:
        try:
            sess = await self._get_session()
            params = {"limit": "50", "active": "true", "closed": "false"}
            async with sess.get(f"{self._base}/markets", params=params) as resp:
                if resp.status != 200:
                    return await self._fallback.next_snapshot()
                markets = await resp.json()
            if not isinstance(markets, list) or not markets:
                return await self._fallback.next_snapshot()

            best = None
            best_score = -1
            for m in markets:
                q = str(m.get("question") or "")
                score = self._extract_symbol_score(q)
                if self._cfg.market_query:
                    needle = self._cfg.market_query.upper()
                    if needle and needle not in q.upper():
                        score -= 1
                if score > best_score:
                    best = m
                    best_score = score
            if not isinstance(best, dict):
                return await self._fallback.next_snapshot()

            parsed = self._parse_price_fields(best)
            if parsed is None:
                return await self._fallback.next_snapshot()
            best_bid, best_ask, mid = parsed
            symbol = str(best.get("question") or "POLYMARKET")
            yes_token_id, no_token_id = self._parse_token_ids(best)
            return MarketSnapshot(
                symbol=symbol,
                best_bid=best_bid,
                best_ask=best_ask,
                mid=mid,
                ts=time.time(),
                market_id=str(best.get("id") or ""),
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
            )
        except Exception:
            return await self._fallback.next_snapshot()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

