"""WebSocket server — serves dashboard and pushes state updates."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson
from aiohttp import web, WSMsgType

from .scalp_bot.scalp_config import (
    ScalpBotConfig,
    effective_scalp_fee_bps_per_leg,
    wfo_fee_bps_per_leg,
)
from .scalp_bot.scalp_mode_resolution import resolve_auto_mode
from .scalp_bot.strategy_lookback import champion_row_matches_pair_interval
from .scalp_bot.scalp_wfo import (
    WFOConfig,
    load_champion_for_symbol,
    wfo_effective_roll_span_hours,
)
from .ui_event_log import UiEventLog

if TYPE_CHECKING:
    from .config import AppConfig
    from .state import BotState

LOG = logging.getLogger(__name__)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_NEW_DIST = _PROJECT_ROOT / "frontend-new" / "dist"
LEGACY_FRONTEND_DIR = _PROJECT_ROOT / "frontend"


def _resolve_dashboard_static_dir() -> Path:
    """Prefer ``frontend-new/dist`` (current React scalp UI); fall back to legacy ``frontend/``."""
    if (FRONTEND_NEW_DIST / "index.html").is_file():
        return FRONTEND_NEW_DIST
    return LEGACY_FRONTEND_DIR


FRONTEND_DIR = _resolve_dashboard_static_dir()


def _frontend_src_newer_than_dist() -> bool:
    """Return True if any file under ``frontend-new/src`` or ``frontend-new/index.html``
    is newer than the current dist bundle — i.e. the dist is stale and needs a rebuild."""
    src_root = _PROJECT_ROOT / "frontend-new" / "src"
    dist_assets = FRONTEND_NEW_DIST / "assets"
    # Find the oldest dist JS bundle (if none exist, always stale)
    dist_js = list(dist_assets.glob("index-*.js")) if dist_assets.is_dir() else []
    if not dist_js:
        return True
    dist_mtime = min(f.stat().st_mtime for f in dist_js)
    # Check source tree for anything newer
    check_roots = [src_root]
    for extra in ("index.html", "vite.config.ts", "tsconfig.json", "tsconfig.app.json"):
        p = _PROJECT_ROOT / "frontend-new" / extra
        if p.is_file():
            check_roots.append(p)
    for root in check_roots:
        if root.is_file():
            if root.stat().st_mtime > dist_mtime:
                return True
            continue
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if f.is_file() and f.stat().st_mtime > dist_mtime:
                return True
    return False


def _run_frontend_npm_build() -> tuple[int, str]:
    """Run ``npm run build`` in ``frontend-new/``. Returns ``(exit_code, log_tail)``."""
    frontend = _PROJECT_ROOT / "frontend-new"
    if not (frontend / "package.json").is_file():
        return 127, f"Missing {frontend / 'package.json'}"
    import shutil

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        return 127, "npm not found on PATH (install Node.js and ensure npm is on PATH)"
    try:
        r = subprocess.run(
            [npm, "run", "build"],
            cwd=str(frontend),
            capture_output=True,
            text=True,
            timeout=600,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "npm run build exceeded 10 minute timeout"
    except Exception as exc:
        return 1, str(exc)
    combined = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    tail = combined[-8000:] if len(combined) > 8000 else combined
    return int(r.returncode), tail or "(no output)"


def _argv_for_process_restart() -> list[str]:
    """Argv for spawning a fresh backend process (Windows-safe; avoids broken ``os.execv``)."""
    if getattr(sys, "frozen", False):
        return list(sys.argv)
    exe = sys.executable
    av = list(sys.argv)
    if not av:
        return [exe, "-m", "backend.server.main"]
    if len(av) == 1:
        return [exe, av[0]]
    return [exe] + av[1:]


def _spawn_fresh_backend_process() -> None:
    """Start a new Python process with the same entrypoint, then exit this one (releases the port)."""
    argv = _argv_for_process_restart()
    cwd = os.getcwd()
    env = os.environ.copy()
    if sys.platform == "win32":
        creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            creationflags=creation,
            close_fds=False,
        )
        os._exit(0)
    os.execv(argv[0], argv)


def _json_sanitize_ws(obj: Any) -> Any:
    """Strict JSON for browser ``JSON.parse`` — drop NaN/Inf and numpy scalars that break parsing."""
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    try:
        item = getattr(obj, "item", None)
        if callable(item):
            return _json_sanitize_ws(item())
    except Exception:
        pass
    if isinstance(obj, dict):
        return {str(k): _json_sanitize_ws(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_sanitize_ws(v) for v in obj]
    if isinstance(obj, tuple):
        return [_json_sanitize_ws(v) for v in obj]
    return obj


# orjson is ~3–10x faster than stdlib json on the snapshot payload. The sanitize pass above
# already strips NaN/Inf and numpy scalars (orjson would otherwise raise on NaN/Inf); the
# `default=` fallback below catches any stragglers without killing the broadcast.
_ORJSON_OPTS = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS


def _ws_default(obj: Any) -> Any:
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            val = item()
            if isinstance(val, float) and not math.isfinite(val):
                return None
            return val
        except Exception:
            pass
    return None


def _encode_ws(msg: Any) -> str:
    """Fast JSON encode for WS TEXT frames. Assumes payload has already been passed through
    ``_json_sanitize_ws`` when it may contain NaN/Inf. For small pre-known payloads
    (error replies, config) this is safe to call directly."""
    return orjson.dumps(msg, default=_ws_default, option=_ORJSON_OPTS).decode()


def _ui_log_sizes_from_env() -> tuple[int, int]:
    def _parse(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            return max(10, int(raw))
        except ValueError:
            return default

    max_e = _parse("UI_LOG_MAX_ENTRIES", 15000)
    tail = min(_parse("UI_LOG_SNAPSHOT_TAIL", 500), max_e)
    return max_e, tail


_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "private_key",
    "pem",
    "credential",
)


def _redact_ws_msg_for_log(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(frag in lk for frag in _SENSITIVE_KEY_FRAGMENTS):
                out[k] = "<redacted>"
            else:
                out[k] = _redact_ws_msg_for_log(v)
        return out
    if isinstance(obj, list):
        return [_redact_ws_msg_for_log(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_redact_ws_msg_for_log(x) for x in obj)
    return obj


def _toml_float(v: object, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _toml_int(v: object, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _toml_opt_float(v: object) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _toml_bool(v: object, default: bool = False) -> bool:
    if v is None:
        return default
    return bool(v)


class DashboardServer:
    def __init__(
        self,
        state: BotState,
        config: AppConfig,
        scalp_cfg: ScalpBotConfig | None = None,
        *,
        bot_toml: dict[str, Any] | None = None,
        pairs_toml: dict[str, Any] | None = None,
        session_logger: Any = None,
    ) -> None:
        self._state = state
        self._config = config
        self._scalp_cfg = scalp_cfg
        self._bot_toml: dict[str, Any] = dict(bot_toml or {})
        self._pairs_toml: dict[str, Any] = dict(pairs_toml or {})
        self._session_logger = session_logger
        self._scalp_runtime = None
        self._app = web.Application()
        self._clients: set[web.WebSocketResponse] = set()
        self._push_task: asyncio.Task | None = None
        _max_log, _tail_log = _ui_log_sizes_from_env()
        self._ui_log = UiEventLog(max_entries=_max_log, tail_for_snapshot=_tail_log)
        try:
            _boot_loop = asyncio.get_running_loop()
        except RuntimeError:
            _boot_loop = None
        self._asyncio_loop = _boot_loop
        self._ui_mirror_handler: logging.Handler | None = None
        state._alert_fn = self.broadcast_alert
        # Same loop as ``start()`` — set early so worker-thread alerts (bar_store/WFO) are not
        # dropped if they fire before ``await dashboard.start()`` reaches its body.
        state._alert_loop = _boot_loop
        state._request_snapshot_bump = self._schedule_snapshot_bump
        self._setup_routes()

    def bind_scalp_runtime(self, sr: Any) -> None:
        """Bind the ScalpRuntime and wire operator-flow push events."""
        self._scalp_runtime = sr
        if sr is None or not hasattr(sr, "set_flow_push"):
            return

        async def push() -> None:
            try:
                snap = self._state.snapshot()
                snap["scalp"] = self._scalp_payload(include_closed_candles=True)
                self._attach_ui_log_tail(snap)
                await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(snap)})
            except Exception:
                LOG.exception("scalp flow_push snapshot broadcast failed")

        sr.set_flow_push(push)

    async def _broadcast_state_snapshot(self, *, include_closed_candles: bool = True) -> None:
        snap = self._state.snapshot()
        snap["scalp"] = self._scalp_payload(include_closed_candles=include_closed_candles)
        self._attach_ui_log_tail(snap)
        await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(snap)})

    def _setup_routes(self) -> None:
        self._app.router.add_get("/health", self._health_handler)
        self._app.router.add_get("/ws", self._ws_handler)
        self._app.router.add_get("/", self._index_handler)
        self._app.router.add_get("/favicon.svg", self._favicon_handler)
        self._app.router.add_static("/assets", FRONTEND_DIR / "assets", show_index=False)
        self._app.router.add_static("/static", FRONTEND_DIR, show_index=False)

    async def _health_handler(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "tradingbot-dashboard"})

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
        # NM-008: restrict non-loopback connections to token-bearing requests.
        remote = request.remote or ""
        is_loopback = remote in ("127.0.0.1", "::1", "localhost", "")
        token_env = os.environ.get("DASHBOARD_TOKEN", "").strip()
        if not is_loopback:
            if token_env:
                provided = request.rel_url.query.get("token", "")
                if provided != token_env:
                    LOG.warning("WS: rejected non-loopback connection from %s — invalid or missing token", remote)
                    raise web.HTTPForbidden(reason="invalid or missing dashboard token")
            else:
                LOG.warning(
                    "WS: non-loopback connection from %s accepted with no DASHBOARD_TOKEN set "
                    "— set DASHBOARD_TOKEN env var to restrict access",
                    remote,
                )

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # Register only after the first snapshot is sent — otherwise _push_loop can broadcast
        # a partial scalp payload (no `closed` candles) before init, and the UI has no prior row to merge.

        try:
            init_snap = self._state.snapshot()
            try:
                init_snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            except Exception:
                LOG.exception("Initial scalp snapshot failed")
                init_snap["scalp"] = self._scalp_placeholder_payload()
            self._attach_ui_log_tail(init_snap)
            init_snap = _json_sanitize_ws(init_snap)
            await ws.send_str(_encode_ws({"type": "snapshot", "data": init_snap}))
            await ws.send_str(_encode_ws({"type": "config", "data": self._config_snapshot()}))
            self._clients.add(ws)
            LOG.info("Dashboard client connected from %s (%d total)", remote or "loopback", len(self._clients))
            await self.log_action(
                "info",
                "Dashboard WS connected",
                f"remote={remote or 'loopback'} open_clients={len(self._clients)}",
                "ws_lifecycle",
                kind="ws_lifecycle",
            )

            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_message(ws, msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            self._clients.discard(ws)
            n_left = len(self._clients)
            LOG.info("Dashboard client disconnected (%d remaining)", n_left)
            try:
                asyncio.get_running_loop().create_task(
                    self.log_action(
                        "info",
                        "Dashboard WS disconnected",
                        f"open_clients={n_left}",
                        "ws_lifecycle",
                        kind="ws_lifecycle",
                    )
                )
            except RuntimeError:
                pass

        return ws

    async def _handle_message(self, ws: web.WebSocketResponse, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as e:
            snippet = raw if len(raw) <= 4000 else raw[:4000] + "…"
            await self.log_action(
                "warning",
                "WS invalid JSON",
                f"{e}\n{snippet!r}",
                "ws_in",
                kind="ws_error",
            )
            return
        if not isinstance(msg, dict):
            await self.log_action(
                "warning",
                "WS non-object JSON",
                json.dumps(_json_sanitize_ws(msg), ensure_ascii=False, default=str)[:8000],
                "ws_in",
                kind="ws_error",
            )
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
        if os.environ.get("UI_LOG_WS_CMD", "1").strip().lower() not in ("0", "false", "no", "off"):
            try:
                red = _json_sanitize_ws(_redact_ws_msg_for_log(dict(msg)))
                payload = json.dumps(red, ensure_ascii=False, default=str)
                if len(payload) > 12000:
                    payload = payload[:12000] + "…"
                await self.log_action(
                    "info",
                    f"WS inbound action={action!r}",
                    payload,
                    "ws_in",
                    kind="ws_cmd",
                )
            except Exception:
                LOG.debug("ws_cmd ui log failed", exc_info=True)

        if action == "set_mode":
            new_mode = msg.get("mode", "paper")
            if new_mode not in ("paper", "live"):
                await ws.send_json({"type": "error", "message": f"Invalid mode: {new_mode}"})
                return
            self._config.mode = new_mode
            self._state.mode = new_mode
            self._bot_toml["mode"] = new_mode
            LOG.info(
                "Mode switched to %s (Coinbase scalp uses its own sim/live; "
                "restart process if order manager must re-init)",
                new_mode,
            )
            await self.log_action(
                "info",
                f"Engine mode: {new_mode}",
                "Paper vs live for legacy dashboard; scalp sim/live is separate.",
                "dashboard",
            )
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "set_active_pair":
            pair_key = msg.get("pair_key", "")
            valid = set(self._pairs_toml.keys())
            if self._scalp_cfg:
                valid |= set(self._scalp_cfg.pairs.keys())
            valid |= set(self._state.pairs.keys())
            if pair_key in valid:
                self._state.active_pair_key = pair_key

        elif action == "reset_pnl":
            import time as _time
            s = self._state
            scope = msg.get("scope", "session")
            if scope == "all":
                await ws.send_json(
                    {
                        "type": "error",
                        "message": "Lifetime P&L is not resettable from the dashboard. Use session reset only.",
                    },
                )
                LOG.warning("reset_pnl scope=all rejected — lifetime total_pnl is immutable")
                return
            s.session_start_pnl = s.total_pnl
            s.session_start_ts = _time.time()
            s.peak_pnl = s.total_pnl
            s.total_trades = 0
            s.total_wins = 0
            s.fill_event_count = 0
            s.recent_fills.clear()
            s.pnl_curve.clear()
            LOG.info("Session P&L reset via dashboard (baseline=%.6f)", s.total_pnl)
            sl = self._session_logger
            if sl is not None and hasattr(sl, "log_scalp"):
                sl.log_scalp(f"pnl_reset_{scope}")
            await self.log_action("info", "P&L reset", f"scope={scope}", "dashboard")
            await self._broadcast_state_snapshot()

        elif action == "update_risk":
            b = self._bot_toml
            try:
                if "daily_profit_target_usd" in msg:
                    v = msg["daily_profit_target_usd"]
                    b["daily_profit_target_usd"] = None if v is None or v == "" else float(v)
                if "daily_loss_limit_usd" in msg:
                    v = msg["daily_loss_limit_usd"]
                    b["daily_loss_limit_usd"] = None if v is None or v == "" else float(v)
                if "max_drawdown_pct" in msg:
                    v = msg["max_drawdown_pct"]
                    b["max_drawdown_pct"] = None if v is None or v == "" else float(v)
                if "min_total_pnl_usd" in msg:
                    v = msg["min_total_pnl_usd"]
                    b["min_total_pnl_usd"] = None if v is None or v == "" else float(v)
            except (ValueError, TypeError) as e:
                await ws.send_json({"type": "error", "message": f"Invalid risk value: {e}"})
                return
            if bool(msg.get("resume_risk_halt", False)):
                self._state.risk_halted = False
                self._state.risk_halt_reason = ""
            LOG.info("Risk limits updated (in-memory; edit config.toml to persist)")
            await self.log_action("info", "Risk limits updated", "In memory — persist in config.toml to keep.", "dashboard")
            await self._broadcast({"type": "config", "data": self._config_snapshot()})
            await self._broadcast_state_snapshot()

        elif action == "start":
            if self._state.risk_halted:
                self._state.risk_halted = False
                self._state.risk_halt_reason = ""
                LOG.info("Risk halt cleared on START")
            LOG.info("START: spread engine removed — use scalp operator flow; no-op")
            await self.log_action("info", "START", "Spread engine removed — no-op; use scalp operator flow in Settings.", "dashboard")
            await self._broadcast_state_snapshot()

        elif action in ("stop", "kill"):
            LOG.info("%s: spread engine removed — no-op", action.upper())
            await self.log_action("info", action.upper(), "Spread engine removed — no-op.", "dashboard")
            await self._broadcast_state_snapshot()

        elif action == "toggle_pair":
            pair_key = msg.get("pair_key", "")
            enabled = bool(msg.get("enabled", True))
            if pair_key not in self._pairs_toml:
                return
            ep = self._bot_toml.get("enabled_pairs")
            if ep is None:
                ep = list(self._pairs_toml.keys())
                self._bot_toml["enabled_pairs"] = ep
            if not isinstance(ep, list):
                ep = list(ep)
                self._bot_toml["enabled_pairs"] = ep
            if enabled and pair_key not in ep:
                ep.append(pair_key)
                LOG.info("Pair %s enabled in dashboard copy (not persisted to config.toml)", pair_key)
            elif not enabled and pair_key in ep:
                ep.remove(pair_key)
                LOG.info("Pair %s disabled in dashboard copy (not persisted to config.toml)", pair_key)
            await self.log_action(
                "info",
                f"Pair {pair_key}",
                "enabled" if enabled else "disabled",
                "dashboard",
            )
            await self._broadcast({"type": "config", "data": self._config_snapshot()})
            await self._broadcast_state_snapshot()

        elif action == "soft_restart":
            # Reload dashboard copies of [bot]/[pairs] from disk — no process exit. Scalp engine
            # still follows values loaded at startup; use ``restart_process`` for full reload.
            try:
                from .config import load_raw_toml

                raw = load_raw_toml()
                self._bot_toml = dict(raw.get("bot", {}) or {})
                self._pairs_toml = dict(raw.get("pairs", {}) or {})
                LOG.info("WS: soft_restart — reloaded [bot]/[pairs] from config.toml (dashboard only)")
                await self.log_action("info", "Soft restart", "Reloaded [bot]/[pairs] from disk into dashboard copy only.", "dashboard")
                await self._broadcast({"type": "config", "data": self._config_snapshot()})
                await self._broadcast_state_snapshot()
            except Exception as exc:
                LOG.exception("WS: soft_restart failed")
                await ws.send_json({"type": "error", "message": f"soft_restart failed: {exc}"})

        elif action in (
            "update_pair",
            "update_trailing",
            "set_adaptive_tuning",
            "smart_defaults",
            "update_systems",
            "apply_preset",
            "hot_reload_config",
            "reseed_barriers",
            "trailing_exit",
            "apply_trading_controls",
        ):
            LOG.info("WS: action %s ignored (spread/MM engine not in this build)", action)
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "set_scalp_mode":
            mode = msg.get("mode", "")
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            if mode not in ("sim", "live", "off"):
                await ws.send_json({"type": "error", "message": f"Invalid scalp mode: {mode}"})
                return

            if mode in ("sim", "live"):
                cb_mgr = getattr(sr, "_live_mgr", None)

                # ── Snapshot positions before reset ────────────────────────
                open_legs = [p for p in sr._trader._positions.values() if p.status == "open"]
                open_pks = sorted({p.pair_key for p in open_legs})
                pending_legs = [
                    p for p in sr._trader._positions.values() if p.status == "pending"
                ]

                # Warn loudly if open exchange legs will be detached from local state
                if open_legs:
                    warn = (
                        f"set_scalp_mode({mode}): clearing local state with "
                        f"{len(open_legs)} open leg(s) on {len(open_pks)} pair(s) — "
                        f"{', '.join(open_pks)}. Triggering immediate exchange order snapshot refresh."
                    )
                    LOG.warning(warn)
                    await self.broadcast_alert("warning", "Mode switch with open positions", warn, "scalp_mode")

                # Cancel pending entry orders on exchange before wiping state
                if cb_mgr is not None:
                    for pos in pending_legs:
                        if pos.entry_cl_ord_id:
                            try:
                                await cb_mgr.cancel_order(pos.entry_cl_ord_id)
                            except Exception:
                                LOG.warning("set_scalp_mode: cancel failed for %s", pos.entry_cl_ord_id[:16])

                sr._trader.reset_session()
                sr._trader.sim_mode = mode == "sim"
                sr._cfg.enabled = True

                # Stopgap bridge: re-adopt any open exchange legs immediately.
                if cb_mgr is not None and open_pks:
                    asyncio.create_task(
                        cb_mgr.refresh_scalp_exchange_snapshots(),
                        name="scalp_mode_switch_reconcile",
                    )

                LOG.info("WS: scalp mode set to %s (session reset)", mode.upper())

            elif mode == "off":
                sr._trader.sim_mode = False
                sr._cfg.enabled = False
                LOG.info("WS: scalp mode set to OFF")

            await self.log_action("info", f"Scalp mode: {mode}", "Operator changed sim/live/off.", "scalp_mode")
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "set_scalp_strategy":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            pair_key = msg.get("pair_key", "")
            if pair_key not in sr._cfg.pairs:
                await ws.send_json({"type": "error", "message": f"Unknown pair_key: {pair_key!r}"})
                return
            strategy = msg.get("strategy", "auto")
            valid = {
                "auto", "daviddtech_scalp", "ema_momentum", "macd_scalp", "ema_scalp", "rsi_reversion",
                "supertrend", "squeeze_momentum", "qqe_mod", "utbot_alert", "hull_suite", "sar_chop",
            }
            if strategy not in valid:
                await ws.send_json({"type": "error", "message": f"Invalid strategy: {strategy}"})
                return
            pc = sr._cfg.pairs[pair_key]
            if strategy == "auto":
                row = load_champion_for_symbol(pc.symbol)
                if isinstance(row, dict) and not champion_row_matches_pair_interval(row, pc.interval):
                    row = None
                fb = getattr(pc, "auto_mode_fallback", None) or getattr(
                    sr._cfg, "auto_mode_fallback", "sar_chop"
                )
                resolved = resolve_auto_mode(
                    "auto",
                    champion_row=row if isinstance(row, dict) else None,
                    auto_mode_fallback=fb,
                )
                sr._active_mode[pair_key] = resolved
                sr._mode_source[pair_key] = "operator_auto"
                LOG.info(
                    "WS: scalp strategy for %s set to auto → resolved %s",
                    pair_key, resolved,
                )
            else:
                sr._active_mode[pair_key] = strategy
                sr._mode_source[pair_key] = "operator"
                LOG.info("WS: scalp strategy for %s set to %s", pair_key, strategy)
            await self.log_action(
                "info",
                f"Strategy {pair_key}",
                strategy,
                "scalp_strategy",
            )
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "set_scalp_max_concurrent_positions":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            raw = msg.get("max_concurrent_positions", msg.get("value"))
            if raw is None:
                await ws.send_json({"type": "error", "message": "Missing max_concurrent_positions"})
                return
            try:
                n = int(raw)
            except (TypeError, ValueError):
                await ws.send_json({"type": "error", "message": "max_concurrent_positions must be an integer"})
                return
            if n < 0 or n > 64:
                await ws.send_json(
                    {"type": "error", "message": "max_concurrent_positions must be 0 (unlimited) through 64"},
                )
                return
            sr._cfg.max_concurrent_positions = n
            LOG.info("WS: scalp max_concurrent_positions set to %d", n)
            await self.log_action("info", "Max concurrent positions", str(n), "scalp_settings")
            await self._broadcast({"type": "config", "data": self._config_snapshot()})

        elif action == "update_scalp_session_policy":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            raw_patch = msg.get("patch")
            if isinstance(raw_patch, dict):
                patch = dict(raw_patch)
            else:
                patch = {k: v for k, v in msg.items() if k not in ("action", "type")}
            ok, detail = sr.apply_session_policy_runtime_patch(patch)
            if not ok:
                await ws.send_json({"type": "error", "message": detail})
                return
            await self.broadcast_alert(
                "info",
                "WFO / session policy updated",
                "In memory only — edit config.toml to persist after restart.",
                "scalp_session_policy",
            )
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "scalp_refresh_fee_tier":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            ok, detail = await sr.refresh_fee_tier_from_exchange()
            if not ok:
                await ws.send_json({"type": "error", "message": f"Fee tier refresh failed: {detail}"})
                return
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "acknowledge_exchange_errors":
            raw_ids = msg.get("error_ids")
            if raw_ids is None:
                self._state.acknowledge_exchange_errors(None)
            elif isinstance(raw_ids, list):
                self._state.acknowledge_exchange_errors([str(x) for x in raw_ids])
            else:
                await ws.send_json({"type": "error", "message": "error_ids must be a list or omitted to clear all"})
                return
            await self.log_action(
                "info",
                "Exchange errors acknowledged",
                "all" if raw_ids is None else f"{len(raw_ids)} id(s)",
                "dashboard",
            )
            await self._push_snapshot_now()

        elif action == "scalp_operator_go_live":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            sr.operator_go_live()
            asyncio.create_task(sr.operator_flow_go_live_ui(), name="scalp_operator_flow_go_live")
            await self.log_action("info", "Go live", "Operator requested go live.", "scalp_operator")
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "scalp_operator_standby":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            sr.operator_enter_standby()
            asyncio.create_task(sr.operator_flow_standby_ui(), name="scalp_operator_flow_standby")
            await self.log_action("info", "Standby", "Operator entered standby.", "scalp_operator")
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "scalp_risk_halt":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            reason = str(msg.get("reason") or "").strip() or "ws_halt"
            sr.set_scalp_risk_halt(reason, "ws")
            await self.log_action("warning", "Scalp risk halt", reason, "scalp_operator")
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "scalp_risk_resume":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            sr.clear_scalp_risk_halt("ws")
            await self.log_action("info", "Scalp risk resume", "Portfolio halt cleared.", "scalp_operator")
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "scalp_emergency_stop":
            # JSON: { "action": "scalp_emergency_stop", "reason": "optional string" }
            # Enters operator standby, best-effort cancels resting scalp orders on Coinbase (no position flatten).
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            reason = str(msg.get("reason") or "").strip() or "ws_emergency"
            sr.set_scalp_risk_halt(reason, "scalp_emergency_stop")
            sr.operator_enter_standby()
            asyncio.create_task(sr.operator_flow_standby_ui(), name="scalp_emergency_standby_ui")
            cb_mgr = getattr(sr, "_live_mgr", None)
            cancel_fn = getattr(cb_mgr, "cancel_all_scalp_open_orders", None)
            n_cancelled = 0
            if callable(cancel_fn):
                try:
                    n_cancelled = int(await cancel_fn())
                except Exception:
                    LOG.exception("scalp_emergency_stop: cancel_all_scalp_open_orders failed")
            sl = self._session_logger
            if sl is not None and hasattr(sl, "log_scalp"):
                sl.log_scalp(
                    "scalp_emergency_stop",
                    reason=reason,
                    source="ws",
                    cancelled_orders=n_cancelled,
                )
            await self.log_action(
                "warning",
                "Scalp emergency stop",
                f"Standby + cancel resting orders ({n_cancelled} attempted). reason={reason}",
                "scalp_operator",
            )
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "scalp_emergency_flatten":
            # JSON: { "action": "scalp_emergency_flatten", "confirm": "CONFIRM_FLATTEN", "reason": "optional" }
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            if str(msg.get("confirm") or "").strip() != "CONFIRM_FLATTEN":
                await ws.send_json(
                    {"type": "error", "message": "confirm must be exactly CONFIRM_FLATTEN"},
                )
                return
            reason = str(msg.get("reason") or "").strip() or "ws_emergency_flatten"
            try:
                n_mkt = await sr.emergency_flatten_all_positions(reason, source="ws")
            except Exception:
                LOG.exception("scalp_emergency_flatten failed")
                await ws.send_json({"type": "error", "message": "emergency_flatten failed (see server log)"})
                return
            await self.log_action(
                "warning",
                "Scalp emergency flatten",
                f"Halt + reduce-only market exits submitted (n={n_mkt}). reason={reason}",
                "scalp_operator",
            )
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "scalp_operator_manual_cancel_orders":
            # JSON: { "action": "...", "reason": "optional" } — cancel resting scalp orders only; no halt/standby.
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            reason = str(msg.get("reason") or "").strip() or "operator_ui"
            try:
                n = await sr.manual_cancel_all_open_orders(reason, source="ws")
            except Exception:
                LOG.exception("scalp_operator_manual_cancel_orders failed")
                await ws.send_json({"type": "error", "message": "manual cancel orders failed (see server log)"})
                return
            await self.log_action(
                "info",
                "Manual cancel scalp orders",
                f"Operator requested resting-order cancel (n={n}). reason={reason}",
                "scalp_operator",
            )
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "scalp_operator_manual_close_positions":
            # JSON: { "action": "...", "reason": "optional" } — reduce-only market exits; no portfolio halt.
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            reason = str(msg.get("reason") or "").strip() or "operator_ui"
            try:
                n_mkt = await sr.manual_close_all_open_positions(reason, source="ws")
            except Exception:
                LOG.exception("scalp_operator_manual_close_positions failed")
                await ws.send_json({"type": "error", "message": "manual close positions failed (see server log)"})
                return
            await self.log_action(
                "info",
                "Manual close scalp positions",
                f"Operator requested market closes (n={n_mkt}). Positions logged user_manual_close. reason={reason}",
                "scalp_operator",
            )
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "scalp_begin_warmup":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            sr.operator_begin_warmup()
            await self.log_action("info", "Begin warmup", "Warmup sequence started.", "scalp_operator")
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "scalp_begin_prep_session":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            asyncio.create_task(sr.operator_begin_prep_session(), name="scalp_begin_prep_session")
            await self.log_action("info", "Prep session", "Prep session task started.", "scalp_operator")
            _snap = self._state.snapshot()
            _snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(_snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(_snap)})

        elif action == "test_trade":
            sr = self._scalp_runtime
            if sr is None:
                await ws.send_json({"type": "error", "message": "Scalp runtime not available"})
                return
            cb_mgr = getattr(sr, "_live_mgr", None)
            if cb_mgr is None:
                await ws.send_json({"type": "error", "message": "Coinbase order manager not available"})
                return
            if not sr._trader.sim_mode and not sr._cfg.sim_mode and not msg.get("confirm_live"):
                await ws.send_json({
                    "type": "error",
                    "message": "test_trade refused: scalp is in LIVE mode. Pass confirm_live=true to override (PLACES REAL MARKET ORDERS).",
                })
                return
            pair_key = msg.get("pair_key", "")
            product = sr._cfg.pairs.get(pair_key)
            if product is None:
                await ws.send_json({"type": "error", "message": f"Unknown pair: {pair_key}"})
                return
            results: list[dict] = []
            import uuid as _uuid
            for side in ("BUY", "SELL"):
                cid = f"test-{_uuid.uuid4().hex[:8]}"
                try:
                    oid = await cb_mgr.add_order(params={
                        "symbol": product.symbol,
                        "side": side.lower(),
                        "order_type": "market",
                        "order_qty": 1,
                        "cl_ord_id": cid,
                    })
                    if oid:
                        results.append({"side": side, "cl_ord_id": cid, "order_id": oid, "status": "ok"})
                        LOG.info("WS test_trade %s %s %s: placed ok (%s)", pair_key, side, product.symbol, oid)
                    else:
                        results.append({"side": side, "cl_ord_id": cid, "status": "error", "error": "Order rejected (check logs for 403/permission error)"})
                        LOG.warning("WS test_trade %s %s %s: rejected (empty oid)", pair_key, side, product.symbol)
                except Exception as exc:
                    results.append({"side": side, "cl_ord_id": cid, "status": "error", "error": str(exc)})
                    LOG.exception("WS test_trade %s %s %s: failed", pair_key, side, product.symbol)
            await ws.send_json({"type": "test_trade_result", "pair_key": pair_key, "results": results})

        elif action == "rebuild_frontend_dist":
            LOG.info("rebuild_frontend_dist requested via dashboard")
            asyncio.create_task(self._rebuild_frontend_dist_task(ws), name="rebuild_frontend_dist")

        elif action == "restart_process":
            LOG.info("Restart process requested via dashboard — spawning fresh backend process")
            await self.broadcast_alert(
                "info",
                "Restarting",
                "Backend process restarting; config will reload from disk.",
                "dashboard",
            )
            await asyncio.sleep(0.5)
            try:
                _spawn_fresh_backend_process()
            except Exception:
                LOG.exception(
                    "Restart failed — fix logs above (Windows: ensure port %s is free)",
                    self._config.server.port,
                )

        else:
            try:
                red = _json_sanitize_ws(_redact_ws_msg_for_log(dict(msg)))
                detail = json.dumps(red, ensure_ascii=False, default=str)
                if len(detail) > 8000:
                    detail = detail[:8000] + "…"
            except Exception:
                detail = repr(msg)[:8000]
            if action is None:
                await self.log_action(
                    "warning",
                    "WS message missing action",
                    detail,
                    "ws_in",
                    kind="ws_unknown",
                )
            else:
                await self.log_action(
                    "warning",
                    f"Unknown WS action: {action!r}",
                    detail,
                    "ws_in",
                    kind="ws_unknown",
                )
            LOG.debug("WS: unhandled action=%s", action)

    def _install_ui_log_mirror(self) -> None:
        raw = os.environ.get("UI_LOG_MIRROR", "1").strip().lower()
        if raw in ("0", "false", "no", "off"):
            return
        from .ui_mirror_log_handler import install_ui_mirror_log_handlers

        self._ui_mirror_handler = install_ui_mirror_log_handlers(self)

    def _config_snapshot(self) -> dict:
        """Dashboard config message: [bot]/[pairs] from TOML (mutable copy for WS tweaks) + scalp summary."""
        b = self._bot_toml
        vol_30d = 0.0
        pairs: dict[str, dict[str, Any]] = {}
        fee_tier_by_pair: dict[str, dict[str, Any]] = {}
        for key, raw in self._pairs_toml.items():
            if not isinstance(raw, dict):
                continue
            eff_fee = _toml_float(raw.get("fee_bps"), 0.0)
            sched = str(raw.get("fee_schedule", "") or "").strip()
            label = sched if sched else f"{eff_fee:g}bps_static"
            pairs[key] = {
                "symbol": str(raw.get("symbol", key)),
                "spread_bps": _toml_int(raw.get("spread_bps"), 0),
                "order_size": _toml_float(raw.get("order_size"), 0.0),
                "max_inventory": _toml_float(raw.get("max_inventory"), 0.0),
                "fee_bps": eff_fee,
                "fee_schedule": sched,
                "cycle_ms": _toml_int(raw.get("cycle_ms"), 0) or _toml_int(b.get("default_cycle_ms"), 3000),
                "inventory_skew_scale": _toml_float(raw.get("inventory_skew_scale"), 0.0),
                "spread_floor_bps": _toml_int(raw.get("spread_floor_bps"), 0),
                "bootstrap_half_spread_bps": _toml_int(raw.get("bootstrap_half_spread_bps"), 0),
                "bootstrap_until_sell_trades": _toml_int(raw.get("bootstrap_until_sell_trades"), 0),
            }
            fee_tier_by_pair[key] = {"label": label, "maker_bps": eff_fee}

        ep = b.get("enabled_pairs")
        if ep is None:
            pair_keys_for_trading = list(pairs.keys())
        else:
            pair_keys_for_trading = list(ep) if isinstance(ep, list) else list(pairs.keys())

        spread_bot = _toml_bool(b.get("spread_bot_enabled"), False)

        return {
            "mode": self._config.mode,
            "spread_bot_enabled": spread_bot,
            "default_cycle_ms": _toml_int(b.get("default_cycle_ms"), 3000),
            "enabled_pairs": list(ep) if isinstance(ep, list) else None,
            "pair_keys_for_trading": pair_keys_for_trading,
            "pairs": pairs,
            "adaptive_tuning": _toml_bool(b.get("adaptive_tuning"), False),
            "adaptive_interval_sec": _toml_int(b.get("adaptive_interval_sec"), 90),
            "adaptive_target_win_pct": _toml_float(b.get("adaptive_target_win_pct"), 48.0),
            "adaptive_win_band_pct": _toml_float(b.get("adaptive_win_band_pct"), 8.0),
            "learner_enabled": _toml_bool(b.get("learner_enabled"), False),
            "optimizer_enabled": _toml_bool(b.get("optimizer_enabled"), False),
            "optimizer_interval_sec": _toml_int(b.get("optimizer_interval_sec"), 900),
            "optimizer_train_hours": _toml_float(b.get("optimizer_train_hours"), 4.0),
            "optimizer_holdout_pct": _toml_float(b.get("optimizer_holdout_pct"), 0.25),
            "optimizer_max_delta_spread_bps": _toml_int(b.get("optimizer_max_delta_spread_bps"), 6),
            "optimizer_max_delta_size_pct": _toml_float(b.get("optimizer_max_delta_size_pct"), 50.0),
            "optimizer_min_fills": _toml_int(b.get("optimizer_min_fills"), 20),
            "optimizer_objective": str(b.get("optimizer_objective", "total_dollar_wins")),
            "learner_loss_lookback_sells": _toml_int(b.get("learner_loss_lookback_sells"), 5),
            "learner_widen_on_avg_loss": _toml_bool(b.get("learner_widen_on_avg_loss"), True),
            "per_trade_profitability": _toml_bool(b.get("per_trade_profitability"), False),
            "min_total_pnl_usd": _toml_opt_float(b.get("min_total_pnl_usd")),
            "daily_profit_target_usd": _toml_opt_float(b.get("daily_profit_target_usd")),
            "daily_loss_limit_usd": _toml_opt_float(b.get("daily_loss_limit_usd")),
            "max_drawdown_pct": _toml_opt_float(b.get("max_drawdown_pct")),
            "fee_tier": {"label": "n/a", "maker_bps": 0.0},
            "fee_tier_by_pair": fee_tier_by_pair,
            "volume_30d": round(vol_30d, 2),
            "volume_30d_source": "n/a",
            "rate_limit_order_per_sec": _toml_int(b.get("rate_limit_order_per_sec"), 10),
            "rate_limit_burst": _toml_int(b.get("rate_limit_burst"), 20),
            "threat_quoting_pause": _toml_bool(b.get("threat_quoting_pause"), True),
            "abort_on_withdraw_permission": _toml_bool(b.get("abort_on_withdraw_permission"), False),
            "trailing_stop_enabled": _toml_bool(b.get("trailing_stop_enabled"), False),
            "trailing_stop_pct": _toml_float(b.get("trailing_stop_pct"), 50.0),
            "take_profit_usd": _toml_opt_float(b.get("take_profit_usd")),
            "oco_enabled": _toml_bool(b.get("oco_enabled"), False),
            "oco_stop_bps": _toml_int(b.get("oco_stop_bps"), 30),
            "oco_tp_bps": _toml_int(b.get("oco_tp_bps"), 30),
            "twap_enabled": _toml_bool(b.get("twap_enabled"), False),
            "twap_slice_count": _toml_int(b.get("twap_slice_count"), 5),
            "twap_duration_sec": _toml_int(b.get("twap_duration_sec"), 30),
            "btd_enabled": _toml_bool(b.get("btd_enabled"), False),
            "btd_sma_short": _toml_int(b.get("btd_sma_short"), 20),
            "btd_sma_long": _toml_int(b.get("btd_sma_long"), 60),
            "btd_levels": _toml_int(b.get("btd_levels"), 3),
            "btd_step_bps": _toml_int(b.get("btd_step_bps"), 20),
            "btd_size_multiplier": _toml_float(b.get("btd_size_multiplier"), 1.5),
            "learner_interval_sec": _toml_int(b.get("learner_interval_sec"), 30),
            "learner_min_samples": _toml_int(b.get("learner_min_samples"), 2),
            "learner_max_daily_adjustments": _toml_int(b.get("learner_max_daily_adjustments"), 50),
            "learner_lookback_max_age_sec": _toml_int(b.get("learner_lookback_max_age_sec"), 3600),
            "adaptive_min_sample_sells": _toml_int(b.get("adaptive_min_sample_sells"), 10),
            "adaptive_lookback_sells": _toml_int(b.get("adaptive_lookback_sells"), 30),
            "adaptive_spread_step_bps": _toml_int(b.get("adaptive_spread_step_bps"), 2),
            "adaptive_spread_floor_bps": _toml_int(b.get("adaptive_spread_floor_bps"), 1),
            "adaptive_spread_ceiling_bps": _toml_int(b.get("adaptive_spread_ceiling_bps"), 120),
            "momentum_hold_sells": _toml_int(b.get("momentum_hold_sells"), 4),
            "momentum_hold_sec": _toml_int(b.get("momentum_hold_sec"), 30),
            "decay_start_sec": _toml_int(b.get("decay_start_sec"), 90),
            "decay_interval_sec": _toml_int(b.get("decay_interval_sec"), 60),
            "decay_step_bps": _toml_int(b.get("decay_step_bps"), 1),
            "pain_floor_decay_hours": _toml_float(b.get("pain_floor_decay_hours"), 1.0),
            "threat_imbalance_threshold": _toml_float(b.get("threat_imbalance_threshold"), 0.65),
            "threat_spread_blowout_ratio": _toml_float(b.get("threat_spread_blowout_ratio"), 2.5),
            "threat_velocity_bps": _toml_int(b.get("threat_velocity_bps"), 25),
            "threat_critical_velocity_bps": _toml_int(b.get("threat_critical_velocity_bps"), 80),
            "threat_spread_multiplier": _toml_float(b.get("threat_spread_multiplier"), 1.35),
            "depeg_threshold_bps": _toml_int(b.get("depeg_threshold_bps"), 15),
            "min_quote_half_spread_bps": _toml_int(b.get("min_quote_half_spread_bps"), 1),
            "mev_detection_enabled": _toml_bool(b.get("mev_detection_enabled"), False),
            "mev_bot_widen_scale": _toml_float(b.get("mev_bot_widen_scale"), 1.25),
            "mev_arb_widen_scale": _toml_float(b.get("mev_arb_widen_scale"), 1.15),
            "mev_clean_tighten_scale": _toml_float(b.get("mev_clean_tighten_scale"), 0.92),
            "mev_bot_score_threshold": _toml_float(b.get("mev_bot_score_threshold"), 0.55),
            "mev_detector_window_sec": _toml_float(b.get("mev_detector_window_sec"), 4.0),
            "fill_cooldown_sec": _toml_float(b.get("fill_cooldown_sec"), 5.0),
            "presets": [],
            "pair_archetypes": {},
            "scalp": self._scalp_config_summary(),
        }

    def _scalp_config_summary(self) -> dict | None:
        sr = self._scalp_runtime
        if sr is not None:
            c = sr.config
            return {
                "enabled": c.enabled,
                "sim_mode": sr._trader.sim_mode,
                "allocated_capital_usd": c.allocated_capital_usd,
                "max_concurrent_positions": c.max_concurrent_positions,
                "order_type": c.order_type,
                "fee_bps_maker_per_leg": float(c.fee_bps_per_leg),
                "fee_bps_taker_per_leg": float(c.fee_bps_taker_per_leg),
                "fee_bps_effective_per_leg": float(effective_scalp_fee_bps_per_leg(c)),
                "fee_bps_wfo_sim_per_leg": float(wfo_fee_bps_per_leg(c)),
                "wfo_assume_taker_fee": bool(getattr(c, "wfo_assume_taker_fee", False)),
                "fee_usd_per_contract_per_leg": float(c.fee_usd_per_contract_per_leg),
                "scalp_fee_assumption_revision": int(
                    getattr(c, "scalp_fee_assumption_revision", 0) or 0
                ),
                "fee_tier_30d_volume_usd": getattr(c, "fee_tier_30d_volume_usd", None),
                "fee_tier_volume_source": str(
                    getattr(c, "fee_tier_volume_source", "exchange")
                ),
                "fee_tier_poll_interval_sec": float(
                    getattr(c, "fee_tier_poll_interval_sec", 900.0) or 900.0
                ),
                "fee_tier_add_bot_fill_notional": bool(
                    getattr(c, "fee_tier_add_bot_fill_notional", False)
                ),
                "fee_tier_auto_apply_exchange_fee_rates": bool(
                    getattr(c, "fee_tier_auto_apply_exchange_fee_rates", True)
                ),
                "daily_loss_limit_pct": c.daily_loss_limit_pct,
                "auto_mode_fallback": str(
                    getattr(c, "auto_mode_fallback", "sar_chop") or "sar_chop"
                ),
                "pair_keys": list(c.pairs.keys()),
            }
        c = self._scalp_cfg
        if c is None:
            return None
        return {
            "enabled": c.enabled,
            "sim_mode": c.sim_mode,
            "allocated_capital_usd": c.allocated_capital_usd,
            "max_concurrent_positions": c.max_concurrent_positions,
            "order_type": c.order_type,
            "fee_bps_maker_per_leg": float(c.fee_bps_per_leg),
            "fee_bps_taker_per_leg": float(c.fee_bps_taker_per_leg),
            "fee_bps_effective_per_leg": float(effective_scalp_fee_bps_per_leg(c)),
            "fee_bps_wfo_sim_per_leg": float(wfo_fee_bps_per_leg(c)),
            "wfo_assume_taker_fee": bool(getattr(c, "wfo_assume_taker_fee", False)),
            "fee_usd_per_contract_per_leg": float(c.fee_usd_per_contract_per_leg),
            "scalp_fee_assumption_revision": int(
                getattr(c, "scalp_fee_assumption_revision", 0) or 0
            ),
            "fee_tier_30d_volume_usd": getattr(c, "fee_tier_30d_volume_usd", None),
            "fee_tier_volume_source": str(
                getattr(c, "fee_tier_volume_source", "exchange")
            ),
            "fee_tier_poll_interval_sec": float(
                getattr(c, "fee_tier_poll_interval_sec", 900.0) or 900.0
            ),
            "fee_tier_add_bot_fill_notional": bool(
                getattr(c, "fee_tier_add_bot_fill_notional", False)
            ),
            "fee_tier_auto_apply_exchange_fee_rates": bool(
                getattr(c, "fee_tier_auto_apply_exchange_fee_rates", True)
            ),
            "daily_loss_limit_pct": c.daily_loss_limit_pct,
            "auto_mode_fallback": str(
                getattr(c, "auto_mode_fallback", "sar_chop") or "sar_chop"
            ),
            "pair_keys": list(c.pairs.keys()),
        }

    async def _broadcast(self, msg: dict) -> None:
        # Encode once; send the same string to every client. orjson skips stdlib json's
        # per-client re-encode (aiohttp.send_json) which was ~30-50ms on the big snapshot.
        try:
            payload = _encode_ws(msg)
        except Exception:
            LOG.exception("_broadcast encode failed; dropping message")
            return
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send_str(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    def _schedule_snapshot_bump(self) -> None:
        try:
            asyncio.get_running_loop().create_task(self._push_snapshot_now())
        except RuntimeError:
            pass

    async def _push_snapshot_now(self) -> None:
        if not self._clients:
            return
        try:
            snap = self._state.snapshot()
            snap["scalp"] = self._scalp_payload(include_closed_candles=True)
            self._attach_ui_log_tail(snap)
            await self._broadcast({"type": "snapshot", "data": _json_sanitize_ws(snap)})
        except Exception:
            LOG.debug("_push_snapshot_now failed", exc_info=True)

    def _attach_ui_log_tail(self, snap: dict[str, Any]) -> None:
        snap["ui_log_tail"] = self._ui_log.tail()

    async def log_action(
        self,
        level: str = "info",
        title: str = "",
        detail: str = "",
        source: str = "action",
        *,
        kind: str = "action",
        meta: dict[str, Any] | None = None,
    ) -> None:
        row = self._ui_log.append(
            kind=kind,
            level=level,
            title=title,
            detail=detail,
            source=source,
            meta=meta,
        )
        await self._broadcast({"type": "log_event", "data": _json_sanitize_ws(row)})

    async def broadcast_alert(
        self,
        level: str,
        title: str,
        detail: str = "",
        source: str = "",
        persistent: bool = False,
        exchange_error_id: str | None = None,
    ) -> None:
        """Push a UI alert to all dashboard clients.

        level: "error" | "warning" | "info" | "success"
        """
        import time as _time

        ts = _time.time()
        row = self._ui_log.append(
            kind="alert",
            level=level,
            title=title,
            detail=detail,
            source=source,
            ts=ts,
            exchange_error_id=exchange_error_id,
            persistent=persistent,
        )
        payload: dict = {
            "type": "alert",
            "id": row["id"],
            "level": level,
            "title": title,
            "detail": detail,
            "source": source,
            "ts": ts,
            "persistent": bool(persistent),
        }
        if exchange_error_id:
            payload["exchange_error_id"] = exchange_error_id
        await self._broadcast(payload)
        await self._broadcast({"type": "log_event", "data": _json_sanitize_ws(row)})

    def _scalp_placeholder_payload(self) -> dict:
        """Shape matches live `ScalpRuntime.snapshot()` enough for the dashboard when
        `scalp_runtime` is not wired yet (early listen after dashboard.start) or snapshot fails."""
        c = self._scalp_cfg
        venue = str(getattr(c, "venue", "coinbase_perps") or "coinbase_perps") if c else "coinbase_perps"
        enabled = bool(c.enabled) if c else False
        sim_mode = bool(c.sim_mode) if c else False
        max_cp = int(c.max_concurrent_positions) if c else 0
        pair_symbols = {k: pc.symbol for k, pc in c.pairs.items()} if c else {}
        req_go_live = bool(getattr(c, "require_manual_go_live", False)) if c else False
        session_policy = {
            "warmup_enabled": True,
            "warmup_min_bars": 500,
            "warmup_require_champion": True,
            "warmup_max_hours": 0.0,
            "wfo_enabled": True,
        }
        warmup_enabled = False
        if c is not None:
            _wfo = WFOConfig(
                continuous_eval_hours=float(
                    getattr(c, "wfo_continuous_eval_hours", 672.0) or 672.0,
                ),
                continuous_warmup_hours=float(
                    getattr(c, "wfo_continuous_warmup_hours", 168.0) or 168.0,
                ),
            )
            session_policy.update({
                "warmup_enabled": bool(c.warmup_enabled),
                "warmup_min_bars": int(c.warmup_min_bars),
                "warmup_require_champion": bool(c.warmup_require_champion),
                "require_champion_to_trade": bool(
                    getattr(c, "require_champion_to_trade", True),
                ),
                "warmup_max_hours": float(c.warmup_max_hours),
                "wfo_enabled": bool(c.wfo_enabled),
                "wfo_interval_sec": float(c.wfo_interval_sec),
                "wfo_train_hours": float(c.wfo_train_hours),
                "wfo_objective": str(c.wfo_objective),
                "wfo_continuous_eval_hours": float(c.wfo_continuous_eval_hours),
                "wfo_continuous_warmup_hours": float(c.wfo_continuous_warmup_hours),
                "wfo_continuous_min_trades": int(c.wfo_continuous_min_trades),
                "param_tuner_interval_sec": float(c.param_tuner_interval_sec),
                "wfo_roll_span_hours": float(wfo_effective_roll_span_hours(_wfo)),
                "wfo_min_trades": int(c.wfo_min_trades),
                "wfo_min_holdout_trades": int(getattr(c, "wfo_min_holdout_trades", 0) or 0),
                "wfo_min_profit_factor": float(getattr(c, "wfo_min_profit_factor", 0.8) or 0.8),
                "wfo_min_win_rate": float(getattr(c, "wfo_min_win_rate", 0.20) or 0.20),
                "wfo_max_train_drawdown_pct": float(
                    getattr(c, "wfo_max_train_drawdown_pct", 30.0) or 30.0,
                ),
                "backtest_funding_enabled": bool(getattr(c, "backtest_funding_enabled", False)),
                "backtest_funding_bps_per_hour": float(
                    getattr(c, "backtest_funding_bps_per_hour", 0.0) or 0.0
                ),
                "scalp_fee_assumption_revision": int(
                    getattr(c, "scalp_fee_assumption_revision", 0) or 0
                ),
                "fee_tier_30d_volume_usd": getattr(c, "fee_tier_30d_volume_usd", None),
                "fee_tier_volume_source": str(
                    getattr(c, "fee_tier_volume_source", "exchange" if venue == "coinbase_perps" else "manual")
                ),
                "fee_tier_poll_interval_sec": float(
                    getattr(c, "fee_tier_poll_interval_sec", 900.0) or 900.0
                ),
                "fee_tier_add_bot_fill_notional": bool(
                    getattr(c, "fee_tier_add_bot_fill_notional", False)
                ),
                "fee_tier_auto_apply_exchange_fee_rates": bool(
                    getattr(c, "fee_tier_auto_apply_exchange_fee_rates", True)
                ),
                "scalp_auto_invalidate_champion_on_fee_change": bool(
                    getattr(c, "scalp_auto_invalidate_champion_on_fee_change", False)
                ),
                "param_tuner_require_wfo_champion": bool(
                    getattr(c, "param_tuner_require_wfo_champion", True)
                ),
                "param_tuner_allow_mode_override_champion": bool(
                    getattr(c, "param_tuner_allow_mode_override_champion", False)
                ),
                "wfo_assume_taker_fee": bool(getattr(c, "wfo_assume_taker_fee", False)),
                "wfo_fee_bps_sim_per_leg": float(wfo_fee_bps_per_leg(c)),
                "wfo_forward_min_trades": int(getattr(c, "wfo_forward_min_trades", 10)),
                "wfo_forward_demotion_threshold": float(
                    getattr(c, "wfo_forward_demotion_threshold", -0.5)
                ),
                "funding_warn_bps_per_hour": float(
                    getattr(c, "funding_warn_bps_per_hour", 5.0) or 5.0
                ),
                "empirical_market_promotion_enabled": bool(
                    getattr(c, "empirical_market_promotion_enabled", False)
                ),
                "empirical_market_missed_move_bps": float(
                    getattr(c, "empirical_market_missed_move_bps", 12.0) or 12.0
                ),
                "empirical_market_miss_eval_window_sec": float(
                    getattr(c, "empirical_market_miss_eval_window_sec", 600.0) or 600.0
                ),
                "empirical_market_min_pattern_in_window": int(
                    getattr(c, "empirical_market_min_pattern_in_window", 3)
                ),
                "empirical_market_pattern_window_sec": float(
                    getattr(c, "empirical_market_pattern_window_sec", 86400.0) or 86400.0
                ),
                "empirical_market_promotion_entries": int(
                    getattr(c, "empirical_market_promotion_entries", 2)
                ),
                "empirical_market_promotion_cooldown_sec": float(
                    getattr(c, "empirical_market_promotion_cooldown_sec", 3600.0) or 3600.0
                ),
                "empirical_market_ttl_cancel_arms_promotion": bool(
                    getattr(c, "empirical_market_ttl_cancel_arms_promotion", False)
                ),
                "empirical_market_ttl_cancel_promotion_entries": int(
                    getattr(c, "empirical_market_ttl_cancel_promotion_entries", 1)
                ),
                "daily_loss_set_scalp_halt": bool(
                    getattr(c, "daily_loss_set_scalp_halt", True),
                ),
                "slip_calibration_enabled": bool(getattr(c, "slip_calibration_enabled", False)),
                "slip_calibration_ema_alpha": float(
                    getattr(c, "slip_calibration_ema_alpha", 0.2) or 0.2,
                ),
                "slip_calibration_min_samples": int(
                    getattr(c, "slip_calibration_min_samples", 8) or 8,
                ),
                "slip_calibration_floor_bps": float(
                    getattr(c, "slip_calibration_floor_bps", 0.0) or 0.0,
                ),
                "slip_calibration_cap_bps": float(
                    getattr(c, "slip_calibration_cap_bps", 80.0) or 80.0,
                ),
                "slip_calibration_mode": str(
                    getattr(c, "slip_calibration_mode", "max_with_config") or "max_with_config",
                ),
            })
            warmup_enabled = bool(c.warmup_enabled)
        _ft_src = "manual"
        _manual_vol = None
        _poll_iv = 900.0
        if c is not None:
            _ft_src = str(
                getattr(
                    c,
                    "fee_tier_volume_source",
                    "exchange" if venue == "coinbase_perps" else "manual",
                )
            ).lower()
            _manual_vol = getattr(c, "fee_tier_30d_volume_usd", None)
            _poll_iv = float(getattr(c, "fee_tier_poll_interval_sec", 900.0) or 900.0)
        _disp = None
        if _ft_src == "manual" and _manual_vol is not None:
            try:
                _disp = float(_manual_vol)
            except (TypeError, ValueError):
                _disp = None
        _mk = float(getattr(c, "fee_bps_per_leg", 0.0) or 0.0) if c is not None else 0.0
        _tk = float(getattr(c, "fee_bps_taker_per_leg", 0.0) or 0.0) if c is not None else 0.0
        _auto_fee = bool(getattr(c, "fee_tier_auto_apply_exchange_fee_rates", True)) if c is not None else True
        _fee_tier = {
            "volume_source": _ft_src,
            "display_volume_usd": _disp,
            "manual_baseline_usd": float(_manual_vol) if _manual_vol is not None else None,
            "bot_fill_usd_session": 0.0,
            "exchange": None,
            "last_poll_ts": 0.0,
            "poll_error": None,
            "poll_interval_sec": _poll_iv,
            "auto_apply_exchange_fee_rates": _auto_fee,
            "effective_maker_bps": _mk,
            "effective_taker_bps": _tk,
        }
        return {
            "runtime_attached": False,
            "enabled": enabled,
            "venue": venue,
            "sim_mode": sim_mode,
            "max_concurrent_positions": max_cp,
            "startup_phase": "standby",
            "operator": {
                "standby": True,
                "prep_busy": False,
                "require_manual_go_live": req_go_live,
                "flow": None,
                "flow_seq": 0,
                "flow_event": None,
                "startup_phase": "standby",
                "can_begin_warmup": True,
                "can_go_live": False,
                "warmup_steps": [],
            },
            "session_policy": session_policy,
            "portfolio_risk": {
                "scalp_risk_halted": bool(getattr(self._state, "scalp_risk_halted", False)),
                "scalp_risk_halt_reason": str(getattr(self._state, "scalp_risk_halt_reason", "") or ""),
                "scalp_risk_halted_ts": float(getattr(self._state, "scalp_risk_halted_ts", 0.0) or 0.0),
                "scalp_entries_blocked": self._state.scalp_entries_blocked(),
                "mm_spread_bot_enabled": bool(getattr(self._state, "mm_spread_bot_enabled", False)),
                "mm_risk_halted": bool(getattr(self._state, "risk_halted", False)),
                **self._state.scalp_exchange_throttle_diag(),
            },
            "config_warnings": [],
            "slip_calibration": {
                "enabled": bool(getattr(c, "slip_calibration_enabled", False)) if c else False,
                "samples": 0,
                "ema_bps": None,
                "effective_bps": round(float(getattr(c, "slippage_bps", 1.0) or 1.0), 6) if c else 1.0,
                "config_bps": round(float(getattr(c, "slippage_bps", 1.0) or 1.0), 6) if c else 1.0,
                "mode": str(getattr(c, "slip_calibration_mode", "max_with_config") or "max_with_config")
                if c
                else "max_with_config",
            },
            "warmup": {"phase": "disabled", "enabled": warmup_enabled, "startup_steps": []},
            "trader": {
                "open_positions": {},
                "open_count": 0,
                "daily_pnl": 0.0,
                "reserved_capital": 0.0,
                "trade_history": [],
                "sim_mode": sim_mode,
            },
            "pair_symbols": pair_symbols,
            "active_modes": {},
            "mode_sources": {},
            "indicators": {},
            "candles": {},
            "orderbooks": {},
            "exchange_open_orders": [],
            "exchange_open_orders_all": [],
            "exchange_open_orders_outside_pairs": [],
            "fee_tier": _fee_tier,
        }

    def _scalp_payload(self, *, include_closed_candles: bool = True) -> dict:
        """Always returns a dict so WebSocket snapshots never omit `scalp` (avoids UI stuck on SCALP_ENGINE_OFFLINE)."""
        sr = self._scalp_runtime
        if sr is None:
            return self._scalp_placeholder_payload()
        try:
            out = sr.snapshot(include_closed_candles=include_closed_candles)
            if isinstance(out, dict):
                out["runtime_attached"] = True
                return out
        except Exception:
            LOG.exception("scalp snapshot failed; sending placeholder")
        ph = self._scalp_placeholder_payload()
        ph["snapshot_error"] = True
        return ph

    async def _send_rebuild_frontend_result(self, ws: web.WebSocketResponse, ok: bool, detail: str) -> None:
        payload = {"type": "rebuild_frontend_result", "ok": ok, "detail": str(detail)[:8000]}
        if ws not in self._clients:
            return
        if getattr(ws, "closed", False):
            return
        try:
            await ws.send_str(_encode_ws(_json_sanitize_ws(payload)))
        except Exception:
            LOG.debug("rebuild_frontend_result: send failed", exc_info=True)

    async def _rebuild_frontend_dist_task(self, ws: web.WebSocketResponse) -> None:
        await self.broadcast_alert(
            "info",
            "Building dashboard",
            "Running npm run build in frontend-new (often 30–90s)…",
            "dashboard",
        )
        loop = asyncio.get_running_loop()
        try:
            code, tail = await loop.run_in_executor(None, _run_frontend_npm_build)
        except Exception:
            LOG.exception("rebuild_frontend_dist: executor failed")
            brief = "Executor failed — see server log"
            await self.broadcast_alert("error", "Dashboard build failed", brief, "dashboard")
            await self._send_rebuild_frontend_result(ws, False, brief)
            return
        if code == 0:
            LOG.info("rebuild_frontend_dist: npm run build succeeded")
            await self.broadcast_alert(
                "success",
                "Dashboard dist rebuilt",
                "Hard refresh the browser (Ctrl+Shift+R) to load new JS/CSS from dist.",
                "dashboard",
            )
            await self._send_rebuild_frontend_result(ws, True, tail[-4000:] if tail else "ok")
        else:
            LOG.warning("rebuild_frontend_dist: npm exited %s", code)
            brief = (tail[-4000:] if tail else "") or f"exit code {code}"
            await self.broadcast_alert(
                "error",
                "Dashboard build failed",
                brief[:600],
                "dashboard",
            )
            await self._send_rebuild_frontend_result(ws, False, brief)

    async def _push_loop(self) -> None:
        # Tick cadence: live/incomplete bar, indicators, positions, logs — NOT closed candles.
        # Closed candles are large and change only on bar-close, so the runtime fires an
        # on-demand snapshot bump (_schedule_snapshot_bump → _push_snapshot_now with
        # include_closed_candles=True) from _on_closed_candle. Initial WS connect still
        # sends the full closed-candle history.
        while True:
            try:
                if self._clients:
                    snap = self._state.snapshot()
                    snap["scalp"] = self._scalp_payload(include_closed_candles=False)
                    self._attach_ui_log_tail(snap)
                    snap = _json_sanitize_ws(snap)
                    await self._broadcast({"type": "snapshot", "data": snap})
            except Exception:
                LOG.exception("_push_loop error (will retry)")
            await asyncio.sleep(0.5)

    async def _auto_rebuild_frontend_if_stale(self) -> None:
        """Background task: rebuild frontend-new dist when source files are newer than the bundle.

        Runs once at startup, non-blocking.  On success the browser needs a hard refresh
        (Ctrl+Shift+R); we broadcast an info alert so the operator knows.
        """
        try:
            stale = await asyncio.to_thread(_frontend_src_newer_than_dist)
        except Exception:
            LOG.debug("frontend stale-check failed (non-fatal)", exc_info=True)
            return
        if not stale:
            LOG.debug("frontend-new/dist is up-to-date — no rebuild needed")
            return
        LOG.warning(
            "frontend-new/dist is STALE vs source files — rebuilding automatically "
            "(~30 s). Hard-refresh the browser when done."
        )
        try:
            code, tail = await asyncio.to_thread(_run_frontend_npm_build)
        except Exception:
            LOG.exception("frontend auto-rebuild: executor error (non-fatal)")
            return
        if code == 0:
            LOG.info("frontend auto-rebuild: SUCCESS — hard-refresh the browser (Ctrl+Shift+R).")
            await self.broadcast_alert(
                "info",
                "Dashboard rebuilt",
                "Source files were newer than the dist — rebuilt automatically. "
                "Hard-refresh the browser (Ctrl+Shift+R) to load the updated UI.",
                "dashboard",
            )
        else:
            LOG.error(
                "frontend auto-rebuild: FAILED (exit %d) — run `npm run build` manually in frontend-new/. "
                "Last output: %s",
                code,
                tail[-600:],
            )

    async def start(self) -> web.AppRunner:
        self._asyncio_loop = asyncio.get_running_loop()
        self._state._alert_loop = self._asyncio_loop
        self._install_ui_log_mirror()
        self._push_task = asyncio.create_task(self._push_loop())
        asyncio.create_task(self._auto_rebuild_frontend_if_stale(), name="frontend_auto_rebuild")
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(
            runner, self._config.server.host, self._config.server.port,
            reuse_address=False,
        )
        await site.start()
        if FRONTEND_DIR.resolve() == FRONTEND_NEW_DIST.resolve():
            LOG.info("Dashboard static UI: frontend-new/dist (scalp UI)")
        else:
            LOG.warning(
                "Dashboard static UI: legacy frontend/ — run `npm run build` in frontend-new "
                "and restart backend to serve the current scalp UI here (port %d)",
                self._config.server.port,
            )
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
        if self._ui_mirror_handler is not None:
            for name in ("backend.server", "arceus"):
                logging.getLogger(name).removeHandler(self._ui_mirror_handler)
            self._ui_mirror_handler = None
        self._asyncio_loop = None
        self._state._alert_loop = None
        for ws in list(self._clients):
            await ws.close()
