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

from .btd import BTDSignal
from .config import PROFITABILITY_MARGIN_BPS
from .state import FillBarrier, ThreatLevel
from .twap import TWAPExecutor

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
BOOK_STALE_SEC = 600.0
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
    "DRIFT/USD": 4,
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
        self._live_mgr: OrderManager | None = None
        self._btd_signals: dict[str, BTDSignal] = {}
        self._twap = TWAPExecutor(state)
        self._twap_tasks: dict[str, asyncio.Task] = {}
        self._consecutive_errors = 0

    def set_live_order_mgr(self, mgr: OrderManager) -> None:
        """Hot-swap to the live order manager."""
        self._live_mgr = mgr

    def _active_order_mgr(self) -> OrderManager:
        if self._state.mode == "live" and self._live_mgr is not None:
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
        self._state.push_alert(
            "error" if log_level == "warning" else "warning",
            "Risk Halt Triggered",
            reason,
            "risk_engine",
        )

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
        self._state.last_order_reject_ts = 0.0
        self._state.last_order_reject_reason = ""
        self._state.order_reject_count = 0
        if self._state.session_start_ts == 0.0:
            self._state.session_start_ts = time.time()
            self._state.session_start_pnl = self._state.total_pnl
        self._last_tick.clear()
        self._momentum_sell_ts.clear()
        self._momentum_active.clear()
        for task in self._twap_tasks.values():
            task.cancel()
        self._twap_tasks.clear()
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
        for task in self._twap_tasks.values():
            task.cancel()
        self._twap_tasks.clear()
        await self._paper_mgr.cancel_all()
        if self._live_mgr is not None:
            await self._live_mgr.cancel_all()
        LOG.info("Spread engine stopped")

    async def _run_loop(self) -> None:
        """Run each pair on its own interval (from config pair_cycle_ms)."""
        while self._state.running:
            self._state.last_engine_heartbeat_ts = time.time()
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
                self._consecutive_errors = 0
                self._state.trim_lists()
                await self._check_trailing_exit()
                await self._check_triple_barriers()
            except Exception:
                self._consecutive_errors += 1
                LOG.exception("Error in spread engine cycle (%d consecutive)", self._consecutive_errors)
                if self._consecutive_errors == 1 or self._consecutive_errors % 10 == 0:
                    self._state.push_alert(
                        "error",
                        f"Engine Error ({self._consecutive_errors} consecutive)",
                        "Repeated failures in the trading loop. Check logs.",
                        "engine",
                    )
                if self._consecutive_errors >= 50:
                    LOG.critical("50 consecutive errors — halting engine")
                    await self._trigger_risk_halt("engine error circuit breaker (50 consecutive failures)")
                    return

            await asyncio.sleep(0.05)

    def _clean_microprice(self, pair_key: str, ps: "PairState") -> float:
        """Microprice that excludes our own resting order from top-of-book.

        Kraken's book feed includes our orders.  On thin pairs our own sell
        can become the best ask, dragging the microprice toward the bid and
        causing us to quote sells *below* the real market.  Strip out our
        order qty from the top level before computing."""
        if not (ps.best_bid and ps.best_ask):
            return 0.0

        own_buy_price = 0.0
        own_buy_qty = 0.0
        own_sell_price = 0.0
        own_sell_qty = 0.0
        for o in self._state.active_orders.values():
            if o.pair_key != pair_key:
                continue
            if o.side == "buy":
                own_buy_price = o.price
                own_buy_qty += o.qty
            elif o.side == "sell":
                own_sell_price = o.price
                own_sell_qty += o.qty

        bid_price = ps.best_bid
        bid_vol = ps.bid_levels[0].volume if ps.bid_levels else 0.0
        ask_price = ps.best_ask
        ask_vol = ps.ask_levels[0].volume if ps.ask_levels else 0.0

        if own_buy_price > 0 and abs(bid_price - own_buy_price) < 1e-12:
            bid_vol = max(0.0, bid_vol - own_buy_qty)
            if bid_vol <= 0 and len(ps.bid_levels) > 1:
                bid_price = ps.bid_levels[1].price
                bid_vol = ps.bid_levels[1].volume

        if own_sell_price > 0 and abs(ask_price - own_sell_price) < 1e-12:
            ask_vol = max(0.0, ask_vol - own_sell_qty)
            if ask_vol <= 0 and len(ps.ask_levels) > 1:
                ask_price = ps.ask_levels[1].price
                ask_vol = ps.ask_levels[1].volume

        if bid_price <= 0 or ask_price <= 0:
            return ps.mid_price

        total = bid_vol + ask_vol
        if total <= 0:
            return (bid_price + ask_price) / 2.0
        return (bid_price * ask_vol + ask_price * bid_vol) / total

    async def _tick(self, pair_key: str) -> None:
        if self._state.risk_halted:
            return

        pc = self._config.pairs[pair_key]
        ps = self._state.pairs[pair_key]
        bot = self._config.bot

        if ps.pair_halted:
            return

        ref = self._clean_microprice(pair_key, ps)
        if ref == 0:
            return

        # --- WARMUP: observe market before placing any orders ---
        warmup_sec = getattr(bot, "warmup_sec", 30.0)
        if warmup_sec > 0:
            now = time.time()
            if ps.warmup_start_ts == 0.0:
                ps.warmup_start_ts = now
                LOG.info("WARMUP %s: observing market for %.0fs before placing orders", pair_key, warmup_sec)
            ps.warmup_prices.append(ref)
            # Keep a rolling window (warmup_sec worth of samples)
            max_samples = max(10, int(warmup_sec / (pc.cycle_ms / 1000.0 if hasattr(pc, "cycle_ms") and pc.cycle_ms else bot.default_cycle_ms / 1000.0)))
            if len(ps.warmup_prices) > max_samples * 3:
                ps.warmup_prices = ps.warmup_prices[-max_samples:]
            if not ps.warmup_complete:
                elapsed = now - ps.warmup_start_ts
                if elapsed < warmup_sec:
                    return
                ps.warmup_complete = True
                lo = min(ps.warmup_prices)
                hi = max(ps.warmup_prices)
                pct = getattr(bot, "warmup_buy_percentile", 25.0) / 100.0
                target_entry = lo + (hi - lo) * pct
                LOG.info(
                    "WARMUP %s complete: %d samples, range=%.6f–%.6f, "
                    "target entry (p%.0f)=%.6f",
                    pair_key, len(ps.warmup_prices), lo, hi,
                    pct * 100, target_entry,
                )

        if ps.last_book_update_ts > 0:
            book_age = time.time() - ps.last_book_update_ts
            if book_age > BOOK_STALE_SEC:
                stale_key = f"_stale_alerted_{pair_key}"
                if not getattr(self, stale_key, False):
                    setattr(self, stale_key, True)
                    self._state.push_alert(
                        "warning",
                        f"Stale Book: {pair_key}",
                        f"No book update for {book_age:.0f}s — quoting paused.",
                        "book_client",
                    )
                    LOG.warning(
                        "STALE BOOK %s: last update %.1fs ago — skipping tick",
                        pair_key, book_age,
                    )
                return
            else:
                stale_key = f"_stale_alerted_{pair_key}"
                if getattr(self, stale_key, False):
                    setattr(self, stale_key, False)

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

        # Session P&L floor (do not trip on replayed historical P&L at startup)
        min_pnl = getattr(bot, "min_total_pnl_usd", None)
        session_pnl = pnl - self._state.session_start_pnl
        if min_pnl is not None and session_pnl <= min_pnl:
            await self._trigger_risk_halt(
                reason=(
                    f"{CancelReason.SURVIVAL_PNL.value}: "
                    f"session_pnl={session_pnl:.4f} threshold={min_pnl:.2f}"
                ),
                log_level="warning",
            )
            return

        # Daily profit target
        daily_target = getattr(bot, "daily_profit_target_usd", None)
        daily_pnl = session_pnl
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

        # --- TRAILING STOP / TAKE PROFIT (per-pair realized PnL) ---
        pair_pnl = ps.pair_realized_pnl
        if pair_pnl > ps.trailing_high_pnl:
            ps.trailing_high_pnl = pair_pnl
        if bot.trailing_stop_enabled and ps.trailing_high_pnl > 0:
            ps.trailing_stop_active = True
            trail_floor = ps.trailing_high_pnl * (1.0 - max(0.0, bot.trailing_stop_pct) / 100.0)
            if pair_pnl <= trail_floor:
                if self._session_logger is not None and hasattr(self._session_logger, "log_trailing_stop"):
                    self._session_logger.log_trailing_stop(pair_key, "trailing_floor", pair_pnl)
                mgr = self._active_order_mgr()
                await mgr.cancel_all(pair_key)
                await self._emergency_liquidate(pair_key)
                ps.pair_halted = True
                LOG.warning("Pair %s halted after trailing stop", pair_key)
                return
        if bot.take_profit_usd is not None and pair_pnl >= float(bot.take_profit_usd):
            if self._session_logger is not None and hasattr(self._session_logger, "log_trailing_stop"):
                self._session_logger.log_trailing_stop(pair_key, "take_profit", pair_pnl)
            mgr = self._active_order_mgr()
            await mgr.cancel_all(pair_key)
            await self._emergency_liquidate(pair_key)
            ps.pair_halted = True
            LOG.warning("Pair %s halted after take profit", pair_key)
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

        if dynamic_widen_bps >= 3:
            LOG.info(
                "Dynamic widen %s: +%dbps (vol=%.5f vel=%.1fbps)",
                pair_key, dynamic_widen_bps, sigma, velocity,
            )

        level = ps.threat_level if isinstance(ps.threat_level, ThreatLevel) else ThreatLevel.CALM
        if level == ThreatLevel.HIGH:
            effective_spread_bps = round(effective_spread_bps * max(1.0, bot.threat_spread_multiplier))
            if self._session_logger is not None and hasattr(self._session_logger, "log_threat_action"):
                self._session_logger.log_threat_action(pair_key, level.value, "widen_spread")
        elif level == ThreatLevel.CRITICAL and bot.threat_quoting_pause:
            if self._session_logger is not None and hasattr(self._session_logger, "log_threat_action"):
                self._session_logger.log_threat_action(pair_key, level.value, "pause_quoting")
            return

        # --- BOT THREAT ADJUSTMENT (MEV detection) ---
        # Informational, not panicky. Modest widen on strong signals,
        # modest tighten when the book looks clean.
        if getattr(bot, "mev_detection_enabled", False) and ps.bot_threat is not None:
            bt = ps.bot_threat
            bt_threshold = getattr(bot, "mev_bot_score_threshold", 0.5)
            if bt.bot_activity_score > bt_threshold:
                effective_spread_bps = round(
                    effective_spread_bps * bt.recommended_spread_mult
                )
            elif bt.classification == "clean":
                tighten = getattr(bot, "mev_clean_tighten_scale", 0.10)
                effective_spread_bps = round(
                    effective_spread_bps * max(0.90, 1.0 - tighten)
                )
            effective_spread_bps = max(effective_spread_bps, floor_bps)

        # Clamp to ceiling
        effective_spread_bps = min(effective_spread_bps, bot.adaptive_spread_ceiling_bps)

        # Time-based quoting: widen spread during off-peak / normal hours
        if getattr(bot, "time_quoting_enabled", False):
            tm = self._time_of_day_multiplier()
            if tm != 1.0:
                effective_spread_bps = min(
                    int(effective_spread_bps * tm), bot.adaptive_spread_ceiling_bps,
                )

        half_spread = ref * (effective_spread_bps / 10_000)

        # --- INVENTORY SKEW (mean-reversion / rebalancing) ---
        inventory = ps.inventory_base
        max_inv = pc.max_inventory if pc.max_inventory > 0 else 0.0
        q = inventory / max_inv if max_inv > 0 else 0.0
        q = max(-1.0, min(1.0, q))
        skew = -q * max(0.0, float(pc.inventory_skew_scale)) * half_spread

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

        # Warmup bias: push buy price toward the low end of the observed range.
        # Only active for the first 10 minutes after warmup — after that the
        # learner and spread logic have enough data to stand on their own.
        warmup_expiry_sec = warmup_sec * 20  # ~10 min for 30s warmup
        if (
            ps.warmup_complete
            and ps.warmup_prices
            and ps.warmup_start_ts > 0
            and (time.time() - ps.warmup_start_ts) < warmup_expiry_sec
        ):
            warmup_lo = min(ps.warmup_prices)
            warmup_hi = max(ps.warmup_prices)
            warmup_range = warmup_hi - warmup_lo
            if warmup_range > 0 and ref > 0:
                pct = getattr(bot, "warmup_buy_percentile", 25.0) / 100.0
                target_buy = warmup_lo + warmup_range * pct
                if target_buy < buy_price:
                    buy_price = target_buy

        signal = self._btd_signals.get(pair_key)
        if signal is None:
            signal = BTDSignal(bot.btd_sma_short, bot.btd_sma_long)
            self._btd_signals[pair_key] = signal
        btd_active = bool(bot.btd_enabled and signal.update(ps.mid_price))
        ps.btd_active = btd_active
        if btd_active:
            step = max(1, int(bot.btd_step_bps)) / 10_000.0
            buy_price = buy_price * (1.0 - step)
            if self._session_logger is not None and hasattr(self._session_logger, "log_btd"):
                self._session_logger.log_btd(pair_key, True, "downtrend")

        tick = _PRICE_DECIMALS.get(pc.symbol, 8)
        buy_price = round(buy_price, tick)
        sell_price = round(sell_price, tick)

        # Per-fill cost basis floor: never sell below the cheapest barrier's breakeven.
        # Hysteresis: suppress sells when ref < min_sell, but only re-enable when
        # ref clears min_sell by 5 bps to avoid oscillation at the boundary.
        min_sell = self._inventory.min_profitable_sell_price(pair_key)
        suppress_sell_no_profit = False
        if min_sell > 0 and sell_price < min_sell:
            sell_price = round(min_sell, tick)
            was_suppressed = getattr(ps, "_sell_profit_suppressed", False)
            hysteresis = min_sell * (1.0 + 5 / 10_000)
            if ref < min_sell:
                suppress_sell_no_profit = True
                ps._sell_profit_suppressed = True
            elif was_suppressed and ref < hysteresis:
                suppress_sell_no_profit = True
            else:
                ps._sell_profit_suppressed = False

        self._state.current_spread_bps = effective_spread_bps

        # ── SUPPRESSION DECISION BLOCK ──────────────────────────────────
        # Priority hierarchy (highest wins):
        #   1. Anti-deadlock: NEVER suppress both sides simultaneously.
        #      If both would be suppressed, clear the weaker suppression.
        #   2. sell_paused_until (consecutive-loss cooldown, time-bounded)
        #   3. Ping-pong (alternating single-side suppression after fills)
        #   4. Momentum hold (suppress buys during sell cascades)
        #   5. suppress_sell_no_profit (sell price below cost basis)
        #
        # Ping-pong override: after a buy fill the intent is to sell, so
        # profitability suppression does NOT block sells — the sell price
        # is already floored to min_sell.

        pp_enabled = getattr(bot, "ping_pong_enabled", False)
        now_t = time.time()

        suppress_buy = self._apply_momentum_hold(pair_key)
        if pp_enabled and ps.last_fill_side == "buy":
            suppress_buy = True
        if ps.buy_cooldown_until > now_t:
            suppress_buy = True

        suppress_sell = ps.sell_paused_until > now_t
        if pp_enabled and ps.last_fill_side == "sell":
            suppress_sell = True
        if ps.sell_cooldown_until > now_t:
            suppress_sell = True
        pp_wants_sell = pp_enabled and ps.last_fill_side == "buy"
        if suppress_sell_no_profit and not pp_wants_sell:
            suppress_sell = True

        # Anti-deadlock guarantee: if both sides suppressed, force one open.
        # Ping-pong intention takes priority — if last fill was buy, allow sells;
        # if last fill was sell, allow buys.  Fallback: allow buys (accumulate).
        # Skip if either side is in a short fill cooldown — that resolves on its own.
        in_cooldown = ps.buy_cooldown_until > now_t or ps.sell_cooldown_until > now_t
        if suppress_buy and suppress_sell and not in_cooldown:
            both_stuck_key = f"_both_suppressed_since_{pair_key}"
            since = getattr(self, both_stuck_key, 0.0)
            if since == 0.0:
                setattr(self, both_stuck_key, time.time())
            elif time.time() - since > 30.0:
                # Stuck for 30s — clear the stale ping-pong latch
                LOG.warning(
                    "ANTI-DEADLOCK %s: both sides suppressed for %.0fs, "
                    "clearing last_fill_side (was %r)",
                    pair_key, time.time() - since, ps.last_fill_side,
                )
                ps.last_fill_side = ""
                suppress_buy = self._apply_momentum_hold(pair_key)
                suppress_sell = ps.sell_paused_until > time.time()
                setattr(self, both_stuck_key, 0.0)
            else:
                # Give short grace period, but still resolve now
                if pp_enabled and ps.last_fill_side == "buy":
                    suppress_sell = False
                elif pp_enabled and ps.last_fill_side == "sell":
                    suppress_buy = False
                else:
                    suppress_buy = False
        else:
            both_stuck_key = f"_both_suppressed_since_{pair_key}"
            setattr(self, both_stuck_key, 0.0)

        if suppress_sell:
            LOG.debug("TICK %s: sell suppressed (paused=%.0f pp=%s side=%s profit_ok=%s)",
                      pair_key, ps.sell_paused_until, pp_enabled,
                      ps.last_fill_side, not suppress_sell_no_profit)
        if suppress_buy:
            LOG.debug("TICK %s: buy suppressed (momentum=%s pp=%s side=%s)",
                      pair_key, self._apply_momentum_hold(pair_key),
                      pp_enabled, ps.last_fill_side)

        # Ping-pong enforcement: cancel resting orders on the suppressed side.
        # Only cancel orders that have been resting for >1 tick (avoid cancelling
        # orders we just placed this cycle in _smart_live_tick).
        if self._state.mode != "paper" and (suppress_buy or suppress_sell):
            mgr = self._active_order_mgr()
            min_age = getattr(bot, "default_cycle_ms", 3000) / 1000.0 * 1.5
            for oid, order in list(self._state.active_orders.items()):
                if order.pair_key != pair_key:
                    continue
                age = time.time() - order.placed_at
                if age < min_age:
                    continue
                if suppress_sell and order.side == "sell":
                    cancelled = await mgr.cancel_order(oid)
                    if cancelled:
                        LOG.info("PING-PONG cancel stale sell %s [%s]", pair_key, oid[:16])
                elif suppress_buy and order.side == "buy":
                    cancelled = await mgr.cancel_order(oid)
                    if cancelled:
                        LOG.info("PING-PONG cancel stale buy %s [%s]", pair_key, oid[:16])

        LOG.debug(
            "TICK %s: micro=%.6f buy=%.6f sell=%.6f spread=%dbps%s",
            pair_key, ref, buy_price, sell_price, effective_spread_bps,
            " [BOOTSTRAP]" if in_bootstrap else "",
        )

        if self._state.mode == "paper":
            await self._paper_tick(pair_key, buy_price, sell_price, suppress_buy, suppress_sell)
        else:
            await self._smart_live_tick(
                pair_key, buy_price, sell_price,
                half_spread, effective_spread_bps, suppress_buy, suppress_sell,
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
        self,
        pair_key: str,
        buy_price: float,
        sell_price: float,
        suppress_buy: bool = False,
        suppress_sell: bool = False,
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

        eff_spread = self._state.current_spread_bps or pc.spread_bps

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
                    spread_bps=eff_spread,
                    market_spread_bps=market_spread_bps,
                )
            ps.last_fill_side = "buy"
            if getattr(self._config.bot, "triple_barrier_enabled", False):
                stop_pct = getattr(self._config.bot, "tb_stop_pct", 2.0) / 100.0
                tp_pct = getattr(self._config.bot, "tb_tp_pct", 1.5) / 100.0
                hold_sec = getattr(self._config.bot, "tb_max_hold_sec", 3600.0)
                ps.pending_barriers.append(FillBarrier(
                    buy_price=filled_buy.price,
                    qty=filled_buy.qty,
                    stop_price=filled_buy.price * (1.0 - stop_pct),
                    tp_price=filled_buy.price * (1.0 + tp_pct),
                    max_hold_until=time.time() + hold_sec,
                ))

        if filled_sell:
            fee = filled_sell.price * filled_sell.qty * (fee_bps / 10_000)
            gross = self._inventory.gross_spread_on_sell(
                pair_key, filled_sell.qty, filled_sell.price, fee,
            )
            net = self._inventory.record_sell(
                pair_key, filled_sell.qty, filled_sell.price, fee,
            )
            ps.pair_realized_pnl += net
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
                    spread_bps=eff_spread,
                    market_spread_bps=market_spread_bps,
                )
            ps.last_fill_side = "sell"
            # Per-fill P&L: match against cheapest pending barrier
            per_fill_pnl = net
            if ps.pending_barriers:
                ps.pending_barriers.sort(key=lambda b: b.buy_price)
                matched = None
                for barrier in ps.pending_barriers:
                    if barrier.qty > 0:
                        matched = barrier
                        break
                if matched is not None:
                    sell_qty = min(filled_sell.qty, matched.qty)
                    per_fill_pnl = sell_qty * (filled_sell.price - matched.buy_price) - fee
                    matched.qty -= sell_qty
                    if matched.qty < 1.0:
                        ps.pending_barriers.remove(matched)
                    LOG.info(
                        "PER-FILL P&L %s: sold %.0f @ %.4f (bought @ %.4f) = $%.4f",
                        pair_key, sell_qty, filled_sell.price, matched.buy_price, per_fill_pnl,
                    )

            # Consecutive loss tracking uses per-fill P&L
            bot = self._config.bot
            if per_fill_pnl < 0:
                ps.consecutive_loss_count += 1
                halt_n = getattr(bot, "consecutive_loss_halt_count", 3)
                pause_s = getattr(bot, "consecutive_loss_pause_sec", 300.0)
                if ps.consecutive_loss_count >= halt_n:
                    ps.sell_paused_until = time.time() + pause_s
                    ps.consecutive_loss_count = 0
                    LOG.warning(
                        "CONSECUTIVE LOSS HALT %s: pausing sells for %.0fs "
                        "after %d consecutive losses",
                        pair_key, pause_s, halt_n,
                    )
                    self._state.push_alert(
                        "warning",
                        f"Consecutive Loss Pause: {pair_key}",
                        f"Pausing sells for {pause_s:.0f}s after {halt_n} losing sells in a row.",
                        "risk_engine",
                    )
            else:
                ps.consecutive_loss_count = 0

        levels = max(1, pc.order_levels)
        buy_prices, sell_prices = self._level_prices(buy_price, sell_price, pc)

        cur_spread_bps = getattr(self._state, "current_spread_bps", pc.spread_bps)
        half_spread = ps.mid_price * (cur_spread_bps / 10_000)
        last_reason = ""
        for oid, o in list(self._state.active_orders.items()):
            if o.pair_key != pair_key:
                continue
            nearest_target = buy_prices[0] if o.side == "buy" else sell_prices[0]
            reason = self._should_cancel_order(o, nearest_target, half_spread, ps)
            if reason is not None:
                await self._paper_mgr.cancel_order(oid)
                last_reason = reason
        if last_reason:
            self._state.last_cancel_reason[pair_key] = last_reason
        else:
            self._state.last_cancel_reason.pop(pair_key, None)

        num_buys = sum(
            1 for o in self._state.active_orders.values()
            if o.pair_key == pair_key and o.side == "buy"
        )
        num_sells = sum(
            1 for o in self._state.active_orders.values()
            if o.pair_key == pair_key and o.side == "sell"
        )

        slots_buy = levels - num_buys
        if not suppress_buy and slots_buy > 0 and self._inventory.can_buy(pair_key):
            total_buy_qty = self._inventory.affordable_buy_qty(pair_key, pc.order_size)
            if getattr(self._config.bot, "time_quoting_enabled", False) and total_buy_qty > 0:
                hour_utc = time.gmtime().tm_hour
                peak_s = getattr(self._config.bot, "time_peak_start_utc", 13)
                peak_e = getattr(self._config.bot, "time_peak_end_utc", 21)
                if hour_utc < 8 and not (peak_s <= hour_utc < peak_e):
                    size_pct = getattr(self._config.bot, "time_offpeak_size_pct", 50.0) / 100.0
                    total_buy_qty *= size_pct
            per_level = total_buy_qty
            existing_buy_prices = {
                round(o.price, 8) for o in self._state.active_orders.values()
                if o.pair_key == pair_key and o.side == "buy"
            }
            placed = 0
            for bp in buy_prices:
                if placed >= slots_buy:
                    break
                if round(bp, 8) in existing_buy_prices:
                    continue
                if per_level >= 1.0:
                    await self._paper_mgr.place_order(
                        pair_key, pc.symbol, "buy", bp, per_level,
                    )
                    placed += 1

        slots_sell = levels - num_sells
        if not suppress_sell and slots_sell > 0 and self._inventory.can_sell(pair_key):
            sell_size = pc.sell_order_size if pc.sell_order_size is not None else pc.order_size
            committed_sells = sum(
                o.qty for o in self._state.active_orders.values()
                if o.pair_key == pair_key and o.side == "sell"
            )
            available_base = max(0.0, ps.inventory_base - committed_sells)
            total_sell_qty = min(sell_size, available_base)
            if getattr(self._config.bot, "time_quoting_enabled", False) and total_sell_qty > 0:
                hour_utc = time.gmtime().tm_hour
                peak_s = getattr(self._config.bot, "time_peak_start_utc", 13)
                peak_e = getattr(self._config.bot, "time_peak_end_utc", 21)
                if hour_utc < 8 and not (peak_s <= hour_utc < peak_e):
                    size_pct = getattr(self._config.bot, "time_offpeak_size_pct", 50.0) / 100.0
                    total_sell_qty *= size_pct
            per_level = total_sell_qty
            existing_sell_prices = {
                round(o.price, 8) for o in self._state.active_orders.values()
                if o.pair_key == pair_key and o.side == "sell"
            }
            placed = 0
            for sp in sell_prices:
                if placed >= slots_sell:
                    break
                if round(sp, 8) in existing_sell_prices:
                    continue
                if per_level >= 1.0:
                    await self._paper_mgr.place_order(
                        pair_key, pc.symbol, "sell", sp, per_level,
                    )
                    placed += 1

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

        # Hanging orders: protect opposite-side order from stale-age cancel,
        # but still allow repricing when the target price drifts away.
        bot = self._config.bot
        is_hanging = False
        if getattr(bot, "hanging_orders_enabled", False) and ps.last_fill_side:
            if order.side != ps.last_fill_side and ps.mid_price > 0:
                distance_pct = abs(order.price - ps.mid_price) / ps.mid_price * 100
                cancel_pct = getattr(bot, "hanging_orders_cancel_pct", 3.0)
                if distance_pct <= cancel_pct:
                    is_hanging = True

        if not is_hanging and age > STALE_ORDER_SEC:
            return CancelReason.STALE.value

        if drift > drift_threshold:
            # No-op guard: drift exists pre-rounding but maps to the same exchange tick.
            # Don't cancel — the replacement order would land at an identical price.
            pc = self._config.pairs.get(order.pair_key)
            if pc is not None:
                tick = _PRICE_DECIMALS.get(pc.symbol, 8)
                if round(order.price, tick) == round(target_price, tick):
                    return None
            return CancelReason.PRICE_DRIFT.value

        return None

    def _level_prices(
        self, base_buy: float, base_sell: float, pc: "PairConfig",
    ) -> tuple[list[float], list[float]]:
        """Compute staggered prices for multi-level quoting.

        Level 0 = base_buy/base_sell (closest to mid).
        Each subsequent level steps further from mid by level_step_bps.
        """
        levels = max(1, pc.order_levels)
        step_frac = pc.level_step_bps / 10_000.0
        tick = _PRICE_DECIMALS.get(pc.symbol, 8)
        buys = [round(base_buy * (1.0 - i * step_frac), tick) for i in range(levels)]
        sells = [round(base_sell * (1.0 + i * step_frac), tick) for i in range(levels)]
        return buys, sells

    async def _smart_live_tick(
        self,
        pair_key: str,
        buy_price: float,
        sell_price: float,
        half_spread: float,
        effective_spread_bps: int,
        suppress_buy: bool = False,
        suppress_sell: bool = False,
    ) -> None:
        """Live mode with smart cancellation and multi-level quoting."""
        reject_ts = getattr(self._state, "last_order_reject_ts", 0.0)
        if reject_ts:
            reject_count = getattr(self._state, "order_reject_count", 0)
            backoff_sec = min(60.0, 5.0 * (1 + reject_count // 3))
            if time.time() - reject_ts < backoff_sec:
                return

        mgr = self._active_order_mgr()
        pc = self._config.pairs[pair_key]
        ps = self._state.pairs[pair_key]
        levels = max(1, pc.order_levels)
        buy_prices, sell_prices = self._level_prices(buy_price, sell_price, pc)

        existing_buys: list["ActiveOrder"] = []
        existing_sells: list["ActiveOrder"] = []
        to_cancel: list[tuple[str, str]] = []

        reprice_threshold = max(pc.level_step_bps * 4, 120) / 10_000.0

        for oid, order in list(self._state.active_orders.items()):
            if order.pair_key != pair_key:
                continue
            nearest_target = buy_prices[0] if order.side == "buy" else sell_prices[0]
            reason = self._should_cancel_order(order, nearest_target, half_spread, ps)
            if reason is None and nearest_target > 0:
                target_drift = abs(order.price - nearest_target) / nearest_target
                if target_drift > reprice_threshold:
                    reason = "reprice: target shifted"
            if reason is not None:
                to_cancel.append((oid, reason))
            elif order.side == "buy":
                existing_buys.append(order)
            elif order.side == "sell":
                existing_sells.append(order)

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

        twap_task = self._twap_tasks.get(pair_key)
        if twap_task is not None and twap_task.done():
            self._twap_tasks.pop(pair_key, None)
            twap_task = None

        # --- Place buy levels ---
        slots_to_fill_buy = levels - len(existing_buys)
        if not suppress_buy and slots_to_fill_buy > 0 and self._inventory.can_buy(pair_key):
            raw_total_qty = pc.order_size * (self._config.bot.btd_size_multiplier if ps.btd_active else 1.0)
            total_buy_qty = self._inventory.affordable_buy_qty(pair_key, raw_total_qty)
            if getattr(self._config.bot, "time_quoting_enabled", False) and total_buy_qty > 0:
                hour_utc = time.gmtime().tm_hour
                peak_s = getattr(self._config.bot, "time_peak_start_utc", 13)
                peak_e = getattr(self._config.bot, "time_peak_end_utc", 21)
                if hour_utc < 8 and not (peak_s <= hour_utc < peak_e):
                    size_pct = getattr(self._config.bot, "time_offpeak_size_pct", 50.0) / 100.0
                    total_buy_qty *= size_pct
            per_level_buy = total_buy_qty
            existing_buy_prices = {round(o.price, 8) for o in existing_buys}
            placed = 0
            for bp in buy_prices:
                if placed >= slots_to_fill_buy:
                    break
                if round(bp, 8) in existing_buy_prices:
                    continue
                qty = per_level_buy
                if qty < 1.0:
                    continue
                twap_threshold = pc.twap_threshold_qty if pc.twap_threshold_qty is not None else qty
                if self._config.bot.twap_enabled and twap_task is None and qty >= twap_threshold:
                    _bp, _qty = bp, qty
                    async def _run_twap(p=_bp, q=_qty) -> None:
                        try:
                            twap_id = await self._twap.execute(
                                pair_key=pair_key, symbol=pc.symbol, side="buy",
                                price=p, qty=q,
                                slice_count=self._config.bot.twap_slice_count,
                                duration_sec=self._config.bot.twap_duration_sec,
                                place_fn=mgr.place_order,
                            )
                            if self._session_logger is not None and hasattr(self._session_logger, "log_twap"):
                                self._session_logger.log_twap(pair_key, twap_id, "buy", self._config.bot.twap_slice_count)
                        except Exception:
                            LOG.exception("TWAP task failed for %s", pair_key)
                    self._twap_tasks[pair_key] = asyncio.create_task(_run_twap(), name=f"twap-{pair_key}")
                else:
                    await mgr.place_order(pair_key, pc.symbol, "buy", bp, qty)
                placed += 1

        # --- Place sell levels ---
        slots_to_fill_sell = levels - len(existing_sells)
        if not suppress_sell and slots_to_fill_sell > 0 and self._inventory.can_sell(pair_key):
            sell_size = pc.sell_order_size if pc.sell_order_size is not None else pc.order_size
            committed_sells = sum(
                o.qty for o in self._state.active_orders.values()
                if o.pair_key == pair_key and o.side == "sell"
            )
            available_base = max(0.0, ps.inventory_base - committed_sells)
            total_sell_qty = min(sell_size, available_base)

            # Cap total sell exposure to cheap (profitable) barrier qty.
            # This prevents selling legacy inventory at a loss — only sell
            # what was recently acquired at or below current market levels.
            min_sell_price = self._inventory.min_profitable_sell_price(pair_key)
            if min_sell_price > 0 and ps.pending_barriers:
                cheap_qty = sum(
                    b.qty for b in ps.pending_barriers
                    if b.buy_price <= min_sell_price and b.qty > 0
                )
                cheap_qty = max(0.0, cheap_qty - committed_sells)
                if cheap_qty < total_sell_qty:
                    LOG.debug(
                        "SELL CAP %s: capping from %.0f to %.0f (cheap barrier qty)",
                        pair_key, total_sell_qty, cheap_qty,
                    )
                    total_sell_qty = cheap_qty

            if getattr(self._config.bot, "time_quoting_enabled", False) and total_sell_qty > 0:
                hour_utc = time.gmtime().tm_hour
                peak_s = getattr(self._config.bot, "time_peak_start_utc", 13)
                peak_e = getattr(self._config.bot, "time_peak_end_utc", 21)
                if hour_utc < 8 and not (peak_s <= hour_utc < peak_e):
                    size_pct = getattr(self._config.bot, "time_offpeak_size_pct", 50.0) / 100.0
                    total_sell_qty *= size_pct
            # Ensure per-level qty meets Kraken's minimum (default 75 for most alts).
            # Reduce levels rather than placing sub-minimum orders.
            min_order_qty = getattr(pc, "min_order_qty", 75.0)
            effective_sell_levels = slots_to_fill_sell
            if total_sell_qty > 0 and min_order_qty > 0:
                max_levels = int(total_sell_qty / min_order_qty)
                effective_sell_levels = max(1, min(slots_to_fill_sell, max_levels))
            per_level_sell = total_sell_qty / max(1, effective_sell_levels)
            if per_level_sell < min_order_qty:
                per_level_sell = 0.0
            existing_sell_prices = {round(o.price, 8) for o in existing_sells}
            placed = 0
            for sp in sell_prices:
                if placed >= effective_sell_levels:
                    break
                if round(sp, 8) in existing_sell_prices:
                    continue
                qty = per_level_sell
                if qty < 1.0:
                    continue
                await mgr.place_order(pair_key, pc.symbol, "sell", sp, qty)
                placed += 1

    def _time_of_day_multiplier(self) -> float:
        """Spread multiplier based on UTC hour for time-based quoting."""
        bot = self._config.bot
        hour = time.gmtime().tm_hour
        peak_start = getattr(bot, "time_peak_start_utc", 13)
        peak_end = getattr(bot, "time_peak_end_utc", 21)
        if peak_start <= hour < peak_end:
            return 1.0
        if hour < 8:
            return float(getattr(bot, "time_offpeak_multiplier", 1.35))
        return float(getattr(bot, "time_normal_multiplier", 1.15))

    async def _check_triple_barriers(self) -> None:
        """Fire stop-loss / take-profit / time-limit sells for pending fill barriers."""
        bot = self._config.bot
        if not getattr(bot, "triple_barrier_enabled", False):
            return
        if self._state.risk_halted:
            return

        now = time.time()
        mgr = self._active_order_mgr()

        for pair_key in self._config.pair_keys_for_trading():
            ps = self._state.pairs.get(pair_key)
            if ps is None or not ps.pending_barriers or ps.pair_halted:
                continue

            mid = ps.mid_price
            if mid <= 0:
                continue

            pc = self._config.pairs.get(pair_key)
            if pc is None:
                continue

            triggered: list[FillBarrier] = []
            for barrier in list(ps.pending_barriers):
                reason = ""
                sell_price = 0.0
                post_only = False

                if mid <= barrier.stop_price:
                    reason = f"stop_loss mid={mid:.4f} <= stop={barrier.stop_price:.4f}"
                    sell_price = ps.best_bid if ps.best_bid > 0 else mid
                elif mid >= barrier.tp_price:
                    reason = f"take_profit mid={mid:.4f} >= tp={barrier.tp_price:.4f}"
                    sell_price = barrier.tp_price
                    post_only = True
                elif now >= barrier.max_hold_until:
                    held_min = (now - barrier.created_at) / 60
                    reason = f"time_limit held={held_min:.0f}m"
                    sell_price = ps.best_bid if ps.best_bid > 0 else mid

                if reason and sell_price > 0:
                    tick = _PRICE_DECIMALS.get(pc.symbol, 8)
                    sell_price = round(sell_price, tick)
                    committed = sum(
                        o.qty for o in self._state.active_orders.values()
                        if o.pair_key == pair_key and o.side == "sell"
                    )
                    available = max(0.0, ps.inventory_base - committed)
                    qty = min(barrier.qty, available)
                    if qty >= 1.0:
                        LOG.warning(
                            "TRIPLE BARRIER %s: %s — selling %.0f @ %.4f",
                            pair_key, reason, qty, sell_price,
                        )
                        self._state.push_alert(
                            "warning",
                            f"Triple Barrier: {pair_key}",
                            f"{reason} — selling {qty:.0f}",
                            "triple_barrier",
                        )
                        await mgr.place_order(
                            pair_key, pc.symbol, "sell", sell_price, qty,
                            post_only=post_only,
                        )
                    triggered.append(barrier)

            for b in triggered:
                try:
                    ps.pending_barriers.remove(b)
                except ValueError:
                    pass

    async def _check_trailing_exit(self) -> None:
        """Monitor price for the trailing exit feature. Sells all inventory
        when price drops trail_pct from the observed peak."""
        s = self._state
        if not s.trailing_exit_active or not s.trailing_exit_pair:
            return
        pk = s.trailing_exit_pair
        ps = s.pairs.get(pk)
        if ps is None or ps.mid_price <= 0:
            return

        mid = ps.mid_price

        if mid > s.trailing_exit_peak_price:
            s.trailing_exit_peak_price = mid

        trigger_price = s.trailing_exit_peak_price * (1 - s.trailing_exit_trail_pct / 100)
        if mid <= trigger_price and not s.trailing_exit_triggered:
            s.trailing_exit_triggered = True
            qty = ps.inventory_base
            if qty <= 0:
                LOG.info("Trailing exit triggered for %s but no inventory to sell", pk)
                s.trailing_exit_active = False
                return

            sell_price = ps.best_bid
            if sell_price <= 0:
                LOG.warning("Trailing exit triggered for %s but no bid available", pk)
                return

            LOG.warning(
                "TRAILING EXIT %s: peak=$%.4f, trigger=$%.4f, mid=$%.4f — selling %.2f @ $%.4f",
                pk, s.trailing_exit_peak_price, trigger_price, mid, qty, sell_price,
            )
            s.push_alert(
                "warning",
                f"Trailing Exit Triggered: {pk}",
                f"Peak ${s.trailing_exit_peak_price:.4f} → dropped to ${mid:.4f} ({s.trailing_exit_trail_pct}% trail). Selling {qty:.0f} units.",
                "trailing_exit",
            )

            pc = self._config.pairs.get(pk)
            if pc is None:
                return
            mgr = self._active_order_mgr()
            # Cancel any existing orders first
            for oid, order in list(self._state.active_orders.items()):
                if order.pair_key == pk:
                    await mgr.cancel_order(oid)
            # Place the sell
            await mgr.place_order(pk, pc.symbol, "sell", sell_price, qty, post_only=False)
            s.trailing_exit_active = False

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
            ps.pair_realized_pnl += net
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
        inventory_skew_scale: float | None = None,
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
        if inventory_skew_scale is not None:
            pc.inventory_skew_scale = max(0.0, float(inventory_skew_scale))
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
            "skew=%.3f, floor=%s, bootstrap=%s/%s",
            pair_key,
            pc.spread_bps,
            pc.order_size,
            pc.max_inventory,
            pc.inventory_skew_scale,
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
