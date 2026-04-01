"""Entry point — starts the order book, spread engine, and dashboard server."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .adaptive_spread import AdaptiveSpreadTuner
from .book_client import start_book_client
from .config import load_config
from .inventory import InventoryManager
from .live_order_manager import LiveOrderManager
from .order_manager import OrderManager
from .pnl import PnLTracker
from .runtime import BotRuntime
from .session_logger import SessionLogger
from .spread_engine import SpreadEngine
from .state import BotState
from .strategy_learner import StrategyLearner
from .threat_detector import ThreatDetector
from .ws_server import DashboardServer


async def run() -> None:
    adaptive_tuner: AdaptiveSpreadTuner | None = None
    learner: StrategyLearner | None = None
    session_log: SessionLogger | None = None
    config = load_config()

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

    state = BotState()
    state.mode = config.mode
    for key, pc in config.pairs.items():
        state.init_pair(key, pc.symbol)
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
        inventory.sync_from_kraken()

    runtime = BotRuntime(
        state=state,
        config=config,
        pnl=pnl,
        inventory=inventory,
        paper_mgr=paper_mgr,
        live_mgr=live_mgr,
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

    dashboard = DashboardServer(state, config, engine, runtime)
    adaptive_tuner = AdaptiveSpreadTuner(
        state, config, engine, dashboard.broadcast_config,
    )
    adaptive_tuner.start()
    learner = StrategyLearner(state, config, engine, session_logger=session_log)
    learner.start()
    runtime.learner = learner

    runner = await dashboard.start()

    log.info("Connecting to Kraken order book...")
    book_client = await start_book_client(state, config, threat_detector=threat_detector)
    runtime.book_client = book_client

    await asyncio.sleep(3)

    if config.mode == "live":
        for key in config.pairs:
            inventory.seed_cost_basis_from_mid(key)
        log.info("Re-seeded cost basis from live book mid prices")

    log.info("Starting spread engine...")
    await engine.start()

    session_log.log_session_start()
    state.session_start_ts = __import__("time").time()

    async def _snapshot_loop() -> None:
        while True:
            await asyncio.sleep(300)  # every 5 minutes
            try:
                session_log.log_snapshot()
            except Exception:
                pass

    snapshot_task = asyncio.create_task(_snapshot_loop(), name="session_snapshot")

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
        if adaptive_tuner is not None:
            await adaptive_tuner.stop()
        if learner is not None:
            await learner.stop()
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
