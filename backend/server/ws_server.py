"""WebSocket server — serves dashboard and pushes state updates."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web, WSMsgType

from .runtime import BotRuntime

if TYPE_CHECKING:
    from .config import AppConfig
    from .spread_engine import SpreadEngine
    from .state import BotState

LOG = logging.getLogger(__name__)
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


class DashboardServer:
    def __init__(
        self,
        state: BotState,
        config: AppConfig,
        engine: SpreadEngine,
        runtime: BotRuntime,
    ) -> None:
        self._state = state
        self._config = config
        self._engine = engine
        self._runtime = runtime
        self._app = web.Application()
        self._clients: set[web.WebSocketResponse] = set()
        self._push_task: asyncio.Task | None = None
        state._alert_fn = self.broadcast_alert
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/ws", self._ws_handler)
        self._app.router.add_get("/", self._index_handler)
        self._app.router.add_get("/favicon.svg", self._favicon_handler)
        self._app.router.add_static("/assets", FRONTEND_DIR / "assets", show_index=False)
        self._app.router.add_static("/static", FRONTEND_DIR, show_index=False)

    async def _index_handler(self, request: web.Request) -> web.Response:
        index_path = FRONTEND_DIR / "index.html"
        resp = web.FileResponse(index_path)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    async def _favicon_handler(self, request: web.Request) -> web.Response:
        favicon_path = FRONTEND_DIR / "favicon.svg"
        if favicon_path.exists():
            return web.FileResponse(favicon_path)
        return web.Response(status=204)

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._clients.add(ws)
        LOG.info("Dashboard client connected (%d total)", len(self._clients))

        try:
            await ws.send_json({"type": "snapshot", "data": self._state.snapshot()})
            await ws.send_json({"type": "config", "data": self._config_snapshot()})

            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_message(ws, msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            self._clients.discard(ws)
            LOG.info("Dashboard client disconnected (%d remaining)", len(self._clients))

        return ws

    async def _handle_message(self, ws: web.WebSocketResponse, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict):
            return

        action = msg.get("action")
        LOG.info("WS recv action=%s keys=%s", action, list(msg.keys()))
        try:
            await self._dispatch_action(ws, msg, action)
        except Exception as exc:
            LOG.exception("Error handling action=%s", action)
            await self.broadcast_alert(
                "error",
                f"Server Error: {action}",
                str(exc)[:200],
                "ws_server",
            )

    async def _dispatch_action(self, ws: web.WebSocketResponse, msg: dict, action: str | None) -> None:

        if action == "update_config":
            pair_key = msg.get("pair_key")
            if pair_key:
                self._engine.update_pair_config(
                    pair_key,
                    spread_bps=msg.get("spread_bps"),
                    order_size=msg.get("order_size"),
                    max_inventory=msg.get("max_inventory"),
                    cycle_ms=msg.get("cycle_ms"),
                    inventory_skew_scale=msg.get("inventory_skew_scale"),
                    spread_floor_bps=msg.get("spread_floor_bps"),
                    bootstrap_half_spread_bps=msg.get("bootstrap_half_spread_bps"),
                    bootstrap_until_sell_trades=msg.get("bootstrap_until_sell_trades"),
                    clear_bootstrap=bool(msg.get("clear_bootstrap_config", False)),
                )
                await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "set_mode":
            new_mode = msg.get("mode", "paper")
            if new_mode not in ("paper", "live"):
                await ws.send_json({"type": "error", "message": f"Invalid mode: {new_mode}"})
                return
            if new_mode == "live":
                if not self._config.api_key:
                    await ws.send_json({
                        "type": "error",
                        "message": "No API keys configured. Add them to .env first.",
                    })
                    return
                try:
                    await self._runtime.ensure_live()
                except Exception as e:
                    LOG.exception("Failed to start live trading")
                    await ws.send_json({
                        "type": "error",
                        "message": f"Live mode failed: {e!s}",
                    })
                    return
            self._config.mode = new_mode
            self._state.mode = new_mode
            self._runtime.pnl.switch_mode(new_mode)
            if self._runtime.learner is not None:
                self._runtime.learner.switch_mode(new_mode)
            if self._runtime.optimizer is not None:
                self._runtime.optimizer.switch_mode(new_mode)
            if new_mode == "paper" and self._runtime.live_mgr:
                await self._runtime.live_mgr.cancel_all()
            LOG.info("Mode switched to %s", new_mode)
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "start":
            if self._state.running:
                LOG.info("START pressed but engine already running")
                await ws.send_json({"type": "error", "message": "Engine is already running"})
                await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})
            else:
                import time as _time
                if self._state.risk_halted:
                    self._state.risk_halted = False
                    self._state.risk_halt_reason = ""
                    LOG.info("Risk halt cleared on START")
                if self._config.mode == "live" and self._config.api_key and self._runtime.live_mgr is not None:
                    self._runtime.inventory.load_cost_basis()
                    self._runtime.inventory.load_barriers()
                    for key in self._config.pairs:
                        self._runtime.inventory.seed_cost_basis_from_mid(key)
                    LOG.info("Cost basis loaded (persisted or mid-seeded)")
                    # Auto-reseed any pair whose sell floor is >N% above current market mid.
                    # Detects underwater barriers on startup so the operator doesn't have to.
                    auto_pct = self._config.bot.barrier_auto_reseed_pct
                    if auto_pct > 0:
                        inv = self._runtime.inventory
                        for key in self._config.pairs:
                            ps = self._state.pairs.get(key)
                            if ps is None or not ps.pending_barriers:
                                continue
                            if ps.mid_price <= 0:
                                continue
                            min_sell = inv.min_profitable_sell_price(key)
                            threshold = ps.mid_price * (1.0 + auto_pct / 100.0)
                            if min_sell > threshold:
                                LOG.warning(
                                    "Auto-reseeding %s: sell_floor=%.5f > mid=%.5f + %.0f%% "
                                    "— position underwater, resetting cost basis to current market",
                                    key, min_sell, ps.mid_price, auto_pct,
                                )
                                inv.reseed_barriers_at_mid(key)
                                learner = self._runtime.learner
                                if learner is not None:
                                    learner.reset_pair(key)
                await self._engine.start()
                sl = self._runtime.session_logger
                if sl is not None:
                    sl.log_session_start()
                if self._state.session_start_ts == 0.0:
                    self._state.session_start_ts = _time.time()
                    self._state.session_start_pnl = self._state.total_pnl
                LOG.info("Engine started via dashboard")
                await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})

        elif action == "stop":
            if self._state.running:
                await self._engine.stop()

        elif action == "kill":
            await self._engine.kill()
            await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})

        elif action == "update_risk":
            b = self._config.bot
            try:
                if "daily_profit_target_usd" in msg:
                    v = msg["daily_profit_target_usd"]
                    b.daily_profit_target_usd = None if v is None or v == "" else float(v)
                if "daily_loss_limit_usd" in msg:
                    v = msg["daily_loss_limit_usd"]
                    b.daily_loss_limit_usd = None if v is None or v == "" else float(v)
                if "max_drawdown_pct" in msg:
                    v = msg["max_drawdown_pct"]
                    b.max_drawdown_pct = None if v is None or v == "" else float(v)
                if "min_total_pnl_usd" in msg:
                    v = msg["min_total_pnl_usd"]
                    b.min_total_pnl_usd = None if v is None or v == "" else float(v)
            except (ValueError, TypeError) as e:
                await ws.send_json({"type": "error", "message": f"Invalid risk value: {e}"})
                return
            # Explicit resume control: updating limits alone does not auto-resume.
            if bool(msg.get("resume_risk_halt", False)):
                self._state.risk_halted = False
                self._state.risk_halt_reason = ""
            LOG.info(
                "Risk limits updated: daily_target=%s daily_loss=%s drawdown=%s "
                "pnl_floor=%s resume=%s",
                b.daily_profit_target_usd, b.daily_loss_limit_usd,
                b.max_drawdown_pct, b.min_total_pnl_usd, bool(msg.get("resume_risk_halt", False)),
            )
            await self._broadcast({"type": "config", "data": self._config_snapshot()})


        elif action == "update_trailing":
            b = self._config.bot
            if "trailing_stop_enabled" in msg:
                b.trailing_stop_enabled = bool(msg.get("trailing_stop_enabled", False))
            if "trailing_stop_pct" in msg:
                b.trailing_stop_pct = float(msg.get("trailing_stop_pct", b.trailing_stop_pct))
            if "take_profit_usd" in msg:
                v = msg.get("take_profit_usd")
                b.take_profit_usd = None if v is None or v == "" else float(v)
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "set_active_pair":
            pair_key = msg.get("pair_key", "")
            if pair_key in self._config.pairs:
                self._state.active_pair_key = pair_key

        elif action == "toggle_pair":
            pair_key = msg.get("pair_key", "")
            enabled = bool(msg.get("enabled", True))
            if pair_key not in self._config.pairs:
                return
            ep = self._config.bot.enabled_pairs
            if ep is None:
                ep = list(self._config.pairs.keys())
                self._config.bot.enabled_pairs = ep
            if enabled and pair_key not in ep:
                ep.append(pair_key)
                LOG.info("Pair %s ENABLED", pair_key)
            elif not enabled and pair_key in ep:
                ep.remove(pair_key)
                LOG.info("Pair %s DISABLED", pair_key)
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "set_adaptive_tuning":
            self._config.bot.adaptive_tuning = bool(msg.get("enabled", False))
            LOG.info("Adaptive spread tuning %s", "ON" if self._config.bot.adaptive_tuning else "OFF")
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "smart_defaults":
            pair_key = msg.get("pair_key", "")
            defaults = self._engine.smart_defaults(pair_key)
            if defaults is not None:
                await ws.send_json({
                    "type": "smart_defaults",
                    "pair_key": pair_key,
                    "data": defaults,
                })

        elif action == "update_systems":
            b = self._config.bot
            _BOOL = lambda v: bool(v)
            _INT  = lambda v: int(float(v))
            _FLOAT = lambda v: float(v)
            _OPT_FLOAT = lambda v: None if v is None or v == "" else float(v)
            ALLOWED: dict[str, object] = {
                # Strategy Learner
                "learner_enabled": _BOOL,
                "learner_interval_sec": _INT,
                "learner_min_samples": _INT,
                "learner_max_daily_adjustments": _INT,
                "learner_lookback_max_age_sec": _INT,
                "learner_loss_lookback_sells": _INT,
                "learner_widen_on_avg_loss": _BOOL,
                # Walk-Forward Optimizer
                "optimizer_enabled": _BOOL,
                "optimizer_interval_sec": _INT,
                "optimizer_train_hours": _FLOAT,
                "optimizer_holdout_pct": _FLOAT,
                "optimizer_min_fills": _INT,
                "optimizer_max_delta_spread_bps": _INT,
                "optimizer_max_delta_size_pct": _FLOAT,
                "optimizer_objective": str,
                # Adaptive Spread Tuner
                "adaptive_tuning": _BOOL,
                "adaptive_target_win_pct": _FLOAT,
                "adaptive_win_band_pct": _FLOAT,
                "adaptive_spread_step_bps": _INT,
                "adaptive_spread_floor_bps": _INT,
                "adaptive_spread_ceiling_bps": _INT,
                "adaptive_min_sample_sells": _INT,
                "adaptive_lookback_sells": _INT,
                "adaptive_interval_sec": _INT,
                # Momentum Hold / Cascade Protection
                "momentum_hold_sells": _INT,
                "momentum_hold_sec": _INT,
                "fill_cooldown_sec": _FLOAT,
                # Buy-the-Dip
                "btd_enabled": _BOOL,
                "btd_step_bps": _INT,
                "btd_size_multiplier": _FLOAT,
                "btd_sma_short": _INT,
                "btd_sma_long": _INT,
                "btd_levels": _INT,
                # Trailing Stop / Take Profit
                "trailing_stop_enabled": _BOOL,
                "trailing_stop_pct": _FLOAT,
                "take_profit_usd": _OPT_FLOAT,
                # OCO
                "oco_enabled": _BOOL,
                "oco_stop_bps": _INT,
                "oco_tp_bps": _INT,
                # TWAP
                "twap_enabled": _BOOL,
                "twap_slice_count": _INT,
                "twap_duration_sec": _INT,
                # Threat Detection
                "threat_quoting_pause": _BOOL,
                "threat_velocity_bps": _INT,
                "threat_critical_velocity_bps": _INT,
                "threat_spread_multiplier": _FLOAT,
                "threat_imbalance_threshold": _FLOAT,
                "threat_spread_blowout_ratio": _FLOAT,
                # Risk Limits
                "min_total_pnl_usd": _OPT_FLOAT,
                "daily_loss_limit_usd": _OPT_FLOAT,
                "daily_profit_target_usd": _OPT_FLOAT,
                "max_drawdown_pct": _OPT_FLOAT,
                "per_trade_profitability": _BOOL,
                # Misc
                "depeg_threshold_bps": _INT,
                "min_quote_half_spread_bps": _INT,
                "pain_floor_decay_hours": _FLOAT,
                "decay_start_sec": _INT,
                "decay_interval_sec": _INT,
                "decay_step_bps": _INT,
                # MEV / Bot Detection
                "mev_detection_enabled": _BOOL,
                "mev_bot_widen_scale": _FLOAT,
                "mev_arb_widen_scale": _FLOAT,
                "mev_clean_tighten_scale": _FLOAT,
                "mev_bot_score_threshold": _FLOAT,
                "mev_detector_window_sec": _FLOAT,
            }
            applied: list[str] = []
            errors: list[str] = []
            for key, coerce in ALLOWED.items():
                if key not in msg:
                    continue
                try:
                    setattr(b, key, coerce(msg[key]))  # type: ignore[operator]
                    applied.append(key)
                except (ValueError, TypeError, AttributeError) as exc:
                    errors.append(f"{key}: {exc}")
            if errors:
                LOG.warning("update_systems errors: %s", errors)
            if applied:
                LOG.info("update_systems applied: %s", applied)
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "apply_preset":
            from .presets import apply_preset, PRESETS
            preset_name = msg.get("preset", "")
            pair_key = msg.get("pair_key", "")
            if preset_name not in PRESETS:
                await ws.send_json({"type": "error", "message": f"Unknown preset: {preset_name}"})
                return
            if pair_key not in self._config.pairs:
                await ws.send_json({"type": "error", "message": f"Unknown pair: {pair_key}"})
                return
            applied = apply_preset(preset_name, pair_key, self._config, self._engine)
            LOG.info("Preset '%s' applied to %s: %s", preset_name, pair_key, applied)
            await self._broadcast({"type": "config", "data": self._config_snapshot()})
            await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})

        elif action == "reset_pnl":
            import time as _time
            s = self._state
            scope = msg.get("scope", "session")
            if scope == "all":
                s.total_pnl = 0.0
                s.total_trades = 0
                s.total_wins = 0
                s.fill_event_count = 0
                s.spread_captured = 0.0
                s.pnl_curve.clear()
                s.recent_fills.clear()
                s.peak_pnl = 0.0
                s.session_start_pnl = 0.0
                s.session_start_ts = _time.time()
                s.risk_halted = False
                s.risk_halt_reason = ""
                for ps in s.pairs.values():
                    ps.pair_realized_pnl = 0.0
                    ps.trailing_high_pnl = 0.0
                    ps.last_fill_side = ""
                    ps.consecutive_loss_count = 0
                    ps.sell_paused_until = 0.0
                    ps.pending_barriers.clear()
                    ps._sell_profit_suppressed = False
                    ps.warmup_start_ts = 0.0
                    ps.warmup_prices.clear()
                    ps.warmup_complete = False
                # Persist cleared barriers + cost basis so load_barriers() on next START
                # falls through to the mid-price bootstrap instead of reloading old data.
                inv = self._runtime.inventory
                inv.save_barriers()
                inv.save_cost_basis()
                # Reset learner pain floors so spread can explore below the old floor.
                learner = self._runtime.learner
                if learner is not None:
                    for pk in self._config.pairs:
                        learner.reset_pair(pk)
                LOG.info("Full P&L stats reset via dashboard")
            else:
                s.session_start_pnl = s.total_pnl
                s.session_start_ts = _time.time()
                s.peak_pnl = s.total_pnl
                s.total_trades = 0
                s.total_wins = 0
                s.fill_event_count = 0
                s.spread_captured = 0.0
                s.recent_fills.clear()
                s.pnl_curve.clear()
                LOG.info("Session P&L reset via dashboard (baseline=%.6f)", s.total_pnl)
            sl = self._runtime.session_logger
            if sl is not None:
                sl.log_optimizer(f"pnl_reset_{scope}")
            await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})

        elif action == "hot_reload_config":
            try:
                from .config import load_config
                fresh = load_config()
                for key, new_pc in fresh.pairs.items():
                    old_pc = self._config.pairs.get(key)
                    if old_pc is None:
                        continue
                    old_pc.order_size = new_pc.order_size
                    old_pc.sell_order_size = new_pc.sell_order_size
                    old_pc.spread_bps = new_pc.spread_bps
                    old_pc.spread_floor_bps = new_pc.spread_floor_bps
                    old_pc.max_inventory = new_pc.max_inventory
                    old_pc.inventory_skew_scale = new_pc.inventory_skew_scale
                    old_pc.fee_bps = new_pc.fee_bps
                    old_pc.fee_schedule = new_pc.fee_schedule
                    old_pc.sell_floor_base = new_pc.sell_floor_base
                    old_pc.order_levels = new_pc.order_levels
                    old_pc.level_step_bps = new_pc.level_step_bps
                LOG.info("Hot-reloaded config from config.toml")
                self._state.push_alert("info", "Config Reloaded", "config.toml applied without restart", "config")
            except Exception as e:
                LOG.exception("Hot reload failed")
                await ws.send_json({"type": "error", "message": f"Reload failed: {e}"})
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "soft_restart":
            await self._engine.soft_restart()
            await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "restart_process":
            import sys, os
            LOG.info("Restart process requested via dashboard — re-execing now")
            await self._broadcast({"type": "alert", "severity": "info",
                                   "title": "Restarting", "message": "Process restarting, config will reload from disk..."})
            await asyncio.sleep(0.5)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        elif action == "reseed_barriers":
            pair_key = msg.get("pair_key", "")
            if not pair_key or pair_key not in self._config.pairs:
                await ws.send_json({"type": "error", "message": f"Unknown pair_key: {pair_key!r}"})
            else:
                self._runtime.inventory.reseed_barriers_at_mid(pair_key)
                learner = self._runtime.learner
                if learner is not None:
                    learner.reset_pair(pair_key)
                LOG.info("reseed_barriers for %s triggered via dashboard", pair_key)
            await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})

        elif action == "trailing_exit":
            pair_key = msg.get("pair_key", "")
            activate = msg.get("activate", False)
            trail_pct = msg.get("trail_pct", 3.0)
            if activate:
                ps = self._state.pairs.get(pair_key)
                if ps is None or ps.inventory_base <= 0:
                    await ws.send_json({"type": "error", "message": "No inventory to exit"})
                    return
                self._state.trailing_exit_active = True
                self._state.trailing_exit_pair = pair_key
                self._state.trailing_exit_peak_price = ps.mid_price
                self._state.trailing_exit_trail_pct = float(trail_pct)
                self._state.trailing_exit_qty = ps.inventory_base
                self._state.trailing_exit_triggered = False
                LOG.warning(
                    "Trailing exit ARMED for %s: %.0f units, trail=%.1f%%, peak=$%.4f",
                    pair_key, ps.inventory_base, trail_pct, ps.mid_price,
                )
                self._state.push_alert(
                    "warning",
                    f"Trailing Exit Armed: {pair_key}",
                    f"Will sell {ps.inventory_base:.0f} units if price drops {trail_pct}% from peak (${ps.mid_price:.4f})",
                    "trailing_exit",
                )
            else:
                self._state.trailing_exit_active = False
                self._state.trailing_exit_triggered = False
                LOG.info("Trailing exit DISARMED")
            await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})

        elif action == "apply_trading_controls":
            pk = msg.get("pair_key", "")
            if not pk or pk not in self._config.pairs:
                return
            b = self._config.bot
            if "per_trade_profitability" in msg:
                b.per_trade_profitability = bool(msg["per_trade_profitability"])
            if "min_total_pnl_usd" in msg:
                v = msg["min_total_pnl_usd"]
                b.min_total_pnl_usd = None if v is None else float(v)
            self._engine.update_pair_config(
                pk,
                spread_bps=msg.get("spread_bps"),
                order_size=msg.get("order_size"),
                max_inventory=msg.get("max_inventory"),
                cycle_ms=msg.get("cycle_ms"),
                inventory_skew_scale=msg.get("inventory_skew_scale"),
                spread_floor_bps=msg.get("spread_floor_bps"),
                bootstrap_half_spread_bps=msg.get("bootstrap_half_spread_bps"),
                bootstrap_until_sell_trades=msg.get("bootstrap_until_sell_trades"),
                clear_bootstrap=bool(msg.get("clear_bootstrap_config", False)),
            )
            if msg.get("soft_restart"):
                await self._engine.soft_restart()
            await self._broadcast({"type": "config", "data": self._config_snapshot()})
            await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})

    def _config_snapshot(self) -> dict:
        from .fee_schedule import current_tier_info
        from .presets import detect_archetype, preset_info

        vol_30d = self._state.volume_30d
        pairs = {}
        fee_tier_by_pair = {}
        for key, pc in self._config.pairs.items():
            eff_fee = self._config.effective_fee_bps(key, vol_30d)
            pairs[key] = {
                "symbol": pc.symbol,
                "spread_bps": pc.spread_bps,
                "order_size": pc.order_size,
                "max_inventory": pc.max_inventory,
                "fee_bps": eff_fee,
                "fee_schedule": pc.fee_schedule,
                "cycle_ms": pc.cycle_ms or self._config.bot.default_cycle_ms,
                "inventory_skew_scale": pc.inventory_skew_scale,
                "spread_floor_bps": pc.spread_floor_bps,
                "bootstrap_half_spread_bps": pc.bootstrap_half_spread_bps,
                "bootstrap_until_sell_trades": pc.bootstrap_until_sell_trades,
            }
            fee_tier_by_pair[key] = current_tier_info(vol_30d, pc.fee_schedule)
        b = self._config.bot
        return {
            "mode": self._config.mode,
            "default_cycle_ms": b.default_cycle_ms,
            "enabled_pairs": b.enabled_pairs,
            "pair_keys_for_trading": self._config.pair_keys_for_trading(),
            "pairs": pairs,
            "adaptive_tuning": b.adaptive_tuning,
            "adaptive_interval_sec": b.adaptive_interval_sec,
            "adaptive_target_win_pct": b.adaptive_target_win_pct,
            "adaptive_win_band_pct": b.adaptive_win_band_pct,
            "learner_enabled": b.learner_enabled,
            "optimizer_enabled": b.optimizer_enabled,
            "optimizer_interval_sec": b.optimizer_interval_sec,
            "optimizer_train_hours": b.optimizer_train_hours,
            "optimizer_holdout_pct": b.optimizer_holdout_pct,
            "optimizer_max_delta_spread_bps": b.optimizer_max_delta_spread_bps,
            "optimizer_max_delta_size_pct": b.optimizer_max_delta_size_pct,
            "optimizer_min_fills": b.optimizer_min_fills,
            "optimizer_objective": b.optimizer_objective,
            "learner_loss_lookback_sells": b.learner_loss_lookback_sells,
            "learner_widen_on_avg_loss": b.learner_widen_on_avg_loss,
            "per_trade_profitability": b.per_trade_profitability,
            "min_total_pnl_usd": b.min_total_pnl_usd,
            "daily_profit_target_usd": b.daily_profit_target_usd,
            "daily_loss_limit_usd": b.daily_loss_limit_usd,
            "max_drawdown_pct": b.max_drawdown_pct,
            "fee_tier": current_tier_info(vol_30d, "spot_crypto"),
            "fee_tier_by_pair": fee_tier_by_pair,
            "volume_30d": round(vol_30d, 2),
            "volume_30d_source": self._state.volume_30d_source,
            "rate_limit_order_per_sec": b.rate_limit_order_per_sec,
            "rate_limit_burst": b.rate_limit_burst,
            "threat_quoting_pause": b.threat_quoting_pause,
            "abort_on_withdraw_permission": b.abort_on_withdraw_permission,
            "trailing_stop_enabled": b.trailing_stop_enabled,
            "trailing_stop_pct": b.trailing_stop_pct,
            "take_profit_usd": b.take_profit_usd,
            "oco_enabled": b.oco_enabled,
            "oco_stop_bps": b.oco_stop_bps,
            "oco_tp_bps": b.oco_tp_bps,
            "twap_enabled": b.twap_enabled,
            "twap_slice_count": b.twap_slice_count,
            "twap_duration_sec": b.twap_duration_sec,
            "btd_enabled": b.btd_enabled,
            "btd_sma_short": b.btd_sma_short,
            "btd_sma_long": b.btd_sma_long,
            "btd_levels": b.btd_levels,
            "btd_step_bps": b.btd_step_bps,
            "btd_size_multiplier": b.btd_size_multiplier,
            # --- exposed for live controls panel ---
            "learner_interval_sec": b.learner_interval_sec,
            "learner_min_samples": b.learner_min_samples,
            "learner_max_daily_adjustments": b.learner_max_daily_adjustments,
            "learner_lookback_max_age_sec": b.learner_lookback_max_age_sec,
            "adaptive_min_sample_sells": b.adaptive_min_sample_sells,
            "adaptive_lookback_sells": b.adaptive_lookback_sells,
            "adaptive_spread_step_bps": b.adaptive_spread_step_bps,
            "adaptive_spread_floor_bps": b.adaptive_spread_floor_bps,
            "adaptive_spread_ceiling_bps": b.adaptive_spread_ceiling_bps,
            "momentum_hold_sells": b.momentum_hold_sells,
            "momentum_hold_sec": b.momentum_hold_sec,
            "decay_start_sec": b.decay_start_sec,
            "decay_interval_sec": b.decay_interval_sec,
            "decay_step_bps": b.decay_step_bps,
            "pain_floor_decay_hours": b.pain_floor_decay_hours,
            "threat_imbalance_threshold": b.threat_imbalance_threshold,
            "threat_spread_blowout_ratio": b.threat_spread_blowout_ratio,
            "threat_velocity_bps": b.threat_velocity_bps,
            "threat_critical_velocity_bps": b.threat_critical_velocity_bps,
            "threat_spread_multiplier": b.threat_spread_multiplier,
            "depeg_threshold_bps": b.depeg_threshold_bps,
            "min_quote_half_spread_bps": b.min_quote_half_spread_bps,
            # MEV / Bot Detection
            "mev_detection_enabled": b.mev_detection_enabled,
            "mev_bot_widen_scale": b.mev_bot_widen_scale,
            "mev_arb_widen_scale": b.mev_arb_widen_scale,
            "mev_clean_tighten_scale": b.mev_clean_tighten_scale,
            "mev_bot_score_threshold": b.mev_bot_score_threshold,
            "mev_detector_window_sec": b.mev_detector_window_sec,
            "presets": preset_info(),
            "pair_archetypes": {
                k: detect_archetype(pc) for k, pc in self._config.pairs.items()
            },
        }

    async def _broadcast(self, msg: dict) -> None:
        dead = set()
        for ws in self._clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def broadcast_alert(
        self, level: str, title: str, detail: str = "", source: str = "",
    ) -> None:
        """Push a UI alert to all dashboard clients.

        level: "error" | "warning" | "info" | "success"
        """
        import time as _time
        await self._broadcast({
            "type": "alert",
            "level": level,
            "title": title,
            "detail": detail,
            "source": source,
            "ts": _time.time(),
        })

    async def _push_loop(self) -> None:
        while True:
            if self._clients:
                await self._broadcast({
                    "type": "snapshot",
                    "data": self._state.snapshot(),
                })
            await asyncio.sleep(0.5)

    async def broadcast_config(self) -> None:
        """Push current config to all dashboard clients (e.g. after adaptive spread changes)."""
        await self._broadcast({"type": "config", "data": self._config_snapshot()})

    async def start(self) -> web.AppRunner:
        self._push_task = asyncio.create_task(self._push_loop())
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(
            runner, self._config.server.host, self._config.server.port,
            reuse_address=False,
        )
        await site.start()
        LOG.info(
            "Dashboard running at http://%s:%d",
            self._config.server.host, self._config.server.port,
        )
        return runner

    async def stop(self) -> None:
        if self._push_task:
            self._push_task.cancel()
            try:
                await self._push_task
            except asyncio.CancelledError:
                pass
        for ws in list(self._clients):
            await ws.close()
