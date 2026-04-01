"""Inventory manager — tracks base/quote holdings per pair, enforces limits."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kraken.spot import User

if TYPE_CHECKING:
    from .config import AppConfig
    from .state import BotState

LOG = logging.getLogger(__name__)

SYMBOL_BASE_MAP = {
    "TEL/USD": ("TEL", "ZUSD"),   # Kraken: TEL base, USD quote (ZUSD internal name)
    "XRP/USDT": ("XXRP", "USDT"),
    "XRP/USD": ("XXRP", "ZUSD"),
    "SOL/USD": ("SOL", "ZUSD"),
    "XBT/USDT": ("XXBT", "USDT"),
    "ETH/USDT": ("XETH", "USDT"),
    "USDC/USDT": ("USDC", "USDT"),
    "USDG/USD": ("USDG", "ZUSD"),
    "USDG/USDT": ("USDG", "USDT"),
    "USDG/USDC": ("USDG", "USDC"),
    "USDE/USD": ("USDE", "ZUSD"),
    "USDE/USDT": ("USDE", "USDT"),
    "USDE/USDC": ("USDE", "USDC"),
}


class InventoryManager:
    def __init__(self, state: BotState, config: AppConfig) -> None:
        self._state = state
        self._config = config

    def can_buy(self, pair_key: str) -> bool:
        pc = self._config.pairs.get(pair_key)
        ps = self._state.pairs.get(pair_key)
        if pc is None or ps is None:
            return False
        if ps.inventory_base + pc.order_size > pc.max_inventory:
            return False
        # Must have enough quote to fund the purchase (with 5% buffer for fees)
        cost = pc.order_size * ps.best_ask if ps.best_ask > 0 else pc.order_size
        if ps.inventory_quote < cost * 1.05:
            return False
        return True

    def can_sell(self, pair_key: str) -> bool:
        pc = self._config.pairs.get(pair_key)
        ps = self._state.pairs.get(pair_key)
        if pc is None or ps is None:
            return False
        return ps.inventory_base >= pc.order_size

    def record_buy(self, pair_key: str, qty: float, price: float, fee: float) -> None:
        ps = self._state.pairs.get(pair_key)
        if ps is None:
            return
        cost = qty * price + fee
        ps.inventory_base += qty
        ps.inventory_quote -= cost
        ps.position_cost_quote += cost
        LOG.debug(
            "BUY %s: +%.6f base, -%.6f quote | inv=%.4f",
            pair_key, qty, cost, ps.inventory_base,
        )

    def record_sell(self, pair_key: str, qty: float, price: float, fee: float) -> float:
        """Apply sell; return net P&amp;L in quote (revenue minus allocated cost basis)."""
        ps = self._state.pairs.get(pair_key)
        if ps is None or ps.inventory_base <= 0:
            return 0.0
        # Allocate cost proportional to sold size
        share = qty / ps.inventory_base
        cost_alloc = ps.position_cost_quote * share
        ps.position_cost_quote -= cost_alloc
        ps.inventory_base -= qty
        revenue = qty * price - fee
        ps.inventory_quote += revenue
        pnl = revenue - cost_alloc
        LOG.debug(
            "SELL %s: -%.6f base, +%.6f quote | pnl=%.6f | inv=%.4f",
            pair_key, qty, revenue, pnl, ps.inventory_base,
        )
        return pnl

    def gross_spread_on_sell(
        self, pair_key: str, qty: float, price: float, fee: float,
    ) -> float:
        """Economic edge before fees: (exit - avg entry) * qty."""
        ps = self._state.pairs.get(pair_key)
        if ps is None or ps.inventory_base <= 0:
            return 0.0
        share = qty / ps.inventory_base
        cost_alloc = ps.position_cost_quote * share
        avg_entry = cost_alloc / qty if qty else 0.0
        return max(0.0, (price - avg_entry) * qty)

    def set_initial(
        self, pair_key: str, base: float, quote: float,
    ) -> None:
        ps = self._state.pairs.get(pair_key)
        if ps is None:
            return
        ps.inventory_base = base
        ps.inventory_quote = quote
        if base <= 0:
            ps.position_cost_quote = 0.0

    def seed_cost_basis_from_mid(self, pair_key: str) -> None:
        """Best-effort cost basis for existing live holdings after balance sync."""
        ps = self._state.pairs.get(pair_key)
        if ps is None:
            return
        if ps.inventory_base <= 0:
            ps.position_cost_quote = 0.0
            return
        mid = ps.mid_price
        if mid <= 0:
            return
        ps.position_cost_quote = ps.inventory_base * mid

    def sync_from_kraken(self) -> None:
        """Fetch real balances from Kraken and update inventory for all pairs."""
        if not self._config.api_key or not self._config.api_secret:
            LOG.warning("Cannot sync balances: no API keys")
            return

        try:
            user = User(key=self._config.api_key, secret=self._config.api_secret)
            balances = user.get_account_balance()
        except Exception:
            LOG.exception("Failed to fetch Kraken balances")
            return

        LOG.info("Synced Kraken balances: %s", {k: v for k, v in balances.items() if float(v) > 0})

        for pair_key, pc in self._config.pairs.items():
            mapping = SYMBOL_BASE_MAP.get(pc.symbol)
            if mapping is None:
                continue
            base_asset, quote_asset = mapping
            base_bal = float(balances.get(base_asset, 0))
            quote_bal = float(balances.get(quote_asset, 0))
            self.set_initial(pair_key, base_bal, quote_bal)
            self.seed_cost_basis_from_mid(pair_key)
