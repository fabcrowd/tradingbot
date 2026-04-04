"""Inventory manager — tracks base/quote holdings per pair, enforces limits."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from kraken.spot import User

if TYPE_CHECKING:
    from .config import AppConfig
    from .state import BotState

LOG = logging.getLogger(__name__)

COST_BASIS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "cost_basis.json"
BARRIERS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "fill_barriers.json"

SYMBOL_BASE_MAP = {
    "TEL/USD": ("TEL", "ZUSD"),
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
    "DRIFT/USD": ("DRIFT", "ZUSD"),
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
        if self._state.trailing_exit_active and self._state.trailing_exit_pair == pair_key:
            LOG.debug("can_buy %s: suppressed — trailing exit active", pair_key)
            return False
        if self._state.insufficient_funds_until > time.time():
            LOG.debug("can_buy %s: suppressed — insufficient funds cooldown", pair_key)
            return False
        if pc.max_inventory > 0 and ps.inventory_base + pc.order_size > pc.max_inventory:
            LOG.debug("can_buy %s: inventory cap (%.2f + %.2f > %.2f)",
                      pair_key, ps.inventory_base, pc.order_size, pc.max_inventory)
            return False
        cost = pc.order_size * ps.best_ask if ps.best_ask > 0 else pc.order_size
        required_quote = cost * 1.05
        assets = self._pair_assets(pair_key)
        if assets is None:
            if ps.inventory_quote < required_quote:
                return False
            return True
        _base_asset, quote_asset = assets
        wallet_quote = self._wallet_quote_balance(quote_asset)
        committed_quote = self._committed_quote_for_open_buys(quote_asset)
        available = wallet_quote - committed_quote
        if committed_quote + required_quote > wallet_quote:
            LOG.warning(
                "can_buy %s BLOCKED: wallet_%s=%.4f committed=%.4f available=%.4f required=%.4f",
                pair_key, quote_asset, wallet_quote, committed_quote, available, required_quote,
            )
            return False
        return True

    def affordable_buy_qty(self, pair_key: str, desired_qty: float) -> float:
        """Return the largest buy qty <= desired_qty that fits the wallet."""
        pc = self._config.pairs.get(pair_key)
        ps = self._state.pairs.get(pair_key)
        if pc is None or ps is None or desired_qty <= 0:
            return 0.0
        price = ps.best_ask if ps.best_ask > 0 else 1.0
        assets = self._pair_assets(pair_key)
        if assets is None:
            available = ps.inventory_quote
        else:
            quote_asset = assets[1]
            wallet_quote = self._wallet_quote_balance(quote_asset)
            committed = self._committed_quote_for_open_buys(quote_asset)
            available = wallet_quote - committed
        max_qty = available / (price * 1.05) if price > 0 else 0.0
        return min(desired_qty, max_qty)

    def can_sell(self, pair_key: str) -> bool:
        pc = self._config.pairs.get(pair_key)
        ps = self._state.pairs.get(pair_key)
        if pc is None or ps is None:
            return False
        assets = self._pair_assets(pair_key)
        if assets is None:
            return ps.inventory_base >= pc.order_size
        base_asset, _quote_asset = assets
        wallet_base = self._wallet_base_balance(base_asset)
        committed_base = self._committed_base_for_open_sells(base_asset)
        sell_qty = pc.sell_order_size if pc.sell_order_size is not None else pc.order_size
        # Sell-floor guard: don't allow selling wallet below this minimum
        if pc.sell_floor_base is not None and pc.sell_floor_base > 0:
            remaining = wallet_base - committed_base - sell_qty
            if remaining < pc.sell_floor_base:
                LOG.debug(
                    "can_sell %s: sell_floor_base %.0f reached "
                    "(wallet=%.0f committed=%.0f sell=%.0f remaining=%.0f)",
                    pair_key, pc.sell_floor_base,
                    wallet_base, committed_base, sell_qty, remaining,
                )
                return False
        return committed_base + sell_qty <= wallet_base

    def _pair_assets(self, pair_key: str) -> tuple[str, str] | None:
        pc = self._config.pairs.get(pair_key)
        if pc is None:
            return None
        return SYMBOL_BASE_MAP.get(pc.symbol)

    def _wallet_quote_balance(self, quote_asset: str) -> float:
        # Pair state can mirror the same wallet balance across multiple pairs; use
        # max() as conservative wallet estimate instead of summing duplicates.
        balances: list[float] = []
        for key, ps in self._state.pairs.items():
            assets = self._pair_assets(key)
            if assets is None:
                continue
            if assets[1] == quote_asset:
                balances.append(ps.inventory_quote)
        return max(balances) if balances else 0.0

    def _wallet_base_balance(self, base_asset: str) -> float:
        balances: list[float] = []
        for key, ps in self._state.pairs.items():
            assets = self._pair_assets(key)
            if assets is None:
                continue
            if assets[0] == base_asset:
                balances.append(ps.inventory_base)
        return max(balances) if balances else 0.0

    def _committed_quote_for_open_buys(self, quote_asset: str) -> float:
        committed = 0.0
        for order in self._state.active_orders.values():
            if order.side != "buy":
                continue
            assets = self._pair_assets(order.pair_key)
            if assets is None:
                continue
            if assets[1] != quote_asset:
                continue
            committed += order.qty * order.price * 1.05
        return committed

    def _committed_base_for_open_sells(self, base_asset: str) -> float:
        committed = 0.0
        for order in self._state.active_orders.values():
            if order.side != "sell":
                continue
            assets = self._pair_assets(order.pair_key)
            if assets is None:
                continue
            if assets[0] != base_asset:
                continue
            committed += order.qty
        return committed

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
        self.save_cost_basis()
        self.save_barriers()

    def record_sell(self, pair_key: str, qty: float, price: float, fee: float) -> float:
        """Apply sell; return net P&L in quote (revenue minus allocated cost basis)."""
        ps = self._state.pairs.get(pair_key)
        if ps is None or ps.inventory_base <= 0:
            return 0.0
        qty = min(qty, ps.inventory_base)
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
        self.save_cost_basis()
        self.save_barriers()
        return pnl

    def gross_spread_on_sell(
        self, pair_key: str, qty: float, price: float, fee: float,
    ) -> float:
        """Economic edge before fees: (exit - avg entry) * qty."""
        ps = self._state.pairs.get(pair_key)
        if ps is None or ps.inventory_base <= 0:
            return 0.0
        qty = min(qty, ps.inventory_base)
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

    def save_cost_basis(self) -> None:
        """Persist position_cost_quote for all pairs to disk."""
        data = {}
        for pair_key, ps in self._state.pairs.items():
            if ps.inventory_base > 0 and ps.position_cost_quote > 0:
                data[pair_key] = {
                    "position_cost_quote": ps.position_cost_quote,
                    "inventory_base": ps.inventory_base,
                }
        try:
            COST_BASIS_FILE.parent.mkdir(parents=True, exist_ok=True)
            COST_BASIS_FILE.write_text(json.dumps(data, indent=2))
        except Exception:
            LOG.debug("Failed to save cost basis", exc_info=True)

    def load_cost_basis(self) -> None:
        """Restore position_cost_quote from disk if available."""
        if not COST_BASIS_FILE.exists():
            return
        try:
            data = json.loads(COST_BASIS_FILE.read_text())
        except Exception:
            LOG.warning("Failed to load cost basis file", exc_info=True)
            return
        for pair_key, vals in data.items():
            ps = self._state.pairs.get(pair_key)
            if ps is None:
                continue
            saved_cost = vals.get("position_cost_quote", 0.0)
            saved_base = vals.get("inventory_base", 0.0)
            if saved_cost > 0 and saved_base > 0:
                ps.position_cost_quote = saved_cost
                LOG.info(
                    "Restored cost basis for %s: $%.4f (saved_base=%.2f, current_base=%.2f)",
                    pair_key, saved_cost, saved_base, ps.inventory_base,
                )

    def seed_cost_basis_from_mid(self, pair_key: str) -> None:
        """Best-effort cost basis — only used if no persisted cost basis exists."""
        ps = self._state.pairs.get(pair_key)
        if ps is None:
            return
        if ps.inventory_base <= 0:
            ps.position_cost_quote = 0.0
            return
        if ps.position_cost_quote > 0:
            LOG.info("Skipping mid-seed for %s — persisted cost basis already loaded ($%.4f)",
                     pair_key, ps.position_cost_quote)
            return
        mid = ps.mid_price
        if mid <= 0:
            return
        ps.position_cost_quote = ps.inventory_base * mid
        LOG.info("Seeded cost basis from mid for %s: %.2f x $%.4f = $%.4f",
                 pair_key, ps.inventory_base, mid, ps.position_cost_quote)

    def save_barriers(self) -> None:
        """Persist per-fill barriers to disk so sell decisions survive restarts."""
        from .state import FillBarrier  # noqa: F811
        data: dict[str, list[dict]] = {}
        for pair_key, ps in self._state.pairs.items():
            if ps.pending_barriers:
                data[pair_key] = [
                    {
                        "buy_price": b.buy_price,
                        "qty": b.qty,
                        "stop_price": b.stop_price,
                        "tp_price": b.tp_price,
                        "max_hold_until": b.max_hold_until,
                        "created_at": b.created_at,
                    }
                    for b in ps.pending_barriers
                    if b.qty > 0
                ]
        try:
            BARRIERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            BARRIERS_FILE.write_text(json.dumps(data, indent=2))
        except Exception:
            LOG.debug("Failed to save fill barriers", exc_info=True)

    def load_barriers(self) -> None:
        """Restore per-fill barriers from disk. Bootstrap from blended cost basis if no file."""
        from .state import FillBarrier
        loaded = False
        if BARRIERS_FILE.exists():
            try:
                data = json.loads(BARRIERS_FILE.read_text())
            except Exception:
                LOG.warning("Failed to load fill barriers file", exc_info=True)
                data = {}
            for pair_key, barrier_list in data.items():
                ps = self._state.pairs.get(pair_key)
                if ps is None:
                    continue
                restored = []
                for b in barrier_list:
                    if b.get("qty", 0) <= 0:
                        continue
                    restored.append(FillBarrier(
                        buy_price=b["buy_price"],
                        qty=b["qty"],
                        stop_price=b.get("stop_price", 0.0),
                        tp_price=b.get("tp_price", 0.0),
                        max_hold_until=b.get("max_hold_until", 0.0),
                        created_at=b.get("created_at", 0.0),
                    ))
                if restored:
                    ps.pending_barriers = restored
                    # Gap reconciliation: if tracked barrier qty < wallet inventory,
                    # seed the gap with a proportional blended-cost barrier so
                    # min_profitable_sell_price accounts for all held units.
                    if ps.inventory_base > 0 and ps.position_cost_quote > 0:
                        total_tracked = sum(b.qty for b in restored if b.qty > 0)
                        gap = ps.inventory_base - total_tracked
                        if gap > 1.0:
                            gap_price = ps.position_cost_quote / ps.inventory_base
                            ps.pending_barriers.append(FillBarrier(
                                buy_price=gap_price,
                                qty=gap,
                                stop_price=0.0,
                                tp_price=0.0,
                                max_hold_until=0.0,
                                created_at=time.time(),
                            ))
                            LOG.info(
                                "Gap-reconciled barrier for %s: %.0f untracked units @ $%.6f",
                                pair_key, gap, gap_price,
                            )
                    total_qty = sum(b.qty for b in ps.pending_barriers)
                    cheapest = min(b.buy_price for b in ps.pending_barriers)
                    LOG.info(
                        "Restored %d fill barriers for %s: %.0f units, cheapest @ $%.6f",
                        len(restored), pair_key, total_qty, cheapest,
                    )
                    loaded = True

        if not loaded:
            for pair_key, ps in self._state.pairs.items():
                if ps.inventory_base > 0 and not ps.pending_barriers:
                    # Prefer current mid price — populated by BookClient before START is pressed.
                    # Falls back to blended cost if mid is not yet available.
                    seed_price = 0.0
                    source = ""
                    if ps.mid_price > 0:
                        seed_price = ps.mid_price
                        source = "mid"
                    elif ps.position_cost_quote > 0:
                        seed_price = ps.position_cost_quote / ps.inventory_base
                        source = "blended cost"
                    if seed_price <= 0:
                        continue
                    ps.pending_barriers.append(FillBarrier(
                        buy_price=seed_price,
                        qty=ps.inventory_base,
                        stop_price=0.0,
                        tp_price=0.0,
                        max_hold_until=0.0,
                        created_at=time.time(),
                    ))
                    LOG.info(
                        "Bootstrapped barrier for %s from %s: %.0f units @ $%.6f",
                        pair_key, source, ps.inventory_base, seed_price,
                    )

    def reseed_barriers_at_mid(self, pair_key: str) -> None:
        """Reset all barriers for a pair to the current market mid price.

        Use when the position is underwater and the old cost basis is blocking sells.
        Clears the stale barriers, creates a single fresh one at live mid, and
        immediately persists both barriers and cost basis to disk.
        If triple_barrier is enabled, also sets stop/tp/max_hold on the new barrier.
        """
        from .state import FillBarrier
        ps = self._state.pairs.get(pair_key)
        if ps is None:
            return
        mid = ps.mid_price
        if mid <= 0:
            LOG.warning("reseed_barriers_at_mid %s: no mid price available", pair_key)
            self._state.push_alert(
                "warning", "Reseed Failed",
                f"No mid price for {pair_key} — try again once the book is live.",
                "inventory",
            )
            return
        ps.pending_barriers.clear()
        ps._sell_profit_suppressed = False
        if ps.inventory_base > 0:
            bot = self._config.bot
            tb_enabled = getattr(bot, "triple_barrier_enabled", False)
            stop_price = 0.0
            tp_price = 0.0
            max_hold_until = 0.0
            if tb_enabled:
                stop_pct = getattr(bot, "tb_stop_pct", 2.0) / 100.0
                tp_pct   = getattr(bot, "tb_tp_pct", 1.5) / 100.0
                hold_sec = getattr(bot, "tb_max_hold_sec", 3600.0)
                stop_price = mid * (1.0 - stop_pct)
                tp_price   = mid * (1.0 + tp_pct)
                max_hold_until = time.time() + hold_sec
            ps.pending_barriers.append(FillBarrier(
                buy_price=mid,
                qty=ps.inventory_base,
                stop_price=stop_price,
                tp_price=tp_price,
                max_hold_until=max_hold_until,
                created_at=time.time(),
            ))
            ps.position_cost_quote = ps.inventory_base * mid
            LOG.info(
                "reseed_barriers_at_mid %s: %.0f units @ $%.6f (triple_barrier=%s)",
                pair_key, ps.inventory_base, mid, tb_enabled,
            )
            self._state.push_alert(
                "success", "Barriers Reseeded",
                f"{pair_key}: {ps.inventory_base:.0f} units @ ${mid:.5f}. Sells unlocked.",
                "inventory",
            )
        self.save_barriers()
        self.save_cost_basis()

    def min_profitable_sell_price(self, pair_key: str) -> float:
        """Return the minimum sell price that would be profitable against the cheapest barrier.

        Returns 0.0 if no barriers exist (no cost basis to protect).
        """
        ps = self._state.pairs.get(pair_key)
        if ps is None or not ps.pending_barriers:
            return 0.0
        pc = self._config.pairs.get(pair_key)
        fee_bps = pc.fee_bps if pc else 25
        cheapest = min(b.buy_price for b in ps.pending_barriers if b.qty > 0)
        fee_mult = 1.0 + (fee_bps * 2) / 10_000.0
        return cheapest * fee_mult

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
            self._state.push_alert(
                "error", "Balance Sync Failed",
                "Could not fetch balances from Kraken. Inventory may be stale.",
                "inventory",
            )
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
