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
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/ws", self._ws_handler)
        self._app.router.add_get("/", self._index_handler)
        self._app.router.add_static("/static", FRONTEND_DIR, show_index=False)

    async def _index_handler(self, request: web.Request) -> web.Response:
        index_path = FRONTEND_DIR / "index.html"
        return web.FileResponse(index_path)

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

        action = msg.get("action")

        if action == "update_config":
            pair_key = msg.get("pair_key")
            if pair_key:
                self._engine.update_pair_config(
                    pair_key,
                    spread_bps=msg.get("spread_bps"),
                    order_size=msg.get("order_size"),
                    max_inventory=msg.get("max_inventory"),
                    cycle_ms=msg.get("cycle_ms"),
                    spread_floor_bps=msg.get("spread_floor_bps"),
                    bootstrap_half_spread_bps=msg.get("bootstrap_half_spread_bps"),
                    bootstrap_until_sell_trades=msg.get("bootstrap_until_sell_trades"),
                    clear_bootstrap=bool(msg.get("clear_bootstrap_config", False)),
                )
                await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "set_mode":
            new_mode = msg.get("mode", "paper")
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
            if new_mode == "paper" and self._runtime.live_mgr:
                await self._runtime.live_mgr.cancel_all()
            LOG.info("Mode switched to %s", new_mode)
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "start":
            if not self._state.running:
                await self._engine.start()

        elif action == "stop":
            if self._state.running:
                await self._engine.stop()

        elif action == "kill":
            await self._engine.kill()
            await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})

        elif action == "update_risk":
            b = self._config.bot
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
            # Reset halt so bot can resume after limits are updated
            self._state.risk_halted = False
            LOG.info(
                "Risk limits updated: daily_target=%s daily_loss=%s drawdown=%s pnl_floor=%s",
                b.daily_profit_target_usd, b.daily_loss_limit_usd,
                b.max_drawdown_pct, b.min_total_pnl_usd,
            )
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

        elif action == "soft_restart":
            await self._engine.soft_restart()
            await self._broadcast({"type": "snapshot", "data": self._state.snapshot()})
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

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

        vol_30d = self._state.volume_30d
        pairs = {}
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
                "spread_floor_bps": pc.spread_floor_bps,
                "bootstrap_half_spread_bps": pc.bootstrap_half_spread_bps,
                "bootstrap_until_sell_trades": pc.bootstrap_until_sell_trades,
            }
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
            "learner_loss_lookback_sells": b.learner_loss_lookback_sells,
            "learner_widen_on_avg_loss": b.learner_widen_on_avg_loss,
            "per_trade_profitability": b.per_trade_profitability,
            "min_total_pnl_usd": b.min_total_pnl_usd,
            "daily_profit_target_usd": b.daily_profit_target_usd,
            "daily_loss_limit_usd": b.daily_loss_limit_usd,
            "max_drawdown_pct": b.max_drawdown_pct,
            "fee_tier": current_tier_info(vol_30d, "spot_crypto"),
        }

    async def _broadcast(self, msg: dict) -> None:
        dead = set()
        for ws in self._clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

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
            reuse_address=True,
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
