"""Entry point — dashboard server and Coinbase scalp bot."""

from __future__ import annotations

import asyncio
import atexit
import logging
import signal
import sys

from .scalp_bot.scalp_config import load_scalp_config
from .scalp_bot.scalp_runtime import ScalpRuntime
from .coinbase_order_manager import CoinbaseOrderManager
from .config import load_config, load_raw_toml
from .session_logger import SessionLogger
from .state import BotState
from .windows_power import allow_system_sleep, prevent_system_sleep
from .ws_server import DashboardServer


async def run() -> None:
    session_log: SessionLogger | None = None
    config = load_config()
    try:
        _raw_toml = load_raw_toml()
    except Exception:
        _raw_toml = {}
    scalp_cfg = load_scalp_config(_raw_toml)

    logging.basicConfig(
        format="%(asctime)s %(name)-20s %(levelname)8s | %(message)s",
        datefmt="%H:%M:%S",
        level=getattr(logging, config.server.log_level, logging.INFO),
    )
    log = logging.getLogger("arceus")

    if sys.platform == "win32":
        prevent_system_sleep()
        atexit.register(allow_system_sleep)

    state = BotState()
    state.mode = config.mode

    session_log = SessionLogger(state, config)
    session_log.log_session_start()

    coinbase_mgr: CoinbaseOrderManager | None = None

    dashboard = DashboardServer(
        state,
        config,
        scalp_cfg=scalp_cfg,
        bot_toml=_raw_toml.get("bot", {}),
        pairs_toml=_raw_toml.get("pairs", {}),
        session_logger=session_log,
    )

    if (
        config.mode == "live"
        and scalp_cfg.enabled
        and scalp_cfg.pairs
        and getattr(scalp_cfg, "venue", "coinbase_perps") == "coinbase_perps"
        and config.coinbase_api_key
        and config.coinbase_api_secret
    ):
        ks = getattr(config, "coinbase_credential_slot", "1")
        kk = config.coinbase_api_key.strip()
        kmask = f"{kk[:28]}…{kk[-8:]}" if len(kk) > 36 else ("(short)" if kk else "(empty)")
        log.info(
            "Coinbase REST: credential slot=%s key=%s (set COINBASE_CDP_CREDENTIAL_SLOT=2 to use KEY2)",
            ks,
            kmask,
        )
        coinbase_mgr = CoinbaseOrderManager(state, config, scalp_cfg, session_logger=session_log)
        try:
            await coinbase_mgr.initialize()
            log.info("Coinbase Advanced Trade: scalp execution manager initialized")
        except Exception:
            log.exception("Coinbase Advanced Trade: failed to initialize scalp manager")
            coinbase_mgr = None
    else:
        log.info(
            "Coinbase manager not started (mode=%s enabled=%s venue=%s keys=%s)",
            config.mode,
            scalp_cfg.enabled,
            getattr(scalp_cfg, "venue", "coinbase_perps"),
            bool(config.coinbase_api_key),
        )

    scalp_runtime = ScalpRuntime(state, scalp_cfg, coinbase_mgr, session_logger=session_log)
    if coinbase_mgr is not None and scalp_cfg.enabled and scalp_cfg.pairs:
        coinbase_mgr.register_scalp_runtime(scalp_runtime)
        try:
            await coinbase_mgr.refresh_scalp_exchange_snapshots()
        except Exception:
            log.exception("Coinbase perps: startup position reconcile failed")
    scalp_runtime.start()
    dashboard.bind_scalp_runtime(scalp_runtime)

    runner = await dashboard.start()
    log.info("Dashboard up on port %d — Coinbase scalp mode.", config.server.port)

    async def _snapshot_loop() -> None:
        while True:
            await asyncio.sleep(300)
            try:
                session_log.log_snapshot()
                if scalp_cfg.enabled and scalp_runtime is not None:
                    try:
                        session_log.log_scalp_snapshot(scalp_runtime.snapshot())
                    except Exception:
                        log.debug("Scalp snapshot log error", exc_info=True)
            except Exception:
                log.debug("Snapshot loop error", exc_info=True)

    snapshot_task = asyncio.create_task(_snapshot_loop(), name="session_snapshot")
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("Shutdown signal received")
        shutdown_event.set()

    if sys.platform == "win32":
        signal.signal(signal.SIGINT, lambda *_: _signal_handler())
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, lambda *_: _signal_handler())  # type: ignore[attr-defined]
    else:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

    try:
        await shutdown_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        log.info("Shutting down...")
        snapshot_task.cancel()
        await scalp_runtime.stop()
        if coinbase_mgr:
            await coinbase_mgr.close()
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
