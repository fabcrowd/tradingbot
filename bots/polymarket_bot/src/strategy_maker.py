from __future__ import annotations

from dataclasses import dataclass

from .features import Features


@dataclass
class MakerQuote:
    symbol: str
    market_id: str
    yes_token_id: str
    no_token_id: str
    bid: float
    ask: float
    size: float
    edge_bps: float
    post_only: bool = True
    suppress_buy: bool = False
    suppress_sell: bool = False


class MakerStrategy:
    """Inventory-aware maker quoting with Avellaneda-Stoikov style skew.

    Places post_only limit orders on both sides of the market.
    Skews quotes based on current inventory to encourage rebalancing.
    """

    def __init__(
        self,
        quote_size: float = 10.0,
        min_half_spread_bps: float = 8.0,
        skew_scale: float = 0.4,
        max_position_shares: float = 100.0,
    ) -> None:
        self._quote_size = quote_size
        self._min_half_spread_bps = min_half_spread_bps
        self._skew_scale = skew_scale
        self._max_position_shares = max_position_shares

    def quote(
        self,
        feat: Features,
        inventory_ratio: float = 0.0,
        at_max_long: bool = False,
        at_max_short: bool = False,
    ) -> MakerQuote:
        half_min = self._min_half_spread_bps / 10000.0 * feat.mid
        half_market = feat.spread / 2.0
        half = max(half_min, half_market, 0.005)

        # Avellaneda-Stoikov reservation price shift
        # inventory_ratio > 0 means long YES -> lower reservation to encourage sells
        skew = self._skew_scale * inventory_ratio * half
        reservation = feat.mid - skew

        bid = max(0.01, reservation - half)
        ask = min(0.99, reservation + half)

        if bid >= ask:
            mid = (bid + ask) / 2.0
            bid = max(0.01, mid - 0.005)
            ask = min(0.99, mid + 0.005)

        edge_bps = (ask - bid) / max(feat.mid, 1e-9) * 10000.0

        return MakerQuote(
            symbol=feat.symbol,
            market_id=feat.market_id,
            yes_token_id=feat.yes_token_id,
            no_token_id=feat.no_token_id,
            bid=round(bid, 4),
            ask=round(ask, 4),
            size=self._quote_size,
            edge_bps=edge_bps,
            post_only=True,
            suppress_buy=at_max_long,
            suppress_sell=at_max_short,
        )
