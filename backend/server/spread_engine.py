"""Spread engine — the core market-making loop.

Simple: base half-spread (from config or bootstrap) + inventory skew.
The learner adjusts spread_bps over time; the floor protects against bleeding.
Depeg circuit breaker and survival P&L floor protect the account.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .config import PROFITABILITY_MARGIN_BPS

if TYPE_CHECKING:
    from .config import AppConfig, PairConfig
    from .inventory import InventoryManager
    from .order_manager import OrderManager
    from .pnl import PnLTracker
    from .session_logger import SessionLogger
    from .state import ActiveOrder, BotState, PairState

LOG = logging.getLogger(__name__)

DRIFT_CANCEL_MULT = 1.5
NEAR_FILL_BPS = 3
NEAR_FILL_MAX_AGE_SEC = 300
STALE_ORDER_SEC = 600
BOOK_STALE_SEC = 120.0
INVENTORY_SKEW_SCALE = 0.4
VOL_WIDEN_THRESHOLD = 0.0003
VOL_WIDEN_SCALE = 0.3
# VPIN / velocity: widen spread when price is moving fast (friend: toxicity detection)
VELOCITY_WIDEN_FLOOR_BPS = 10.0   # only widen above this velocity
VELOCITY_WIDEN_SCALE = 0.5        # add 1 bps per 2 bps of velocity above floor

_PRICE_DECIMALS: dict[str, int] = {
    "TEL/USD": 6,   # sub-cent asset — needs 6 decimal places
    "USDC/USDT": 4,
    "USDG/USD": 4,
    "USDG/USDT": 4,
    "USDG/USDC": 4,
    "USDE/USD": 4,
    "USDE/USDT": 4,
    "USDE/USDC": 4,
    "XRP/USDT": 4,
    "XRP/USD": 4,
    "SOL/USD": 2,
    "XBT/USDT": 1,
    "ETH/USDT": 2,
}


class SpreadEngine:
    def __init__(
        self,
        state: BotState,
        config: AppConfig,
        order_mgr: OrderManager,
        inventory: InventoryManager,
        pnl: PnLTracker,
        session_logger: "SessionLogger | None" = None,
    ) -> None:
        self._state = state
        self._config = config
        self._paper_mgr = order_mgr
        self._inventory = inventory
        self._pnl = pnl
        self._session_logger = session_logger
        self._task: asyncio.Task | None = None
        self._last_tick: dict[str, float] = {}
        self._bootstrap_was_active: dict[str, bool] = {}
        self._momentum_sell_ts: dict[str, list[float]] = {}
        self._momentum_active: dict[str, bool] = {}

    def set_live_order_mgr(self, mgr: OrderManager) -> None:
        """Hot-swap to the live order manager."""
        self._live_mgr = mgr

    def _active_order_mgr(self) -> OrderManager:
        if self._state.mode == "live" and getattr(self, "_live_mgr", None):
            return self._live_mgr
        return self._paper_mgr

    @staticmethod
    def _pair_sell_count(state: "BotState", pair_key: str) -> int:
        return sum(
            1 for f in state.recent_fills
            if f.side == "sell" and f.pair_key == pair_key
        )

    def _base_half_spread_bps(self, pair_key: str, pc: "PairConfig") -> tuple[int, bool]:
        """Config half-spread; optional bootstrap tightens until enough sells."""
        n = self._pair_sell_count(self._state, pair_key)
        if (
            pc.bootstrap_half_spread_bps is not None
            and pc.bootstrap_until_sell_trades > 0
            and n < pc.bootstrap_until_sell_trades
        ):
            return max(int(pc.bootstrap_half_spread_bps), 1), True
        return pc.spread_bps, False

    async def _trigger_risk_halt(self, reason: str, log_level: str = "warning") -> None:
        if self._state.risk_halted:
            return
        self._state.risk_halted = True
        self._state.risk_halt_reason = reason
        mgr = self._active_order_mgr()
        for key in self._config.pair_keys_for_trading():
            await mgr.cancel_all(key)
            self._state.last_cancel_reason[key] = reason
        if self._session_logger is not None and hasattr(self._session_logger, "log_risk_halt"):
            self._session_logger.log_risk_halt(reason=reason)
        if log_level == "info":
            LOG.info("Risk halt triggered: %s", reason)
        else:
            LOG.warning("Risk halt triggered: %s", reason)

    def _apply_momentum_hold(self, pair_key: str) -> bool:
        cfg = self._config.bot
        required_sells = max(1, int(getattr(cfg, "momentum_hold_sells", 2)))
        window_sec = max(1.0, float(getattr(cfg, "momentum_hold_sec", 60.0)))
        now = time.time()

        sells = self._momentum_sell_ts.setdefault(pair_key, [])
        sells.extend(
            f.timestamp
            for f in self._state.recent_fills
            if f.pair_key == pair_key and f.side == "sell" and f.timestamp not in sells
        )
        cutoff = now - window_sec
        sells[:] = [ts for ts in sells if ts >= cutoff]
        is_active = len(sells) >= required_sells
        was_active = self._momentum_active.get(pair_key, False)

        if is_active != was_active:
            self._momentum_active[pair_key] = is_active
            if self._session_logger is not None and hasattr(self._session_logger, "log_momentum"):
                self._session_logger.log_momentum(
                    pair=pair_key,
                    active=is_active,
                    sells_in_window=len(sells),
                    window_sec=window_sec,
                )
            LOG.info(
                "Momentum hold %s for %s (%d sells in %.0fs)",
                "ON" if is_active else "OFF",
                pair_key,
                len(sells),
                window_sec,
            )
        return is_active

    async def start(self) -> None:
        self._state.running = True
        self._state.risk_halted = False
        self._state.risk_halt_reason = ""
        if self._state.session_start_ts == 0.0:
            self._state.session_start_ts = time.time()
            self._state.session_start_pnl = self._state.total_pnl
        self._last_tick.clear()
        self._momentum_sell_ts.clear()
        self._momentum_active.clear()
        self._task = asyncio.create_task(self._run_loop())
        LOG.info("Spread engine started")

    async def stop(self) -> None:
        self._state.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._paper_mgr.cancel_all()
        if getattr(self, "_live_mgr", None):
            await self._live_mgr.cancel_all()
        LOG.info("Spread engine stopped")

    async def _run_loop(self) -> None:
        """Run each pair on its own interval (from config pair_cycle_ms)."""
        while self._state.running:
            try:
                now = time.monotonic()
                for pair_key in self._config.pair_keys_for_trading():
                    ps = self._state.pairs.get(pair_key)
                    if ps is None or ps.mid_price == 0:
                        continue
                    interval_s = self._config.pair_cycle_ms(pair_key) / 1000.0
                    last = self._last_tick.get(pair_key, 0.0)
                    if last == 0.0 or now - last >= interval_s:
                        await self._tick(pair_key)
                        self._last_tick[pair_key] = now
            except Exception:
                LOG.exception("Error in spread engine cycle")

            await asyncio.sleep(0.05)

    async def _tick(self, pair_key: str) -> None:
        if self._state.risk_halted:
            return

        pc = self._config.pairs[pair_key]
        ps = self._state.pairs[pair_key]
        bot = self._config.bot

        ref = ps.microprice
        if ref == 0:
            return

        if ps.last_book_update_ts > 0:
            book_age = time.time() - ps.last_book_update_ts
            if book_age > BOOK_STALE_SEC:
                LOG.warning(
                    "STALE BOOK %s: last update %.1fs ago — skipping tick",
                    pair_key, book_age,
                )
                return

        from .state import CancelReason

        # --- DEPEG CIRCUIT BREAKER (stablecoin pairs) ---
        peg = pc.peg_price
        if peg is not None and peg > 0:
            deviation_bps = abs(ref - peg) / peg * 10_000
            if deviation_bps > bot.depeg_threshold_bps:
                LOG.warning(
                    "DEPEG %s: %.4f vs peg %.4f (%.0f bps) — halt + liquidate",
                    pair_key, ref, peg, deviation_bps,
                )
                mgr = self._active_order_mgr()
                await mgr.cancel_all(pair_key)
                self._state.last_cancel_reason[pair_key] = CancelReason.DEPEG.value
                await self._emergency_liquidate(pair_key)
                return

        # --- RISK MANAGEMENT AUTO-STOPS ---
        pnl = self._state.total_pnl
        # Track peak for drawdown calculation
        if pnl > self._state.peak_pnl:
            self._state.peak_pnl = pnl

        # Cumulative P&L floor
        min_pnl = getattr(bot, "min_total_pnl_usd", None)
        if min_pnl is not None and pnl <= min_pnl:
            await self._trigger_risk_halt(
                reason=(
                    f"{CancelReason.SURVIVAL_PNL.value}: "
                    f"total_pnl={pnl:.4f} threshold={min_pnl:.2f}"
                ),
                log_level="warning",
            )
            return

        # Daily profit target
        daily_target = getattr(bot, "daily_profit_target_usd", None)
        daily_pnl = pnl - self._state.session_start_pnl
        if daily_target is not None and daily_pnl >= daily_target:
            await self._trigger_risk_halt(
                reason=f"daily profit target reached: session_pnl={daily_pnl:.4f} target={daily_target:.2f}",
                log_level="info",
            )
            return

        # Daily loss limit
        daily_loss_limit = getattr(bot, "daily_loss_limit_usd", None)
        if daily_loss_limit is not None and daily_pnl <= -abs(daily_loss_limit):
            await self._trigger_risk_halt(
                reason=f"daily loss limit reached: session_pnl={daily_pnl:.4f} limit={daily_loss_limit:.2f}",
                log_level="warning",
            )
            return

        # Max drawdown from peak
        max_dd = getattr(bot, "max_drawdown_pct", None)
        if max_dd is not None and self._state.peak_pnl > 0:
            drawdown_pct = (self._state.peak_pnl - pnl) / self._state.peak_pnl * 100
            if drawdown_pct >= max_dd:
                await self._trigger_risk_halt(
                    reason=(
                        f"max drawdown {drawdown_pct:.1f}% from peak "
                        f"(peak={self._state.peak_pnl:.4f}, total_pnl={pnl:.4f}, limit={max_dd:.1f}%)"
                    ),
                    log_level="warning",
                )
                return

        # --- BASE SPREAD (bootstrap or config; learner adjusts spread_bps) ---
        base_spread_bps, in_bootstrap = self._base_half_spread_bps(pair_key, pc)
        was_boot = self._bootstrap_was_active.get(pair_key, False)
        if was_boot and not in_bootstrap:
            LOG.info(
                "Bootstrap complete %s: base half-spread now %d bps (learner refines)",
                pair_key, pc.spread_bps,
            )
        self._bootstrap_was_active[pair_key] = in_bootstrap

        # --- HALF-SPREAD FLOOR (graduated ramp) ---
        fee_bps = self._config.effective_fee_bps(pair_key, self._state.volume_30d)
        min_q = max(1, int(getattr(bot, "min_quote_half_spread_bps", 2)))
        survival_pair_floor = pc.spread_floor_bps if pc.spread_floor_bps is not None else min_q
        survival_floor = max(min_q, int(bot.adaptive_spread_floor_bps), survival_pair_floor)

        if getattr(bot, "per_trade_profitability", True):
            sells = self._pair_sell_count(self._state, pair_key)
            if sells < 5:
                floor_bps = survival_floor
            elif sells < 10:
                margin = min(1, PROFITABILITY_MARGIN_BPS)
                floor_bps = max(fee_bps + margin, survival_floor)
            else:
                margin = PROFITABILITY_MARGIN_BPS
                floor_bps = max(fee_bps + margin, survival_floor)
        else:
            floor_bps = survival_floor

        effective_spread_bps = max(floor_bps, base_spread_bps)

        # Vol guard: widen when realized vol is abnormally high
        dynamic_widen_bps = 0
        sigma = ps.realized_vol if ps.realized_vol > 0 else 0.0
        if sigma > VOL_WIDEN_THRESHOLD:
            vol_add = int(sigma * 10_000 * VOL_WIDEN_SCALE)
            effective_spread_bps += vol_add
            dynamic_widen_bps += vol_add

        # VPIN / velocity toxicity guard: widen when price moving fast
        velocity = ps.mid_velocity_bps if ps.mid_velocity_bps > 0 else 0.0
        if velocity > VELOCITY_WIDEN_FLOOR_BPS:
            vel_add = int((velocity - VELOCITY_WIDEN_FLOOR_BPS) * VELOCITY_WIDEN_SCALE)
            effective_spread_bps += vel_add
            dynamic_widen_bps += vel_add

        if dynamic_widen_bps > 0:
            LOG.info(
                "Dynamic widen %s: +%dbps (vol=%.5f vel=%.1fbps)",
                pair_key, dynamic_widen_bps, sigma, velocity,
            )

        # Clamp to ceiling
        effective_spread_bps = min(effective_spread_bps, bot.adaptive_spread_ceiling_bps)

        half_spread = ref * (effective_spread_bps / 10_000)

        # --- INVENTORY SKEW (mean-reversion / rebalancing) ---
        inventory = ps.inventory_base
        max_inv = pc.max_inventory if pc.max_inventory > 0 else 1.0
        q = inventory / max_inv if max_inv > 0 else 0.0
        q = max(-1.0, min(1.0, q))
        skew = -q * INVENTORY_SKEW_SCALE * half_spread

        reservation = ref + skew

        # Peg-reversion bias for stable/pegged pairs:
        # quote more aggressively toward the peg side to improve reversion fills.
        if peg is not None and peg > 0:
            peg_dev_bps = abs(ref - peg) / peg * 10_000
            if peg_dev_bps > 1:
                bias_strength = min(0.5, peg_dev_bps / 50.0)
                bias = half_spread * bias_strength
                reservation += bias if ref < peg else -bias

        buy_price = reservation - half_spread
        sell_price = reservation + half_spread

        tick = _PRICE_DECIMALS.get(pc.symbol, 8)
        buy_price = round(buy_price, tick)
        sell_price = round(sell_price, tick)

        self._state.current_spread_bps = effective_spread_bps  # type: ignore[attr-defined]
        suppress_buy = self._apply_momentum_hold(pair_key)

        LOG.debug(
            "TICK %s: micro=%.6f buy=%.6f sell=%.6f spread=%dbps%s",
            pair_key, ref, buy_price, sell_price, effective_spread_bps,
            " [BOOTSTRAP]" if in_bootstrap else "",
        )

        if self._state.mode == "paper":
            await self._paper_tick(pair_key, buy_price, sell_price, suppress_buy)
        else:
            await self._smart_live_tick(
                pair_key, buy_price, sell_price,
                half_spread, effective_spread_bps, suppress_buy,
            )

    def _paper_fill_check(
        self, order: "ActiveOrder", ps: "PairState", pc: "PairConfig",
    ) -> bool:
        """Determine if a paper order would fill using only real book data.

        1. Cross — market price moved past our limit (instant fill).
        2. Book depth — our price sits within the visible book levels,
           meaning real resting liquidity exists at or beyond our price.
           Requires a 2-second age to model queue position.
        """
        age = time.time() - order.placed_at

        if order.side == "buy":
            if ps.best_ask > 0 and ps.best_ask <= order.price:
                return True
            deepest_bid = ps.bid_levels[-1].price if ps.bid_levels else 0
            if deepest_bid > 0 and order.price >= deepest_bid:
                return age >= 2.0
        elif order.side == "sell":
            if ps.best_bid > 0 and ps.best_bid >= order.price:
                return True
            deepest_ask = ps.ask_levels[-1].price if ps.ask_levels else 0
            if deepest_ask > 0 and order.price <= deepest_ask:
                return age >= 2.0
        return False

    async def _paper_tick(
        self, pair_key: str, buy_price: float, sell_price: float, suppress_buy: bool = False,
    ) -> None:
        """Paper mode: virtual limits with realistic fill simulation."""
        pc = self._config.pairs[pair_key]
        ps = self._state.pairs[pair_key]

        filled_buy = None
        filled_sell = None
        to_remove = []
        for oid, order in self._state.active_orders.items():
            if order.pair_key != pair_key:
                continue
            if order.side == "buy" and self._paper_fill_check(order, ps, pc):
                filled_buy = order
                to_remove.append(oid)
            elif order.side == "sell" and self._paper_fill_check(order, ps, pc):
                filled_sell = order
                to_remove.append(oid)

        for oid in to_remove:
            self._state.active_orders.pop(oid, None)

        fee_bps = self._config.effective_fee_bps(pair_key, self._state.volume_30d)

        market_spread_bps = (
            (ps.best_ask - ps.best_bid) / ps.mid_price * 10_000
            if ps.mid_price > 0 else 0.0
        )

        if filled_buy:
            fee = filled_buy.price * filled_buy.qty * (fee_bps / 10_000)
            self._inventory.record_buy(pair_key, filled_buy.qty, filled_buy.price, fee)
            self._pnl.record_fill(
                pair_key=pair_key,
                symbol=pc.symbol,
                side="buy",
                price=filled_buy.price,
                qty=filled_buy.qty,
                fee=fee,
                pnl_delta=0.0,
            )
            self._state.last_fill_ts[pair_key] = time.time()
            if self._session_logger is not None:
                self._session_logger.log_fill(
                    pair=pair_key, side="buy",
                    price=filled_buy.price, qty=filled_buy.qty,
                    fee=fee, pnl=0.0,
                    spread_bps=pc.spread_bps,
                    market_spread_bps=market_spread_bps,
                )

        if filled_sell:
            fee = filled_sell.price * filled_sell.qty * (fee_bps / 10_000)
            gross = self._inventory.gross_spread_on_sell(
                pair_key, filled_sell.qty, filled_sell.price, fee,
            )
            net = self._inventory.record_sell(
                pair_key, filled_sell.qty, filled_sell.price, fee,
            )
            self._pnl.record_fill(
                pair_key=pair_key,
                symbol=pc.symbol,
                side="sell",
                price=filled_sell.price,
                qty=filled_sell.qty,
                fee=fee,
                pnl_delta=net,
                gross_spread=gross,
            )
            self._state.last_fill_ts[pair_key] = time.time()
            if self._session_logger is not None:
                self._session_logger.log_fill(
                    pair=pair_key, side="sell",
                    price=filled_sell.price, qty=filled_sell.qty,
                    fee=fee, pnl=net,
                    spread_bps=pc.spread_bps,
                    market_spread_bps=market_spread_bps,
                )

        cur_spread_bps = getattr(self._state, "current_spread_bps", pc.spread_bps)
        half_spread = ps.mid_price * (cur_spread_bps / 10_000)
        last_reason = ""
        for oid, o in list(self._state.active_orders.items()):
            if o.pair_key != pair_key:
                continue
            target = buy_price if o.side == "buy" else sell_price
            reason = self._should_cancel_order(o, target, half_spread, ps)
            if reason is not None:
                await self._paper_mgr.cancel_order(oid)
                last_reason = reason
        if last_reason:
            self._state.last_cancel_reason[pair_key] = last_reason
        else:
            self._state.last_cancel_reason.pop(pair_key, None)

        has_buy = any(
            o.pair_key == pair_key and o.side == "buy"
            for o in self._state.active_orders.values()
        )
        has_sell = any(
            o.pair_key == pair_key and o.side == "sell"
            for o in self._state.active_orders.values()
        )

        if not suppress_buy and not has_buy and self._inventory.can_buy(pair_key):
            await self._paper_mgr.place_order(
                pair_key, pc.symbol, "buy", buy_price, pc.order_size,
            )

        if not has_sell and self._inventory.can_sell(pair_key):
            await self._paper_mgr.place_order(
                pair_key, pc.symbol, "sell", sell_price, pc.order_size,
            )

    def _should_cancel_order(
        self,
        order: "ActiveOrder",
        target_price: float,
        half_spread: float,
        ps: "PairState",
    ) -> str | None:
        """Return a cancel reason string, or None to keep the order alive."""
        from .state import CancelReason

        now = time.time()
        age = now - order.placed_at
        drift = abs(order.price - target_price)
        drift_threshold = half_spread * DRIFT_CANCEL_MULT

        near_fill = False
        if order.side == "buy" and ps.best_ask > 0:
            distance_bps = (ps.best_ask - order.price) / order.price * 10_000
            if distance_bps <= NEAR_FILL_BPS:
                near_fill = True
        elif order.side == "sell" and ps.best_bid > 0:
            distance_bps = (order.price - ps.best_bid) / order.price * 10_000
            if distance_bps <= NEAR_FILL_BPS:
                near_fill = True

        if near_fill and age <= NEAR_FILL_MAX_AGE_SEC:
            return None

        if age > STALE_ORDER_SEC:
            return CancelReason.STALE.value

        if drift > drift_threshold:
            return CancelReason.PRICE_DRIFT.value

        return None

    async def _smart_live_tick(
        self,
        pair_key: str,
        buy_price: float,
        sell_price: float,
        half_spread: float,
        effective_spread_bps: int,
        suppress_buy: bool = False,
    ) -> None:
        """Live mode with smart cancellation — only cancel orders that need it."""
        # Backoff: if a recent order was rejected, skip for 5 seconds to avoid spam
        reject_ts = getattr(self._state, "last_order_reject_ts", 0.0)
        if reject_ts and time.time() - reject_ts < 5.0:
            return

        mgr = self._active_order_mgr()
        pc = self._config.pairs[pair_key]
        ps = self._state.pairs[pair_key]

        existing_buy = None
        existing_sell = None
        to_cancel: list[tuple[str, str]] = []

        for oid, order in list(self._state.active_orders.items()):
            if order.pair_key != pair_key:
                continue

            target = buy_price if order.side == "buy" else sell_price
            reason = self._should_cancel_order(order, target, half_spread, ps)

            if reason is not None:
                to_cancel.append((oid, reason))
            elif order.side == "buy":
                existing_buy = order
            elif order.side == "sell":
                existing_sell = order

        last_reason = ""
        for oid, reason in to_cancel:
            cancelled = await mgr.cancel_order(oid)
            if cancelled:
                last_reason = reason
                LOG.info("CANCEL %s [%s]: %s", pair_key, oid[:16], reason)

        if last_reason:
            self._state.last_cancel_reason[pair_key] = last_reason
        elif not to_cancel:
            self._state.last_cancel_reason.pop(pair_key, None)

        if not suppress_buy and existing_buy is None and self._inventory.can_buy(pair_key):
            await mgr.place_order(
                pair_key, pc.symbol, "buy", buy_price, pc.order_size,
            )

        if existing_sell is None and self._inventory.can_sell(pair_key):
            await mgr.place_order(
                pair_key, pc.symbol, "sell", sell_price, pc.order_size,
            )

    async def _emergency_liquidate(self, pair_key: str) -> None:
        """CRITICAL threat: dump all inventory at best_bid to go flat."""
        ps = self._state.pairs[pair_key]
        pc = self._config.pairs[pair_key]
        qty = ps.inventory_base
        if qty <= 0:
            return

        sell_price = ps.best_bid
        if sell_price <= 0:
            LOG.warning("CRITICAL %s: want to liquidate %.4f but no bid", pair_key, qty)
            return

        LOG.warning(
            "CRITICAL %s: emergency liquidate %.4f @ %.8f (best_bid)",
            pair_key, qty, sell_price,
        )

        if self._state.mode == "paper":
            eff_fee = self._config.effective_fee_bps(pair_key, self._state.volume_30d)
            fee = sell_price * qty * (eff_fee / 10_000)
            gross = self._inventory.gross_spread_on_sell(pair_key, qty, sell_price, fee)
            net = self._inventory.record_sell(pair_key, qty, sell_price, fee)
            self._pnl.record_fill(
                pair_key=pair_key,
                symbol=pc.symbol,
                side="sell",
                price=sell_price,
                qty=qty,
                fee=fee,
                pnl_delta=net,
                gross_spread=gross,
            )
        else:
            mgr = self._active_order_mgr()
            if hasattr(mgr, "place_aggressive_sell"):
                await mgr.place_aggressive_sell(pair_key, pc.symbol, sell_price, qty)
            else:
                await mgr.place_order(pair_key, pc.symbol, "sell", sell_price, qty)

    def update_pair_config(
        self,
        pair_key: str,
        spread_bps: int | None = None,
        order_size: float | None = None,
        max_inventory: float | None = None,
        cycle_ms: int | None = None,
        spread_floor_bps: int | None = None,
        bootstrap_half_spread_bps: int | None = None,
        bootstrap_until_sell_trades: int | None = None,
        clear_bootstrap: bool = False,
    ) -> None:
        pc = self._config.pairs.get(pair_key)
        if pc is None:
            return
        if spread_bps is not None:
            pc.spread_bps = spread_bps
        if order_size is not None:
            pc.order_size = order_size
        if max_inventory is not None:
            pc.max_inventory = max_inventory
        if cycle_ms is not None:
            pc.cycle_ms = cycle_ms
        if spread_floor_bps is not None:
            pc.spread_floor_bps = spread_floor_bps
        if clear_bootstrap:
            pc.bootstrap_half_spread_bps = None
            pc.bootstrap_until_sell_trades = 0
        if bootstrap_half_spread_bps is not None:
            pc.bootstrap_half_spread_bps = bootstrap_half_spread_bps
        if bootstrap_until_sell_trades is not None:
            pc.bootstrap_until_sell_trades = bootstrap_until_sell_trades
            if pc.bootstrap_until_sell_trades <= 0:
                pc.bootstrap_half_spread_bps = None
                pc.bootstrap_until_sell_trades = 0
        LOG.info(
            "Config updated for %s: spread=%d, size=%.4f, max_inv=%.4f, "
            "floor=%s, bootstrap=%s/%s",
            pair_key,
            pc.spread_bps,
            pc.order_size,
            pc.max_inventory,
            pc.spread_floor_bps,
            pc.bootstrap_half_spread_bps,
            pc.bootstrap_until_sell_trades,
        )

    def reset_bootstrap_tracking(self, pair_key: str | None = None) -> None:
        """Clear in-memory bootstrap transition flags (e.g. after UI 'restart')."""
        if pair_key is None:
            self._bootstrap_was_active.clear()
        else:
            self._bootstrap_was_active.pop(pair_key, None)

    async def kill(self) -> None:
        """Stop engine, cancel all orders, then liquidate all base inventory at best_bid."""
        LOG.warning("KILL: stopping engine and liquidating all base inventory")
        await self.stop()
        for pair_key in self._config.pairs:
            ps = self._state.pairs.get(pair_key)
            if ps and ps.inventory_base >= 0.001:
                await self._emergency_liquidate(pair_key)
        LOG.warning("KILL: liquidation complete")

    async def soft_restart(self) -> None:
        """Stop engine, cancel orders, reset bootstrap UI tracking, start again if was running."""
        was = self._state.running
        if was:
            await self.stop()
        self.reset_bootstrap_tracking()
        if was:
            await self.start()
            LOG.info("Soft restart: engine cycle restarted")
        else:
            LOG.info("Soft restart: engine was stopped; tracking cleared")

    def smart_defaults(self, pair_key: str) -> dict | None:
        """Compute recommended config based on pair and current fee tier."""
        pc = self._config.pairs.get(pair_key)
        ps = self._state.pairs.get(pair_key)
        if pc is None:
            return None

        fee = self._config.effective_fee_bps(pair_key, self._state.volume_30d)
        bot = self._config.bot
        min_spread_profit = fee + PROFITABILITY_MARGIN_BPS

        defaults = _PAIR_SMART_DEFAULTS.get(pair_key)
        if defaults is not None:
            result = dict(defaults)
            if getattr(bot, "per_trade_profitability", True):
                result["spread_bps"] = max(result["spread_bps"], min_spread_profit)
                result["reason"] = f"preset for {pc.symbol} (fee={fee}bps @ tier)"
            else:
                result["reason"] = (
                    f"preset survival/volume for {pc.symbol} (fee={fee}bps — not clamped)"
                )
            return result

        mid = ps.mid_price if ps else 0.0

        if fee == 0:
            spread_bps = 8
        elif getattr(bot, "per_trade_profitability", True):
            spread_bps = max(min_spread_profit, 40)
        else:
            spread_bps = max(int(getattr(bot, "min_quote_half_spread_bps", 2)), 8)

        if mid > 10_000:
            order_size, max_inv = 0.0005, 0.004
        elif mid > 500:
            order_size, max_inv = 0.005, 0.04
        elif mid > 10:
            order_size, max_inv = 5.0, 50.0
        elif mid > 0.5:
            order_size, max_inv = 50.0, 500.0
        else:
            order_size, max_inv = pc.order_size, pc.max_inventory

        return {
            "spread_bps": min(spread_bps, 200),
            "order_size": order_size,
            "max_inventory": max_inv,
            "cycle_ms": 500,
            "reason": f"auto: fee={fee}bps @ tier, mid=${mid:.2f}" if mid > 0 else f"fee={fee}bps",
        }


_PAIR_SMART_DEFAULTS: dict[str, dict] = {
    # TEL/USD: Kraken 0% maker fee (rebate pair) — friend's exact settings
    "TEL_USD": {
        "spread_bps": 60,
        "order_size": 10000.0,
        "max_inventory": 50000.0,
        "cycle_ms": 6000,
        "bootstrap_half_spread_bps": 30,
        "bootstrap_until_sell_trades": 10,
    },
    "XRP_USDT": {
        "spread_bps": 8,
        "order_size": 30.0,
        "max_inventory": 300.0,
        "cycle_ms": 500,
        "bootstrap_half_spread_bps": 8,
        "bootstrap_until_sell_trades": 20,
    },
    "XRP_USD": {
        "spread_bps": 8,
        "order_size": 30.0,
        "max_inventory": 300.0,
        "cycle_ms": 500,
        "bootstrap_half_spread_bps": 8,
        "bootstrap_until_sell_trades": 20,
    },
    "USDC_USDT": {
        "spread_bps": 24,
        "order_size": 10.0,
        "max_inventory": 500.0,
        "cycle_ms": 500,
    },
    "USDG_USD": {
        "spread_bps": 3,
        "order_size": 50.0,
        "max_inventory": 500.0,
        "cycle_ms": 500,
    },
    "USDG_USDT": {
        "spread_bps": 4,
        "order_size": 30.0,
        "max_inventory": 200.0,
        "cycle_ms": 500,
    },
    "USDG_USDC": {
        "spread_bps": 3,
        "order_size": 30.0,
        "max_inventory": 200.0,
        "cycle_ms": 500,
    },
    "USDE_USD": {
        "spread_bps": 3,
        "order_size": 40.0,
        "max_inventory": 400.0,
        "cycle_ms": 500,
    },
    "USDE_USDT": {
        "spread_bps": 4,
        "order_size": 40.0,
        "max_inventory": 400.0,
        "cycle_ms": 500,
    },
    "USDE_USDC": {
        "spread_bps": 4,
        "order_size": 30.0,
        "max_inventory": 300.0,
        "cycle_ms": 500,
    },
    "BTC_USDT": {  # Kraken symbol is XBT/USDT
        "spread_bps": 40,
        "order_size": 0.0005,
        "max_inventory": 0.004,
        "cycle_ms": 500,
    },
    "ETH_USDT": {
        "spread_bps": 40,
        "order_size": 0.005,
        "max_inventory": 0.04,
        "cycle_ms": 500,
    },
    "SOL_USD": {
        "spread_bps": 36,
        "order_size": 0.2,
        "max_inventory": 2.0,
        "cycle_ms": 500,
        "bootstrap_half_spread_bps": 24,
        "bootstrap_until_sell_trades": 10,
    },
}
