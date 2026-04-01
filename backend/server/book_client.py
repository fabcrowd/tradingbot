"""Order book client — subscribes to Kraken WS v2 and maintains live bid/ask."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kraken.spot import SpotOrderBookClient

from .state import BotState, OrderBookLevel
from .threat_detector import ThreatDetector

if TYPE_CHECKING:
    from .config import AppConfig

LOG = logging.getLogger(__name__)


class OrderBook(SpotOrderBookClient):
    """Wraps SpotOrderBookClient and pushes updates into BotState."""

    def __init__(
        self,
        state: BotState,
        config: AppConfig,
        depth: int = 10,
        threat_detector: ThreatDetector | None = None,
    ) -> None:
        super().__init__(depth=depth)
        self._state = state
        self._config = config
        self._threat_detector = threat_detector
        self._symbol_to_key: dict[str, str] = {}
        for key, pc in config.pairs.items():
            self._symbol_to_key[pc.symbol] = key

    async def on_book_update(self, pair: str, message: list) -> None:
        book: dict[str, Any] = self.get(pair=pair)
        if not book.get("valid", False):
            LOG.warning("Invalid checksum for %s, waiting for resync", pair)
            return

        key = self._symbol_to_key.get(pair)
        if key is None:
            return

        ps = self._state.pairs.get(key)
        if ps is None:
            return

        bids = list(book["bid"].items())
        asks = list(book["ask"].items())

        if bids:
            ps.best_bid = float(bids[0][0])
            ps.bid_levels = [
                OrderBookLevel(price=float(p), volume=float(v[0]))
                for p, v in bids[: self.depth]
            ]
        if asks:
            ps.best_ask = float(asks[0][0])
            ps.ask_levels = [
                OrderBookLevel(price=float(p), volume=float(v[0]))
                for p, v in asks[: self.depth]
            ]

        if self._threat_detector is not None:
            self._threat_detector.update(key, ps)


async def start_book_client(
    state: BotState,
    config: AppConfig,
    threat_detector: ThreatDetector | None = None,
) -> OrderBook:
    """Create and start the order book client, subscribing to all configured pairs."""
    ob = OrderBook(state=state, config=config, depth=25, threat_detector=threat_detector)
    symbols = config.symbols()
    LOG.info("Starting order book client for %s", symbols)

    await ob.start()
    await ob.add_book(pairs=symbols)
    return ob
