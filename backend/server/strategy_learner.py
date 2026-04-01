"""Strategy learner — dynamic self-learning spread optimizer.

Hill-climbs on EMA-smoothed profit rate ($/min) per pair.
loss_widen: if recent sells average negative, widen to capture more edge.
One-interval cooldown after a loss_widen step prevents oscillation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .config import PROFITABILITY_MARGIN_BPS

if TYPE_CHECKING:
    from .config import AppConfig
    from .spread_engine import SpreadEngine
    from .state import BotState

LOG = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
EMA_ALPHA = 0.3


def _learner_file(mode: str) -> Path:
    return DATA_DIR / f"learner_state_{mode}.json"


class StrategyLearner:
    """Optimizes spread_bps per pair by hill-climbing on profit rate."""

    def __init__(self, state: BotState, config: AppConfig, engine: SpreadEngine) -> None:
        self._state = state
        self._config = config
        self._engine = engine
        self._task: asyncio.Task | None = None
        self._mode = config.mode
        self._learner_file = _learner_file(self._mode)
        self._prev_rate: dict[str, float] = {}
        self._ema_rate: dict[str, float] = {}
        self._direction: dict[str, int] = {}
        self._prev_snapshot: dict[str, tuple[float, float, int]] = {}
        self._cooldown: dict[str, bool] = {}
        self._last_adjust_day: int = 0
        self._adjust_count_today: int = 0
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load_state()

    def switch_mode(self, new_mode: str) -> None:
        """Reset learner tracking and load state for the new mode."""
        self._mode = new_mode
        self._learner_file = _learner_file(new_mode)
        self._prev_rate.clear()
        self._ema_rate.clear()
        self._direction.clear()
        self._prev_snapshot.clear()
        self._cooldown.clear()
        self._last_adjust_day = 0
        self._adjust_count_today = 0
        self._load_state()
        LOG.info("Learner switched to %s mode", new_mode)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="strategy_learner")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        bot = self._config.bot
        if not getattr(bot, "learner_enabled", False):
            return
        interval = float(getattr(bot, "learner_interval_sec", 120.0))
        while True:
            try:
                self._step()
            except asyncio.CancelledError:
                break
            except Exception:
                LOG.exception("StrategyLearner step failed")
            await asyncio.sleep(interval)

    def _pair_floor(self, pair_key: str) -> int:
        pc = self._config.pairs.get(pair_key)
        if pc is None:
            return 20
        bot = self._config.bot
        global_floor = max(4, int(getattr(bot, "adaptive_spread_floor_bps", 4)))
        if getattr(bot, "per_trade_profitability", True):
            fee_bps = self._config.effective_fee_bps(pair_key, self._state.volume_30d)
            pair_floor = (
                pc.spread_floor_bps
                if pc.spread_floor_bps is not None
                else fee_bps + PROFITABILITY_MARGIN_BPS
            )
            return max(
                fee_bps + PROFITABILITY_MARGIN_BPS,
                pair_floor,
                global_floor,
            )
        min_q = max(1, int(getattr(bot, "min_quote_half_spread_bps", 2)))
        pair_floor = pc.spread_floor_bps if pc.spread_floor_bps is not None else min_q
        return max(min_q, pair_floor, global_floor)

    def _ceiling(self) -> int:
        bot = self._config.bot
        return max(30, int(getattr(bot, "adaptive_spread_ceiling_bps", 100)))

    @staticmethod
    def _pair_fill_count(state: "BotState", pair_key: str) -> int:
        return sum(1 for f in state.recent_fills if f.pair_key == pair_key)

    @staticmethod
    def _pair_pnl(state: "BotState", pair_key: str) -> float:
        return sum(f.pnl_delta for f in state.recent_fills if f.pair_key == pair_key)

    def _step(self) -> None:
        bot = self._config.bot
        step_bps = max(1, int(getattr(bot, "adaptive_spread_step_bps", 2)))
        max_daily = int(getattr(bot, "learner_max_daily_adjustments", 12))

        day = int(time.time() // 86400)
        if day != self._last_adjust_day:
            self._last_adjust_day = day
            self._adjust_count_today = 0

        if self._adjust_count_today >= max_daily:
            return

        now = time.time()

        for pair_key in self._config.pair_keys_for_trading():
            if self._adjust_count_today >= max_daily:
                break
            pc = self._config.pairs.get(pair_key)
            if pc is None:
                continue

            # Skip during bootstrap
            if (
                pc.bootstrap_half_spread_bps is not None
                and pc.bootstrap_until_sell_trades > 0
            ):
                sells = sum(
                    1 for f in self._state.recent_fills
                    if f.side == "sell" and f.pair_key == pair_key
                )
                if sells < pc.bootstrap_until_sell_trades:
                    continue

            # Cooldown: skip one interval after a loss_widen step
            if self._cooldown.pop(pair_key, False):
                continue

            # Per-pair fill count and P&L (not global totals)
            pair_fills = self._pair_fill_count(self._state, pair_key)
            pair_pnl = self._pair_pnl(self._state, pair_key)

            if pair_fills < 3:
                continue

            prev_pnl, prev_time, prev_fills = self._prev_snapshot.get(
                pair_key, (pair_pnl, now, pair_fills),
            )
            new_fills = pair_fills - prev_fills
            elapsed = now - prev_time

            # Gate: not enough data yet — DON'T update snapshot (preserves the window)
            if elapsed < 30 or new_fills < 2:
                if pair_key not in self._prev_snapshot:
                    self._prev_snapshot[pair_key] = (pair_pnl, now, pair_fills)
                continue

            # Gate passed: NOW update snapshot
            self._prev_snapshot[pair_key] = (pair_pnl, now, pair_fills)

            pnl_delta = pair_pnl - prev_pnl
            rate = (pnl_delta / elapsed) * 60.0

            # EMA-smooth the rate signal to reduce noise
            prev_ema = self._ema_rate.get(pair_key, rate)
            ema = EMA_ALPHA * rate + (1.0 - EMA_ALPHA) * prev_ema
            self._ema_rate[pair_key] = ema

            # Recent sell P&L for loss detection
            lookback = max(2, int(getattr(bot, "learner_loss_lookback_sells", 5)))
            recent_pnls: list[float] = []
            for f in reversed(self._state.recent_fills):
                if f.side != "sell" or f.pair_key != pair_key:
                    continue
                recent_pnls.append(f.pnl_delta)
                if len(recent_pnls) >= lookback:
                    break
            recent_pnls.reverse()
            avg_sell_pnl = (
                sum(recent_pnls) / len(recent_pnls) if recent_pnls else 0.0
            )

            loss_widen = (
                getattr(bot, "learner_widen_on_avg_loss", True)
                and len(recent_pnls) >= 2
                and avg_sell_pnl < 0
            )

            prev_ema_val = self._prev_rate.get(pair_key, 0.0)
            direction = self._direction.get(pair_key, -1)

            if loss_widen:
                direction = 1
            elif ema >= prev_ema_val:
                pass  # momentum — keep direction
            else:
                direction = -direction

            self._prev_rate[pair_key] = ema
            self._direction[pair_key] = direction

            floor = self._pair_floor(pair_key)
            ceiling = self._ceiling()
            cur = pc.spread_bps
            new_spread = cur + direction * step_bps
            new_spread = max(floor, min(ceiling, new_spread))

            if new_spread == cur:
                if loss_widen and direction == 1:
                    self._cooldown[pair_key] = True
                    continue
                direction = -direction
                self._direction[pair_key] = direction
                new_spread = cur + direction * step_bps
                new_spread = max(floor, min(ceiling, new_spread))

            if new_spread == cur:
                continue

            self._engine.update_pair_config(pair_key, spread_bps=new_spread)
            self._adjust_count_today += 1

            if loss_widen:
                self._cooldown[pair_key] = True

            arrow = "↓" if new_spread < cur else "↑"
            lw = " loss_learn" if loss_widen else ""
            LOG.info(
                "Learner %s: spread %d %s %d%s (ema=$%.4f/min, avg_sell=$%.4f/%d, "
                "+%d fills / %.0fs, %s)",
                pair_key, cur, arrow, new_spread, lw,
                ema, avg_sell_pnl, len(recent_pnls),
                new_fills, elapsed,
                "tighten" if direction < 0 else "widen",
            )
            self._save_state(pair_key, new_spread, ema)
            self._state.learner_info[pair_key] = {
                "spread_bps": new_spread,
                "rate_per_min": round(ema, 4),
                "avg_sell_pnl_recent": round(avg_sell_pnl, 6),
                "loss_widen": loss_widen,
                "direction": "tighten" if direction < 0 else "widen",
                "fills_this_interval": new_fills,
            }

    def _save_state(self, pair_key: str, spread: int, rate: float) -> None:
        state: dict = {}
        if self._learner_file.exists():
            try:
                state = json.loads(self._learner_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        state[pair_key] = {
            "spread_bps": spread,
            "rate_per_min": round(rate, 6),
            "direction": self._direction.get(pair_key, -1),
            "updated": time.time(),
        }
        try:
            self._learner_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            LOG.debug("Failed to write learner state", exc_info=True)

    def _load_state(self) -> None:
        if not self._learner_file.exists():
            return
        floor_default = 4
        ceiling = self._ceiling()
        try:
            state = json.loads(self._learner_file.read_text(encoding="utf-8"))
            for pair_key, info in state.items():
                pc = self._config.pairs.get(pair_key)
                if pc is None:
                    continue
                saved_spread = info.get("spread_bps")
                if isinstance(saved_spread, int) and saved_spread > 0:
                    floor = self._pair_floor(pair_key)
                    clamped = max(floor, min(ceiling, saved_spread))
                    pc.spread_bps = clamped
                    LOG.info(
                        "Learner loaded %s: spread_bps=%d%s",
                        pair_key, clamped,
                        f" (clamped from {saved_spread})" if clamped != saved_spread else "",
                    )
                saved_dir = info.get("direction")
                if isinstance(saved_dir, int) and saved_dir in (-1, 1):
                    self._direction[pair_key] = saved_dir
                saved_rate = info.get("rate_per_min")
                if isinstance(saved_rate, (int, float)):
                    self._prev_rate[pair_key] = float(saved_rate)
        except Exception:
            LOG.debug("Failed to load learner state", exc_info=True)
