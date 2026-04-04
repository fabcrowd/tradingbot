"""Entry point — starts the order book, spread engine, and dashboard server."""

from __future__ import annotations

import asyncio
import atexit
import logging
import signal
import sys
import time

from kraken.spot import User

from .adaptive_spread import AdaptiveSpreadTuner
from .book_client import start_book_client
from .scalp_bot.scalp_config import load_scalp_config
from .scalp_bot.scalp_runtime import ScalpRuntime
from .bot_classifier import BotClassifier
from .cex_bot_detector import CEXBotDetector
from .chain_detector import chain_for_pair
from .config import load_config
from .inventory import InventoryManager
from .live_order_manager import LiveOrderManager
from .order_manager import OrderManager
from .optimizer import WalkForwardOptimizer
from .pnl import PnLTracker
from .runtime import BotRuntime
from .session_logger import SessionLogger
from .spread_engine import SpreadEngine
from .state import BotState
from .strategy_learner import StrategyLearner
from .threat_detector import ThreatDetector
from .windows_power import allow_system_sleep, prevent_system_sleep
from .ws_server import DashboardServer




async def _check_api_permissions(config, session_log, log) -> None:
    if not config.api_key or not config.api_secret:
        return
    user = User(key=config.api_key, secret=config.api_secret)
    has_withdraw = False
    detail = "withdraw permission check unavailable"
    try:
        if hasattr(user, "get_withdrawal_methods"):
            try:
                await asyncio.to_thread(user.get_withdrawal_methods, asset="USD")
            except TypeError:
                await asyncio.to_thread(user.get_withdrawal_methods)
            has_withdraw = True
            detail = "API key appears to allow withdrawal method queries"
    except Exception as exc:
        detail = f"withdrawal endpoint denied/unavailable: {exc!s}"

    if has_withdraw:
        log.warning("SECURITY: API key may include withdrawal permissions. Restrict to trade-only key.")
        if session_log is not None and hasattr(session_log, "log_security"):
            session_log.log_security("warning", detail)
        if config.bot.abort_on_withdraw_permission:
            raise RuntimeError("Aborting startup: API key appears to have withdrawal permissions")
    else:
        if session_log is not None and hasattr(session_log, "log_security"):
            session_log.log_security("ok", detail)

async def run() -> None:
    adaptive_tuner: AdaptiveSpreadTuner | None = None
    learner: StrategyLearner | None = None
    optimizer: WalkForwardOptimizer | None = None
    session_log: SessionLogger | None = None
    config = load_config()
    try:
        import sys as _sys
        if _sys.version_info >= (3, 11):
            import tomllib as _tomllib
        else:
            import tomli as _tomllib
        _cfg_path = __file__
        import pathlib as _pathlib
        _raw_toml = _tomllib.loads(
            (_pathlib.Path(_cfg_path).resolve().parent.parent.parent / "config.toml")
            .read_text(encoding="utf-8")
        )
    except Exception:
        _raw_toml = {}
    scalp_cfg = load_scalp_config(_raw_toml)

    logging.basicConfig(
        format="%(asctime)s %(name)-20s %(levelname)8s | %(message)s",
        datefmt="%H:%M:%S",
        level=getattr(logging, config.server.log_level, logging.INFO),
    )
    log = logging.getLogger("mitch")
    log.info(
        "Loading config: %d pairs, trading=%s, mode=%s",
        len(config.pairs),
        config.pair_keys_for_trading(),
        config.mode,
    )
    if sys.platform == "win32":
        prevent_system_sleep()
        atexit.register(allow_system_sleep)

    state = BotState()
    state.mode = config.mode
    for key, pc in config.pairs.items():
        state.init_pair(key, pc.symbol)
        state.pairs[key].chain = chain_for_pair(key, config).value
    trading = config.pair_keys_for_trading()
    if trading:
        state.active_pair_key = trading[0]
    elif config.pairs:
        state.active_pair_key = next(iter(config.pairs))

    session_log = SessionLogger(state, config)

    pnl = PnLTracker(state, mode=config.mode)
    inventory = InventoryManager(state, config)
    paper_mgr = OrderManager(state, config)

    live_mgr: LiveOrderManager | None = None
    if config.mode == "live" and config.api_key:
        live_mgr = LiveOrderManager(state, config, inventory, pnl, session_logger=session_log)
        await live_mgr.initialize()
        await _check_api_permissions(config, session_log, log)
        await asyncio.sleep(2)
        await asyncio.to_thread(inventory.sync_from_kraken)

    runtime = BotRuntime(
        state=state,
        config=config,
        pnl=pnl,
        inventory=inventory,
        paper_mgr=paper_mgr,
        live_mgr=live_mgr,
        session_logger=session_log,
    )

    engine = SpreadEngine(state, config, paper_mgr, inventory, pnl, session_logger=session_log)
    if live_mgr:
        engine.set_live_order_mgr(live_mgr)

    runtime.engine = engine

    if config.mode == "paper":
        for key, pc in config.pairs.items():
            ps = state.pairs[key]
            if ps.inventory_quote == 0 and ps.inventory_base == 0:
                inventory.set_initial(key, base=0.0, quote=50.0)

    threat_detector = ThreatDetector(config)

    # MEV / bot detection layer
    cex_bot_detector = CEXBotDetector(
        window_sec=config.bot.mev_detector_window_sec,
    )
    bot_classifier = BotClassifier(
        state, config, cex_bot_detector, session_logger=session_log,
    )

    dashboard = DashboardServer(state, config, engine, runtime)
    adaptive_tuner = AdaptiveSpreadTuner(
        state, config, engine, dashboard.broadcast_config,
    )
    adaptive_tuner.start()
    learner = StrategyLearner(state, config, engine, session_logger=session_log)
    learner.start()
    runtime.learner = learner
    optimizer = WalkForwardOptimizer(state, config, engine, session_logger=session_log)
    optimizer.start()
    runtime.optimizer = optimizer
    bot_classifier.start()

    scalp_runtime = ScalpRuntime(state, scalp_cfg, live_mgr)
    scalp_runtime.start()

    runner = await dashboard.start()

    log.info("Connecting to Kraken order book...")
    book_client = await start_book_client(
        state, config,
        threat_detector=threat_detector,
        cex_bot_detector=cex_bot_detector,
    )
    runtime.book_client = book_client

    await asyncio.sleep(3)

    log.info("Ready — engine paused. Press START on the dashboard to begin trading.")

    async def _snapshot_loop() -> None:
        while True:
            await asyncio.sleep(300)  # every 5 minutes
            try:
                session_log.log_snapshot()
            except Exception:
                log.debug("Snapshot loop error", exc_info=True)

    async def _volume_sync_loop() -> None:
        """Sync 30-day volume from Kraken every 5 min. Runs in a thread
        so the blocking REST call never touches the order path."""
        if not config.api_key or not config.api_secret:
            return
        from .fee_schedule import current_tier_info

        interval = 300  # 5 minutes
        while True:
            try:
                user = User(key=config.api_key, secret=config.api_secret)
                result = await asyncio.to_thread(user.get_trade_volume)
                vol_str = result.get("volume", "0")
                vol = float(vol_str)
                prev = state.volume_30d
                state.volume_30d = vol
                state.volume_30d_source = "kraken"
                state.volume_30d_synced_at = time.time()
                if abs(vol - prev) > 1.0:
                    tier = current_tier_info(vol)
                    log.info(
                        "Volume sync: $%.2f (was $%.2f) | maker=%d bps | next tier at $%s",
                        vol, prev, tier["maker_fee_bps"],
                        f'{tier.get("next_tier_threshold", "max"):.0f}'
                        if tier.get("next_tier_threshold") else "max",
                    )
            except Exception:
                log.debug("Volume sync error", exc_info=True)
            await asyncio.sleep(interval)

    WATCHDOG_TIMEOUT_SEC = 30.0
    WATCHDOG_ESCALATION_SEC = 30.0

    async def _watchdog_loop() -> None:
        """Detect frozen engine loop and escalate: alert -> soft restart -> risk halt."""
        restart_attempted_at = 0.0
        while True:
            await asyncio.sleep(5)
            if not state.running:
                restart_attempted_at = 0.0
                continue
            hb = state.last_engine_heartbeat_ts
            if hb <= 0:
                continue
            stale_sec = time.time() - hb
            if stale_sec < WATCHDOG_TIMEOUT_SEC:
                restart_attempted_at = 0.0
                continue

            if restart_attempted_at > 0:
                if time.time() - restart_attempted_at > WATCHDOG_ESCALATION_SEC:
                    log.critical(
                        "WATCHDOG: engine still frozen %.0fs after soft_restart — risk halt",
                        time.time() - restart_attempted_at,
                    )
                    state.push_alert(
                        "error",
                        "Watchdog: Engine Unrecoverable",
                        f"No heartbeat for {stale_sec:.0f}s. Halting and cancelling all orders.",
                        "watchdog",
                    )
                    state.risk_halted = True
                    state.risk_halt_reason = f"watchdog: engine frozen {stale_sec:.0f}s, soft_restart failed"
                    mgr = engine._active_order_mgr()
                    for key in config.pair_keys_for_trading():
                        try:
                            await mgr.cancel_all(key)
                        except Exception:
                            log.debug("Watchdog cancel_all failed for %s", key, exc_info=True)
                    state.running = False
                    restart_attempted_at = 0.0
                continue

            log.warning("WATCHDOG: engine heartbeat stale %.0fs — attempting soft_restart", stale_sec)
            state.push_alert(
                "warning",
                "Watchdog: Engine Frozen",
                f"No heartbeat for {stale_sec:.0f}s. Attempting automatic restart.",
                "watchdog",
            )
            restart_attempted_at = time.time()
            try:
                await engine.soft_restart()
            except Exception:
                log.exception("Watchdog soft_restart failed")

    snapshot_task = asyncio.create_task(_snapshot_loop(), name="session_snapshot")
    watchdog_task = asyncio.create_task(_watchdog_loop(), name="engine_watchdog")
    volume_sync_task = asyncio.create_task(_volume_sync_loop(), name="volume_sync")

    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("Shutdown signal received")
        shutdown_event.set()

    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

    try:
        if sys.platform == "win32":
            while not shutdown_event.is_set():
                await asyncio.sleep(0.25)
        else:
            await shutdown_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        log.info("Shutting down...")
        snapshot_task.cancel()
        watchdog_task.cancel()
        volume_sync_task.cancel()
        if adaptive_tuner is not None:
            await adaptive_tuner.stop()
        if learner is not None:
            await learner.stop()
        if optimizer is not None:
            await optimizer.stop()
        await scalp_runtime.stop()
        await bot_classifier.stop()
        await engine.stop()
        await paper_mgr.close()
        if live_mgr:
            await live_mgr.close()
        bc = runtime.book_client
        if bc is not None:
            try:
                await bc.close()
            except Exception:
                log.debug("Book client close failed", exc_info=True)
        await dashboard.stop()
        await runner.cleanup()
        if session_log is not None:
            session_log.close()
        log.info("Goodbye.")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
