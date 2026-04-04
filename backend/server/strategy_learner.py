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
    from .session_logger import SessionLogger
    from .spread_engine import SpreadEngine
    from .state import BotState

LOG = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
EMA_ALPHA = 0.3


def _learner_file(mode: str) -> Path:
    return DATA_DIR / f"learner_state_{mode}.json"


REGIMES = ("clean", "competitive", "toxic")


class StrategyLearner:
    """Optimizes spread_bps per pair by hill-climbing on profit rate.

    When MEV detection is enabled, maintains per-regime EMA tracks and pain
    floors so the learner can discover different optimal spreads for clean
    vs. bot-heavy market conditions.
    """

    def __init__(
        self,
        state: "BotState",
        config: "AppConfig",
        engine: "SpreadEngine",
        session_logger: "SessionLogger | None" = None,
    ) -> None:
        self._state = state
        self._config = config
        self._engine = engine
        self._session_logger = session_logger
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
        self._pain_floor: dict[str, int] = {}
        self._pain_floor_last_decay_ts: dict[str, float] = {}
        self._last_decay_ts: dict[str, float] = {}
        self._prev_fill_count: dict[str, int] = {}
        self._consecutive_loss_evals: dict[str, int] = {}
        # Regime-aware state (keyed by f"{pair_key}:{regime}")
        self._regime_ema: dict[str, float] = {}
        self._regime_direction: dict[str, int] = {}
        self._regime_prev_rate: dict[str, float] = {}
        self._regime_pain_floor: dict[str, dict[str, int]] = {}
        self._regime_spread_bps: dict[str, dict[str, int]] = {}
        self._current_regime: dict[str, str] = {}
        self._regime_dwell_since: dict[str, float] = {}
        self._REGIME_MIN_DWELL_SEC = 30.0
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
        self._pain_floor.clear()
        self._pain_floor_last_decay_ts.clear()
        self._last_decay_ts.clear()
        self._prev_fill_count.clear()
        self._consecutive_loss_evals.clear()
        self._regime_ema.clear()
        self._regime_direction.clear()
        self._regime_prev_rate.clear()
        self._regime_pain_floor.clear()
        self._regime_spread_bps.clear()
        self._current_regime.clear()
        self._regime_dwell_since.clear()
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
        if getattr(bot, "optimizer_enabled", False):
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

    @staticmethod
    def _pair_sell_count(state, pair_key: str) -> int:
        return sum(1 for f in state.recent_fills if f.side == "sell" and f.pair_key == pair_key)

    def _pair_floor(self, pair_key: str) -> int:
        pc = self._config.pairs.get(pair_key)
        if pc is None:
            return 20
        bot = self._config.bot
        global_floor = max(1, int(getattr(bot, "adaptive_spread_floor_bps", 2)))
        min_q = max(1, int(getattr(bot, "min_quote_half_spread_bps", 2)))
        survival_pair = pc.spread_floor_bps if pc.spread_floor_bps is not None else min_q
        survival_floor = max(min_q, survival_pair, global_floor)

        if getattr(bot, "per_trade_profitability", True):
            fee_bps = self._config.effective_fee_bps(pair_key, self._state.volume_30d)
            sells = self._pair_sell_count(self._state, pair_key)
            if sells < 5:
                return survival_floor
            elif sells < 10:
                margin = min(1, PROFITABILITY_MARGIN_BPS)
                return max(fee_bps + margin, survival_floor)
            else:
                margin = PROFITABILITY_MARGIN_BPS
                return max(fee_bps + margin, survival_floor)
        return survival_floor

    def _ceiling(self) -> int:
        bot = self._config.bot
        return max(30, int(getattr(bot, "adaptive_spread_ceiling_bps", 100)))

    def _effective_floor(self, pair_key: str, regime: str | None = None) -> int:
        """Config floor raised by pain floor — decay never goes below this.

        When regime is provided and MEV detection is on, uses the
        regime-specific pain floor if available.
        """
        config_floor = self._pair_floor(pair_key)
        pain = self._pain_floor.get(pair_key, 0)
        if regime and pair_key in self._regime_pain_floor:
            regime_pain = self._regime_pain_floor[pair_key].get(regime, 0)
            pain = max(pain, regime_pain)
        return max(config_floor, pain)

    def _decay_pain_floor(self, pair_key: str, now: float) -> None:
        """Slowly lower the pain floor over time so the bot can re-explore.

        Safety valve: if the pain floor has been pushed all the way to the
        ceiling, the learner is trapped — it can never tighten.  In that case,
        reset pain to the midpoint between config floor and ceiling so the
        learner can start exploring again immediately.
        """
        bot = self._config.bot
        hours = max(0.5, float(getattr(bot, "pain_floor_decay_hours", 1.0)))
        decay_sec = hours * 3600
        pain = self._pain_floor.get(pair_key, 0)
        if pain <= 0:
            return

        ceiling = self._ceiling()
        config_floor = self._pair_floor(pair_key)

        if pain >= ceiling:
            midpoint = (config_floor + ceiling) // 2
            LOG.warning(
                "Learner %s: pain floor (%d) hit ceiling (%d) — "
                "trapped, resetting to midpoint %d",
                pair_key, pain, ceiling, midpoint,
            )
            self._pain_floor[pair_key] = midpoint
            if self._session_logger is not None:
                self._session_logger.log_pain_floor(pair_key, pain, midpoint, "ceiling_reset")
            self._save_state(pair_key, self._config.pairs[pair_key].spread_bps,
                             self._ema_rate.get(pair_key, 0.0))
            self._pain_floor_last_decay_ts[pair_key] = now
            return

        last_decay = self._pain_floor_last_decay_ts.get(pair_key, now)
        if now - last_decay >= decay_sec:
            new_pain = max(config_floor, pain - 1)
            if new_pain != pain:
                LOG.info(
                    "Learner %s: pain floor decay %d -> %d (%.1fh elapsed)",
                    pair_key, pain, new_pain, (now - last_decay) / 3600,
                )
                self._pain_floor[pair_key] = new_pain
                if self._session_logger is not None:
                    self._session_logger.log_pain_floor(pair_key, pain, new_pain, "time_decay")
                self._save_state(pair_key, self._config.pairs[pair_key].spread_bps,
                                 self._ema_rate.get(pair_key, 0.0))
            self._pain_floor_last_decay_ts[pair_key] = now

    def _no_fill_decay(self, pair_key: str, now: float) -> bool:
        """Tighten spread when idle. Returns True if a decay adjustment was made."""
        bot = self._config.bot
        decay_start = float(getattr(bot, "decay_start_sec", 90.0))
        decay_interval = float(getattr(bot, "decay_interval_sec", 60.0))
        decay_step = max(1, int(getattr(bot, "decay_step_bps", 1)))

        last_fill = self._state.last_fill_ts.get(pair_key, 0.0)
        if last_fill <= 0:
            last_fill = self._state.session_start_ts or now
            self._state.last_fill_ts.setdefault(pair_key, last_fill)

        idle_sec = now - last_fill
        if idle_sec < decay_start:
            self._last_decay_ts.pop(pair_key, None)
            return False

        last_decay_action = self._last_decay_ts.get(pair_key, 0.0)
        if last_decay_action > 0 and (now - last_decay_action) < decay_interval:
            return False

        pc = self._config.pairs.get(pair_key)
        if pc is None:
            return False

        eff_floor = self._effective_floor(pair_key)
        cur = pc.spread_bps
        if cur <= eff_floor:
            return False

        new_spread = max(eff_floor, cur - decay_step)
        if new_spread == cur:
            return False

        pain = self._pain_floor.get(pair_key, 0)
        self._engine.update_pair_config(pair_key, spread_bps=new_spread)
        self._last_decay_ts[pair_key] = now
        self._adjust_count_today += 1
        LOG.info(
            "Learner %s: no-fill decay %d -> %d (idle %.0fs, pain floor %d)",
            pair_key, cur, new_spread, idle_sec, pain,
        )
        if self._session_logger is not None:
            self._session_logger.log_learner(
                pair=pair_key, action="decay",
                spread_old=cur, spread_new=new_spread,
                reason="no-fill decay",
                pain_floor=pain, idle_sec=idle_sec,
                ema_rate=self._ema_rate.get(pair_key, 0.0),
            )
        self._save_state(pair_key, new_spread, self._ema_rate.get(pair_key, 0.0))
        self._state.learner_info[pair_key] = {
            "spread_bps": new_spread,
            "rate_per_min": round(self._ema_rate.get(pair_key, 0.0), 4),
            "decay_active": True,
            "pain_floor": pain,
            "idle_sec": round(idle_sec, 0),
            "direction": "decay-tighten",
        }
        return True

    def _pair_fill_count(self, pair_key: str) -> int:
        max_age = float(getattr(self._config.bot, "learner_lookback_max_age_sec", 3600.0))
        cutoff = time.time() - max_age
        return sum(1 for f in self._state.recent_fills
                   if f.pair_key == pair_key and f.timestamp >= cutoff)

    def _pair_pnl(self, pair_key: str) -> float:
        max_age = float(getattr(self._config.bot, "learner_lookback_max_age_sec", 3600.0))
        cutoff = time.time() - max_age
        return sum(f.pnl_delta for f in self._state.recent_fills
                   if f.pair_key == pair_key and f.timestamp >= cutoff)

    def _step(self) -> None:
        bot = self._config.bot
        step_bps = max(1, int(getattr(bot, "adaptive_spread_step_bps", 2)))
        max_daily = int(getattr(bot, "learner_max_daily_adjustments", 12))
        max_age = float(getattr(bot, "learner_lookback_max_age_sec", 3600.0))

        day = int(time.time() // 86400)
        if day != self._last_adjust_day:
            self._last_adjust_day = day
            self._adjust_count_today = 0

        if self._adjust_count_today >= max_daily:
            return

        now = time.time()
        cutoff = now - max_age

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

            # --- Pain floor slow decay (long-horizon re-exploration) ---
            self._decay_pain_floor(pair_key, now)

            # --- No-fill decay: tighten when idle ---
            if self._no_fill_decay(pair_key, now):
                continue

            # Cooldown: skip one interval after a loss_widen step
            if self._cooldown.pop(pair_key, False):
                continue

            # Per-pair fill count and P&L (time-capped lookback)
            pair_fills = self._pair_fill_count(pair_key)
            pair_pnl = self._pair_pnl(pair_key)

            if pair_fills < 3:
                continue

            prev_pnl, prev_time, prev_fills = self._prev_snapshot.get(
                pair_key, (pair_pnl, now, pair_fills),
            )
            new_fills = pair_fills - prev_fills
            elapsed = now - prev_time

            if elapsed < 30 or new_fills < 2:
                if pair_key not in self._prev_snapshot:
                    self._prev_snapshot[pair_key] = (pair_pnl, now, pair_fills)
                continue

            self._prev_snapshot[pair_key] = (pair_pnl, now, pair_fills)

            pnl_delta = pair_pnl - prev_pnl
            rate = (pnl_delta / elapsed) * 60.0

            prev_ema = self._ema_rate.get(pair_key, rate)
            ema = EMA_ALPHA * rate + (1.0 - EMA_ALPHA) * prev_ema
            self._ema_rate[pair_key] = ema

            # --- Regime detection from bot_threat signal ---
            regime = "clean"
            mev_enabled = getattr(bot, "mev_detection_enabled", False)
            ps = self._state.pairs.get(pair_key)
            if mev_enabled and ps is not None and ps.bot_threat is not None:
                regime = ps.bot_threat.regime or "clean"
            prev_regime = self._current_regime.get(pair_key, "clean")
            regime_changed = regime != prev_regime
            dwell_since = self._regime_dwell_since.get(pair_key, now)
            if regime_changed and (now - dwell_since) >= self._REGIME_MIN_DWELL_SEC:
                if mev_enabled:
                    self._regime_spread_bps.setdefault(pair_key, {})[prev_regime] = pc.spread_bps
                self._current_regime[pair_key] = regime
                self._regime_dwell_since[pair_key] = now
                if mev_enabled and regime in self._regime_spread_bps.get(pair_key, {}):
                    saved_spread = self._regime_spread_bps[pair_key][regime]
                    if saved_spread != pc.spread_bps:
                        self._engine.update_pair_config(pair_key, spread_bps=saved_spread)
                        LOG.info(
                            "Learner %s: regime %s -> %s, restored spread %d",
                            pair_key, prev_regime, regime, saved_spread,
                        )
                if self._session_logger is not None and hasattr(self._session_logger, "log_regime_change"):
                    self._session_logger.log_regime_change(
                        pair=pair_key, old_regime=prev_regime, new_regime=regime,
                        spread_bps=pc.spread_bps,
                    )

            rkey = f"{pair_key}:{regime}"
            prev_regime_ema = self._regime_ema.get(rkey, rate)
            regime_ema = EMA_ALPHA * rate + (1.0 - EMA_ALPHA) * prev_regime_ema
            self._regime_ema[rkey] = regime_ema

            # Recent sell P&L for loss detection (time-capped)
            lookback = max(2, int(getattr(bot, "learner_loss_lookback_sells", 5)))
            recent_pnls: list[float] = []
            for f in reversed(self._state.recent_fills):
                if f.side != "sell" or f.pair_key != pair_key:
                    continue
                if f.timestamp < cutoff:
                    break
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

            # Track consecutive loss evaluations; after 3 in a row use a larger jump
            if loss_widen:
                self._consecutive_loss_evals[pair_key] = (
                    self._consecutive_loss_evals.get(pair_key, 0) + 1
                )
            else:
                self._consecutive_loss_evals[pair_key] = 0

            # Aggressive widen: 5× step (min 10 bps) after 3 consecutive loss evals
            effective_step_bps = step_bps
            if loss_widen and self._consecutive_loss_evals.get(pair_key, 0) >= 3:
                effective_step_bps = max(step_bps * 5, 10)

            if loss_widen:
                direction = 1
            elif ema >= prev_ema_val:
                pass
            else:
                direction = -direction

            self._prev_rate[pair_key] = ema
            self._direction[pair_key] = direction

            floor = self._effective_floor(pair_key, regime if mev_enabled else None)
            ceiling = self._ceiling()
            cur = pc.spread_bps
            new_spread = cur + direction * effective_step_bps
            new_spread = max(floor, min(ceiling, new_spread))

            if new_spread == cur:
                if loss_widen and direction == 1:
                    self._cooldown[pair_key] = True
                    continue
                direction = -direction
                self._direction[pair_key] = direction
                new_spread = cur + direction * effective_step_bps
                new_spread = max(floor, min(ceiling, new_spread))

            if new_spread == cur:
                continue

            self._engine.update_pair_config(pair_key, spread_bps=new_spread)
            self._adjust_count_today += 1

            # --- Pain floor: remember losing spreads (capped at ceiling - 10) ---
            if loss_widen:
                self._cooldown[pair_key] = True
                old_pain = self._pain_floor.get(pair_key, 0)
                pain_cap = max(ceiling - 10, floor)
                new_pain = min(max(old_pain, cur), pain_cap)
                if new_pain != old_pain:
                    self._pain_floor[pair_key] = new_pain
                    LOG.info("Learner %s: pain floor raised %d -> %d", pair_key, old_pain, new_pain)
                    if self._session_logger is not None:
                        self._session_logger.log_pain_floor(pair_key, old_pain, new_pain, "loss_widen")
                # Also update regime-specific pain floor
                if mev_enabled:
                    rpf = self._regime_pain_floor.setdefault(pair_key, {})
                    old_rpain = rpf.get(regime, 0)
                    new_rpain = max(old_rpain, cur)
                    if new_rpain != old_rpain:
                        rpf[regime] = new_rpain
                        LOG.info("Learner %s: regime '%s' pain floor %d -> %d",
                                 pair_key, regime, old_rpain, new_rpain)
            elif not loss_widen and avg_sell_pnl > 0 and direction < 0:
                pain = self._pain_floor.get(pair_key, 0)
                if pain > 0 and cur <= pain + 2:
                    new_pain = max(self._pair_floor(pair_key), pain - 1)
                    if new_pain != pain:
                        self._pain_floor[pair_key] = new_pain
                        LOG.info("Learner %s: pain floor lowered %d -> %d (profitable)", pair_key, pain, new_pain)
                        if self._session_logger is not None:
                            self._session_logger.log_pain_floor(pair_key, pain, new_pain, "profitable_fill")
                # Also lower regime pain floor
                if mev_enabled:
                    rpf = self._regime_pain_floor.get(pair_key, {})
                    rpain = rpf.get(regime, 0)
                    if rpain > 0 and cur <= rpain + 2:
                        new_rpain = max(self._pair_floor(pair_key), rpain - 1)
                        if new_rpain != rpain:
                            rpf[regime] = new_rpain

            arrow = "↓" if new_spread < cur else "↑"
            lw = " loss_learn" if loss_widen else ""
            pain = self._pain_floor.get(pair_key, 0)
            action = "loss_widen" if loss_widen else ("tighten" if direction < 0 else "widen")
            LOG.info(
                "Learner %s: spread %d %s %d%s (ema=$%.4f/min, avg_sell=$%.4f/%d, "
                "+%d fills / %.0fs, %s, pain=%d)",
                pair_key, cur, arrow, new_spread, lw,
                ema, avg_sell_pnl, len(recent_pnls),
                new_fills, elapsed,
                "tighten" if direction < 0 else "widen",
                pain,
            )
            if self._session_logger is not None:
                self._session_logger.log_learner(
                    pair=pair_key, action=action,
                    spread_old=cur, spread_new=new_spread,
                    reason="loss_widen" if loss_widen else "hill_climb",
                    pain_floor=pain,
                    avg_sell_pnl=avg_sell_pnl,
                    ema_rate=ema,
                    fills_interval=new_fills,
                )
            self._save_state(pair_key, new_spread, ema)
            regime_pain = self._regime_pain_floor.get(pair_key, {}).get(regime, 0) if mev_enabled else 0
            self._state.learner_info[pair_key] = {
                "spread_bps": new_spread,
                "rate_per_min": round(ema, 4),
                "avg_sell_pnl_recent": round(avg_sell_pnl, 6),
                "loss_widen": loss_widen,
                "direction": "tighten" if direction < 0 else "widen",
                "fills_this_interval": new_fills,
                "pain_floor": pain,
                "decay_active": False,
                "regime": regime,
                "regime_ema": round(regime_ema, 4) if mev_enabled else None,
                "regime_pain_floor": regime_pain if mev_enabled else None,
            }

    def _save_state(self, pair_key: str, spread: int, rate: float) -> None:
        """Persist learner state.  Pain floor and regime pain floors survive
        across sessions; spread/direction/rate are diagnostic."""
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
            "pain_floor": self._pain_floor.get(pair_key, 0),
            "pain_floor_decay_ts": self._pain_floor_last_decay_ts.get(pair_key, 0.0),
            "regime_pain_floors": self._regime_pain_floor.get(pair_key, {}),
            "regime_spreads": self._regime_spread_bps.get(pair_key, {}),
            "current_regime": self._current_regime.get(pair_key, "clean"),
            "updated": time.time(),
        }
        try:
            self._learner_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            LOG.debug("Failed to write learner state", exc_info=True)

    def reset_pair(self, pair_key: str) -> None:
        """Clear pain floor and EMA state so the learner re-explores from the config floor.

        Called on full P&L reset or explicit reseed_barriers dashboard action.
        """
        if pair_key not in self._config.pairs:
            return
        old_pain = self._pain_floor.get(pair_key, 0)
        self._pain_floor.pop(pair_key, None)
        self._regime_pain_floor.pop(pair_key, None)
        self._ema_rate.pop(pair_key, None)
        self._prev_rate.pop(pair_key, None)
        self._direction[pair_key] = 0
        self._pain_floor_last_decay_ts.pop(pair_key, None)
        self._last_decay_ts.pop(pair_key, None)
        self._cooldown.pop(pair_key, None)
        self._consecutive_loss_evals.pop(pair_key, None)
        pc = self._config.pairs[pair_key]
        self._save_state(pair_key, pc.spread_bps, 0.0)
        LOG.info(
            "Learner %s: reset (old pain_floor=%d, spread back to config %d bps)",
            pair_key, old_pain, pc.spread_bps,
        )
        if self._session_logger is not None:
            self._session_logger.log_learner(
                pair=pair_key, action="reset",
                spread_old=old_pain, spread_new=pc.spread_bps,
                reason="manual reset",
                pain_floor=0, idle_sec=0,
                ema_rate=0.0,
            )

    def _load_state(self) -> None:
        """Restore pain_floor and regime state from disk.  spread_bps always
        starts from config.toml so config changes take effect immediately on
        restart.  Direction and EMA rate are session-transient."""
        if not self._learner_file.exists():
            for pair_key in self._config.pair_keys_for_trading():
                pc = self._config.pairs.get(pair_key)
                if pc:
                    LOG.info("Learner %s: starting at config spread_bps=%d (no saved state)",
                             pair_key, pc.spread_bps)
            return
        try:
            state = json.loads(self._learner_file.read_text(encoding="utf-8"))
        except Exception:
            LOG.debug("Failed to load learner state", exc_info=True)
            return

        for pair_key in self._config.pair_keys_for_trading():
            pc = self._config.pairs.get(pair_key)
            if pc is None:
                continue
            info = state.get(pair_key, {})

            saved_pain = info.get("pain_floor")
            if isinstance(saved_pain, int) and saved_pain > 0:
                ceiling = self._ceiling()
                pain_cap = max(ceiling - 10, self._pair_floor(pair_key))
                self._pain_floor[pair_key] = min(saved_pain, pain_cap)
            saved_pain_ts = info.get("pain_floor_decay_ts")
            if isinstance(saved_pain_ts, (int, float)) and saved_pain_ts > 0:
                self._pain_floor_last_decay_ts[pair_key] = float(saved_pain_ts)

            regime_pf = info.get("regime_pain_floors")
            if isinstance(regime_pf, dict):
                cleaned = {}
                for r, v in regime_pf.items():
                    if r in REGIMES and isinstance(v, (int, float)) and v > 0:
                        cleaned[r] = int(v)
                if cleaned:
                    self._regime_pain_floor[pair_key] = cleaned

            regime_spreads = info.get("regime_spreads")
            if isinstance(regime_spreads, dict):
                cleaned_rs = {}
                for r, v in regime_spreads.items():
                    if r in REGIMES and isinstance(v, (int, float)) and v > 0:
                        cleaned_rs[r] = int(v)
                if cleaned_rs:
                    self._regime_spread_bps[pair_key] = cleaned_rs

            saved_regime = info.get("current_regime", "clean")
            if saved_regime in REGIMES:
                self._current_regime[pair_key] = saved_regime

            pain = self._pain_floor.get(pair_key, 0)
            rpf = self._regime_pain_floor.get(pair_key, {})
            LOG.info(
                "Learner %s: starting at config spread_bps=%d, pain_floor=%d, "
                "regime_pain=%s",
                pair_key, pc.spread_bps, pain, rpf or "none",
            )
