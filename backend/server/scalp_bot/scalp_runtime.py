"""Scalp bot runtime — wires candle feed, indicators, signals, and trader together.

Runs as an asyncio Task alongside the MM bot. Shares BotState for halt propagation
and capital awareness. Uses separate pairs from the MM bot to avoid rate limit conflicts.

Warmup mode
-----------
When ``warmup_enabled`` is True (default), the bot enters a data-collection phase on
startup.  During warmup it:

1. Streams candles and persists them to bar_store (building historical data).
2. Updates indicators so they are primed when trading begins.
3. Triggers the WFO optimizer as soon as ``warmup_min_bars`` are collected.
4. Blocks all trade entry until a champion strategy has been found
   (when ``warmup_require_champion`` is True) *or* until ``warmup_max_hours``
   have elapsed (0 = no time limit — only champion matters).

Once warmup completes, trading proceeds normally with the optimizer continuing
to run on its regular interval.

Usage in main.py:
    from .scalp_bot.scalp_runtime import ScalpRuntime
    scalp = ScalpRuntime(state, scalp_cfg, live_mgr, session_logger=session_log)
    scalp.start()
    # on shutdown:
    await scalp.stop()
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import enum
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from . import bar_store
from .candle_feed import Candle, start_candle_feed
from .indicators import IndicatorSet, IndicatorValues
from .scalp_config import ScalpBotConfig
from .scalp_trader import ScalpTrader
from .scalp_wfo import CHAMPION_PATH, ScalpWalkForwardOptimizer, WFOConfig, load_champion
from .strategy_lookback import (
    NO_CHAMPION_BOOTSTRAP_HOURS,
    build_strategy_lookback_snapshot,
    best_mode_bootstrap_no_champion,
    nemesis_advisory_champion_vs_bootstrap,
    nemesis_resolve_bootstrap_vs_tuner,
    pair_has_wfo_champion,
)
from .param_tuner import run_tuner_cycle, apply_tuner_result, save_tuner_state, load_tuner_state, TunerResult
from .signal_engine import SignalEngine

if TYPE_CHECKING:
    from ..coinbase_order_manager import CoinbaseOrderManager
    from ..live_order_manager import LiveOrderManager
    from ..session_logger import SessionLogger
    from ..state import BotState

LOG = logging.getLogger(__name__)

_STRATEGY_LOOKBACK_REFRESH_SEC = 60.0
_TUNER_INTERVAL_SEC = 120.0  # run self-tuner every 2 minutes for faster convergence
_NEMESIS_ADVISORY_SEC = 300.0  # champion vs bootstrap comparison (bar loads) — throttled

class WarmupPhase(enum.Enum):
    DISABLED = "disabled"
    COLLECTING = "collecting"
    OPTIMIZING = "optimizing"
    READY = "ready"


class StartupPhase(enum.Enum):
    """Operator-facing lifecycle state — overlaid on WarmupPhase for the dashboard."""
    STANDBY    = "standby"      # dormant; nothing runs until Begin Warmup
    WARMING_UP = "warming_up"   # warmup steps in progress
    PRIMED     = "primed"       # all steps done; awaiting Go Live
    LIVE       = "live"         # fully armed; orders allowed


@dataclasses.dataclass
class WarmupStep:
    key: str
    label: str
    status: str = "pending"   # pending | running | done | failed
    pct: float = 0.0
    detail: str = ""
    retry_count: int = 0
    error: str = ""


class ScalpRuntime:
    """Top-level coordinator for the scalp bot."""

    def __init__(
        self,
        state: "BotState",
        cfg: ScalpBotConfig,
        live_mgr: "LiveOrderManager | CoinbaseOrderManager | None" = None,
        session_logger: "SessionLogger | None" = None,
    ) -> None:
        self._state = state
        self._cfg = cfg
        self._live_mgr = live_mgr
        self._session_log = session_logger
        self._task: asyncio.Task | None = None
        self._feed = None

        self._signal_engine = SignalEngine()
        self._trader = ScalpTrader(
            state, cfg, self._signal_engine, live_mgr, session_logger=session_logger,
        )
        self._trader.sim_mode = cfg.sim_mode
        # Operator go-live gate: blocks try_open / reversals while True.
        self._operator_standby: bool = bool(getattr(cfg, "require_manual_go_live", False))
        self._prep_session_busy: bool = False
        self._trader._entries_paused_fn = lambda: self._operator_standby

        # Indicator sets per pair
        use_numpy = getattr(cfg, "use_numpy_indicators", False)
        self._indicators: dict[str, IndicatorSet] = {
            key: IndicatorSet(pair_cfg, use_numpy=use_numpy)
            for key, pair_cfg in cfg.pairs.items()
        }
        # Latest indicator values per pair
        self._latest_iv: dict[str, IndicatorValues] = {}
        # Ensure bar_store knows the venue before we read from it
        bar_store.set_bar_store_venue(getattr(cfg, "venue", "kraken_spot"))

        # Active strategy mode per pair — config until backfill + WFO/bootstrap run
        # (_apply_no_champion_bootstrap after bars exist; champion reload overrides).
        self._active_mode: dict[str, str] = {
            k: cfg.pairs[k].strategy_mode for k in cfg.pairs
        }
        self._mode_source: dict[str, str] = {
            k: "config" for k in cfg.pairs
        }

        # Regime risk-on (volume / vol-scaled moves) — shortens WFO sleep, bootstrap window, Nemesis gates
        self._regime_risk_on_until: float = 0.0
        self._regime_pair_reasons: dict[str, list[str]] = {}
        # Live velocity: (unix_ts, price) for regime_live_velocity_* (trimmed per tick)
        self._regime_live_prices: dict[str, deque[tuple[float, float]]] = {}
        self._regime_live_log_at: dict[str, float] = {}

        # Walk-forward optimizer
        wfo_cfg = WFOConfig(
            enabled=cfg.wfo_enabled,
            interval_sec=cfg.wfo_interval_sec,
            train_hours=cfg.wfo_train_hours,
            holdout_hours=cfg.wfo_holdout_hours,
            step_hours=cfg.wfo_step_hours,
            min_trades=cfg.wfo_min_trades,
            objective=cfg.wfo_objective,
        )
        self._wfo = ScalpWalkForwardOptimizer(
            cfg,
            wfo_cfg,
            session_logger=session_logger,
            interval_sec_resolver=lambda: float(self._effective_wfo_sleep_sec()),
        )
        self._champion_mtime: float = -1.0
        self._champion_data: dict[str, dict] | None = None
        self._champion_period_start: dict[str, float] = {}
        self._champion_apply_sig: dict[str, tuple] = {}

        # Throttle dashboard alerts when INTX list snapshot misses an internal open leg.
        self._intx_reconcile_gap_alert_at: dict[str, float] = {}
        # INTX rows whose product_id is not in [scalp.pairs] — surfaced in dashboard snapshot.
        self._intx_unmapped_positions: list[dict] = []

        # Warmup state
        if cfg.warmup_enabled:
            self._warmup_phase = WarmupPhase.COLLECTING
        else:
            self._warmup_phase = WarmupPhase.DISABLED
        self._warmup_start_ts: float = 0.0
        self._warmup_bars_collected: dict[str, int] = {k: 0 for k in cfg.pairs}
        self._warmup_wfo_triggered: bool = False
        self._warmup_champion_found: bool = False
        # Always run WFO on startup — trading is blocked until this completes
        # (even if a saved champion exists from a prior session).
        self._startup_wfo_done: bool = not cfg.warmup_enabled

        self._strategy_lookback_snapshot: dict | None = None
        self._strategy_lookback_ts: float = 0.0

        # Self-tuner state
        self._tuner_results: dict[str, TunerResult] = {}
        self._tuner_last_run: float = 0.0
        self._tuner_snapshot: dict | None = load_tuner_state()
        # Nemesis-style dual-lens reconciliation (WFO vs bootstrap advisory; bootstrap vs tuner resolution)
        self._nemesis_advisory: dict[str, dict] = {}
        self._nemesis_advisory_ts: float = 0.0
        self._nemesis_resolution: dict[str, dict] = {}

        # Operator flow UI (Settings: standby / prep / go live) — dashboard progress + modals
        self._operator_flow: dict | None = None
        self._operator_flow_seq: int = 0
        self._operator_flow_event: dict | None = None
        self._operator_flow_pulse_task: asyncio.Task | None = None
        self._flow_push: Callable[[], Awaitable[None]] | None = None

        # Startup phase state machine (operator-facing)
        self._startup_phase: StartupPhase = StartupPhase.STANDBY
        self._warmup_steps: list[WarmupStep] = self._build_warmup_steps()
        # Set when operator clicks "Begin Warmup" — unblocks _run() past the gate
        self._warmup_requested: asyncio.Event = asyncio.Event()

    def set_flow_push(self, fn: Callable[[], Awaitable[None]] | None) -> None:
        """Optional dashboard hook: broadcast a snapshot right after a flow_event is emitted."""
        self._flow_push = fn

    def _regime_risk_on_global(self) -> bool:
        if not bool(getattr(self._cfg, "regime_risk_on_enabled", True)):
            return False
        return time.time() < self._regime_risk_on_until

    def _apply_regime_risk_on(self, pair_key: str, reasons: list[str]) -> None:
        if not reasons:
            return
        if not bool(getattr(self._cfg, "regime_risk_on_enabled", True)):
            return
        hold = float(getattr(self._cfg, "risk_on_hold_sec", 900.0))
        now = time.time()
        self._regime_risk_on_until = max(self._regime_risk_on_until, now + hold)
        self._regime_pair_reasons[pair_key] = reasons
        if any(str(r).startswith("live_") for r in reasons):
            last = self._regime_live_log_at.get(pair_key, 0.0)
            if now - last >= 120.0:
                self._regime_live_log_at[pair_key] = now
                LOG.info(
                    "ScalpRuntime %s: regime risk-on LIVE reasons=%s until_ts=%.0f",
                    pair_key, reasons, self._regime_risk_on_until,
                )

    def _touch_regime_risk_on(self, pair_key: str, iv: IndicatorValues) -> None:
        from .regime_risk import regime_risk_on_triggers

        reasons = regime_risk_on_triggers(iv, self._cfg)
        self._apply_regime_risk_on(pair_key, reasons)

    def _update_regime_live_velocity_bps(self, pair_key: str, price: float) -> float:
        window = float(getattr(self._cfg, "regime_live_velocity_window_sec", 45.0))
        if window <= 0.0 or price <= 0.0:
            return 0.0
        dq = self._regime_live_prices.setdefault(pair_key, deque())
        now = time.time()
        dq.append((now, price))
        cutoff = now - window
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        if len(dq) < 2:
            return 0.0
        prices = [p for _, p in dq]
        lo, hi = min(prices), max(prices)
        mid = (lo + hi) / 2.0
        if mid <= 0.0:
            return 0.0
        return (hi - lo) / mid * 10_000.0

    def _touch_regime_risk_on_live(
        self, pair_key: str, iv: IndicatorValues, candle: Candle, live_velocity_bps: float,
    ) -> None:
        from .regime_risk import regime_risk_on_triggers_live

        reasons = regime_risk_on_triggers_live(
            iv, candle, self._cfg, live_velocity_bps=live_velocity_bps,
        )
        self._apply_regime_risk_on(pair_key, reasons)

    def _effective_bootstrap_hours(self) -> float:
        base = float(NO_CHAMPION_BOOTSTRAP_HOURS)
        if not bool(getattr(self._cfg, "regime_risk_on_enabled", True)):
            return base
        if not self._regime_risk_on_global():
            return base
        risk_h = float(getattr(self._cfg, "risk_on_bootstrap_hours", 1.0))
        if risk_h <= 0:
            return base
        return min(base, risk_h)

    def _effective_wfo_sleep_sec(self) -> float:
        base = max(60.0, float(self._cfg.wfo_interval_sec))
        if not bool(getattr(self._cfg, "regime_risk_on_enabled", True)):
            return base
        if not self._regime_risk_on_global():
            return base
        floor_sec = max(60.0, float(getattr(self._cfg, "risk_on_wfo_min_interval_sec", 300.0)))
        scale = float(getattr(self._cfg, "risk_on_wfo_interval_scale", 0.35))
        eff = float(self._cfg.wfo_interval_sec) * scale
        return max(floor_sec, max(60.0, eff))

    def _nemesis_dual_gate_kwargs(self) -> dict:
        if not bool(getattr(self._cfg, "regime_risk_on_enabled", True)) or not self._regime_risk_on_global():
            return {"expectancy_slack": 0.0, "tuner_min_pf": 1.0}
        return {
            "expectancy_slack": float(getattr(self._cfg, "risk_on_nemesis_expectancy_slack", 0.0)),
            "tuner_min_pf": float(getattr(self._cfg, "risk_on_nemesis_min_pf", 0.95)),
        }

    @staticmethod
    def _build_warmup_steps() -> list[WarmupStep]:
        return [
            WarmupStep("feed",      "Candle Feed"),
            WarmupStep("backfill",  "Bar Backfill"),
            WarmupStep("wfo",       "Walk-Forward Optimization"),
            WarmupStep("champion",  "Champion Validation"),
        ]

    def reset_warmup_steps(self) -> None:
        self._warmup_steps = self._build_warmup_steps()

    @property
    def startup_phase(self) -> StartupPhase:
        return self._startup_phase

    @property
    def config(self) -> ScalpBotConfig:
        return self._cfg

    def start(self) -> None:
        if not self._cfg.enabled:
            LOG.info("ScalpRuntime: disabled in config — not starting")
            return
        if not self._cfg.pairs:
            LOG.info("ScalpRuntime: no pairs configured — not starting")
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="scalp_runtime")
            self._task.add_done_callback(self._on_task_done)
            LOG.info(
                "ScalpRuntime: started for pairs %s",
                list(self._cfg.pairs.keys()),
            )

    @staticmethod
    def _on_task_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            LOG.error("ScalpRuntime task crashed: %s: %s", type(exc).__name__, exc, exc_info=exc)

    async def stop(self) -> None:
        await self._wfo.stop()
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

    @property
    def warmup_complete(self) -> bool:
        return self._warmup_phase in (WarmupPhase.READY, WarmupPhase.DISABLED)

    @staticmethod
    def _champion_row_summary(row: dict) -> dict:
        hm = row.get("holdout_metrics") or {}
        return {
            "mode": row.get("mode", ""),
            "score": row.get("score", 0),
            "stability": row.get("stability", 0),
            "sharpe": hm.get("sharpe", 0),
            "sortino": hm.get("sortino", 0),
            "calmar": hm.get("calmar", 0),
            "recovery_factor": hm.get("recovery_factor", 0),
            "profit_factor": hm.get("profit_factor", 0),
            "max_drawdown_pct": hm.get("max_drawdown_pct", 0),
            "win_rate": hm.get("win_rate", 0),
            "trade_count": hm.get("trade_count", 0),
            "buy_hold_return": hm.get("buy_hold_return", 0),
            "expectancy": hm.get("expectancy", 0),
        }

    def _maybe_run_tuner(self, champ_store: dict | None = None) -> None:
        """Run the self-tuning parameter optimizer (throttled)."""
        if not self._cfg.enabled or not self._cfg.pairs:
            return
        if self._warmup_phase not in (WarmupPhase.READY, WarmupPhase.DISABLED):
            return
        now = time.time()
        if now - self._tuner_last_run < _TUNER_INTERVAL_SEC:
            return
        self._tuner_last_run = now

        lookback_h = float(self._cfg.wfo_train_hours) + float(self._cfg.wfo_holdout_hours)
        any_applied = False
        champ = champ_store if champ_store is not None else self._champion_data
        if champ is None:
            champ = load_champion()

        for pk, pc in self._cfg.pairs.items():
            try:
                result = run_tuner_cycle(pk, pc, self._cfg, lookback_h)
                if result is None:
                    continue

                self._tuner_results[pk] = result

                wfo_champion_active = pair_has_wfo_champion(champ, pc.symbol)
                if wfo_champion_active:
                    self._nemesis_resolution.pop(pk, None)
                else:
                    # Nemesis dual-lens: bootstrap (short return%) vs tuner (expectancy on lookback).
                    old_mode = self._active_mode.get(pk, "?")
                    try:
                        boot_m = best_mode_bootstrap_no_champion(
                            pc, self._cfg, lookback_hours=self._effective_bootstrap_hours(),
                        )
                    except Exception:
                        boot_m = old_mode
                    ng = self._nemesis_dual_gate_kwargs()
                    resolved, n_reason, n_meta = nemesis_resolve_bootstrap_vs_tuner(
                        bootstrap_mode=str(boot_m),
                        tuner_best_mode=str(result.best_mode or ""),
                        tuner_all_modes=result.all_modes,
                        expectancy_slack=float(ng["expectancy_slack"]),
                        tuner_min_pf=float(ng["tuner_min_pf"]),
                    )
                    self._nemesis_resolution[pk] = {
                        "resolved_mode": resolved,
                        "reason": n_reason,
                        **n_meta,
                    }
                    if resolved != old_mode:
                        self._active_mode[pk] = resolved
                        if n_reason == "nemesis_tuner_wins_dual_gate":
                            self._mode_source[pk] = "nemesis_tuner"
                        elif n_reason == "nemesis_agree":
                            self._mode_source[pk] = "bootstrap"
                        elif n_reason in ("nemesis_bootstrap_holds", "nemesis_no_tuner_mode"):
                            self._mode_source[pk] = "bootstrap"
                        elif n_reason == "nemesis_bootstrap_empty":
                            self._mode_source[pk] = "tuner"
                        else:
                            self._mode_source[pk] = "tuner"
                        LOG.info(
                            "ParamTuner %s: Nemesis resolved mode %s -> %s (%s) boot=%s tuner_nom=%s",
                            pk, old_mode, resolved, n_reason, boot_m, result.best_mode,
                        )

                if result.frozen:
                    LOG.info(
                        "ParamTuner %s: FROZEN (wr=%.1f%%, trades=%d) — params locked, mode switch still allowed",
                        pk, result.best_win_rate * 100, result.best_trades,
                    )
                    continue

                # Apply tuned params only for the mode the tuner grid optimized AND that matches
                # active execution mode (avoids pair_cfg desync after Nemesis holds bootstrap).
                effective_mode = str(self._active_mode.get(pk) or "")
                if effective_mode != str(result.best_mode or ""):
                    LOG.info(
                        "ParamTuner %s: skip apply_tuner_result — active_mode=%s tuner_cycle_best=%s",
                        pk, effective_mode, result.best_mode,
                    )
                    if result.adjustments_made:
                        for adj in result.adjustments_made:
                            LOG.info("ParamTuner %s: %s", pk, adj)
                    else:
                        LOG.info(
                            "ParamTuner %s: no improvements applied (mode mismatch or frozen grid)",
                            pk,
                        )
                    continue

                applied = apply_tuner_result(result, pc)
                if applied:
                    any_applied = True
                    LOG.info(
                        "ParamTuner %s: applied %d changes [%s] best_mode=%s wr=%.1f%% pnl=%.4f",
                        pk, len(applied), ", ".join(applied),
                        result.best_mode, result.best_win_rate * 100, result.best_pnl,
                    )
                    if self._session_log is not None:
                        self._session_log.log_scalp(
                            "tuner_applied",
                            pair_key=pk,
                            best_mode=result.best_mode,
                            win_rate=round(result.best_win_rate, 4),
                            pnl=round(result.best_pnl, 6),
                            changes=applied,
                            aggressiveness=result.aggressiveness,
                        )

                if result.adjustments_made:
                    for adj in result.adjustments_made:
                        LOG.info("ParamTuner %s: %s", pk, adj)
                else:
                    LOG.info(
                        "ParamTuner %s: no improvements found (wr=%.1f%% across %d modes)",
                        pk, result.best_win_rate * 100, len(result.all_modes),
                    )

            except Exception:
                LOG.exception("ParamTuner: error tuning %s", pk)

        if self._tuner_results:
            try:
                save_tuner_state(self._tuner_results)
                self._tuner_snapshot = {
                    pk: {
                        "best_mode": r.best_mode,
                        "best_win_rate": r.best_win_rate,
                        "best_pnl": r.best_pnl,
                        "best_trades": r.best_trades,
                        "frozen": r.frozen,
                        "aggressiveness": r.aggressiveness,
                        "adjustments": r.adjustments_made[-5:],
                        "all_modes": r.all_modes,
                        "timestamp": r.timestamp,
                    }
                    for pk, r in self._tuner_results.items()
                }
            except Exception:
                LOG.exception("ParamTuner: error saving state")

    def _maybe_refresh_strategy_lookback(self) -> None:
        """Recompute per-mode backtest win % for the dashboard (bar store, throttled)."""
        if not self._cfg.enabled or not self._cfg.pairs:
            return
        now = time.time()
        if now - self._strategy_lookback_ts < _STRATEGY_LOOKBACK_REFRESH_SEC:
            return
        try:
            self._strategy_lookback_snapshot = build_strategy_lookback_snapshot(self._cfg)
        except Exception:
            LOG.exception("ScalpRuntime: strategy_lookback refresh failed")
        finally:
            self._strategy_lookback_ts = now

    def snapshot(self, *, include_closed_candles: bool = True) -> dict:
        self._maybe_refresh_strategy_lookback()

        wfo_risk_on_active = (
            bool(getattr(self._cfg, "regime_risk_on_enabled", True))
            and self._regime_risk_on_global()
        )
        wfo_risk_on_label = "WFO risk on" if wfo_risk_on_active else None

        warmup: dict = {
            "phase": self._warmup_phase.value,
            "enabled": self._cfg.warmup_enabled,
        }
        if self._cfg.warmup_enabled:
            min_bars = self._cfg.warmup_min_bars
            bars_so_far = min(self._warmup_bars_collected.values()) if self._warmup_bars_collected else 0
            warmup.update({
                "bars_collected": dict(self._warmup_bars_collected),
                "bars_required": min_bars,
                "progress_pct": round(min(100.0, bars_so_far / max(1, min_bars) * 100), 1),
                "champion_found": self._warmup_champion_found,
                "wfo_triggered": self._warmup_wfo_triggered,
                "elapsed_sec": round(time.time() - self._warmup_start_ts, 1) if self._warmup_start_ts > 0 else 0,
                "startup_steps": [dataclasses.asdict(s) for s in self._warmup_steps],
            })

        candles: dict = {}
        if self._feed is not None:
            for pair_key in self._cfg.pairs:
                lc = self._feed.get_live_candle(pair_key)
                entry: dict = {
                    "live": (
                        {"t": lc.timestamp, "o": lc.open, "h": lc.high,
                         "l": lc.low, "c": lc.close, "v": lc.volume}
                        if lc else None
                    ),
                    "interval": self._cfg.pairs[pair_key].interval,
                }
                if include_closed_candles:
                    buf = self._feed.get_buffer(pair_key)
                    entry["closed"] = [
                        {"t": c.timestamp, "o": c.open, "h": c.high,
                         "l": c.low, "c": c.close, "v": c.volume}
                        for c in buf[-500:]
                    ]
                candles[pair_key] = entry

        champions_map: dict[str, dict] = {}
        if self._champion_data:
            for sym, row in self._champion_data.items():
                if isinstance(row, dict):
                    champions_map[str(sym)] = self._champion_row_summary(row)

        champion_summary = None
        for _pk, pc in self._cfg.pairs.items():
            row = (self._champion_data or {}).get(pc.symbol)
            if isinstance(row, dict):
                champion_summary = self._champion_row_summary(row)
                break

        any_champion_for_config = bool(
            self._champion_data
            and any(pc.symbol in self._champion_data for pc in self._cfg.pairs.values())
        )

        wfo_ui = None
        if self._cfg.enabled and self._cfg.wfo_enabled:
            wfo_ui = self._wfo.ui_snapshot(
                self._cfg,
                champion_active=any_champion_for_config,
            )

        # Pull balance snapshot from Coinbase manager if available
        balances: dict = {}
        if (
            self._live_mgr is not None
            and hasattr(self._live_mgr, "balance_snapshot")
        ):
            try:
                balances = self._live_mgr.balance_snapshot()
            except Exception:
                pass

        exchange_open_orders: list[dict] = []
        if self._live_mgr is not None and hasattr(self._live_mgr, "scalp_open_orders_snapshot"):
            try:
                exchange_open_orders = self._live_mgr.scalp_open_orders_snapshot()
            except Exception:
                pass

        return {
            "enabled": self._cfg.enabled,
            "venue": getattr(self._cfg, "venue", "kraken_spot"),
            "sim_mode": self._trader.sim_mode,
            "max_concurrent_positions": int(self._cfg.max_concurrent_positions),
            "startup_phase": self._startup_phase.value,
            "operator": {
                "standby": self._operator_standby,
                "prep_busy": self._prep_session_busy,
                "require_manual_go_live": bool(getattr(self._cfg, "require_manual_go_live", False)),
                "flow": copy.deepcopy(self._operator_flow) if self._operator_flow else None,
                "flow_seq": int(self._operator_flow_seq),
                "flow_event": dict(self._operator_flow_event) if self._operator_flow_event else None,
                "startup_phase": self._startup_phase.value,
                "can_begin_warmup": self._startup_phase == StartupPhase.STANDBY,
                "can_go_live": self._startup_phase == StartupPhase.PRIMED,
                "warmup_steps": [dataclasses.asdict(s) for s in self._warmup_steps],
            },
            "session_policy": {
                "warmup_enabled": self._cfg.warmup_enabled,
                "warmup_min_bars": int(self._cfg.warmup_min_bars),
                "warmup_require_champion": bool(self._cfg.warmup_require_champion),
                "warmup_max_hours": float(self._cfg.warmup_max_hours),
                "wfo_enabled": bool(self._cfg.wfo_enabled),
                "wfo_train_hours": float(self._cfg.wfo_train_hours),
                "wfo_holdout_hours": float(self._cfg.wfo_holdout_hours),
                "wfo_step_hours": float(self._cfg.wfo_step_hours),
            },
            "warmup": warmup,
            "trader": self._trader.snapshot(),
            "pair_symbols": {k: pc.symbol for k, pc in self._cfg.pairs.items()},
            "active_modes": {
                k: (m if m != "auto" else "daviddtech_scalp")
                for k, m in self._active_mode.items()
            },
            "mode_sources": dict(self._mode_source),
            "champion": champion_summary,
            "champions": champions_map if champions_map else None,
            "strategy_lookback": self._strategy_lookback_snapshot,
            "tuner": self._tuner_snapshot,
            "nemesis": {
                "champion_bootstrap_advisory": dict(self._nemesis_advisory),
                "no_champion_last_resolution": dict(self._nemesis_resolution),
            },
            "regime_risk_on": {
                "enabled": bool(getattr(self._cfg, "regime_risk_on_enabled", True)),
                "live_enabled": bool(getattr(self._cfg, "regime_live_vol_enabled", True)),
                "active": wfo_risk_on_active,
                "mode_label": wfo_risk_on_label,
                "until_ts": self._regime_risk_on_until,
                "pair_reasons": {k: list(v) for k, v in self._regime_pair_reasons.items()},
                "effective_bootstrap_hours": self._effective_bootstrap_hours(),
                "effective_wfo_sleep_sec": (
                    round(self._effective_wfo_sleep_sec(), 1) if self._cfg.wfo_enabled else None
                ),
            },
            "wfo": wfo_ui,
            "balances": balances,
            "intx_unmapped_positions": list(self._intx_unmapped_positions),
            "exchange_open_orders": exchange_open_orders,
            "indicators": {
                k: {
                    "candles": self._indicators[k].candle_count,
                    "ready": iv.ready,
                    "wfo_risk_on_active": wfo_risk_on_active,
                    "wfo_risk_on_label": wfo_risk_on_label,
                    "ema_fast": round(iv.ema_fast, 5),
                    "ema_slow": round(iv.ema_slow, 5),
                    "rsi": round(iv.rsi, 2),
                    "prev_rsi": round(iv.prev_rsi, 2),
                    "atr": round(iv.atr, 6),
                    "vwap": round(iv.vwap_session, 5),
                    "ema_bullish": iv.ema_bullish,
                    "rsi_bullish": iv.rsi_bullish,
                    "rsi_oversold": iv.rsi_oversold,
                    "rsi_sell_trigger": iv.rsi_sell_trigger,
                    "vwap_bullish": iv.vwap_bullish,
                    "volume_confirmed": iv.volume_confirmed,
                    "ema_scalp": round(iv.ema_scalp, 5),
                    "ema_scalp_cross_bull": iv.ema_scalp_cross_bull,
                    "high_8": round(iv.high_8, 5),
                    "low_8": round(iv.low_8, 5),
                    "macd_line": round(iv.macd_line, 2),
                    "macd_signal": round(iv.macd_signal, 2),
                    "macd_cross_bull": iv.macd_cross_bull,
                    "t3": round(iv.t3, 5),
                    "hlc_green": round(iv.hlc_green, 5),
                    "hlc_red": round(iv.hlc_red, 5),
                    "wae_up": round(iv.wae_up, 4),
                    "wae_down": round(iv.wae_down, 4),
                    "adx": round(iv.adx, 2),
                    "optimized_ready": iv.optimized_ready,
                    "optimized_long_setup": iv.optimized_long_setup,
                    "optimized_short_setup": getattr(iv, "optimized_short_setup", False),
                }
                for k, iv in self._latest_iv.items()
            },
            "candles": candles,
            "orderbooks": {
                k: self._feed.get_orderbook(k)
                for k in self._cfg.pairs
            } if self._feed is not None and hasattr(self._feed, "get_orderbook") else {},
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Main loop: start feed, process candles, evaluate signals."""
        pairs = {k: pc.symbol for k, pc in self._cfg.pairs.items()}
        intervals = {k: pc.interval for k, pc in self._cfg.pairs.items()}

        LOG.info("ScalpRuntime: seeding candle feed from REST...")
        bar_store.set_bar_store_venue(getattr(self._cfg, "venue", "kraken_spot"))
        if self._session_log is not None:
            self._session_log.log_scalp(
                "runtime_task_begin",
                pairs=list(self._cfg.pairs.keys()),
                venue=getattr(self._cfg, "venue", "kraken_spot"),
                wfo_enabled=self._cfg.wfo_enabled,
                sim_mode=self._cfg.sim_mode,
                warmup_enabled=self._cfg.warmup_enabled,
                wfo_train_hours=self._cfg.wfo_train_hours,
                wfo_holdout_hours=self._cfg.wfo_holdout_hours,
                wfo_step_hours=self._cfg.wfo_step_hours,
            )
        try:
            self._feed = await start_candle_feed(
                pairs=pairs,
                intervals=intervals,
                rest_seed_count=self._cfg.rest_seed_candles,
                venue=getattr(self._cfg, "venue", "kraken_spot"),
            )
        except Exception:
            LOG.exception("ScalpRuntime: failed to start candle feed")
            return

        # Seed indicator sets from REST buffer and count seeded bars for warmup
        for pair_key in self._cfg.pairs:
            buf = self._feed.get_buffer(pair_key)
            LOG.info("ScalpRuntime: replaying %d REST candles into %s indicators", len(buf), pair_key)
            ind = self._indicators[pair_key]
            for candle in buf:
                iv = ind.update(candle)
            if buf:
                self._latest_iv[pair_key] = iv  # type: ignore[possibly-undefined]
            self._warmup_bars_collected[pair_key] = len(buf)

        # Also count any bars already in bar_store (from prior runs)
        for pair_key, pair_cfg in self._cfg.pairs.items():
            existing = bar_store.bar_count(pair_cfg.symbol, pair_cfg.interval)
            if existing > self._warmup_bars_collected.get(pair_key, 0):
                self._warmup_bars_collected[pair_key] = existing

        # Register callback for live closed candles
        self._feed.register_callback(self._on_closed_candle)

        # Tick / ohlc-update callback: live regime risk-on + intra-bar stop/TP
        self._feed.register_tick_callback(self._on_tick_update)

        # Initialize warmup
        self._warmup_start_ts = time.time()
        if self._cfg.warmup_enabled:
            # Note: a prior champion on disk is informational only at this point.
            # Warmup will not complete until _startup_wfo_done is set (after the
            # initial WFO pass below).  This ensures fresh lookback re-validation
            # on every session start.
            existing_champ = load_champion()
            if existing_champ is not None:
                LOG.info(
                    "ScalpRuntime: prior champion found on disk — will re-validate "
                    "via startup WFO before trading"
                )
            LOG.info(
                "ScalpRuntime: WARMUP started — collecting bars (need %d per pair), "
                "current: %s",
                self._cfg.warmup_min_bars,
                {k: v for k, v in self._warmup_bars_collected.items()},
            )
        else:
            LOG.info("ScalpRuntime: warmup disabled — trading immediately")

        LOG.info("ScalpRuntime: candle feed live — backfilling historical bars for WFO...")

        # Backfill from REST so WFO has enough history to run immediately
        total_hours_needed = (
            self._cfg.wfo_train_hours
            + self._cfg.wfo_holdout_hours
            + self._cfg.wfo_step_hours * 3
            + 1.0  # margin
        )
        for pair_key, pair_cfg in self._cfg.pairs.items():
            try:
                written = await bar_store.backfill_from_rest(
                    pair_cfg.symbol,
                    pair_cfg.interval,
                    total_hours_needed,
                    venue=getattr(self._cfg, "venue", "kraken_spot"),
                )
                existing = bar_store.bar_count(pair_cfg.symbol, pair_cfg.interval)
                if written > 0:
                    self._warmup_bars_collected[pair_key] = max(
                        self._warmup_bars_collected.get(pair_key, 0), existing,
                    )
                LOG.info(
                    "ScalpRuntime: backfill done for %s — new_rows=%d total_in_store=%d (hours_needed=%.1f)",
                    pair_key, written, existing, total_hours_needed,
                )
                if self._session_log is not None:
                    self._session_log.log_scalp(
                        "bar_backfill",
                        pair_key=pair_key,
                        symbol=pair_cfg.symbol,
                        interval=pair_cfg.interval,
                        new_rows=written,
                        total_in_store=existing,
                        hours_needed=round(total_hours_needed, 2),
                    )
            except Exception:
                LOG.exception("ScalpRuntime: backfill failed for %s", pair_key)
                if self._session_log is not None:
                    self._session_log.log_scalp(
                        "bar_backfill_error",
                        pair_key=pair_key,
                        symbol=pair_cfg.symbol,
                        error="exception",
                    )

        # ── Step 1: Candle Feed ───────────────────────────────────────────────
        # Feed is already live at this point — mark its step done and wait for
        # the operator to click "Begin Warmup" from the Settings tab.
        _feed_step = next(s for s in self._warmup_steps if s.key == "feed")
        _feed_step.status = "done"
        _feed_step.pct = 100.0
        _feed_step.detail = f"Live: {len(self._cfg.pairs)} pair(s) subscribed"

        LOG.info(
            "ScalpRuntime: STANDBY — candle feed live, waiting for operator to click Begin Warmup."
        )
        await self._warmup_requested.wait()
        LOG.info("ScalpRuntime: Begin Warmup received — starting warmup sequence.")

        # ── Step 2: Bar Backfill ──────────────────────────────────────────────
        _backfill_step = next(s for s in self._warmup_steps if s.key == "backfill")
        _backfill_step.status = "running"
        _backfill_step.pct = 0.0
        await self._push_warmup_snapshot()

        total_hours_needed = (
            self._cfg.wfo_train_hours
            + self._cfg.wfo_holdout_hours
            + self._cfg.wfo_step_hours * 3
            + 1.0  # margin
        )
        pair_list = list(self._cfg.pairs.items())
        n_pairs = max(1, len(pair_list))
        backfill_any_failed = True
        for i, (pair_key, pair_cfg) in enumerate(pair_list):
            _backfill_step.detail = f"Backfilling {pair_cfg.symbol} ({i + 1}/{n_pairs})…"
            _backfill_step.pct = round(i / n_pairs * 100, 1)
            await self._push_warmup_snapshot()
            try:
                written = await bar_store.backfill_from_rest(
                    pair_cfg.symbol,
                    pair_cfg.interval,
                    total_hours_needed,
                    venue=getattr(self._cfg, "venue", "kraken_spot"),
                )
                existing = bar_store.bar_count(pair_cfg.symbol, pair_cfg.interval)
                if written > 0:
                    self._warmup_bars_collected[pair_key] = max(
                        self._warmup_bars_collected.get(pair_key, 0), existing,
                    )
                LOG.info(
                    "ScalpRuntime: backfill done for %s — new_rows=%d total_in_store=%d",
                    pair_key, written, existing,
                )
                backfill_any_failed = False
                if self._session_log is not None:
                    self._session_log.log_scalp(
                        "bar_backfill",
                        pair_key=pair_key,
                        symbol=pair_cfg.symbol,
                        interval=pair_cfg.interval,
                        new_rows=written,
                        total_in_store=existing,
                        hours_needed=round(total_hours_needed, 2),
                    )
            except Exception:
                LOG.exception("ScalpRuntime: backfill failed for %s", pair_key)
                if self._session_log is not None:
                    self._session_log.log_scalp(
                        "bar_backfill_error",
                        pair_key=pair_key,
                        symbol=pair_cfg.symbol,
                        error="exception",
                    )

        _backfill_step.status = "done"
        _backfill_step.pct = 100.0
        _backfill_step.detail = (
            f"Complete: {', '.join(f'{k}={self._warmup_bars_collected.get(k, 0)} bars' for k in self._cfg.pairs)}"
        )
        await self._push_warmup_snapshot()

        # ── Step 3: WFO ───────────────────────────────────────────────────────
        # Pre-mark as triggered so the candle-callback path doesn't duplicate.
        self._warmup_wfo_triggered = True
        _wfo_step = next(s for s in self._warmup_steps if s.key == "wfo")

        if self._cfg.wfo_enabled:
            _wfo_step.status = "running"
            _wfo_step.pct = 0.0
            _wfo_step.detail = f"Grid search across {n_pairs} pair(s)… ~60s"
            await self._push_warmup_snapshot()

            LOG.info("ScalpRuntime: running startup WFO pass (trading held until complete)…")

            # Pulse progress while WFO runs in a thread
            _wfo_start = time.time()
            _estimated_sec = 60.0
            wfo_success = False
            for attempt in range(2):
                if attempt > 0:
                    _wfo_step.retry_count += 1
                    _wfo_step.detail = f"Retry {attempt}…"
                    await self._push_warmup_snapshot()

                _wfo_task = asyncio.create_task(asyncio.to_thread(self._wfo.run_once))
                try:
                    while not _wfo_task.done():
                        _elapsed = time.time() - _wfo_start
                        _wfo_step.pct = round(min(92.0, (_elapsed / _estimated_sec) * 100.0), 1)
                        _wfo_step.detail = f"Grid search running… {int(_elapsed)}s elapsed"
                        await self._push_warmup_snapshot()
                        await asyncio.sleep(2.0)

                    results = await _wfo_task
                    for pk, r in results.items():
                        if r is not None:
                            self._warmup_champion_found = True
                            LOG.info("ScalpRuntime: startup WFO champion for %s: mode=%s", pk, r.get("mode"))
                        else:
                            LOG.info("ScalpRuntime: startup WFO — no champion for %s", pk)
                    wfo_success = True
                    break
                except Exception as exc:
                    LOG.exception("ScalpRuntime: startup WFO pass failed (attempt %d)", attempt + 1)
                    if attempt < 1:
                        _wfo_step.error = str(exc)[:200]
                        _wfo_step.pct = 0.0
                        _wfo_start = time.time()
                    else:
                        _wfo_step.status = "failed"
                        _wfo_step.error = str(exc)[:200]
                        _wfo_step.detail = f"Failed: {str(exc)[:80]}"
                        self._state.push_alert(
                            "error",
                            "Warmup: WFO failed",
                            f"Walk-forward optimization failed after retries: {str(exc)[:200]}",
                            "scalp_warmup",
                        )

            if wfo_success:
                _wfo_step.status = "done"
                _wfo_step.pct = 100.0
                n_champ = sum(1 for r in results.values() if r is not None)
                _wfo_step.detail = f"Champion found for {n_champ}/{n_pairs} pair(s)"

        else:
            _wfo_step.status = "done"
            _wfo_step.pct = 100.0
            _wfo_step.detail = "WFO disabled — using config defaults"

        self._startup_wfo_done = True
        await self._push_warmup_snapshot()

        # ── Step 4: Champion Validation ───────────────────────────────────────
        _champ_step = next(s for s in self._warmup_steps if s.key == "champion")
        _champ_step.status = "running"
        _champ_step.pct = 50.0
        _champ_step.detail = "Loading champion from disk…"
        await self._push_warmup_snapshot()

        try:
            _mt0 = CHAMPION_PATH.stat().st_mtime if CHAMPION_PATH.exists() else 0.0
        except OSError:
            _mt0 = 0.0
        _ch0 = load_champion() if _mt0 > self._champion_mtime else None
        self._try_load_champion(_ch0, file_mtime=_mt0)
        self._apply_no_champion_bootstrap()

        _champ_step.status = "done"
        _champ_step.pct = 100.0
        _champ_step.detail = (
            f"Champion active: {self._warmup_champion_found}"
            if self._warmup_champion_found
            else "No champion — using bootstrap strategy defaults"
        )

        # ── Transition: WARMING_UP → PRIMED ──────────────────────────────────
        self._check_warmup_complete()
        if self._warmup_phase == WarmupPhase.READY:
            self._startup_phase = StartupPhase.PRIMED
            LOG.info("ScalpRuntime: system PRIMED — awaiting operator Go Live")
            self._state.push_alert(
                "success",
                "System Primed",
                "Warmup complete. Open Settings → Go Live to begin trading.",
                "scalp_warmup",
            )
            self._flow_emit("warmup_primed", "System primed — click Go Live to arm the engine.")
        else:
            # Warmup conditions not met (e.g. no champion and require_champion) —
            # still mark PRIMED so Go Live is not blocked forever.
            self._startup_phase = StartupPhase.PRIMED
            LOG.warning("ScalpRuntime: warmup gates not met but advancing to PRIMED anyway")

        await self._push_warmup_snapshot()

        # Start the recurring WFO loop (subsequent passes on interval)
        self._wfo.start()

        # Keep running — feed callbacks drive all activity
        while True:
            await asyncio.sleep(60)
            try:
                _mt = CHAMPION_PATH.stat().st_mtime if CHAMPION_PATH.exists() else 0.0
            except OSError:
                _mt = self._champion_mtime
            _ch_tick = load_champion() if _mt > self._champion_mtime else None
            if self._try_load_champion(_ch_tick, file_mtime=_mt):
                self._apply_no_champion_bootstrap()
            self._check_warmup_timeout()
            self._nemesis_refresh_champion_advisory(self._champion_data)
            self._maybe_run_tuner(self._champion_data)
            self._log_status()

    def _on_tick_update(self, pair_key: str, candle: Candle) -> None:
        """Fires on every WS candle/ticker update — live regime risk-on + intra-bar stop/TP."""
        if pair_key not in self._cfg.pairs:
            return

        # Live regime (WFO cadence / bootstrap) — runs during warmup too; needs last closed iv.
        iv = self._latest_iv.get(pair_key)
        if (
            iv is not None
            and bool(getattr(self._cfg, "regime_risk_on_enabled", True))
            and bool(getattr(self._cfg, "regime_live_vol_enabled", True))
        ):
            vel = 0.0
            w = float(getattr(self._cfg, "regime_live_velocity_window_sec", 45.0))
            vmin = float(getattr(self._cfg, "regime_live_velocity_min_bps", 20.0))
            if w > 0.0 and vmin > 0.0:
                vel = self._update_regime_live_velocity_bps(pair_key, float(candle.close))
            self._touch_regime_risk_on_live(pair_key, iv, candle, vel)

        if self._warmup_phase in (WarmupPhase.COLLECTING, WarmupPhase.OPTIMIZING):
            return
        self._trader.check_paper_exits(pair_key, candle)

        if getattr(self._cfg, "tick_entries_enabled", False) and not self._operator_standby:
            self._evaluate_tick_entry(pair_key, candle)

    def _evaluate_tick_entry(self, pair_key: str, candle: Candle) -> None:
        """Optional mid-bar entries using frozen ``_latest_iv`` + live candle close."""
        if not self._cfg.enabled:
            return
        if self._state.mm_spread_bot_enabled and self._state.risk_halted:
            return
        if self._trader.has_position(pair_key):
            return
        cap = self._cfg.concurrent_open_cap()
        if cap is not None and self._trader.open_position_count >= cap:
            return

        iv = self._latest_iv.get(pair_key)
        if iv is None:
            return

        active_mode = self._active_mode.get(pair_key, "daviddtech_scalp")
        eff_mode = active_mode
        if eff_mode == "auto":
            eff_mode = "daviddtech_scalp"
        if eff_mode == "daviddtech_scalp":
            if not iv.optimized_ready:
                return
        elif not iv.ready:
            return

        pair_cfg = self._cfg.pairs[pair_key]
        symbol = pair_cfg.symbol
        mode_override = active_mode if active_mode != "auto" else None
        live_price = float(candle.close)

        signal = self._signal_engine.evaluate_tick(
            pair_key,
            symbol,
            pair_cfg,
            iv,
            live_price,
            mode_override=mode_override,
            shorts_enabled=bool(getattr(self._cfg, "shorts_enabled", False)),
            tick_signal_cooldown_sec=float(
                getattr(self._cfg, "tick_signal_cooldown_sec", 300.0)
            ),
        )
        if signal is None:
            return

        asyncio.create_task(
            self._open_position(signal, pair_cfg),
            name=f"scalp_open_tick_{pair_key}",
        )

    def _on_closed_candle(self, pair_key: str, candle: Candle) -> None:
        """Synchronous callback — called from CandleFeed on confirmed closed candle."""
        if pair_key not in self._cfg.pairs:
            return

        pair_cfg = self._cfg.pairs[pair_key]

        # Persist to bar store for WFO
        bar_store.append_candles(pair_cfg.symbol, pair_cfg.interval, [
            bar_store.candle_dict_from_feed(candle),
        ])

        # Track bars for warmup progress
        self._warmup_bars_collected[pair_key] = self._warmup_bars_collected.get(pair_key, 0) + 1

        ind = self._indicators[pair_key]
        iv = ind.update(candle)
        self._latest_iv[pair_key] = iv
        self._touch_regime_risk_on(pair_key, iv)
        # INTX perps: uPnL mark comes from Coinbase ``get_product`` mid (see CoinbaseOrderManager._refresh_scalp_marks).
        # Using candle close here drifts from the exchange P&L (15m bar vs live mark).
        if str(getattr(self._cfg, "venue", "") or "").strip().lower() != "coinbase_perps":
            self._trader.update_position_mark(pair_key, candle.close)

        LOG.info(
            "ScalpRuntime %s: candle close=%.6f ema_f=%.6f ema_s=%.6f rsi=%.1f "
            "atr=%.6f vwap=%.6f vol_ok=%s ready=%s",
            pair_key, candle.close, iv.ema_fast, iv.ema_slow, iv.rsi,
            iv.atr, iv.vwap_session, iv.volume_confirmed, iv.ready,
        )

        # During warmup: check if bar threshold reached and trigger early WFO
        if self._warmup_phase in (WarmupPhase.COLLECTING, WarmupPhase.OPTIMIZING):
            self._check_warmup_bar_threshold()
            return

        # -- Below here: warmup complete or disabled — normal trading --

        # Time stop check (both paper and live modes)
        self._trader.check_time_stop(pair_key, pair_cfg, candle.close)

        # Break-even / trailing stop adjustment (live + paper).
        # Coinbase INTX: prefer live mark from get_product (updated in fill-poll loop) over
        # candle close so stop ratchets track the venue, not a stale 15m bar.
        trail_ref_price = float(candle.close)
        if str(getattr(self._cfg, "venue", "") or "").strip().lower() == "coinbase_perps":
            op = self._trader.get_position(pair_key)
            if (
                op is not None
                and op.status == "open"
                and float(getattr(op, "mark_price", 0.0) or 0.0) > 0
            ):
                trail_ref_price = float(op.mark_price)

        if iv.atr > 0:
            asyncio.create_task(
                self._trader.check_trail_and_breakeven(
                    pair_key, pair_cfg, trail_ref_price, iv.atr,
                ),
                name=f"scalp_trail_{pair_key}",
            )

        # Paper mode: check stop/TP on every closed candle
        self._trader.check_paper_exits(pair_key, candle)

        # RSI reversion exit: close position when RSI crosses sell threshold
        active_mode = self._active_mode.get(pair_key, "daviddtech_scalp")
        if active_mode == "rsi_reversion" and iv.rsi_sell_trigger:
            self._trader.check_rsi_exit(pair_key, candle.close)

        eff_mode = active_mode
        if eff_mode == "auto":
            eff_mode = "daviddtech_scalp"
        if eff_mode == "daviddtech_scalp":
            if not iv.optimized_ready:
                return
        elif not iv.ready:
            return

        if not self._cfg.enabled:
            return

        if self._state.mm_spread_bot_enabled and self._state.risk_halted:
            return

        # Scalp entries do not require spread-engine START — only OFF/SIM/LIVE on the scalp UI.

        pair_cfg = self._cfg.pairs[pair_key]
        symbol = pair_cfg.symbol
        mode_override = active_mode if active_mode != "auto" else None
        shorts_enabled = bool(getattr(self._cfg, "shorts_enabled", False))

        if self._trader.has_position(pair_key):
            # Check for a strong counter-signal while we hold a position.
            # evaluate_counter bypasses cooldowns and only returns opposite-direction signals.
            # _reversal_score then decides autonomously: skip / exit / full-reversal.
            pos = self._trader.get_position(pair_key)
            if pos is not None and pos.status == "open":
                counter = self._signal_engine.evaluate_counter(
                    pair_key, symbol, pair_cfg, iv,
                    current_direction=pos.direction,
                    mode_override=mode_override,
                    shorts_enabled=shorts_enabled,
                )
                if counter is not None:
                    asyncio.create_task(
                        self._counter_exit(counter, pair_cfg, iv),
                        name=f"scalp_counter_{pair_key}",
                    )
            return

        signal = self._signal_engine.evaluate(
            pair_key,
            symbol,
            pair_cfg,
            iv,
            mode_override=mode_override,
            shorts_enabled=shorts_enabled,
        )
        if signal is None:
            return

        if self._operator_standby:
            return

        asyncio.create_task(
            self._open_position(signal, pair_cfg),
            name=f"scalp_open_{pair_key}",
        )

    async def _open_position(self, signal, pair_cfg) -> None:
        """Async wrapper for try_open — called via create_task from sync callback."""
        if self._operator_standby:
            LOG.info(
                "ScalpRuntime: entry suppressed — operator standby (%s)",
                getattr(signal, "pair_key", "?"),
            )
            return
        available = self._available_capital()
        await self._trader.try_open(signal, pair_cfg, available)

    async def _counter_exit(self, counter_signal, pair_cfg, iv) -> None:
        """Async wrapper for check_counter_signal — called via create_task."""
        available = self._available_capital()
        await self._trader.check_counter_signal(
            counter_signal.pair_key, pair_cfg, counter_signal, iv, available,
        )

    def _available_capital(self) -> float:
        """Total USD available to the scalp bot across all quote balances."""
        # Use allocated_capital_usd as the cap — don't touch MM bot capital
        return self._cfg.allocated_capital_usd

    # ── Warmup logic ────────────────────────────────────────────────────────────

    def _check_warmup_bar_threshold(self) -> None:
        """Trigger WFO once all pairs have enough bars, then check for champion."""
        if self._warmup_phase not in (WarmupPhase.COLLECTING, WarmupPhase.OPTIMIZING):
            return

        min_bars = self._cfg.warmup_min_bars
        all_met = all(
            v >= min_bars for v in self._warmup_bars_collected.values()
        )

        if all_met and not self._warmup_wfo_triggered:
            if self._operator_standby:
                return  # WFO gated — waiting for operator go-live
            self._warmup_phase = WarmupPhase.OPTIMIZING
            self._warmup_wfo_triggered = True
            LOG.info(
                "ScalpRuntime: warmup bar threshold met (%d bars) — running WFO now",
                min_bars,
            )
            # Fire an immediate optimization pass (synchronous, runs in callback thread)
            try:
                results = self._wfo.run_once()
                for pk, r in results.items():
                    if r is not None:
                        self._warmup_champion_found = True
                        LOG.info("ScalpRuntime: warmup WFO found champion for %s", pk)
            except Exception:
                LOG.exception("ScalpRuntime: warmup WFO pass failed")

        self._check_warmup_complete()

    def _check_warmup_complete(self) -> bool:
        """Transition from warmup to ready if conditions are met. Returns True if ready."""
        if self._warmup_phase in (WarmupPhase.READY, WarmupPhase.DISABLED):
            return True

        # Always run a fresh WFO pass before allowing trading, even if a prior
        # champion exists on disk.  This ensures the self-learning loop re-validates
        # strategy params against recent data on every session start.
        if not self._startup_wfo_done:
            return False

        min_bars = self._cfg.warmup_min_bars
        all_bars_met = all(
            v >= min_bars for v in self._warmup_bars_collected.values()
        )

        if not all_bars_met:
            return False

        if self._cfg.warmup_require_champion and not self._warmup_champion_found:
            return False

        # All conditions met
        self._warmup_phase = WarmupPhase.READY
        if not self._operator_standby:
            self._state.running = True
        elapsed = time.time() - self._warmup_start_ts if self._warmup_start_ts > 0 else 0
        LOG.info(
            "ScalpRuntime: WARMUP COMPLETE after %.1f minutes — trading enabled. "
            "bars=%s champion=%s",
            elapsed / 60.0,
            dict(self._warmup_bars_collected),
            self._warmup_champion_found,
        )
        if self._operator_standby:
            warm_detail = (
                f"Self-learning phase finished after {elapsed / 60:.0f}m. "
                f"Champion found: {self._warmup_champion_found}. "
                "STANDBY: open Settings and click Go live to arm new entries."
            )
        else:
            warm_detail = (
                f"Self-learning phase finished after {elapsed / 60:.0f}m. "
                f"Champion found: {self._warmup_champion_found}. New entries allowed "
                "(subject to SIM/LIVE and risk gates)."
            )
        self._state.push_alert(
            "success",
            "Scalp Warmup Complete",
            warm_detail,
            "scalp_warmup",
        )
        if self._session_log is not None:
            self._session_log.log_scalp(
                "warmup_complete",
                elapsed_sec=round(elapsed, 1),
                champion_found=self._warmup_champion_found,
                bars=dict(self._warmup_bars_collected),
            )
        return True

    def _check_warmup_timeout(self) -> None:
        """If warmup_max_hours > 0, force-graduate warmup after that duration."""
        if self._warmup_phase in (WarmupPhase.READY, WarmupPhase.DISABLED):
            return
        if self._operator_standby:
            return  # don't timeout while waiting for operator go-live
        max_h = self._cfg.warmup_max_hours
        if max_h <= 0:
            return
        elapsed = time.time() - self._warmup_start_ts
        if elapsed >= max_h * 3600.0:
            LOG.warning(
                "ScalpRuntime: warmup timeout (%.1f hours) — forcing to READY. "
                "champion_found=%s bars=%s",
                max_h, self._warmup_champion_found,
                dict(self._warmup_bars_collected),
            )
            self._warmup_phase = WarmupPhase.READY
            self._state.push_alert(
                "warning",
                "Scalp Warmup Timeout",
                f"Max warmup time ({max_h}h) reached. Trading enabled with "
                f"{'champion' if self._warmup_champion_found else 'default'} params.",
                "scalp_warmup",
            )
            if self._session_log is not None:
                self._session_log.log_scalp(
                    "warmup_timeout",
                    max_hours=max_h,
                    champion_found=self._warmup_champion_found,
                    bars=dict(self._warmup_bars_collected),
                )

    async def _push_warmup_snapshot(self) -> None:
        """Push a live snapshot to all dashboard clients (progress update during warmup)."""
        if self._flow_push is not None:
            try:
                await self._flow_push()
            except Exception:
                pass

    # ── Operator session controls (dashboard Settings tab) ─────────────────────

    def _flow_emit(self, kind: str, detail: str = "") -> None:
        self._operator_flow_seq += 1
        self._operator_flow_event = {
            "seq": self._operator_flow_seq,
            "kind": str(kind),
            "detail": str(detail or ""),
        }
        fp = self._flow_push
        if fp is not None:
            try:
                asyncio.get_running_loop().create_task(fp())
            except RuntimeError:
                LOG.debug("ScalpRuntime: flow_push skipped (no running event loop)")

    @staticmethod
    def _flow_overall_pct(steps: list) -> float:
        if not steps:
            return 0.0
        return sum(float(s.get("pct", 0)) for s in steps) / float(len(steps))

    def _flow_new_prep(self) -> None:
        self._operator_flow = {
            "visible": True,
            "title": "Session preparation",
            "overall_pct": 0.0,
            "steps": [
                {"key": "standby", "label": "STANDBY", "pct": 0.0, "state": "pending"},
                {"key": "configuration", "label": "CONFIGURATION", "pct": 0.0, "state": "pending"},
                {"key": "go_live", "label": "GO LIVE", "pct": 0.0, "state": "pending"},
            ],
        }

    async def _flow_cancel_pulse(self) -> None:
        t = self._operator_flow_pulse_task
        self._operator_flow_pulse_task = None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def _flow_pulse_configuration(self) -> None:
        try:
            while self._prep_session_busy and self._operator_flow:
                st = self._operator_flow.get("steps") or []
                if len(st) > 1 and st[1].get("state") == "running":
                    cur = float(st[1].get("pct", 0))
                    st[1]["pct"] = min(92.0, cur + 5.0)
                    self._operator_flow["overall_pct"] = round(self._flow_overall_pct(st), 1)
                await asyncio.sleep(0.55)
        except asyncio.CancelledError:
            pass

    async def _flow_fill_step(self, idx: int, target: float = 100.0) -> None:
        if not self._operator_flow:
            return
        steps = self._operator_flow["steps"]
        if idx < 0 or idx >= len(steps):
            return
        s = steps[idx]
        s["state"] = "running"
        cur = float(s.get("pct", 0))
        tgt = float(target)
        while cur < tgt - 0.4:
            cur = min(tgt, cur + max(7.0, (tgt - cur) * 0.4))
            s["pct"] = round(cur, 1)
            self._operator_flow["overall_pct"] = round(self._flow_overall_pct(steps), 1)
            await asyncio.sleep(0.1)
        s["pct"] = round(tgt, 1)
        s["state"] = "done"
        self._operator_flow["overall_pct"] = round(self._flow_overall_pct(steps), 1)
        await asyncio.sleep(0.05)

    async def _flow_hide_later(self, delay_sec: float) -> None:
        try:
            await asyncio.sleep(float(delay_sec))
        except asyncio.CancelledError:
            return
        if not self._prep_session_busy:
            self._operator_flow = None

    def operator_begin_warmup(self) -> None:
        """Trigger the warmup sequence (Begin Warmup button in Settings)."""
        if self._startup_phase != StartupPhase.STANDBY:
            # Allow re-triggering only if fully aborted (all steps failed)
            all_failed = all(s.status in ("failed", "pending") for s in self._warmup_steps)
            if not all_failed:
                LOG.warning("ScalpRuntime: Begin Warmup ignored — phase=%s", self._startup_phase.value)
                return
            # Reset for a fresh attempt
            self._warmup_steps = self._build_warmup_steps()
            self._warmup_wfo_triggered = False
            self._startup_wfo_done = False
            self._warmup_champion_found = False
            self._warmup_requested.clear()

        self._startup_phase = StartupPhase.WARMING_UP
        self._operator_standby = True  # stays True until Go Live
        self._warmup_start_ts = time.time()
        self._warmup_requested.set()
        self._flow_emit("warmup_started", "Warmup sequence initiated — bar backfill and WFO starting.")
        self._state.push_alert(
            "info",
            "Warmup Started",
            "Bar backfill and walk-forward optimization in progress…",
            "scalp_warmup",
        )
        LOG.info("ScalpRuntime: operator_begin_warmup — warmup sequence started")

    def operator_go_live(self) -> None:
        """Arm new scalp entries (subject to warmup, SIM/LIVE, risk gates)."""
        if self._startup_phase != StartupPhase.PRIMED:
            LOG.warning(
                "ScalpRuntime: Go Live ignored — system not primed (phase=%s). "
                "Complete warmup first.",
                self._startup_phase.value,
            )
            self._state.push_alert(
                "warning",
                "Go Live blocked",
                f"System must be PRIMED before going live (current: {self._startup_phase.value}). "
                "Run Begin Warmup first.",
                "scalp_operator",
            )
            return
        self._operator_standby = False
        self._startup_phase = StartupPhase.LIVE
        self._state.running = True
        self._state.push_alert(
            "success",
            "Scalp: Go live",
            "Standby cleared — new entries allowed when signals and gates permit.",
            "scalp_operator",
        )
        LOG.info("ScalpRuntime: operator_go_live — entries armed, phase=LIVE")

    def operator_enter_standby(self) -> None:
        """Block new entries; protective exits and position management continue."""
        self._operator_standby = True
        self._state.running = False
        # LIVE → PRIMED (warmup already done; operator can go live again without re-warmup)
        if self._startup_phase == StartupPhase.LIVE:
            self._startup_phase = StartupPhase.PRIMED
        self._state.push_alert(
            "warning",
            "Scalp: Standby",
            "New entries off. Open positions still manage stops/TP until flat.",
            "scalp_operator",
        )
        LOG.info("ScalpRuntime: operator_enter_standby")

    async def operator_flow_standby_ui(self) -> None:
        """Progress bar + modal for Enter standby (when not in a prep run)."""
        if self._prep_session_busy:
            return
        await self._flow_cancel_pulse()
        self._flow_new_prep()
        try:
            await self._flow_fill_step(0)
            self._flow_emit("standby_complete", "Standby engaged — no new entries.")
        finally:
            asyncio.create_task(self._flow_hide_later(8.0), name="scalp_flow_hide_standby")

    async def operator_flow_go_live_ui(self) -> None:
        """Progress bar + primed modal after Go live."""
        if self._prep_session_busy:
            return
        await self._flow_cancel_pulse()
        _steps = [
            {"key": "standby", "label": "STANDBY", "pct": 100.0, "state": "done"},
            {"key": "configuration", "label": "CONFIGURATION", "pct": 100.0, "state": "done"},
            {"key": "go_live", "label": "GO LIVE", "pct": 0.0, "state": "pending"},
        ]
        self._operator_flow = {
            "visible": True,
            "title": "Arming execution",
            "overall_pct": round(self._flow_overall_pct(_steps), 1),
            "steps": _steps,
        }
        try:
            await self._flow_fill_step(2, 100.0)
            if self.warmup_complete and not self._operator_standby:
                self._flow_emit(
                    "execution_primed",
                    "Bot primed, ready for execution — warm-up complete and entries are armed when signals permit.",
                )
            elif not self.warmup_complete:
                self._flow_emit(
                    "go_live_warmup_pending",
                    "Standby cleared. Warm-up or champion gates may still block entries until the engine is READY.",
                )
            else:
                self._flow_emit(
                    "execution_primed",
                    "Entries armed when signals and risk gates permit.",
                )
        finally:
            asyncio.create_task(self._flow_hide_later(9.0), name="scalp_flow_hide_golive")

    async def operator_begin_prep_session(self) -> None:
        """Re-run walk-forward configuration while forcing operator standby.

        Warm-up bar and champion gates still apply after the WFO pass completes.
        """
        if not self._cfg.enabled:
            self._state.push_alert(
                "error",
                "Scalp prep unavailable",
                "Scalp is disabled in config.",
                "scalp_operator",
            )
            return
        if self._prep_session_busy:
            self._state.push_alert(
                "warning",
                "Scalp prep busy",
                "A configuration pass is already running.",
                "scalp_operator",
            )
            return

        self._operator_standby = True
        self._prep_session_busy = True
        await self._flow_cancel_pulse()
        self._flow_new_prep()
        try:
            self._state.push_alert(
                "info",
                "Scalp prep started",
                "Standby ON — no new entries. Re-running warm-up / WFO configuration…",
                "scalp_operator",
            )

            await self._flow_fill_step(0)
            self._flow_emit("standby_complete", "Standby engaged — no new entries.")

            if not self._cfg.warmup_enabled:
                self._warmup_phase = WarmupPhase.DISABLED
                self._startup_wfo_done = False
                self._warmup_wfo_triggered = True
                if len(self._operator_flow["steps"]) > 1:
                    self._operator_flow["steps"][1]["state"] = "running"
                self._operator_flow_pulse_task = asyncio.create_task(
                    self._flow_pulse_configuration(),
                    name="scalp_prep_config_pulse",
                )
                try:
                    if self._cfg.wfo_enabled:
                        try:
                            results = await asyncio.to_thread(self._wfo.run_once)
                            for _pk, r in results.items():
                                if r is not None:
                                    self._warmup_champion_found = True
                        except Exception:
                            LOG.exception("ScalpRuntime: prep WFO failed (warmup disabled)")
                finally:
                    await self._flow_cancel_pulse()
                await self._flow_fill_step(1)
                self._flow_emit(
                    "configuration_complete",
                    "Walk-forward configuration finished (warmup disabled in config).",
                )
                self._startup_wfo_done = True
                await self._flow_fill_step(2)
                self._flow_emit(
                    "prep_complete",
                    "Prep finished — still in STANDBY. Click Go live when ready to arm entries.",
                )
                asyncio.create_task(self._flow_hide_later(10.0), name="scalp_flow_hide_prep_nowarm")
                return

            self._warmup_phase = WarmupPhase.COLLECTING
            self._warmup_start_ts = time.time()
            self._warmup_champion_found = False
            self._startup_wfo_done = False

            for pair_key, pair_cfg in self._cfg.pairs.items():
                buf_n = 0
                if self._feed is not None:
                    buf_n = len(self._feed.get_buffer(pair_key))
                existing = bar_store.bar_count(pair_cfg.symbol, pair_cfg.interval)
                self._warmup_bars_collected[pair_key] = max(buf_n, existing)

            self._warmup_wfo_triggered = True
            if len(self._operator_flow["steps"]) > 1:
                self._operator_flow["steps"][1]["state"] = "running"
            self._operator_flow_pulse_task = asyncio.create_task(
                self._flow_pulse_configuration(),
                name="scalp_prep_config_pulse_w",
            )
            try:
                if self._cfg.wfo_enabled:
                    LOG.info("ScalpRuntime: prep session — running WFO (operator)")
                    try:
                        results = await asyncio.to_thread(self._wfo.run_once)
                        for _pk, r in results.items():
                            if r is not None:
                                self._warmup_champion_found = True
                    except Exception:
                        LOG.exception("ScalpRuntime: prep WFO pass failed")
            finally:
                await self._flow_cancel_pulse()
            await self._flow_fill_step(1)
            self._flow_emit("configuration_complete", "Walk-forward configuration finished.")

            self._startup_wfo_done = True
            became_ready = self._check_warmup_complete()
            if len(self._operator_flow["steps"]) > 2:
                self._operator_flow["steps"][2]["state"] = "running"
            await self._flow_fill_step(2)
            if became_ready:
                self._flow_emit(
                    "prep_complete",
                    "Warm-up gates satisfied. Click Go live to arm execution.",
                )
            else:
                self._flow_emit(
                    "prep_partial",
                    "WFO re-ran; bar count or champion requirement may still block trading until warm-up catches up.",
                )
                self._state.push_alert(
                    "warning",
                    "Scalp prep (partial)",
                    "WFO re-ran. Bar count or champion requirement may still block trading "
                    "until warm-up catches up. Standby stays ON until you click Go live.",
                    "scalp_operator",
                )
            asyncio.create_task(self._flow_hide_later(10.0), name="scalp_flow_hide_prep")
        finally:
            self._prep_session_busy = False
            await self._flow_cancel_pulse()

    # ── Champion reload ───────────────────────────────────────────────────────

    def _try_load_champion(
        self,
        champ_store: dict[str, dict] | None = None,
        *,
        file_mtime: float | None = None,
    ) -> bool:
        """Hot-reload champion map from disk when the file mtime advances.

        ``champ_store`` should be the result of ``load_champion()`` for this mtime
        (caller loads once per tick to avoid TOCTOU). If None and mtime is new,
        loads from disk here.

        Returns True when a non-empty champion map was applied for this mtime.
        """
        try:
            disk_mtime = CHAMPION_PATH.stat().st_mtime if CHAMPION_PATH.exists() else 0.0
        except OSError:
            disk_mtime = 0.0
        mt = float(file_mtime) if file_mtime is not None else disk_mtime
        if mt <= self._champion_mtime:
            return False

        if champ_store is None:
            champ_store = load_champion()
        if not champ_store:
            self._champion_mtime = mt
            return False

        self._champion_mtime = mt
        self._champion_data = champ_store

        if not self._warmup_champion_found:
            self._warmup_champion_found = True
            LOG.info("ScalpRuntime: champion detected — warmup champion flag set")
            self._check_warmup_complete()

        for pair_key, pair_cfg in self._cfg.pairs.items():
            entry = champ_store.get(pair_cfg.symbol)
            if not isinstance(entry, dict):
                continue

            sig = (
                float(entry.get("timestamp") or 0.0),
                str(entry.get("mode", "")),
                float(entry.get("score") or 0.0),
            )
            prev_sig = self._champion_apply_sig.get(pair_key)
            if prev_sig == sig:
                continue

            if prev_sig is not None and self._session_log is not None:
                started = float(self._champion_period_start.get(pair_key, 0.0))
                fwd = self._trader.forward_pnl_since(pair_key, started)
                self._session_log.log_scalp(
                    "champion_period_end",
                    pair_key=pair_key,
                    symbol=pair_cfg.symbol,
                    forward_pnl=round(fwd, 6),
                    previous_mode=str(prev_sig[1]) if len(prev_sig) > 1 else "",
                )

            self._champion_apply_sig[pair_key] = sig
            self._champion_period_start[pair_key] = time.time()

            if self._session_log is not None:
                self._session_log.log_scalp(
                    "champion_period_start",
                    pair_key=pair_key,
                    symbol=pair_cfg.symbol,
                    mode=str(entry.get("mode", "")),
                    score=entry.get("score"),
                    holdout_metrics=entry.get("holdout_metrics"),
                    objective=str(entry.get("objective", "sharpe")),
                    champion_timestamp=entry.get("timestamp"),
                )

            params = entry.get("params", {})
            new_mode = entry.get("mode", params.get("mode", ""))

            if new_mode and new_mode != self._active_mode.get(pair_key):
                old_mode = self._active_mode.get(pair_key, "?")
                self._active_mode[pair_key] = new_mode
                self._mode_source[pair_key] = "wfo_champion"
                LOG.info(
                    "ScalpRuntime %s: mode switch %s -> %s (WFO champion)",
                    pair_key, old_mode, new_mode,
                )
            elif new_mode and new_mode == self._active_mode.get(pair_key):
                self._mode_source[pair_key] = "wfo_champion"

            changed = []
            for attr, key in [
                ("max_hold_bars", "max_hold_bars"),
                ("atr_stop_mult", "atr_stop_mult"),
                ("atr_tp_mult", "atr_tp_mult"),
                ("min_signals", "min_signals"),
                ("ema_fast", "ema_fast"),
                ("ema_slow", "ema_slow"),
                ("rsi_buy_threshold", "rsi_buy_threshold"),
                ("rsi_sell_threshold", "rsi_sell_threshold"),
                ("ema_scalp_period", "ema_scalp_period"),
                ("ema_scalp_sr_bars", "ema_scalp_sr_bars"),
                ("macd_fast_len", "macd_fast_len"),
                ("macd_slow_len", "macd_slow_len"),
                ("macd_signal_len", "macd_signal_len"),
                ("t3_length", "t3_length"),
                ("t3_vfactor", "t3_vfactor"),
                ("hlc_close_period", "hlc_close_period"),
                ("hlc_low_period", "hlc_low_period"),
                ("hlc_high_period", "hlc_high_period"),
                ("adx_period", "adx_period"),
                ("adx_threshold", "adx_threshold"),
                ("wae_sensitivity", "wae_sensitivity"),
                ("wae_fast_len", "wae_fast_len"),
                ("wae_slow_len", "wae_slow_len"),
                ("wae_bb_len", "wae_bb_len"),
                ("wae_bb_mult", "wae_bb_mult"),
            ]:
                if key in params:
                    old_val = getattr(pair_cfg, attr)
                    new_val = type(old_val)(params[key])
                    if new_val != old_val:
                        setattr(pair_cfg, attr, new_val)
                        changed.append(f"{attr}: {old_val} -> {new_val}")
            if changed:
                LOG.info(
                    "ScalpRuntime %s: champion reload — %s",
                    pair_key, ", ".join(changed),
                )

        return True

    def _apply_no_champion_bootstrap(self, champ_store: dict | None = None) -> None:
        """Set active mode from 2h return-% lookback for pairs with no WFO champion row."""
        if not self._cfg.enabled or not self._cfg.pairs:
            return
        champ = champ_store if champ_store is not None else self._champion_data
        if champ is None:
            try:
                champ = load_champion()
            except Exception:
                champ = None
        for pk, pc in self._cfg.pairs.items():
            if pair_has_wfo_champion(champ, pc.symbol):
                continue
            try:
                mode = best_mode_bootstrap_no_champion(
                    pc, self._cfg, lookback_hours=self._effective_bootstrap_hours(),
                )
            except Exception:
                LOG.warning("ScalpRuntime: bootstrap mode failed for %s", pk, exc_info=True)
                continue
            old = self._active_mode.get(pk)
            if mode != old:
                LOG.info(
                    "ScalpRuntime %s: no-champion bootstrap active mode %s -> %s",
                    pk, old, mode,
                )
                self._active_mode[pk] = mode
                self._mode_source[pk] = "bootstrap"
            elif self._mode_source.get(pk) == "config":
                self._mode_source[pk] = "bootstrap"

    def _nemesis_refresh_champion_advisory(self, champ_store: dict | None = None) -> None:
        """Nemesis Step A/B: surface WFO champion vs short-window bootstrap when both exist.

        Does not change ``_active_mode``. Throttled — loads bars per pair with a champion row.
        """
        if not self._cfg.enabled or not self._cfg.pairs:
            return
        now = time.time()
        if now - self._nemesis_advisory_ts < _NEMESIS_ADVISORY_SEC:
            return
        self._nemesis_advisory_ts = now

        champ = champ_store if champ_store is not None else self._champion_data
        if champ is None:
            try:
                champ = load_champion()
            except Exception:
                champ = None
        if not champ:
            self._nemesis_advisory.clear()
            return

        for pk, pc in self._cfg.pairs.items():
            entry = champ.get(pc.symbol)
            if not isinstance(entry, dict):
                self._nemesis_advisory.pop(pk, None)
                continue
            cm = str(entry.get("mode") or entry.get("params", {}).get("mode", "")).strip()
            if not cm:
                self._nemesis_advisory.pop(pk, None)
                continue
            try:
                self._nemesis_advisory[pk] = nemesis_advisory_champion_vs_bootstrap(
                    pc,
                    self._cfg,
                    champion_mode=cm,
                    bootstrap_lookback_hours=self._effective_bootstrap_hours(),
                )
            except Exception:
                LOG.debug("Nemesis advisory failed for %s", pk, exc_info=True)

    def _log_status(self) -> None:
        """Periodic monitor: one line per configured scalp pair (60s tick)."""
        for pair_key in self._cfg.pairs:
            iv = self._latest_iv.get(pair_key)
            mode = self._active_mode.get(pair_key, "?")
            source = self._mode_source.get(pair_key, "?")
            if iv is None:
                LOG.info(
                    "ScalpRuntime MONITOR %s [%s via %s]: waiting for first indicator update | "
                    "open_pos=%d | daily_pnl=%.4f",
                    pair_key,
                    mode,
                    source,
                    self._trader.open_position_count,
                    self._trader.daily_pnl,
                )
                continue
            if iv.ready:
                LOG.info(
                    "ScalpRuntime MONITOR %s [%s via %s]: rsi=%.1f ema=%s vwap=%s vol=%s | "
                    "open_pos=%d | daily_pnl=%.4f",
                    pair_key, mode, source, iv.rsi,
                    "UP" if iv.ema_crossed_up else ("bull" if iv.ema_bullish else "bear"),
                    iv.vwap_bullish,
                    iv.volume_confirmed,
                    self._trader.open_position_count,
                    self._trader.daily_pnl,
                )
            else:
                LOG.info(
                    "ScalpRuntime MONITOR %s [%s via %s]: warming up (candles=%d) | "
                    "open_pos=%d | daily_pnl=%.4f",
                    pair_key,
                    mode,
                    source,
                    self._indicators[pair_key].candle_count,
                    self._trader.open_position_count,
                    self._trader.daily_pnl,
                )

    @staticmethod
    def _intx_coerce_amount(val: object) -> float:
        """Normalize Coinbase INTX numeric fields (often JSON strings or Amount models)."""
        if val is None or val == "":
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            s = val.strip().replace(",", "")
            if not s:
                return 0.0
            try:
                return float(s)
            except (TypeError, ValueError):
                return 0.0
        if isinstance(val, dict):
            try:
                return float(val.get("value") or val.get("amount") or 0)
            except (TypeError, ValueError):
                return 0.0
        td = getattr(val, "to_dict", None)
        if callable(td):
            try:
                d = td()
                if isinstance(d, dict):
                    return ScalpRuntime._intx_coerce_amount(
                        d.get("value") if d.get("value") is not None else d.get("amount"),
                    )
            except (TypeError, ValueError):
                return 0.0
        inner = getattr(val, "value", None)
        if inner is not None and inner is not val:
            return ScalpRuntime._intx_coerce_amount(inner)
        ud = getattr(val, "__dict__", None)
        if isinstance(ud, dict):
            for k in ("value", "amount"):
                if k in ud:
                    try:
                        return ScalpRuntime._intx_coerce_amount(ud[k])
                    except (TypeError, ValueError):
                        pass
        return 0.0

    async def apply_intx_position_reconciliation(self, position_rows: list[dict]) -> None:
        """Merge Coinbase INTX ``list_perps_positions`` into in-memory scalp legs.

        Called from ``CoinbaseOrderManager.reconcile_scalp_intx_positions`` on a timer
        and optionally once at startup. Uses latest ATR when ready; otherwise a small
        %-of-entry fallback for stop/TP hints (protective orders are re-placed on adopt).
        """
        if str(getattr(self._cfg, "venue", "") or "").strip().lower() != "coinbase_perps":
            return
        # Sync open legs into trader state whenever the exchange reports size (including
        # manual opens and SCALP OFF). Only place/repair protective orders when enabled
        # and not sim — otherwise the UI still shows exchange truth without spamming orders.
        place_protectives = bool(self._cfg.enabled) and not bool(self._trader.sim_mode)

        sym_to_key: dict[str, str] = {}
        sym_upper_to_key: dict[str, str] = {}
        for key, pc in self._cfg.pairs.items():
            s = str(getattr(pc, "symbol", "") or "").strip()
            if s:
                sym_to_key[s] = key
                sym_upper_to_key[s.upper()] = key

        # Track which pair_keys the exchange reports as having a non-zero position.
        # Any pair with an internal "open" position that is absent from this set has
        # been closed on the exchange without the bot receiving the fill event.
        exchange_open_pairs: set[str] = set()
        unmapped: list[dict] = []

        for d in position_rows:
            pid = str(
                d.get("product_id")
                or d.get("productId")
                or d.get("symbol")
                or "",
            ).strip()
            if not pid:
                continue
            pair_key = sym_to_key.get(pid) or sym_upper_to_key.get(pid.upper())
            if not pair_key:
                net_hint = self._intx_coerce_amount(
                    d.get("net_size")
                    or d.get("netSize")
                    or d.get("size")
                    or d.get("quantity")
                    or 0,
                )
                unmapped.append({"product_id": pid, "net_size_hint": round(net_hint, 8)})
                continue
            pc = self._cfg.pairs[pair_key]

            net = self._intx_coerce_amount(
                d.get("net_size")
                or d.get("netSize")
                or d.get("size")
                or d.get("quantity")
                or d.get("contracts"),
            )
            if abs(net) < 1e-12:
                ps = str(d.get("position_side") or d.get("positionSide") or "").strip().upper()
                abs_sz = self._intx_coerce_amount(
                    d.get("aggregated_quantity")
                    or d.get("aggregatedQuantity")
                    or d.get("number_of_contracts")
                    or d.get("numberOfContracts"),
                )
                if ps in ("LONG", "SHORT") and abs_sz > 0:
                    net = abs_sz if ps == "LONG" else -abs_sz
            if abs(net) < 1e-12:
                # Exchange reports flat. If we have an open position internally, that
                # means the stop or TP filled and we missed the fill event — close it.
                ghost = self._trader.get_position(pair_key)
                if ghost is not None and ghost.status == "open":
                    mark = self._intx_coerce_amount(
                        d.get("mark_price") or d.get("markPrice") or 0
                    ) or ghost.entry_price
                    mult = ghost.contract_size if str(getattr(self._cfg, "venue", "") or "").strip().lower() == "coinbase_perps" else 1.0
                    if ghost.direction == "long":
                        pnl = (mark - ghost.entry_price) * ghost.qty * mult
                    else:
                        pnl = (ghost.entry_price - mark) * ghost.qty * mult
                    LOG.warning(
                        "ScalpRuntime %s: GHOST POSITION detected — exchange shows flat, "
                        "internal shows open. Closing at mark=%.5f pnl≈%.4f",
                        pair_key, mark, pnl,
                    )
                    self._trader._close_position(ghost, pnl, "exchange_reconcile", mark)
                    self._state.push_alert(
                        "warning",
                        f"Ghost position closed: {pair_key}",
                        f"Exchange reported flat while bot had open position. Closed at mark={mark:.5f}",
                        "scalp_reconcile",
                    )
                continue

            exchange_open_pairs.add(pair_key)

            direction = "long" if net > 0 else "short"
            contracts = abs(float(net))

            entry = self._intx_coerce_amount(
                d.get("entry_vwap") or d.get("entryVwap") or d.get("vwap") or d.get("entry_price"),
            )
            if entry <= 0:
                entry = self._intx_coerce_amount(d.get("vwap"))
            mark = self._intx_coerce_amount(
                d.get("mark_price")
                or d.get("markPrice")
                or d.get("current_price")
                or d.get("currentPrice")
                or 0,
            )
            if mark <= 0:
                mark = entry
            if entry <= 0:
                entry = mark
            liq = self._intx_coerce_amount(
                d.get("liquidation_price") or d.get("liquidationPrice") or 0,
            )

            iv = self._latest_iv.get(pair_key)
            atr = float(iv.atr) if iv is not None and iv.ready and iv.atr > 0 else 0.0
            if atr > 0:
                stop_dist = atr * float(pc.atr_stop_mult)
                tp_dist = atr * float(pc.atr_tp_mult)
            else:
                # No primed ATR yet (e.g. right after startup) — %-of-entry fallback for protective hints
                fb = 0.01
                stop_dist = entry * fb
                tp_dist = entry * fb * float(pc.atr_tp_mult) / max(float(pc.atr_stop_mult), 1e-9)

            if direction == "long":
                stop_price = entry - stop_dist
                tp_price = entry + tp_dist
            else:
                stop_price = entry + stop_dist
                tp_price = entry - tp_dist

            await self._trader.adopt_intx_position_from_exchange(
                pair_key,
                pc,
                product_id=pid,
                direction=direction,
                contracts=contracts,
                entry_price=entry,
                mark_price=mark,
                liquidation_price=liq,
                stop_price=stop_price,
                tp_price=tp_price,
                place_protectives=place_protectives,
            )

        self._intx_unmapped_positions = unmapped[:24]

        if place_protectives:
            for pk in exchange_open_pairs:
                pos = self._trader.get_position(pk)
                if pos is not None and pos.status == "open":
                    await self._trader.ensure_coinbase_protectives_match_exchange(pk)

        for pk, pc in self._cfg.pairs.items():
            pos = self._trader.get_position(pk)
            if pos is None or pos.status != "open":
                continue
            if pk in exchange_open_pairs:
                continue
            sym = str(getattr(pc, "symbol", "") or "").strip()
            LOG.warning(
                "ScalpRuntime %s: internal OPEN but no non-zero INTX row matched %s — "
                "verify config symbol matches Coinbase product id",
                pk, sym,
            )
            now = time.time()
            last = float(self._intx_reconcile_gap_alert_at.get(pk, 0.0))
            if now - last >= 300.0:
                self._intx_reconcile_gap_alert_at[pk] = now
                self._state.push_alert(
                    "warning",
                    f"INTX reconcile gap: {pk}",
                    f"Bot shows an open leg but the exchange snapshot had no matching position "
                    f"for {sym}. Check that [scalp.pairs.{pk}] symbol matches Advanced Trade.",
                    "scalp_reconcile",
                )

    # ── Fill routing — call these from LiveOrderManager.on_message ────────────

    async def on_entry_fill_authoritative(
        self,
        pair_key: str,
        fill_price: float,
        fill_qty: float,
    ) -> None:
        """Apply entry from exchange order snapshot (average fill / filled size) — not fill-stream partials."""
        pos = self._trader.get_position(pair_key)
        if pos is None or pos.status != "pending":
            return
        pos.entry_fill_cost = 0.0
        pos.entry_fill_qty = 0.0
        await self._trader.on_entry_filled(pair_key, fill_price, fill_qty)

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
            LOG.warning(
                "ScalpRuntime on_fill: no position for pair=%s id=%s (orphan/late fill?)",
                pair_key, cl_ord_id[:28],
            )
            return

        if cl_ord_id == pos.entry_cl_ord_id:
            if pos.status != "pending":
                return
            pos.entry_fill_cost += float(fill_price) * float(fill_qty)
            pos.entry_fill_qty += float(fill_qty)
            if pos.entry_fill_qty > 0:
                pos.entry_price = pos.entry_fill_cost / pos.entry_fill_qty
            target = float(pos.qty)
            if pos.entry_fill_qty + 1e-9 < target:
                LOG.info(
                    "ScalpRuntime: partial entry fill pair=%s cum_qty=%.6f / %.6f @ vwap=%.6f",
                    pair_key, pos.entry_fill_qty, target, pos.entry_price,
                )
                return
            vwap = pos.entry_fill_cost / max(pos.entry_fill_qty, 1e-12)
            await self._trader.on_entry_filled(pair_key, vwap, pos.entry_fill_qty)
        elif cl_ord_id in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
            await self._trader.on_exit_filled(pair_key, cl_ord_id, fill_price)
        elif cl_ord_id.startswith(("scalp_tstop_", "scalp_rsi_")):
            # Time/RSI exit uses a separate market client id; position may already be closed
            # synchronously in check_* — log if we still see an open leg.
            if pos.status == "open":
                LOG.info(
                    "ScalpRuntime on_fill: market exit fill pair=%s id=%s @ %.8f",
                    pair_key, cl_ord_id[:24], fill_price,
                )
            else:
                LOG.debug(
                    "ScalpRuntime on_fill: ignoring post-close market fill pair=%s id=%s",
                    pair_key, cl_ord_id[:24],
                )
        elif cl_ord_id.startswith("scalp_"):
            LOG.debug(
                "ScalpRuntime on_fill: ignored scalp id=%s pair=%s status=%s",
                cl_ord_id[:28], pair_key, pos.status,
            )
        else:
            LOG.warning(
                "ScalpRuntime on_fill: unhandled id=%s pair=%s status=%s",
                cl_ord_id[:28], pair_key, pos.status,
            )
