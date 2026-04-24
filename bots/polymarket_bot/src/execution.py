"""Execution gateways: paper simulation and live CLOB order placement.

Paper executor simulates fills based on quote competitiveness.
Live executor uses py-clob-client with two-step sign+post flow.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Protocol

from .config import BotConfig
from .strategy_maker import MakerQuote

LOG = logging.getLogger("polymarket_bot.execution")


@dataclass
class ExecutionResult:
    filled: bool
    side: str
    price: float
    size: float
    is_maker: bool
    market_id: str = ""
    token_id: str = ""
    order_id: str = ""


class ExecutionGateway(Protocol):
    def execute_maker_cycle(self, quote: MakerQuote) -> list[ExecutionResult]:
        ...

    def execute_taker(self, token_id: str, side: str, price: float, size: float) -> ExecutionResult | None:
        ...

    def to_trade_record(self, res: ExecutionResult, symbol: str) -> dict:
        ...


class PaperExecutor:
    """Realistic paper execution model.

    Simulates fills based on whether our quote is competitive with the
    observed spread. Fill probability is higher when our quote is at or
    inside the market spread.
    """

    def __init__(self, fill_rate_at_top: float = 0.35, fill_rate_wide: float = 0.08) -> None:
        self._fill_rate_at_top = fill_rate_at_top
        self._fill_rate_wide = fill_rate_wide

    def execute_maker_cycle(self, quote: MakerQuote) -> list[ExecutionResult]:
        out: list[ExecutionResult] = []

        if not quote.suppress_buy:
            fill_prob = self._fill_rate_at_top if quote.edge_bps < 200 else self._fill_rate_wide
            if random.random() < fill_prob:
                out.append(ExecutionResult(
                    filled=True,
                    side="buy_yes",
                    price=quote.bid,
                    size=quote.size,
                    is_maker=True,
                    market_id=quote.market_id,
                    token_id=quote.yes_token_id,
                ))

        if not quote.suppress_sell:
            fill_prob = self._fill_rate_at_top if quote.edge_bps < 200 else self._fill_rate_wide
            if random.random() < fill_prob:
                out.append(ExecutionResult(
                    filled=True,
                    side="sell_yes",
                    price=quote.ask,
                    size=quote.size,
                    is_maker=True,
                    market_id=quote.market_id,
                    token_id=quote.yes_token_id,
                ))
        return out

    def execute_taker(self, token_id: str, side: str, price: float, size: float) -> ExecutionResult | None:
        if random.random() < 0.85:
            return ExecutionResult(
                filled=True,
                side=side,
                price=price,
                size=size,
                is_maker=False,
                token_id=token_id,
            )
        return None

    def to_trade_record(self, res: ExecutionResult, symbol: str) -> dict:
        return {
            "ts": time.time(),
            "symbol": symbol,
            "side": res.side,
            "price": res.price,
            "size": res.size,
            "pnl_delta": 0.0,
            "is_maker": res.is_maker,
            "market_id": res.market_id,
            "token_id": res.token_id,
            "order_id": res.order_id,
            "mode": "paper",
        }


class LiveClobExecutor:
    """Live execution gateway using py-clob-client.

    Supports three modes:
      - live_dry_run: signs orders but does not post (validates credentials)
      - live: signs and posts orders via CLOB API
      - disabled: credentials missing or client init failed
    """

    def __init__(self, cfg: BotConfig) -> None:
        self._cfg = cfg
        self._enabled = False
        self._error = ""
        self._client = None
        self._init_client()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def error(self) -> str:
        return self._error

    def _init_client(self) -> None:
        if not self._cfg.has_live_trading_credentials:
            self._error = "missing_live_credentials"
            return
        try:
            from py_clob_client.client import ClobClient  # type: ignore
        except Exception as exc:
            self._error = f"py_clob_client_import_failed: {exc}"
            return
        try:
            self._client = ClobClient(
                self._cfg.polymarket_clob_host,
                key=self._cfg.polymarket_private_key,
                chain_id=self._cfg.polymarket_chain_id,
                signature_type=self._cfg.polymarket_signature_type,
                funder=self._cfg.polymarket_funder,
            )
            self._client.set_api_creds(self._client.create_or_derive_api_creds())
            self._enabled = True
            LOG.info("LiveClobExecutor initialized (dry_run=%s)", self._cfg.live_dry_run)
        except Exception as exc:
            self._error = f"clob_client_init_failed: {exc}"
            self._enabled = False

    def _place_limit_order(
        self, token_id: str, price: float, size: float, side: str, post_only: bool = True
    ) -> ExecutionResult | None:
        if not self._enabled or not self._client:
            return None
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore
            from py_clob_client.order_builder.constants import BUY, SELL  # type: ignore

            clob_side = BUY if "buy" in side.lower() else SELL
            args = OrderArgs(token_id=token_id, price=price, size=size, side=clob_side)
            signed = self._client.create_order(args)

            if self._cfg.live_dry_run:
                LOG.info("DRY_RUN signed order: %s %s @ %.4f x %.0f", side, token_id[:12], price, size)
                return ExecutionResult(
                    filled=True, side=f"dryrun_{side}", price=price, size=size,
                    is_maker=post_only, market_id="", token_id=token_id,
                )

            resp = self._client.post_order(signed, OrderType.GTC, post_only=post_only)
            order_id = resp.get("orderID", "")
            LOG.info("LIVE posted order %s: %s @ %.4f x %.0f", order_id, side, price, size)
            return ExecutionResult(
                filled=True, side=side, price=price, size=size,
                is_maker=post_only, token_id=token_id, order_id=order_id,
            )
        except Exception as exc:
            LOG.error("Order placement failed: %s", exc)
            return None

    def execute_maker_cycle(self, quote: MakerQuote) -> list[ExecutionResult]:
        out: list[ExecutionResult] = []
        if not quote.suppress_buy and quote.yes_token_id:
            res = self._place_limit_order(
                quote.yes_token_id, quote.bid, quote.size, "buy_yes", post_only=True,
            )
            if res:
                res.market_id = quote.market_id
                out.append(res)
        if not quote.suppress_sell and quote.yes_token_id:
            res = self._place_limit_order(
                quote.yes_token_id, quote.ask, quote.size, "sell_yes", post_only=True,
            )
            if res:
                res.market_id = quote.market_id
                out.append(res)
        return out

    def execute_taker(self, token_id: str, side: str, price: float, size: float) -> ExecutionResult | None:
        return self._place_limit_order(token_id, price, size, side, post_only=False)

    def to_trade_record(self, res: ExecutionResult, symbol: str) -> dict:
        mode = "live"
        if self._cfg.live_dry_run:
            mode = "live_dry_run"
        return {
            "ts": time.time(),
            "symbol": symbol,
            "side": res.side,
            "price": res.price,
            "size": res.size,
            "pnl_delta": 0.0,
            "is_maker": res.is_maker,
            "market_id": res.market_id,
            "token_id": res.token_id,
            "order_id": res.order_id,
            "mode": mode,
        }
