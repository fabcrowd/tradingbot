"""Optional adaptive spread tuning from recent sell win rate (paper or live)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from typing import TYPE_CHECKING

from .config import PROFITABILITY_MARGIN_BPS

if TYPE_CHECKING:
    from .config import AppConfig
    from .state import BotState, TradeRecord

LOG = logging.getLogger(__name__)


def _rolling_sell_stats(
    fills: list[TradeRecord], pair_key: str, lookback: int,
) -> tuple[int, int] | None:
    """Return (wins, total) for last up to `lookback` sells for pair, or None if empty."""
    sells = [f for f in fills if f.side == "sell" and f.pair_key == pair_key]
    if not sells:
        return None
    window = sells[-lookback:] if len(sells) > lookback else sells
    wins = sum(1 for f in window if f.pnl_delta > 0)
    return wins, len(window)


class AdaptiveSpreadTuner:
    """
    When enabled, periodically nudges spread_bps per trading pair:
    - Win rate below target band -> widen spread (more edge per fill).
    - Win rate above target band -> narrow spread slightly (more activity), down to a floor.
    """

    def __init__(
        self,
        state: BotState,
        config: AppConfig,
        engine: SpreadEngine,
        broadcast_config: Callable[[], Awaitable[None]],
    ) -> None:
        self._state = state
        self._config = config
        self._engine = engine
        self._broadcast_config = broadcast_config
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="adaptive_spread")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                bot = self._config.bot
                await asyncio.sleep(max(15.0, float(bot.adaptive_interval_sec)))
                if not bot.adaptive_tuning or not self._state.running:
                    continue
                if getattr(bot, "learner_enabled", False):
                    continue
                changed = self._tune_all()
                if changed:
                    await self._broadcast_config()
            except asyncio.CancelledError:
                break
            except Exception:
                LOG.exception("Adaptive spread tuner error")

    def _tune_all(self) -> bool:
        bot = self._config.bot
        lookback = max(5, int(bot.adaptive_lookback_sells))
        min_sells = max(3, int(bot.adaptive_min_sample_sells))
        step = max(1, int(bot.adaptive_spread_step_bps))
        target = float(bot.adaptive_target_win_pct)
        band = max(1.0, float(bot.adaptive_win_band_pct))
        ceiling = max(50, int(bot.adaptive_spread_ceiling_bps))
        floor_global = max(4, int(bot.adaptive_spread_floor_bps))

        fills = self._state.recent_fills
        changed = False
        for pair_key in self._config.pair_keys_for_trading():
            pc = self._config.pairs.get(pair_key)
            if pc is None:
                continue
            stats = _rolling_sell_stats(fills, pair_key, lookback)
            if stats is None:
                continue
            wins, n = stats
            if n < min_sells:
                continue
            win_pct = 100.0 * wins / n
            min_q = max(1, int(getattr(bot, "min_quote_half_spread_bps", 2)))
            survival_pair = pc.spread_floor_bps if pc.spread_floor_bps is not None else min_q
            survival_floor = max(min_q, survival_pair, floor_global)
            if getattr(bot, "per_trade_profitability", True):
                fee_bps = self._config.effective_fee_bps(pair_key, self._state.volume_30d)
                pair_sells = sum(1 for f in fills if f.side == "sell" and f.pair_key == pair_key)
                if pair_sells < 5:
                    min_spread = survival_floor
                elif pair_sells < 10:
                    min_spread = max(fee_bps + 1, survival_floor)
                else:
                    min_spread = max(fee_bps + PROFITABILITY_MARGIN_BPS, survival_floor)
            else:
                min_spread = survival_floor
            cur = pc.spread_bps
            new_spread = cur

            if win_pct < target - band:
                new_spread = min(ceiling, cur + step)
            elif win_pct > target + band and cur > min_spread:
                new_spread = max(min_spread, cur - step)

            if new_spread != cur:
                self._engine.update_pair_config(pair_key, spread_bps=new_spread)
                LOG.info(
                    "Adaptive %s: win=%.0f%% (%d sells) -> spread_bps %d -> %d",
                    pair_key, win_pct, n, cur, new_spread,
                )
                changed = True
        return changed
