"""Scalp bot runtime — wires candle feed, indicators, signals, and trader together.

Runs as an asyncio Task alongside the MM bot. Shares BotState for halt propagation
and capital awareness. Uses separate pairs from the MM bot to avoid rate limit conflicts.

Usage in main.py:
    from .scalp_bot.scalp_runtime import ScalpRuntime
    scalp = ScalpRuntime(state, scalp_cfg, live_mgr)
    scalp.start()
    # on shutdown:
    await scalp.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .candle_feed import Candle, start_candle_feed
from .indicators import IndicatorSet, IndicatorValues
from .scalp_config import ScalpBotConfig
from .scalp_trader import ScalpTrader
from .signal_engine import SignalEngine

if TYPE_CHECKING:
    from ..live_order_manager import LiveOrderManager
    from ..state import BotState

LOG = logging.getLogger(__name__)


class ScalpRuntime:
    """Top-level coordinator for the scalp bot."""

    def __init__(
        self,
        state: "BotState",
        cfg: ScalpBotConfig,
        live_mgr: "LiveOrderManager | None" = None,
    ) -> None:
        self._state = state
        self._cfg = cfg
        self._live_mgr = live_mgr
        self._task: asyncio.Task | None = None
        self._feed = None

        self._signal_engine = SignalEngine()
        self._trader = ScalpTrader(state, cfg, self._signal_engine, live_mgr)

        # Indicator sets per pair
        self._indicators: dict[str, IndicatorSet] = {
            key: IndicatorSet(pair_cfg)
            for key, pair_cfg in cfg.pairs.items()
        }
        # Latest indicator values per pair
        self._latest_iv: dict[str, IndicatorValues] = {}

    def start(self) -> None:
        if not self._cfg.enabled:
            LOG.info("ScalpRuntime: disabled in config — not starting")
            return
        if not self._cfg.pairs:
            LOG.info("ScalpRuntime: no pairs configured — not starting")
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="scalp_runtime")
            LOG.info(
                "ScalpRuntime: started for pairs %s",
                list(self._cfg.pairs.keys()),
            )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        if self._feed is not None:
            try:
                await self._feed.close()
            except Exception:
                pass
        LOG.info("ScalpRuntime: stopped")

    def snapshot(self) -> dict:
        return {
            "enabled": self._cfg.enabled,
            "trader": self._trader.snapshot(),
            "indicators": {
                k: {
                    "candles": self._indicators[k].candle_count,
                    "ready": iv.ready,
                    "ema_fast": round(iv.ema_fast, 5),
                    "ema_slow": round(iv.ema_slow, 5),
                    "rsi": round(iv.rsi, 2),
                    "atr": round(iv.atr, 6),
                    "vwap": round(iv.vwap_session, 5),
                    "ema_bullish": iv.ema_bullish,
                    "rsi_bullish": iv.rsi_bullish,
                    "vwap_bullish": iv.vwap_bullish,
                    "volume_confirmed": iv.volume_confirmed,
                }
                for k, iv in self._latest_iv.items()
            },
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Main loop: start feed, process candles, evaluate signals."""
        pairs = {k: pc.symbol for k, pc in self._cfg.pairs.items()}
        intervals = {k: pc.interval for k, pc in self._cfg.pairs.items()}

        LOG.info("ScalpRuntime: seeding candle feed from REST...")
        try:
            self._feed = await start_candle_feed(
                pairs=pairs,
                intervals=intervals,
                rest_seed_count=self._cfg.rest_seed_candles,
            )
        except Exception:
            LOG.exception("ScalpRuntime: failed to start candle feed")
            return

        # Seed indicator sets from REST buffer
        for pair_key in self._cfg.pairs:
            buf = self._feed.get_buffer(pair_key)
            LOG.info("ScalpRuntime: replaying %d REST candles into %s indicators", len(buf), pair_key)
            ind = self._indicators[pair_key]
            for candle in buf:
                iv = ind.update(candle)
            if buf:
                self._latest_iv[pair_key] = iv  # type: ignore[possibly-undefined]

        # Register callback for live closed candles
        self._feed.register_callback(self._on_closed_candle)

        LOG.info("ScalpRuntime: candle feed live, waiting for signals...")

        # Keep running — feed callbacks drive all activity
        while True:
            await asyncio.sleep(60)
            self._log_status()

    def _on_closed_candle(self, pair_key: str, candle: Candle) -> None:
        """Synchronous callback — called from CandleFeed on confirmed closed candle."""
        if pair_key not in self._cfg.pairs:
            return

        ind = self._indicators[pair_key]
        iv = ind.update(candle)
        self._latest_iv[pair_key] = iv

        LOG.debug(
            "ScalpRuntime %s: candle close=%.5f ema_f=%.5f ema_s=%.5f rsi=%.1f "
            "vwap=%.5f vol_ok=%s ready=%s",
            pair_key, candle.close, iv.ema_fast, iv.ema_slow, iv.rsi,
            iv.vwap_session, iv.volume_confirmed, iv.ready,
        )

        if not iv.ready:
            return

        if self._state.risk_halted or not self._state.running:
            return

        if self._trader.has_position(pair_key):
            return

        pair_cfg = self._cfg.pairs[pair_key]
        symbol = pair_cfg.symbol
        signal = self._signal_engine.evaluate(pair_key, symbol, pair_cfg, iv)
        if signal is None:
            return

        # Schedule async position open from the sync callback
        asyncio.create_task(
            self._open_position(signal, pair_cfg),
            name=f"scalp_open_{pair_key}",
        )

    async def _open_position(self, signal, pair_cfg) -> None:
        """Async wrapper for try_open — called via create_task from sync callback."""
        available = self._available_capital()
        await self._trader.try_open(signal, pair_cfg, available)

    def _available_capital(self) -> float:
        """Total USD available to the scalp bot across all quote balances."""
        # Use allocated_capital_usd as the cap — don't touch MM bot capital
        return self._cfg.allocated_capital_usd

    def _log_status(self) -> None:
        for pair_key, iv in self._latest_iv.items():
            if iv.ready:
                LOG.info(
                    "ScalpRuntime %s: ema_cross=%s rsi=%.1f vwap_bull=%s "
                    "vol_ok=%s | open_pos=%d | daily_pnl=%.4f",
                    pair_key,
                    "UP" if iv.ema_crossed_up else ("bull" if iv.ema_bullish else "bear"),
                    iv.rsi,
                    iv.vwap_bullish,
                    iv.volume_confirmed,
                    self._trader.open_position_count,
                    self._trader.daily_pnl,
                )

    # ── Fill routing — call these from LiveOrderManager.on_message ────────────

    async def on_fill(
        self,
        pair_key: str,
        cl_ord_id: str,
        fill_price: float,
        fill_qty: float,
    ) -> None:
        """Route a fill event to the appropriate position handler."""
        pos = self._trader.get_position(pair_key)
        if pos is None:
            return

        if cl_ord_id == pos.entry_cl_ord_id:
            await self._trader.on_entry_filled(pair_key, fill_price, fill_qty)
        elif cl_ord_id in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
            await self._trader.on_exit_filled(pair_key, cl_ord_id, fill_price)
