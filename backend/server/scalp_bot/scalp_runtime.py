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
import datetime
import enum
import logging
import math
import re
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from . import bar_store
from .candle_feed import Candle, start_candle_feed
from .indicators import IndicatorSet, IndicatorValues
from .scalp_config import ScalpBotConfig, ScalpPairConfig, wfo_fee_bps_per_leg
from .scalp_trader import ScalpTrader
from .scalp_wfo import (
    CHAMPION_PATH,
    ScalpWalkForwardOptimizer,
    WFOConfig,
    load_champion,
    remove_champion_for_symbol,
    wfo_roll_span_hours,
    wfo_verify_stored_roll_coverage,
)
from .strategy_lookback import (
    NO_CHAMPION_BOOTSTRAP_HOURS,
    build_strategy_lookback_snapshot,
    best_mode_bootstrap_no_champion,
    champion_row_matches_pair_interval,
    nemesis_advisory_champion_vs_bootstrap,
    nemesis_resolve_bootstrap_vs_tuner,
    pair_has_wfo_champion,
)
from .param_tuner import (
    STRATEGY_MODES,
    champion_tuner_mode_resolution,
    run_tuner_cycle,
    apply_tuner_result,
    save_tuner_state,
    load_tuner_state,
    TunerResult,
)
from .scalp_mode_resolution import normalize_auto_mode_fallback, resolve_auto_mode
from .scalp_parity_fingerprint import build_scalp_parity_fingerprint, per_pair_parity_row
from .signal_engine import SignalEngine

if TYPE_CHECKING:
    from ..coinbase_order_manager import CoinbaseOrderManager
    from ..live_order_manager import LiveOrderManager
    from ..session_logger import SessionLogger
    from ..state import BotState

LOG = logging.getLogger(__name__)

_STRATEGY_LOOKBACK_REFRESH_SEC = 60.0


def _scalar_coinbase_fee_rate_to_bps(raw: object) -> float | None:
    """Convert Coinbase ``fee_tier`` maker/taker rate to basis points per leg.

    Advanced Trade returns a decimal fraction of notional (e.g. ``0.00065`` → 6.5 bps).
    Values already in the 0–500 range are treated as bps for safety.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        raw = s
    try:
        r = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(r) or r < 0:
        return None
    if r <= 0.2:
        bps = r * 10_000.0
    elif r <= 500.0:
        bps = r
    else:
        return None
    if not math.isfinite(bps) or bps < 0 or bps > 500.0:
        return None
    return float(round(bps, 6))


def _parse_coinbase_summary_fee_bps(data: dict) -> tuple[float, float] | None:
    ft = data.get("fee_tier")
    if not isinstance(ft, dict):
        return None
    mk = _scalar_coinbase_fee_rate_to_bps(ft.get("maker_fee_rate"))
    tk = _scalar_coinbase_fee_rate_to_bps(ft.get("taker_fee_rate"))
    if mk is None or tk is None:
        return None
    return (mk, tk)


def _wfo_config_from_scalp_cfg(cfg: ScalpBotConfig) -> WFOConfig:
    """Build ``WFOConfig`` from live bot config (dashboard patches keep this in sync)."""
    wfo = WFOConfig(
        enabled=cfg.wfo_enabled,
        interval_sec=cfg.wfo_interval_sec,
        train_hours=cfg.wfo_train_hours,
        holdout_hours=cfg.wfo_holdout_hours,
        step_hours=cfg.wfo_step_hours,
        min_trades=cfg.wfo_min_trades,
        min_holdout_trades=(
            int(cfg.wfo_min_holdout_trades)
            if int(getattr(cfg, "wfo_min_holdout_trades", 0) or 0) > 0
            else None
        ),
        top_k=max(1, int(cfg.wfo_top_k)),
        objective=cfg.wfo_objective,
        min_mean_score=cfg.wfo_min_mean_score,
        min_stability_ratio=cfg.wfo_min_stability_ratio,
        require_positive_latest_holdout=cfg.wfo_require_positive_holdout,
        min_latest_holdout_pf=cfg.wfo_min_holdout_pf,
        max_avg_dd_pct=cfg.wfo_max_avg_dd_pct,
        max_roll_windows=max(1, int(cfg.wfo_max_roll_windows)),
        train_same_calendar_day_boost=float(cfg.wfo_train_same_calendar_day_boost),
        min_window_fraction=float(getattr(cfg, "wfo_min_window_fraction", 0.48) or 0.48),
        min_profit_factor=float(getattr(cfg, "wfo_min_profit_factor", 0.8) or 0.8),
        min_win_rate=float(getattr(cfg, "wfo_min_win_rate", 0.20) or 0.20),
        max_drawdown_pct=float(getattr(cfg, "wfo_max_train_drawdown_pct", 30.0) or 30.0),
        champion_cooldown_sec=float(getattr(cfg, "wfo_champion_cooldown_sec", 0.0) or 0.0),
        require_holdout_beat_prior=bool(getattr(cfg, "wfo_require_holdout_beat_prior", False)),
        prior_beat_epsilon=float(getattr(cfg, "wfo_prior_beat_epsilon", 1e-6) or 1e-6),
        max_param_delta_stop=float(getattr(cfg, "wfo_max_param_delta_stop", 1.0) or 1.0),
        max_param_delta_tp=float(getattr(cfg, "wfo_max_param_delta_tp", 1.5) or 1.5),
        allow_promotion_relaxation=bool(getattr(cfg, "wfo_allow_promotion_relaxation", False)),
        holdout_tiebreakers=(
            tuple(
                str(x).strip()
                for x in (getattr(cfg, "wfo_holdout_tiebreakers", ()) or ())
                if str(x).strip()
            )
            or ("stability", "neg_mean_max_dd_pct", "min_holdout_trade_count")
        ),
        holdout_score_epsilon=float(getattr(cfg, "wfo_holdout_score_epsilon", 0.0) or 0.0),
    )
    if bool(getattr(cfg, "wfo_pnl_first_promotion", False)):
        # Maximize simulated USD on rolling holdouts; drop auxiliary gates that often block promotion.
        wfo = dataclasses.replace(
            wfo,
            objective="total_pnl",
            require_positive_latest_holdout=False,
            min_latest_holdout_pf=0.0,
            min_mean_score=-999.0,
            min_stability_ratio=-999.0,
            max_avg_dd_pct=999.0,
            require_holdout_beat_prior=False,
            max_param_delta_hold=10_000,
            max_param_delta_stop=1_000.0,
            max_param_delta_tp=1_000.0,
        )
    return wfo


def _apply_vol_armed_wfo_overlay(base: WFOConfig, cfg: ScalpBotConfig) -> WFOConfig:
    """Tighten a one-pass WFO config while the volatility filter is armed (copy only)."""
    if bool(getattr(cfg, "wfo_pnl_first_promotion", False)):
        return base
    wf = float(getattr(cfg, "wfo_vol_armed_min_window_fraction", 0.0) or 0.0)
    pf = float(getattr(cfg, "wfo_vol_armed_min_latest_holdout_pf", 0.0) or 0.0)
    disallow = bool(getattr(cfg, "wfo_vol_armed_disallow_promotion_relaxation", True))
    if not disallow and wf <= 0 and pf <= 0:
        return base
    kw: dict[str, Any] = {}
    if disallow:
        kw["allow_promotion_relaxation"] = False
    if wf > 0:
        kw["min_window_fraction"] = max(float(base.min_window_fraction), wf)
    if pf > 0:
        kw["min_latest_holdout_pf"] = max(float(base.min_latest_holdout_pf), pf)
    return dataclasses.replace(base, **kw)


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
    # Incremented on each server push while this step runs (proves UI loop is alive).
    heartbeat: int = 0


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
        self._trader._daily_loss_breach_fn = self._on_daily_loss_breach
        self._trader._slip_observation_cb = self._note_slip_calibration_sample

        # Indicator sets per pair
        use_numpy = getattr(cfg, "use_numpy_indicators", False)
        self._indicators: dict[str, IndicatorSet] = {
            key: IndicatorSet(pair_cfg, use_numpy=use_numpy)
            for key, pair_cfg in cfg.pairs.items()
        }
        # Latest indicator values per pair
        self._latest_iv: dict[str, IndicatorValues] = {}
        # Per-candle indicator overlay history (for chart lines): {pair_key: deque of dicts}
        self._indicator_overlay: dict[str, deque] = {
            key: deque(maxlen=500) for key in cfg.pairs
        }
        # Ensure bar_store knows the venue before we read from it
        bar_store.set_bar_store_venue(getattr(cfg, "venue", "coinbase_perps"))

        # Active strategy mode per pair — resolved ``auto`` → fallback until champion/bootstrap.
        self._active_mode: dict[str, str] = {
            k: resolve_auto_mode(
                cfg.pairs[k].strategy_mode,
                champion_row=None,
                auto_mode_fallback=(
                    getattr(cfg.pairs[k], "auto_mode_fallback", None)
                    or getattr(cfg, "auto_mode_fallback", "sar_chop")
                ),
            )
            for k in cfg.pairs
        }
        self._mode_source: dict[str, str] = {
            k: "config" for k in cfg.pairs
        }
        # WFO no_candidates streak tracking — counts consecutive passes per pair with no champion.
        # When streak hits wfo_no_candidates_demotion_passes, active wfo_champion is demoted to bootstrap.
        self._wfo_no_candidates_streak: dict[str, int] = {}
        # Symbols demoted by the staleness gate — _try_load_champion skips re-applying the old
        # champion file entry until a fresh WFO promotion clears the symbol from this set.
        self._wfo_staleness_demoted: set[str] = set()
        # NM-013: mode locked to the mode active when a position was opened.
        # Prevents WFO champion switch from changing exit logic mid-trade.
        self._pair_entry_mode: dict[str, str] = {}
        # NM-014: entry pending guard — prevents dual bar+tick race when tick entries re-enabled.
        self._entry_pending: set[str] = set()

        # Regime risk-on (volume / vol-scaled moves) — shortens WFO sleep, bootstrap window, Nemesis gates
        self._regime_risk_on_until: float = 0.0
        self._regime_pair_reasons: dict[str, list[str]] = {}
        # Live velocity: (unix_ts, price) for regime_live_velocity_* (trimmed per tick)
        self._regime_live_prices: dict[str, deque[tuple[float, float]]] = {}
        self._regime_live_log_at: dict[str, float] = {}
        # Last tick candle + velocity for live stress checks / calm relaxation
        self._regime_tick_candle: dict[str, Candle] = {}
        self._regime_last_vel_bps: dict[str, float] = {}
        self._regime_calm_since: float | None = None
        self._regime_relax_log_at: float = 0.0

        # Volatility filter (execution sizing) — prime + next-bar confirm; see volatility_filter.py
        self._vol_filt_pending: dict[str, Candle] = {}
        self._vol_filt_armed_until: dict[str, float] = {}
        self._vol_filt_last_event: dict[str, str] = {}

        # Walk-forward optimizer
        wfo_cfg = _wfo_config_from_scalp_cfg(cfg)
        # Live entry slip EMA (optional) — see ``effective_slippage_bps_for_sim``.
        self._slip_calib_ema: float | None = None
        self._slip_calib_samples: int = 0
        self._wfo = ScalpWalkForwardOptimizer(
            cfg,
            wfo_cfg,
            session_logger=session_logger,
            interval_sec_resolver=lambda: float(self._effective_wfo_sleep_sec()),
            wfo_pass_cfg_resolver=lambda: self._wfo_pass_config(),
            slippage_bps_resolver=lambda: float(self.effective_slippage_bps_for_sim()),
            results_callback=self._on_wfo_loop_results,
        )
        self._champion_mtime: float = -1.0
        self._champion_data: dict[str, dict] | None = None
        self._champion_period_start: dict[str, float] = {}
        self._champion_apply_sig: dict[str, tuple] = {}
        # Pending champion: queued when promotion fires while a position is open.
        # Applied on the next bar-close where the pair is flat.
        self._pending_champion: dict[str, dict] = {}  # pair_key -> champion entry dict

        # Throttle WS tick → dashboard snapshot (ticker updates live OHLC between 0.5s push_loop ticks).
        self._last_tick_snapshot_bump: float = 0.0

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
        # True after startup WFO finishes without exception (including 0 champions → bootstrap).
        # Used with warmup_require_champion to avoid deadlock when the grid finds no passing mode.
        self._startup_wfo_succeeded: bool = not cfg.warmup_enabled

        self._strategy_lookback_snapshot: dict | None = None
        self._strategy_lookback_ts: float = 0.0

        # Self-tuner state
        self._tuner_results: dict[str, TunerResult] = {}
        self._tuner_last_run: float = 0.0
        self._tuner_bars_since_run: dict[str, int] = {k: 0 for k in cfg.pairs}
        self._tuner_apply_cooldown_until: dict[str, float] = {k: 0.0 for k in cfg.pairs}
        self._tuner_interval_warned: bool = False
        self._tuner_snapshot: dict | None = load_tuner_state()
        # Fee tier / 30d volume (Coinbase transaction_summary + optional manual baseline)
        self._fee_tier_exchange_data: dict | None = None
        self._fee_tier_last_poll_ts: float = 0.0
        self._fee_tier_poll_error: str = ""
        self._fee_tier_bot_fill_usd: float = 0.0
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
            if now - last >= 60.0:
                self._regime_live_log_at[pair_key] = now
                LOG.info(
                    "ScalpRuntime %s: regime risk-on LIVE reasons=%s until_ts=%.0f",
                    pair_key, reasons, self._regime_risk_on_until,
                )

    def _touch_regime_risk_on(self, pair_key: str, iv: IndicatorValues) -> None:
        from .regime_risk import regime_risk_on_triggers

        reasons = regime_risk_on_triggers(iv, self._cfg)
        self._apply_regime_risk_on(pair_key, reasons)

    def _regime_any_pair_stressed(self) -> bool:
        """True if any configured pair still satisfies bar or live regime triggers."""
        from .regime_risk import regime_risk_on_triggers, regime_risk_on_triggers_live

        cfg = self._cfg
        live_on = bool(getattr(cfg, "regime_live_vol_enabled", True))
        for pk in cfg.pairs:
            iv = self._latest_iv.get(pk)
            if iv is None:
                return True
            if regime_risk_on_triggers(iv, cfg):
                return True
            if live_on:
                c = self._regime_tick_candle.get(pk)
                if c is None:
                    continue
                vel = float(self._regime_last_vel_bps.get(pk, 0.0))
                if regime_risk_on_triggers_live(iv, c, cfg, live_velocity_bps=vel):
                    return True
        return False

    def _update_regime_risk_on_calm_relax(self) -> None:
        """End risk-on early once all pairs are calm long enough (volume/move triggers clear)."""
        if not bool(getattr(self._cfg, "regime_risk_on_enabled", True)):
            self._regime_calm_since = None
            return
        relax_sec = float(getattr(self._cfg, "risk_on_relax_after_calm_sec", 0.0))
        if relax_sec <= 0.0:
            return
        if not self._regime_risk_on_global():
            self._regime_calm_since = None
            return
        now = time.time()
        if self._regime_any_pair_stressed():
            self._regime_calm_since = None
            return
        if self._regime_calm_since is None:
            self._regime_calm_since = now
            return
        if now - self._regime_calm_since < relax_sec:
            return
        self._regime_risk_on_until = now
        self._regime_calm_since = None
        self._regime_pair_reasons.clear()
        if now - self._regime_relax_log_at >= 30.0:
            self._regime_relax_log_at = now
            LOG.info(
                "ScalpRuntime: regime risk-on RELAX after %.0fs calm (all pairs below triggers)",
                relax_sec,
            )

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
        floor_sec = max(60.0, float(getattr(self._cfg, "risk_on_wfo_min_interval_sec", 60.0)))
        scale = float(getattr(self._cfg, "risk_on_wfo_interval_scale", 0.25))
        eff = float(self._cfg.wfo_interval_sec) * scale
        frac = float(getattr(self._cfg, "risk_on_wfo_min_base_interval_frac", 0.5) or 0.0)
        min_from_base = base * frac if frac > 0 else 0.0
        return max(floor_sec, max(60.0, eff), min_from_base)

    def _wfo_pass_config(self) -> WFOConfig:
        """WFOConfig for a single optimization pass (vol-armed overlay when any pair is armed)."""
        wfo = _wfo_config_from_scalp_cfg(self._cfg)
        if not any(self._volatility_filter_armed(pk) for pk in self._cfg.pairs):
            return wfo
        return _apply_vol_armed_wfo_overlay(wfo, self._cfg)

    def apply_session_policy_runtime_patch(self, patch: dict[str, Any]) -> tuple[bool, str]:
        """Apply in-memory WFO / tuner / window fields from the dashboard.

        Does not write ``config.toml`` — restart reloads file values.
        """
        if not isinstance(patch, dict):
            return False, "patch must be an object"
        allowed = {
            "wfo_interval_sec",
            "param_tuner_interval_sec",
            "param_tuner_min_bars_between_runs",
            "param_tuner_cooldown_sec_after_apply",
            "param_tuner_warn_interval_below_bar_mult",
            "wfo_holdout_score_epsilon",
            "wfo_max_roll_windows",
            "wfo_top_k",
            "wfo_train_same_calendar_day_boost",
            "wfo_train_hours",
            "wfo_holdout_hours",
            "wfo_step_hours",
            "wfo_min_trades",
            "wfo_min_holdout_trades",
            "backtest_funding_enabled",
            "backtest_funding_bps_per_hour",
            "scalp_fee_assumption_revision",
            "fee_tier_30d_volume_usd",
            "scalp_auto_invalidate_champion_on_fee_change",
            "param_tuner_require_wfo_champion",
            "param_tuner_allow_mode_override_champion",
            "fee_tier_volume_source",
            "fee_tier_poll_interval_sec",
            "fee_tier_add_bot_fill_notional",
            "fee_tier_auto_apply_exchange_fee_rates",
            "empirical_market_promotion_enabled",
            "empirical_market_missed_move_bps",
            "empirical_market_miss_eval_window_sec",
            "empirical_market_min_pattern_in_window",
            "empirical_market_pattern_window_sec",
            "empirical_market_promotion_entries",
            "empirical_market_promotion_cooldown_sec",
            "empirical_market_ttl_cancel_arms_promotion",
            "empirical_market_ttl_cancel_promotion_entries",
            "wfo_forward_min_trades",
            "wfo_forward_demotion_threshold",
            "wfo_assume_taker_fee",
            "funding_warn_bps_per_hour",
            "daily_loss_set_scalp_halt",
            "wfo_pnl_first_promotion",
        }
        incoming = {k: v for k, v in patch.items() if k in allowed}
        if not incoming:
            return False, "no supported fields (see Settings → WFO / tuner)"

        def _f(name: str, lo: float, hi: float) -> float:
            raw = incoming[name]
            x = float(raw)
            if not (lo <= x <= hi):
                raise ValueError(f"{name} must be between {lo} and {hi} (got {x})")
            return x

        def _i(name: str, lo: int, hi: int) -> int:
            raw = incoming[name]
            x = int(raw)
            if not (lo <= x <= hi):
                raise ValueError(f"{name} must be between {lo} and {hi} (got {x})")
            return x

        def _bool(name: str) -> bool:
            raw = incoming[name]
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int, float)) and raw in (0, 1):
                return bool(int(raw))
            s = str(raw).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off", ""):
                return False
            raise ValueError(f"{name} must be a boolean (got {raw!r})")

        try:
            if "wfo_interval_sec" in incoming:
                self._cfg.wfo_interval_sec = _f("wfo_interval_sec", 60.0, 604_800.0)
            if "param_tuner_interval_sec" in incoming:
                self._cfg.param_tuner_interval_sec = _f("param_tuner_interval_sec", 30.0, 7200.0)
                self._tuner_interval_warned = False
            if "param_tuner_min_bars_between_runs" in incoming:
                self._cfg.param_tuner_min_bars_between_runs = _i(
                    "param_tuner_min_bars_between_runs", 0, 10_000,
                )
            if "param_tuner_cooldown_sec_after_apply" in incoming:
                self._cfg.param_tuner_cooldown_sec_after_apply = _f(
                    "param_tuner_cooldown_sec_after_apply", 0.0, 86_400.0,
                )
            if "param_tuner_warn_interval_below_bar_mult" in incoming:
                self._cfg.param_tuner_warn_interval_below_bar_mult = _f(
                    "param_tuner_warn_interval_below_bar_mult", 0.0, 1_000.0,
                )
                self._tuner_interval_warned = False
            if "wfo_holdout_score_epsilon" in incoming:
                self._cfg.wfo_holdout_score_epsilon = _f(
                    "wfo_holdout_score_epsilon", 0.0, 1_000.0,
                )
            if "wfo_max_roll_windows" in incoming:
                self._cfg.wfo_max_roll_windows = _i("wfo_max_roll_windows", 1, 200)
            if "wfo_top_k" in incoming:
                self._cfg.wfo_top_k = _i("wfo_top_k", 1, 300)
            if "wfo_train_same_calendar_day_boost" in incoming:
                self._cfg.wfo_train_same_calendar_day_boost = _f(
                    "wfo_train_same_calendar_day_boost", 0.0, 3.0,
                )
            if "wfo_train_hours" in incoming:
                self._cfg.wfo_train_hours = _f("wfo_train_hours", 0.5, 2000.0)
            if "wfo_holdout_hours" in incoming:
                self._cfg.wfo_holdout_hours = _f("wfo_holdout_hours", 0.5, 500.0)
            if "wfo_step_hours" in incoming:
                self._cfg.wfo_step_hours = _f("wfo_step_hours", 0.25, 10000.0)
            if "wfo_min_trades" in incoming:
                self._cfg.wfo_min_trades = _i("wfo_min_trades", 1, 500)
            if "wfo_min_holdout_trades" in incoming:
                self._cfg.wfo_min_holdout_trades = _i("wfo_min_holdout_trades", 0, 500)
            if "backtest_funding_enabled" in incoming:
                self._cfg.backtest_funding_enabled = _bool("backtest_funding_enabled")
            if "backtest_funding_bps_per_hour" in incoming:
                self._cfg.backtest_funding_bps_per_hour = _f(
                    "backtest_funding_bps_per_hour", -200.0, 200.0,
                )
            if "scalp_fee_assumption_revision" in incoming:
                self._cfg.scalp_fee_assumption_revision = _i(
                    "scalp_fee_assumption_revision", 0, 9_999_999,
                )
            if "fee_tier_30d_volume_usd" in incoming:
                raw_v = incoming["fee_tier_30d_volume_usd"]
                if raw_v is None or raw_v == "":
                    self._cfg.fee_tier_30d_volume_usd = None
                else:
                    xv = float(raw_v)
                    if xv < 0:
                        raise ValueError("fee_tier_30d_volume_usd must be >= 0")
                    self._cfg.fee_tier_30d_volume_usd = xv
            if "scalp_auto_invalidate_champion_on_fee_change" in incoming:
                self._cfg.scalp_auto_invalidate_champion_on_fee_change = _bool(
                    "scalp_auto_invalidate_champion_on_fee_change",
                )
            if "param_tuner_require_wfo_champion" in incoming:
                self._cfg.param_tuner_require_wfo_champion = _bool(
                    "param_tuner_require_wfo_champion",
                )
            if "param_tuner_allow_mode_override_champion" in incoming:
                self._cfg.param_tuner_allow_mode_override_champion = _bool(
                    "param_tuner_allow_mode_override_champion",
                )
            if "fee_tier_volume_source" in incoming:
                raw_s = str(incoming["fee_tier_volume_source"]).strip().lower()
                if raw_s not in ("exchange", "manual"):
                    raise ValueError("fee_tier_volume_source must be 'exchange' or 'manual'")
                self._cfg.fee_tier_volume_source = raw_s
            if "fee_tier_poll_interval_sec" in incoming:
                self._cfg.fee_tier_poll_interval_sec = _f(
                    "fee_tier_poll_interval_sec", 60.0, 86_400.0,
                )
            if "fee_tier_add_bot_fill_notional" in incoming:
                self._cfg.fee_tier_add_bot_fill_notional = _bool("fee_tier_add_bot_fill_notional")
            if "fee_tier_auto_apply_exchange_fee_rates" in incoming:
                self._cfg.fee_tier_auto_apply_exchange_fee_rates = _bool(
                    "fee_tier_auto_apply_exchange_fee_rates",
                )
            if "empirical_market_promotion_enabled" in incoming:
                self._cfg.empirical_market_promotion_enabled = _bool(
                    "empirical_market_promotion_enabled",
                )
            if "empirical_market_missed_move_bps" in incoming:
                self._cfg.empirical_market_missed_move_bps = _f(
                    "empirical_market_missed_move_bps", 0.0, 500.0,
                )
            if "empirical_market_miss_eval_window_sec" in incoming:
                self._cfg.empirical_market_miss_eval_window_sec = _f(
                    "empirical_market_miss_eval_window_sec", 30.0, 172_800.0,
                )
            if "empirical_market_min_pattern_in_window" in incoming:
                self._cfg.empirical_market_min_pattern_in_window = _i(
                    "empirical_market_min_pattern_in_window", 1, 100,
                )
            if "empirical_market_pattern_window_sec" in incoming:
                self._cfg.empirical_market_pattern_window_sec = _f(
                    "empirical_market_pattern_window_sec", 300.0, 30 * 86_400.0,
                )
            if "empirical_market_promotion_entries" in incoming:
                self._cfg.empirical_market_promotion_entries = _i(
                    "empirical_market_promotion_entries", 1, 50,
                )
            if "empirical_market_promotion_cooldown_sec" in incoming:
                self._cfg.empirical_market_promotion_cooldown_sec = _f(
                    "empirical_market_promotion_cooldown_sec", 0.0, 30 * 86_400.0,
                )
            if "empirical_market_ttl_cancel_arms_promotion" in incoming:
                self._cfg.empirical_market_ttl_cancel_arms_promotion = _bool(
                    "empirical_market_ttl_cancel_arms_promotion",
                )
            if "empirical_market_ttl_cancel_promotion_entries" in incoming:
                self._cfg.empirical_market_ttl_cancel_promotion_entries = _i(
                    "empirical_market_ttl_cancel_promotion_entries", 1, 20,
                )
            if "wfo_forward_min_trades" in incoming:
                self._cfg.wfo_forward_min_trades = _i("wfo_forward_min_trades", 1, 500)
            if "wfo_forward_demotion_threshold" in incoming:
                self._cfg.wfo_forward_demotion_threshold = _f(
                    "wfo_forward_demotion_threshold", -20.0, 10.0,
                )
            if "wfo_assume_taker_fee" in incoming:
                self._cfg.wfo_assume_taker_fee = _bool("wfo_assume_taker_fee")
            if "wfo_pnl_first_promotion" in incoming:
                self._cfg.wfo_pnl_first_promotion = _bool("wfo_pnl_first_promotion")
            if "funding_warn_bps_per_hour" in incoming:
                self._cfg.funding_warn_bps_per_hour = _f(
                    "funding_warn_bps_per_hour", 0.0, 500.0,
                )
            if "daily_loss_set_scalp_halt" in incoming:
                self._cfg.daily_loss_set_scalp_halt = _bool("daily_loss_set_scalp_halt")
        except (TypeError, ValueError) as e:
            return False, str(e)

        self._trader._empirical.update_cfg(self._cfg)
        self._wfo._wfo = _wfo_config_from_scalp_cfg(self._cfg)
        if self._session_log is not None:
            self._session_log.log_scalp("session_policy_runtime_patch", **incoming)
        LOG.info("ScalpRuntime: session policy runtime patch applied: %s", incoming)
        return True, "ok"

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
        tuner_iv = max(30.0, float(self._cfg.param_tuner_interval_sec))
        vol_armed_any = bool(getattr(self._cfg, "volatility_filter_enabled", False)) and any(
            self._volatility_filter_armed(pk) for pk in self._cfg.pairs
        )
        if vol_armed_any:
            vmult = float(getattr(self._cfg, "volatility_armed_param_tuner_interval_mult", 1.0) or 0.0)
            if vmult <= 0:
                return
            if vmult > 1.0:
                tuner_iv = max(30.0, tuner_iv * vmult)
        mult_warn = float(getattr(self._cfg, "param_tuner_warn_interval_below_bar_mult", 5.0) or 0.0)
        if mult_warn > 0 and not self._tuner_interval_warned and self._cfg.pairs:
            bar_sec = max(float(pc.interval) * 60.0 for pc in self._cfg.pairs.values())
            if tuner_iv + 1e-6 < mult_warn * bar_sec:
                self._tuner_interval_warned = True
                LOG.warning(
                    "ParamTuner: param_tuner_interval_sec=%.0f < %.1f × longest_bar_sec=%.0f — "
                    "frequent runs may drift knobs vs bar cadence",
                    tuner_iv,
                    mult_warn,
                    bar_sec,
                )
        if now - self._tuner_last_run < tuner_iv:
            return
        self._tuner_last_run = now

        lookback_h = float(self._cfg.wfo_train_hours) + float(self._cfg.wfo_holdout_hours)
        champ = champ_store if champ_store is not None else self._champion_data
        if champ is None:
            champ = load_champion()

        for pk, pc in self._cfg.pairs.items():
            try:
                if now < float(self._tuner_apply_cooldown_until.get(pk, 0.0)):
                    continue
                min_bars_bt = int(getattr(self._cfg, "param_tuner_min_bars_between_runs", 0) or 0)
                if min_bars_bt > 0 and int(self._tuner_bars_since_run.get(pk, 0)) < min_bars_bt:
                    continue

                if bool(getattr(self._cfg, "param_tuner_require_wfo_champion", True)):
                    if not pair_has_wfo_champion(champ, pc.symbol, pc.interval):
                        continue

                eff_for_modes = str(self._active_mode.get(pk) or "").strip()
                if not eff_for_modes:
                    eff_for_modes = str(pc.strategy_mode or "").strip()
                if eff_for_modes == "auto" or not eff_for_modes:
                    eff_for_modes = self._resolved_active_mode(pk)
                modes_only: tuple[str, ...] | None = None
                if pair_has_wfo_champion(champ, pc.symbol, pc.interval) and not bool(
                    getattr(self._cfg, "param_tuner_allow_mode_override_champion", False)
                ):
                    if eff_for_modes in STRATEGY_MODES:
                        modes_only = (eff_for_modes,)

                self._tuner_bars_since_run[pk] = 0

                result = run_tuner_cycle(
                    pk,
                    pc,
                    self._cfg,
                    lookback_h,
                    modes_only=modes_only,
                    slippage_bps=self.effective_slippage_bps_for_sim(),
                )
                if result is None:
                    continue

                self._tuner_results[pk] = result

                wfo_champion_active = pair_has_wfo_champion(champ, pc.symbol, pc.interval)
                if wfo_champion_active:
                    self._nemesis_resolution.pop(pk, None)
                    eff, res_sub = champion_tuner_mode_resolution(
                        wfo_champion_active=True,
                        allow_mode_override_champion=bool(
                            getattr(self._cfg, "param_tuner_allow_mode_override_champion", False)
                        ),
                        current_active_mode=str(self._resolved_active_mode(pk)),
                        tuner_best_mode=str(result.best_mode or ""),
                    )
                    if res_sub == "override_champion":
                        old_m = self._active_mode.get(pk, "?")
                        self._active_mode[pk] = eff
                        self._mode_source[pk] = "param_tuner_override"
                        LOG.warning(
                            "ParamTuner %s: param_tuner_allow_mode_override_champion — "
                            "active mode %s -> %s (tuner grid; live signals use new mode; "
                            "champion JSON params may not match this mode)",
                            pk, old_m, eff,
                        )
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
                effective_mode = str(self._resolved_active_mode(pk))
                apply_mode = effective_mode.strip() or str(result.best_mode or "").strip()
                if (
                    effective_mode
                    and str(result.best_mode or "").strip()
                    and effective_mode != str(result.best_mode or "")
                ):
                    LOG.info(
                        "ParamTuner %s: apply_tuner_result using active_mode=%s (tuner_cycle_best=%s)",
                        pk, apply_mode, result.best_mode,
                    )

                applied = apply_tuner_result(result, pc, apply_mode=apply_mode)
                if applied:
                    cd_ap = float(getattr(self._cfg, "param_tuner_cooldown_sec_after_apply", 0.0) or 0.0)
                    if cd_ap > 0:
                        self._tuner_apply_cooldown_until[pk] = time.time() + cd_ap
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
                            cooldown_sec_after_apply=cd_ap,
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

    def _note_slip_calibration_sample(self, slip_bps: float) -> None:
        """EMA update from live entry ``slip_bps`` (``scalp_fill_execution``); WFO/tuner read via resolver."""
        if not bool(getattr(self._cfg, "slip_calibration_enabled", False)):
            return
        if not math.isfinite(slip_bps) or slip_bps < 0:
            return
        self._slip_calib_samples += 1
        a = float(getattr(self._cfg, "slip_calibration_ema_alpha", 0.2) or 0.2)
        a = max(0.0, min(1.0, a))
        if self._slip_calib_ema is None:
            self._slip_calib_ema = float(slip_bps)
        else:
            self._slip_calib_ema = a * float(slip_bps) + (1.0 - a) * float(self._slip_calib_ema)

    def effective_slippage_bps_for_sim(self) -> float:
        """Slippage bps for WFO / param tuner vec sim (config floor + optional calibrated EMA)."""
        base = float(getattr(self._cfg, "slippage_bps", 1.0) or 0.0)
        if not bool(getattr(self._cfg, "slip_calibration_enabled", False)):
            return base
        min_s = int(getattr(self._cfg, "slip_calibration_min_samples", 8) or 0)
        if self._slip_calib_samples < min_s or self._slip_calib_ema is None:
            return base
        floor_b = float(getattr(self._cfg, "slip_calibration_floor_bps", 0.0) or 0.0)
        cap_b = float(getattr(self._cfg, "slip_calibration_cap_bps", 80.0) or 80.0)
        cal = max(floor_b, min(cap_b, float(self._slip_calib_ema)))
        mode = str(getattr(self._cfg, "slip_calibration_mode", "max_with_config") or "max_with_config").strip().lower()
        if mode == "replace":
            return cal
        return max(base, cal)

    def _maybe_refresh_strategy_lookback(self) -> None:
        """Schedule a background thread refresh of strategy lookback — never blocks the event loop."""
        if not self._cfg.enabled or not self._cfg.pairs:
            return
        now = time.time()
        if now - self._strategy_lookback_ts < _STRATEGY_LOOKBACK_REFRESH_SEC:
            return
        # Mark timestamp immediately so concurrent snapshot() calls don't also schedule
        self._strategy_lookback_ts = now
        cfg = self._cfg

        async def _refresh_in_thread() -> None:
            try:
                result = await asyncio.to_thread(build_strategy_lookback_snapshot, cfg)
                self._strategy_lookback_snapshot = result
            except Exception:
                LOG.exception("ScalpRuntime: strategy_lookback refresh failed")

        try:
            asyncio.get_running_loop().create_task(_refresh_in_thread(), name="scalp_lookback_refresh")
        except RuntimeError:
            pass  # no running loop (e.g. called during shutdown)

    @staticmethod
    def _json_safe_float(x: object) -> float:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return 0.0
        return v if math.isfinite(v) else 0.0

    def _apply_exchange_fee_rates_from_summary(self, data: dict) -> None:
        """Update in-memory maker/taker bps from a successful ``transaction_summary`` dict."""
        if str(getattr(self._cfg, "venue", "")).lower() != "coinbase_perps":
            return
        if not bool(getattr(self._cfg, "fee_tier_auto_apply_exchange_fee_rates", True)):
            return
        if str(getattr(self._cfg, "fee_tier_volume_source", "exchange")).lower() != "exchange":
            return
        parsed = _parse_coinbase_summary_fee_bps(data)
        if parsed is None:
            return
        mk, tk = parsed
        # Coinbase **spot** Advanced ~12.5 / 25 bps; CDE perps ladder tops out ~9.5 / 10 bps on the
        # lowest volume band. If we see spot-scale numbers, the wrong transaction_summary branch may
        # be winning — log loudly (poll still applies what the API returned).
        if mk >= 11.0 or tk > 12.0:
            LOG.warning(
                "ScalpRuntime: fee_tier maker=%.4f taker=%.4f bps look like **spot** scale, not typical "
                "CDE perps (often ~6.5–9.5 / 7–10 bps). Confirm Coinbase is returning **derivatives** "
                "fees (see CoinbaseOrderManager.get_futures_transaction_summary variants).",
                mk,
                tk,
            )
        old_m = float(getattr(self._cfg, "fee_bps_per_leg", 0.0) or 0.0)
        old_t = float(getattr(self._cfg, "fee_bps_taker_per_leg", 0.0) or 0.0)
        if abs(old_m - mk) < 1e-4 and abs(old_t - tk) < 1e-4:
            return

        self._cfg.fee_bps_per_leg = mk
        self._cfg.fee_bps_taker_per_leg = tk
        self._wfo._wfo = _wfo_config_from_scalp_cfg(self._cfg)
        try:
            from .scalp_fee_assumptions import fee_assumption_snapshot, save_fee_assumption_state

            save_fee_assumption_state(fee_assumption_snapshot(self._cfg))
        except Exception:
            LOG.debug("persist fee assumption snapshot after exchange apply failed", exc_info=True)
        LOG.info(
            "ScalpRuntime: applied Coinbase fee_tier rates → maker_bps=%.4f taker_bps=%.4f "
            "(was maker=%.4f taker=%.4f)",
            mk,
            tk,
            old_m,
            old_t,
        )
        if self._session_log is not None:
            self._session_log.log_scalp(
                "exchange_fee_rates_applied",
                maker_bps=mk,
                taker_bps=tk,
                prev_maker_bps=old_m,
                prev_taker_bps=old_t,
            )

        if bool(getattr(self._cfg, "scalp_auto_invalidate_champion_on_fee_change", False)):
            store = load_champion()
            removed = False
            if store:
                for sym in list(store.keys()):
                    if remove_champion_for_symbol(sym):
                        removed = True
            if removed:
                LOG.warning(
                    "ScalpRuntime: cleared champion row(s) after exchange fee rate change "
                    "(scalp_auto_invalidate_champion_on_fee_change=true)",
                )
                try:
                    self._champion_mtime = CHAMPION_PATH.stat().st_mtime if CHAMPION_PATH.exists() else 0.0
                except OSError:
                    self._champion_mtime = 0.0
                self._champion_data = load_champion() or {}

        try:
            self._state.push_alert(
                "info",
                "Scalp fees updated from exchange",
                f"Maker {old_m:.4f}→{mk:.4f} bps/leg, taker {old_t:.4f}→{tk:.4f} bps/leg "
                "(WFO / bar sim / tuner use effective fee from order_type). "
                "config.toml unchanged — edit there to persist across restarts.",
                "scalp_fee_tier",
            )
        except Exception:
            LOG.debug("push_alert after fee apply failed", exc_info=True)

    def _log_scalp_fee_tier_refresh(
        self,
        *,
        trigger: str,
        success: bool,
        detail: str,
        data: dict | None = None,
        prev_maker_bps: float | None = None,
        prev_taker_bps: float | None = None,
        rates_changed: bool | None = None,
    ) -> None:
        """Session JSONL audit for manual dashboard refresh and periodic exchange polls."""
        if self._session_log is None:
            return
        mk = float(getattr(self._cfg, "fee_bps_per_leg", 0.0) or 0.0)
        tk = float(getattr(self._cfg, "fee_bps_taker_per_leg", 0.0) or 0.0)
        row: dict = {
            "trigger": str(trigger)[:32],
            "success": bool(success),
            "detail": str(detail)[:400],
            "maker_bps": round(mk, 6),
            "taker_bps": round(tk, 6),
        }
        if prev_maker_bps is not None:
            row["prev_maker_bps"] = round(float(prev_maker_bps), 6)
        if prev_taker_bps is not None:
            row["prev_taker_bps"] = round(float(prev_taker_bps), 6)
        if rates_changed is not None:
            row["rates_changed"] = bool(rates_changed)
        if isinstance(data, dict):
            tv = data.get("total_volume")
            if tv is not None:
                try:
                    row["total_volume_30d_usd"] = round(float(tv), 4)
                except (TypeError, ValueError):
                    pass
            ft = data.get("fee_tier")
            if isinstance(ft, dict):
                pt = ft.get("pricing_tier")
                if pt is not None:
                    row["exchange_pricing_tier"] = str(pt)[:64]
        self._session_log.log_scalp("scalp_fee_tier_refresh", **row)

    @classmethod
    def _candle_snapshot_dict(cls, c: object) -> dict[str, float]:
        """Dashboard JSON: finite floats only (avoids NaN breaking JS parse / null-like UI bugs)."""
        return {
            "t": cls._json_safe_float(getattr(c, "timestamp", 0)),
            "o": cls._json_safe_float(getattr(c, "open", 0)),
            "h": cls._json_safe_float(getattr(c, "high", 0)),
            "l": cls._json_safe_float(getattr(c, "low", 0)),
            "c": cls._json_safe_float(getattr(c, "close", 0)),
            "v": cls._json_safe_float(getattr(c, "volume", 0)),
        }

    def _record_indicator_overlay(self, pair_key: str, ts: float, iv: "IndicatorValues") -> None:
        """Append per-candle indicator overlay values for chart rendering."""
        buf = self._indicator_overlay.get(pair_key)
        if buf is None:
            return
        mf = float(getattr(iv, "macd_line", 0.0) or 0.0)
        ms = float(getattr(iv, "macd_signal", 0.0) or 0.0)
        macd_hist = round(mf - ms, 8)
        buf.append({
            "t": int(ts),
            "ema_fast": round(iv.ema_fast, 5) if iv.ema_fast else 0.0,
            "ema_slow": round(iv.ema_slow, 5) if iv.ema_slow else 0.0,
            "t3": round(iv.t3, 5) if iv.t3 else 0.0,
            "vwap": round(iv.vwap_session, 5) if iv.vwap_session else 0.0,
            "macd_hist": macd_hist,
        })

    def _fee_tier_note_fill_leg_usd(self, usd: float) -> None:
        """Accumulate session notionals for manual+increment display (not double-counted with exchange)."""
        if not getattr(self._cfg, "fee_tier_add_bot_fill_notional", False):
            return
        if str(getattr(self._cfg, "fee_tier_volume_source", "exchange")).lower() != "manual":
            return
        if usd <= 0.0 or not math.isfinite(usd):
            return
        self._fee_tier_bot_fill_usd += usd

    def _fee_tier_public_payload(self) -> dict:
        src = str(getattr(self._cfg, "fee_tier_volume_source", "exchange")).lower()
        manual = getattr(self._cfg, "fee_tier_30d_volume_usd", None)
        bot = float(self._fee_tier_bot_fill_usd)
        ex = self._fee_tier_exchange_data if isinstance(self._fee_tier_exchange_data, dict) else None
        display: float | None = None
        if src == "exchange" and ex and ex.get("total_volume") is not None:
            try:
                display = float(ex["total_volume"])
            except (TypeError, ValueError):
                display = None
        elif src == "manual":
            if manual is not None:
                base = float(manual)
                if getattr(self._cfg, "fee_tier_add_bot_fill_notional", False):
                    display = base + bot
                else:
                    display = base
        return {
            "volume_source": src,
            "display_volume_usd": display,
            "manual_baseline_usd": float(manual) if manual is not None else None,
            "bot_fill_usd_session": round(bot, 4),
            "exchange": ex,
            "last_poll_ts": float(self._fee_tier_last_poll_ts),
            "poll_error": self._fee_tier_poll_error or None,
            "poll_interval_sec": float(getattr(self._cfg, "fee_tier_poll_interval_sec", 900.0)),
            "auto_apply_exchange_fee_rates": bool(
                getattr(self._cfg, "fee_tier_auto_apply_exchange_fee_rates", True)
            ),
            "effective_maker_bps": float(getattr(self._cfg, "fee_bps_per_leg", 0.0) or 0.0),
            "effective_taker_bps": float(getattr(self._cfg, "fee_bps_taker_per_leg", 0.0) or 0.0),
            # NFA / exchange / clearing flat charge (Coinbase fee page); not returned by transaction_summary.
            "fee_usd_per_contract_per_leg": float(
                getattr(self._cfg, "fee_usd_per_contract_per_leg", 0.0) or 0.0
            ),
        }

    async def _maybe_refresh_fee_tier_volume(self) -> None:
        if str(getattr(self._cfg, "venue", "")).lower() != "coinbase_perps":
            return
        if str(getattr(self._cfg, "fee_tier_volume_source", "exchange")).lower() != "exchange":
            return
        iv = max(60.0, float(getattr(self._cfg, "fee_tier_poll_interval_sec", 900.0)))
        now = time.time()
        if now - self._fee_tier_last_poll_ts < iv:
            return
        lm = self._live_mgr
        fn = getattr(lm, "fetch_futures_transaction_summary", None) if lm is not None else None
        if not callable(fn):
            self._fee_tier_poll_error = "coinbase_manager_or_method_missing"
            self._fee_tier_last_poll_ts = now
            return
        old_m = float(getattr(self._cfg, "fee_bps_per_leg", 0.0) or 0.0)
        old_t = float(getattr(self._cfg, "fee_bps_taker_per_leg", 0.0) or 0.0)
        try:
            data = await fn()
            if isinstance(data, dict) and data.get("ok"):
                self._fee_tier_exchange_data = data
                self._fee_tier_poll_error = ""
                self._apply_exchange_fee_rates_from_summary(data)
                new_m = float(getattr(self._cfg, "fee_bps_per_leg", 0.0) or 0.0)
                new_t = float(getattr(self._cfg, "fee_bps_taker_per_leg", 0.0) or 0.0)
                rc = abs(old_m - new_m) >= 1e-4 or abs(old_t - new_t) >= 1e-4
                self._log_scalp_fee_tier_refresh(
                    trigger="auto_poll",
                    success=True,
                    detail="ok",
                    data=data,
                    prev_maker_bps=old_m,
                    prev_taker_bps=old_t,
                    rates_changed=rc,
                )
            else:
                self._fee_tier_poll_error = "empty_or_failed_response"
        except Exception as e:
            self._fee_tier_poll_error = str(e)[:240]
        self._fee_tier_last_poll_ts = now

    async def refresh_fee_tier_from_exchange(self) -> tuple[bool, str]:
        """Force a Coinbase ``transaction_summary`` poll (dashboard button)."""
        old_m = float(getattr(self._cfg, "fee_bps_per_leg", 0.0) or 0.0)
        old_t = float(getattr(self._cfg, "fee_bps_taker_per_leg", 0.0) or 0.0)
        if str(getattr(self._cfg, "venue", "")).lower() != "coinbase_perps":
            self._log_scalp_fee_tier_refresh(
                trigger="manual",
                success=False,
                detail="venue_not_coinbase_perps",
                prev_maker_bps=old_m,
                prev_taker_bps=old_t,
            )
            return False, "venue_not_coinbase_perps"
        lm = self._live_mgr
        fn = getattr(lm, "fetch_futures_transaction_summary", None) if lm is not None else None
        if not callable(fn):
            self._log_scalp_fee_tier_refresh(
                trigger="manual",
                success=False,
                detail="coinbase_manager_or_method_missing",
                prev_maker_bps=old_m,
                prev_taker_bps=old_t,
            )
            return False, "coinbase_manager_or_method_missing"
        try:
            data = await fn()
            if isinstance(data, dict) and data.get("ok"):
                self._fee_tier_exchange_data = data
                self._fee_tier_poll_error = ""
                self._fee_tier_last_poll_ts = time.time()
                self._apply_exchange_fee_rates_from_summary(data)
                new_m = float(getattr(self._cfg, "fee_bps_per_leg", 0.0) or 0.0)
                new_t = float(getattr(self._cfg, "fee_bps_taker_per_leg", 0.0) or 0.0)
                rc = abs(old_m - new_m) >= 1e-4 or abs(old_t - new_t) >= 1e-4
                self._log_scalp_fee_tier_refresh(
                    trigger="manual",
                    success=True,
                    detail="ok",
                    data=data,
                    prev_maker_bps=old_m,
                    prev_taker_bps=old_t,
                    rates_changed=rc,
                )
                return True, "ok"
            self._log_scalp_fee_tier_refresh(
                trigger="manual",
                success=False,
                detail="empty_or_failed_response",
                prev_maker_bps=old_m,
                prev_taker_bps=old_t,
            )
            return False, "empty_or_failed_response"
        except Exception as e:
            self._fee_tier_poll_error = str(e)[:240]
            self._fee_tier_last_poll_ts = time.time()
            self._log_scalp_fee_tier_refresh(
                trigger="manual",
                success=False,
                detail=str(e)[:200],
                prev_maker_bps=old_m,
                prev_taker_bps=old_t,
            )
            return False, str(e)[:200]

    def snapshot(self, *, include_closed_candles: bool = True) -> dict:
        self._maybe_refresh_strategy_lookback()

        config_warnings: list[str] = []
        if not bool(self._trader.sim_mode):
            pt_pairs = [
                pk
                for pk, pc in self._cfg.pairs.items()
                if bool(getattr(pc, "partial_tp_enabled", False))
            ]
            if pt_pairs:
                config_warnings.append(
                    "partial_tp_enabled is on for "
                    + ", ".join(pt_pairs)
                    + " — live mode closes the full position at TP (true partial exits are paper/sim only)."
                )

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
                    "live": (self._candle_snapshot_dict(lc) if lc else None),
                    "interval": self._cfg.pairs[pair_key].interval,
                }
                if include_closed_candles:
                    buf = self._feed.get_buffer(pair_key)
                    entry["closed"] = [self._candle_snapshot_dict(c) for c in buf[-500:]]
                overlay_buf = self._indicator_overlay.get(pair_key)
                if overlay_buf is not None and include_closed_candles:
                    entry["indicator_overlay"] = list(overlay_buf)
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
        exchange_open_orders_all: list[dict] = []
        exchange_open_orders_outside_pairs: list[dict] = []
        if self._live_mgr is not None:
            if hasattr(self._live_mgr, "scalp_open_orders_snapshot"):
                try:
                    exchange_open_orders = self._live_mgr.scalp_open_orders_snapshot()
                except Exception:
                    pass
            if hasattr(self._live_mgr, "scalp_open_orders_all_snapshot"):
                try:
                    exchange_open_orders_all = self._live_mgr.scalp_open_orders_all_snapshot()
                except Exception:
                    pass
            if hasattr(self._live_mgr, "scalp_open_orders_outside_config_snapshot"):
                try:
                    exchange_open_orders_outside_pairs = (
                        self._live_mgr.scalp_open_orders_outside_config_snapshot()
                    )
                except Exception:
                    pass

        return {
            "enabled": self._cfg.enabled,
            "venue": getattr(self._cfg, "venue", "coinbase_perps"),
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
            "fee_tier": self._fee_tier_public_payload(),
            "session_policy": {
                "warmup_enabled": self._cfg.warmup_enabled,
                "default_candle_interval_minutes": int(
                    next(iter(self._cfg.pairs.values())).interval,
                )
                if self._cfg.pairs
                else 5,
                "warmup_min_bars": int(self._cfg.warmup_min_bars),
                "warmup_require_champion": bool(self._cfg.warmup_require_champion),
                "warmup_max_hours": float(self._cfg.warmup_max_hours),
                "wfo_enabled": bool(self._cfg.wfo_enabled),
                "wfo_interval_sec": float(self._cfg.wfo_interval_sec),
                "wfo_train_hours": float(self._cfg.wfo_train_hours),
                "wfo_holdout_hours": float(self._cfg.wfo_holdout_hours),
                "wfo_step_hours": float(self._cfg.wfo_step_hours),
                "wfo_top_k": max(1, int(self._cfg.wfo_top_k)),
                "wfo_objective": str(self._cfg.wfo_objective),
                "wfo_pnl_first_promotion": bool(
                    getattr(self._cfg, "wfo_pnl_first_promotion", False),
                ),
                "wfo_min_trades": int(self._cfg.wfo_min_trades),
                "wfo_min_holdout_trades": int(getattr(self._cfg, "wfo_min_holdout_trades", 0) or 0),
                "scalp_fee_assumption_revision": int(
                    getattr(self._cfg, "scalp_fee_assumption_revision", 0) or 0
                ),
                "fee_tier_30d_volume_usd": getattr(self._cfg, "fee_tier_30d_volume_usd", None),
                "fee_tier_volume_source": str(getattr(self._cfg, "fee_tier_volume_source", "exchange")),
                "fee_tier_poll_interval_sec": float(
                    getattr(self._cfg, "fee_tier_poll_interval_sec", 900.0) or 900.0
                ),
                "fee_tier_add_bot_fill_notional": bool(
                    getattr(self._cfg, "fee_tier_add_bot_fill_notional", False)
                ),
                "fee_tier_auto_apply_exchange_fee_rates": bool(
                    getattr(self._cfg, "fee_tier_auto_apply_exchange_fee_rates", True)
                ),
                "backtest_funding_enabled": bool(getattr(self._cfg, "backtest_funding_enabled", False)),
                "backtest_funding_bps_per_hour": float(
                    getattr(self._cfg, "backtest_funding_bps_per_hour", 0.0) or 0.0
                ),
                "param_tuner_interval_sec": float(self._cfg.param_tuner_interval_sec),
                "param_tuner_min_bars_between_runs": int(
                    getattr(self._cfg, "param_tuner_min_bars_between_runs", 0) or 0
                ),
                "param_tuner_cooldown_sec_after_apply": float(
                    getattr(self._cfg, "param_tuner_cooldown_sec_after_apply", 0.0) or 0.0
                ),
                "param_tuner_warn_interval_below_bar_mult": float(
                    getattr(self._cfg, "param_tuner_warn_interval_below_bar_mult", 5.0) or 0.0
                ),
                "wfo_holdout_tiebreakers": list(
                    getattr(self._cfg, "wfo_holdout_tiebreakers", ()) or ()
                ),
                "wfo_holdout_score_epsilon": float(
                    getattr(self._cfg, "wfo_holdout_score_epsilon", 0.0) or 0.0
                ),
                "param_tuner_require_wfo_champion": bool(
                    getattr(self._cfg, "param_tuner_require_wfo_champion", True)
                ),
                "param_tuner_allow_mode_override_champion": bool(
                    getattr(self._cfg, "param_tuner_allow_mode_override_champion", False)
                ),
                "wfo_assume_taker_fee": bool(getattr(self._cfg, "wfo_assume_taker_fee", False)),
                "wfo_fee_bps_sim_per_leg": float(wfo_fee_bps_per_leg(self._cfg)),
                "wfo_forward_min_trades": int(getattr(self._cfg, "wfo_forward_min_trades", 10)),
                "wfo_forward_demotion_threshold": float(
                    getattr(self._cfg, "wfo_forward_demotion_threshold", -0.5)
                ),
                "wfo_forward_outperform_factor": float(
                    getattr(self._cfg, "wfo_forward_outperform_factor", 1.5) or 1.5
                ),
                "volatility_armed_param_tuner_interval_mult": float(
                    getattr(self._cfg, "volatility_armed_param_tuner_interval_mult", 1.0) or 1.0
                ),
                "funding_warn_bps_per_hour": float(
                    getattr(self._cfg, "funding_warn_bps_per_hour", 5.0) or 5.0
                ),
                "empirical_market_promotion_enabled": bool(
                    getattr(self._cfg, "empirical_market_promotion_enabled", False)
                ),
                "empirical_market_missed_move_bps": float(
                    getattr(self._cfg, "empirical_market_missed_move_bps", 12.0) or 12.0
                ),
                "empirical_market_miss_eval_window_sec": float(
                    getattr(self._cfg, "empirical_market_miss_eval_window_sec", 600.0) or 600.0
                ),
                "empirical_market_min_pattern_in_window": int(
                    getattr(self._cfg, "empirical_market_min_pattern_in_window", 3)
                ),
                "empirical_market_pattern_window_sec": float(
                    getattr(self._cfg, "empirical_market_pattern_window_sec", 86400.0) or 86400.0
                ),
                "empirical_market_promotion_entries": int(
                    getattr(self._cfg, "empirical_market_promotion_entries", 2)
                ),
                "empirical_market_promotion_cooldown_sec": float(
                    getattr(self._cfg, "empirical_market_promotion_cooldown_sec", 3600.0) or 3600.0
                ),
                "empirical_market_ttl_cancel_arms_promotion": bool(
                    getattr(self._cfg, "empirical_market_ttl_cancel_arms_promotion", False)
                ),
                "empirical_market_ttl_cancel_promotion_entries": int(
                    getattr(self._cfg, "empirical_market_ttl_cancel_promotion_entries", 1)
                ),
                "wfo_max_roll_windows": max(1, int(self._cfg.wfo_max_roll_windows)),
                "wfo_train_same_calendar_day_boost": float(
                    self._cfg.wfo_train_same_calendar_day_boost
                ),
                "wfo_roll_span_hours": float(
                    wfo_roll_span_hours(
                        self._cfg.wfo_train_hours,
                        self._cfg.wfo_holdout_hours,
                        self._cfg.wfo_step_hours,
                        max(1, int(self._cfg.wfo_max_roll_windows)),
                    )
                ),
                "wfo_min_profit_factor": float(
                    getattr(self._cfg, "wfo_min_profit_factor", 0.8) or 0.8,
                ),
                "wfo_min_win_rate": float(getattr(self._cfg, "wfo_min_win_rate", 0.20) or 0.20),
                "wfo_max_train_drawdown_pct": float(
                    getattr(self._cfg, "wfo_max_train_drawdown_pct", 30.0) or 30.0,
                ),
                "daily_loss_set_scalp_halt": bool(
                    getattr(self._cfg, "daily_loss_set_scalp_halt", True),
                ),
                "slip_calibration_enabled": bool(
                    getattr(self._cfg, "slip_calibration_enabled", False),
                ),
                "slip_calibration_ema_alpha": float(
                    getattr(self._cfg, "slip_calibration_ema_alpha", 0.2) or 0.2,
                ),
                "slip_calibration_min_samples": int(
                    getattr(self._cfg, "slip_calibration_min_samples", 8) or 8,
                ),
                "slip_calibration_floor_bps": float(
                    getattr(self._cfg, "slip_calibration_floor_bps", 0.0) or 0.0,
                ),
                "slip_calibration_cap_bps": float(
                    getattr(self._cfg, "slip_calibration_cap_bps", 80.0) or 80.0,
                ),
                "slip_calibration_mode": str(
                    getattr(self._cfg, "slip_calibration_mode", "max_with_config") or "max_with_config",
                ),
            },
            "config_warnings": config_warnings,
            "slip_calibration": {
                "enabled": bool(getattr(self._cfg, "slip_calibration_enabled", False)),
                "samples": int(self._slip_calib_samples),
                "ema_bps": (
                    None
                    if self._slip_calib_ema is None
                    else round(float(self._slip_calib_ema), 6)
                ),
                "effective_bps": round(float(self.effective_slippage_bps_for_sim()), 6),
                "config_bps": round(float(getattr(self._cfg, "slippage_bps", 1.0) or 0.0), 6),
                "mode": str(
                    getattr(self._cfg, "slip_calibration_mode", "max_with_config") or "max_with_config",
                ),
            },
            "portfolio_risk": {
                "scalp_risk_halted": self._state.scalp_risk_halted,
                "scalp_risk_halt_reason": self._state.scalp_risk_halt_reason,
                "scalp_risk_halted_ts": self._state.scalp_risk_halted_ts,
                "scalp_entries_blocked": self._state.scalp_entries_blocked(),
                "mm_spread_bot_enabled": self._state.mm_spread_bot_enabled,
                "mm_risk_halted": self._state.risk_halted,
            },
            "warmup": warmup,
            "trader": self._trader.snapshot(),
            "pair_symbols": {k: pc.symbol for k, pc in self._cfg.pairs.items()},
            "auto_mode_fallback": str(
                getattr(self._cfg, "auto_mode_fallback", "sar_chop") or "sar_chop"
            ),
            "active_modes": dict(self._active_mode),
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
                "relax_after_calm_sec": float(
                    getattr(self._cfg, "risk_on_relax_after_calm_sec", 0.0)
                ),
                "calm_since_ts": self._regime_calm_since,
                "effective_bootstrap_hours": self._effective_bootstrap_hours(),
                "effective_wfo_sleep_sec": (
                    round(self._effective_wfo_sleep_sec(), 1) if self._cfg.wfo_enabled else None
                ),
            },
            "volatility_filter": self._volatility_filter_snapshot(),
            "wfo": wfo_ui,
            "balances": balances,
            "exchange_open_orders": exchange_open_orders,
            "exchange_open_orders_all": exchange_open_orders_all,
            "exchange_open_orders_outside_pairs": exchange_open_orders_outside_pairs,
            "indicators": {
                k: {
                    "candles": self._indicators[k].candle_count,
                    "ready": iv.ready,
                    "mode_ready": getattr(iv, "mode_ready", True),
                    "min_bars_ready_mode": int(getattr(iv, "min_bars_ready_mode", 0) or 0),
                    "ohlc_hist_maxlen": int(self._indicators[k].ohlc_hist_maxlen),
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
            "scalp_parity_fingerprint": self._parity_fingerprint_payload(),
        }

    def _parity_fingerprint_payload(self) -> dict[str, Any]:
        rows = {
            pk: per_pair_parity_row(pk, pc, resolved_mode=self._resolved_active_mode(pk))
            for pk, pc in self._cfg.pairs.items()
        }
        any_ch = bool(
            self._champion_data
            and any(
                isinstance(self._champion_data.get(pc.symbol), dict)
                for pc in self._cfg.pairs.values()
            ),
        )
        return build_scalp_parity_fingerprint(
            self._cfg,
            champion_present=any_ch,
            per_pair=rows,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Main loop: start feed, process candles, evaluate signals."""
        pairs = {k: pc.symbol for k, pc in self._cfg.pairs.items()}
        intervals = {k: pc.interval for k, pc in self._cfg.pairs.items()}

        LOG.info("ScalpRuntime: seeding candle feed from REST...")
        bar_store.set_bar_store_venue(getattr(self._cfg, "venue", "coinbase_perps"))
        bar_store.set_ui_alert_notifier(
            lambda lv, title, detail, src="bar_store": self._state.push_alert(lv, title, detail, src),
        )
        if self._session_log is not None:
            self._session_log.log_scalp(
                "runtime_task_begin",
                pairs=list(self._cfg.pairs.keys()),
                venue=getattr(self._cfg, "venue", "coinbase_perps"),
                wfo_enabled=self._cfg.wfo_enabled,
                sim_mode=self._cfg.sim_mode,
                warmup_enabled=self._cfg.warmup_enabled,
                wfo_train_hours=self._cfg.wfo_train_hours,
                wfo_holdout_hours=self._cfg.wfo_holdout_hours,
                wfo_step_hours=self._cfg.wfo_step_hours,
            )
        try:
            from .scalp_fee_assumptions import reconcile_fee_assumptions_on_startup

            if reconcile_fee_assumptions_on_startup(
                self._cfg,
                auto_invalidate_champions=bool(
                    getattr(self._cfg, "scalp_auto_invalidate_champion_on_fee_change", False)
                ),
            ):
                self._champion_data = load_champion()
                self._champion_mtime = (
                    CHAMPION_PATH.stat().st_mtime if CHAMPION_PATH.exists() else -1.0
                )
        except Exception:
            LOG.exception("ScalpRuntime: fee assumption reconcile failed")
        try:
            self._feed = await start_candle_feed(
                pairs=pairs,
                intervals=intervals,
                rest_seed_count=self._cfg.rest_seed_candles,
                venue=getattr(self._cfg, "venue", "coinbase_perps"),
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
                iv = ind.update(
                    candle,
                    strategy_mode_for_ready=self._resolved_active_mode(pair_key),
                )
                self._record_indicator_overlay(pair_key, candle.timestamp, iv)
            if buf:
                self._latest_iv[pair_key] = iv  # type: ignore[possibly-undefined]
            self._warmup_bars_collected[pair_key] = len(buf)

        # Derivatives maker/taker from Coinbase **before** any startup WFO / vec sim (toml may lag tier moves).
        try:
            await self._maybe_refresh_fee_tier_volume()
        except Exception:
            LOG.warning("ScalpRuntime: startup fee tier poll failed", exc_info=True)

        # Also count any bars already in bar_store (from prior runs)
        for pair_key, pair_cfg in self._cfg.pairs.items():
            existing = bar_store.bar_count(pair_cfg.symbol, pair_cfg.interval)
            if existing > self._warmup_bars_collected.get(pair_key, 0):
                self._warmup_bars_collected[pair_key] = existing

        try:
            LOG.info("ScalpRuntime: parity fingerprint %s", self._parity_fingerprint_payload())
        except Exception:
            LOG.debug("ScalpRuntime: parity fingerprint build failed", exc_info=True)

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

        # Backfill from REST — WFO roll span + wfo_backfill_buffer_hours slack.
        total_hours_needed = self._scalp_wfo_backfill_hours()
        for pair_key, pair_cfg in self._cfg.pairs.items():
            try:
                written = await bar_store.backfill_from_rest(
                    pair_cfg.symbol,
                    pair_cfg.interval,
                    total_hours_needed,
                    venue=getattr(self._cfg, "venue", "coinbase_perps"),
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
                self._verify_wfo_backfill_span(pair_key, pair_cfg)
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

        total_hours_needed = self._scalp_wfo_backfill_hours()
        pair_list = list(self._cfg.pairs.items())
        n_pairs = max(1, len(pair_list))
        for i, (pair_key, pair_cfg) in enumerate(pair_list):
            _backfill_step.detail = f"Backfilling {pair_cfg.symbol} ({i + 1}/{n_pairs})…"
            _backfill_step.pct = round(i / n_pairs * 100, 1)
            await self._push_warmup_snapshot()
            roll_h = self._scalp_wfo_roll_hours()
            _, span_ok = wfo_verify_stored_roll_coverage(
                pair_cfg.symbol, pair_cfg.interval, roll_h,
            )
            if span_ok:
                LOG.info(
                    "ScalpRuntime: warmup backfill skipped for %s — stored span already ≥92%% of WFO roll",
                    pair_key,
                )
                existing = bar_store.bar_count(pair_cfg.symbol, pair_cfg.interval)
                self._warmup_bars_collected[pair_key] = max(
                    self._warmup_bars_collected.get(pair_key, 0), existing,
                )
                if self._session_log is not None:
                    self._session_log.log_scalp(
                        "bar_backfill_skipped",
                        pair_key=pair_key,
                        symbol=pair_cfg.symbol,
                        interval=pair_cfg.interval,
                        reason="span_already_sufficient",
                        total_in_store=existing,
                        roll_hours=round(roll_h, 2),
                    )
                continue
            try:
                written = await bar_store.backfill_from_rest(
                    pair_cfg.symbol,
                    pair_cfg.interval,
                    total_hours_needed,
                    venue=getattr(self._cfg, "venue", "coinbase_perps"),
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
                self._verify_wfo_backfill_span(pair_key, pair_cfg)
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
            _wfo_step.detail = f"Walk-forward grid across {n_pairs} pair(s)…"
            await self._push_warmup_snapshot()

            LOG.info("ScalpRuntime: running startup WFO pass (trading held until complete)…")

            # Pulse progress while WFO runs in a thread (pct driven by window completion in scalp_wfo)
            _wfo_start = time.time()
            _wfo_ui_tick = 0
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
                        _wfo_ui_tick += 1
                        _wfo_step.heartbeat = _wfo_ui_tick
                        pct, detail, last_ts = self._wfo.get_run_progress()
                        _wfo_step.pct = round(min(99.0, float(pct)), 1)
                        now = time.time()
                        age = max(0.0, now - last_ts) if last_ts > 0.0 else 0.0
                        base = detail.strip() if detail else "WFO grid pass"
                        # last_ts updates each time a rolling window finishes; long gaps are normal
                        # while scoring one window's full parameter grid.
                        hint = ""
                        if age >= 90.0:
                            hint = (
                                " — long gap = scoring one rolling window (large grid); "
                                "see session log if this stalls tens of minutes with no log lines."
                            )
                        _wfo_step.detail = (
                            f"{base} · {int(_elapsed)}s total · last progress {int(age)}s ago"
                            f" · UI #{_wfo_ui_tick}{hint}"
                        )
                        await self._push_warmup_snapshot()
                        await asyncio.sleep(2.0)

                    results = await _wfo_task
                    for pk, r in results.items():
                        if r is not None:
                            self._warmup_champion_found = True
                            LOG.info("ScalpRuntime: startup WFO champion for %s: mode=%s", pk, r.get("mode"))
                        else:
                            LOG.info("ScalpRuntime: startup WFO — no champion for %s", pk)
                        self._handle_wfo_result_pair(pk, r)
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

        if self._cfg.wfo_enabled:
            self._startup_wfo_succeeded = wfo_success
        else:
            self._startup_wfo_succeeded = True
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
            self._check_champion_forward_validation()
            try:
                await self._maybe_refresh_fee_tier_volume()
            except Exception:
                LOG.debug("ScalpRuntime: fee tier poll failed", exc_info=True)
            self._log_status()

    def _resolved_active_mode(self, pair_key: str) -> str:
        """Concrete execution mode: manual ``_active_mode``, else champion (if any), else fallback.

        NM-013: If a position is open for this pair, the mode is locked to the mode that was
        active when the position was opened. WFO champion switches are deferred until flat.
        """
        # NM-013: release lock once position is flat
        if pair_key in self._pair_entry_mode and not self._trader.has_position(pair_key):
            self._pair_entry_mode.pop(pair_key, None)

        # If a position is open, hold the entry mode to avoid mid-trade mode switches
        locked = self._pair_entry_mode.get(pair_key)
        if locked:
            return locked

        am = str(self._active_mode.get(pair_key, "") or "").strip()
        pc = self._cfg.pairs[pair_key]
        fb = getattr(pc, "auto_mode_fallback", None) or getattr(
            self._cfg, "auto_mode_fallback", "sar_chop"
        )
        if am and am != "auto":
            return am
        row = None
        if isinstance(self._champion_data, dict):
            raw = self._champion_data.get(pc.symbol)
            if isinstance(raw, dict) and champion_row_matches_pair_interval(raw, pc.interval):
                row = raw
        return resolve_auto_mode(
            "auto",
            champion_row=row,
            auto_mode_fallback=fb,
        )

    def _on_tick_update(self, pair_key: str, candle: Candle) -> None:
        """Fires on every WS candle/ticker update — live regime risk-on + intra-bar stop/TP."""
        if pair_key not in self._cfg.pairs:
            return

        now = time.time()
        if now - self._last_tick_snapshot_bump >= 0.15:
            self._last_tick_snapshot_bump = now
            bump = getattr(self._state, "_request_snapshot_bump", None)
            if callable(bump):
                try:
                    bump()
                except Exception:
                    LOG.debug("tick snapshot bump failed", exc_info=True)

        # Live regime + latest tick snapshot for calm relaxation (volume/velocity clears fast).
        iv = self._latest_iv.get(pair_key)
        vel = 0.0
        if iv is not None:
            self._regime_tick_candle[pair_key] = candle
            if (
                bool(getattr(self._cfg, "regime_risk_on_enabled", True))
                and bool(getattr(self._cfg, "regime_live_vol_enabled", True))
            ):
                w = float(getattr(self._cfg, "regime_live_velocity_window_sec", 45.0))
                vmin = float(getattr(self._cfg, "regime_live_velocity_min_bps", 20.0))
                if w > 0.0 and vmin > 0.0:
                    vel = self._update_regime_live_velocity_bps(pair_key, float(candle.close))
                self._regime_last_vel_bps[pair_key] = vel
                self._touch_regime_risk_on_live(pair_key, iv, candle, vel)
        if bool(getattr(self._cfg, "regime_risk_on_enabled", True)):
            self._update_regime_risk_on_calm_relax()

        if self._warmup_phase in (WarmupPhase.COLLECTING, WarmupPhase.OPTIMIZING):
            return
        self._trader.check_paper_exits(pair_key, candle)

        if getattr(self._cfg, "tick_entries_enabled", False) and not self._operator_standby:
            self._evaluate_tick_entry(pair_key, candle)

    def _evaluate_tick_entry(self, pair_key: str, candle: Candle) -> None:
        """Optional mid-bar entries using frozen ``_latest_iv`` + live candle close."""
        if not self._cfg.enabled:
            return
        if self._state.scalp_entries_blocked():
            return
        if self._trader.has_position(pair_key):
            return  # already have a pending or open position for this pair — skip tick entry
        cap = self._cfg.concurrent_open_cap()
        if cap is not None and self._trader.open_position_count >= cap:
            return

        iv = self._latest_iv.get(pair_key)
        if iv is None:
            return

        eff_mode = self._resolved_active_mode(pair_key)
        if eff_mode == "daviddtech_scalp":
            if not iv.optimized_ready or not iv.mode_ready:
                return
        elif not iv.ready or not iv.mode_ready:
            return

        if bool(getattr(self._cfg, "require_champion_to_trade", False)):
            if self._mode_source.get(pair_key) not in ("wfo_champion", "forward_demotion", "param_tuner_override", "nemesis_tuner"):
                LOG.debug("ScalpRuntime %s: tick entry blocked — no WFO champion yet (source=%s)", pair_key, self._mode_source.get(pair_key))
                return

        pair_cfg = self._cfg.pairs[pair_key]
        symbol = pair_cfg.symbol
        mode_override = eff_mode
        live_price = float(candle.close)

        signal = self._signal_engine.evaluate_tick(
            pair_key,
            symbol,
            pair_cfg,
            iv,
            live_price,
            mode_override=mode_override,
            shorts_enabled=bool(getattr(self._cfg, "shorts_enabled", False)),
            tick_signal_cooldown_sec=self._effective_tick_signal_cooldown_sec(pair_key),
            signal_cooldown_sec=self._effective_signal_cooldown_sec(pair_key, pair_cfg),
        )
        if signal is None:
            return

        # NM-014: prevent dual bar+tick race
        if pair_key in self._entry_pending:
            return
        self._entry_pending.add(pair_key)
        # NM-013: record mode at entry
        self._pair_entry_mode[pair_key] = self._resolved_active_mode(pair_key)

        _erm = self._volatility_exec_risk_mult(pair_key)
        asyncio.create_task(
            self._open_position(signal, pair_cfg, execution_risk_mult=_erm),
            name=f"scalp_open_tick_{pair_key}",
        )

    def _on_closed_candle(self, pair_key: str, candle: Candle) -> None:
        """Synchronous callback — called from CandleFeed on confirmed closed candle."""
        if pair_key not in self._cfg.pairs:
            return

        pair_cfg = self._cfg.pairs[pair_key]

        # Persist to bar store for WFO — offloaded to a thread so Parquet I/O
        # does not block the asyncio event loop and cause snapshot stalls.
        _cd = bar_store.candle_dict_from_feed(candle)
        try:
            asyncio.get_running_loop().create_task(
                asyncio.to_thread(bar_store.append_candles, pair_cfg.symbol, pair_cfg.interval, [_cd]),
                name="bar_store_append",
            )
        except RuntimeError:
            bar_store.append_candles(pair_cfg.symbol, pair_cfg.interval, [_cd])

        # Track bars for warmup progress
        self._warmup_bars_collected[pair_key] = self._warmup_bars_collected.get(pair_key, 0) + 1
        self._tuner_bars_since_run[pair_key] = self._tuner_bars_since_run.get(pair_key, 0) + 1

        ind = self._indicators[pair_key]
        iv = ind.update(
            candle,
            strategy_mode_for_ready=self._resolved_active_mode(pair_key),
        )
        self._latest_iv[pair_key] = iv
        self._record_indicator_overlay(pair_key, candle.timestamp, iv)

        # Push closed-candle + indicator-overlay arrays to the UI on bar-close only.
        # The 0.5s tick loop intentionally omits these heavy arrays; this bump fires the
        # full snapshot (include_closed_candles=True) exactly when new data is available.
        bump = getattr(self._state, "_request_snapshot_bump", None)
        if callable(bump):
            try:
                bump()
            except Exception:
                LOG.debug("closed-candle snapshot bump failed", exc_info=True)
        self._touch_regime_risk_on(pair_key, iv)
        if bool(getattr(self._cfg, "regime_risk_on_enabled", True)):
            self._update_regime_risk_on_calm_relax()
        # CDE perps: uPnL mark comes from Coinbase ``get_product`` mid (see CoinbaseOrderManager._refresh_scalp_marks).
        # Using candle close here drifts from the exchange P&L (15m bar vs live mark).
        if str(getattr(self._cfg, "venue", "") or "").strip().lower() != "coinbase_perps":
            self._trader.update_position_mark(pair_key, candle.close)

        LOG.info(
            "ScalpRuntime %s: candle close=%.6f ema_f=%.6f ema_s=%.6f rsi=%.1f "
            "atr=%.6f vwap=%.6f vol_ok=%s ready=%s",
            pair_key, candle.close, iv.ema_fast, iv.ema_slow, iv.rsi,
            iv.atr, iv.vwap_session, iv.volume_confirmed, iv.ready,
        )

        self._update_volatility_filter(pair_key, candle, iv)

        # During warmup: check if bar threshold reached and trigger early WFO
        if self._warmup_phase in (WarmupPhase.COLLECTING, WarmupPhase.OPTIMIZING):
            self._check_warmup_bar_threshold_async()
            return

        # -- Below here: warmup complete or disabled — normal trading --

        # Apply any queued champion now that the pair is flat.
        if pair_key in self._pending_champion and not self._trader.has_position(pair_key):
            entry = self._pending_champion.pop(pair_key)
            pending_mode = str(entry.get("mode", entry.get("params", {}).get("mode", ""))).strip()
            if pending_mode == "auto":
                pending_mode = normalize_auto_mode_fallback(
                    getattr(pair_cfg, "auto_mode_fallback", None)
                    or getattr(self._cfg, "auto_mode_fallback", "sar_chop")
                )
            old_mode = self._active_mode.get(pair_key, "?")
            self._active_mode[pair_key] = pending_mode
            self._mode_source[pair_key] = "wfo_champion"
            LOG.info(
                "ScalpRuntime %s: deferred champion applied — mode %s -> %s (pair now flat)",
                pair_key, old_mode, pending_mode,
            )
            for attr, key in [
                ("atr_stop_mult", "atr_stop_mult"), ("atr_tp_mult", "atr_tp_mult"),
                ("max_hold_bars", "max_hold_bars"), ("t3_length", "t3_length"),
                ("adx_threshold", "adx_threshold"), ("wae_sensitivity", "wae_sensitivity"),
                ("wae_fast_len", "wae_fast_len"), ("wae_slow_len", "wae_slow_len"),
                ("wae_bb_len", "wae_bb_len"), ("wae_bb_mult", "wae_bb_mult"),
            ]:
                val = entry.get("params", {}).get(key)
                if val is not None:
                    setattr(pair_cfg, attr, type(getattr(pair_cfg, attr))(val))

        # Time stop check (both paper and live modes)
        self._trader.check_time_stop(pair_key, pair_cfg, candle.close)

        # Break-even / trailing stop adjustment (live + paper).
        # Coinbase CDE: prefer live mark from get_product (updated in fill-poll loop) over
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
            for _pos in self._trader.positions_for_pair(pair_key):
                if _pos.status != "open":
                    continue
                leg_ref = trail_ref_price
                if float(getattr(_pos, "mark_price", 0.0) or 0.0) > 0:
                    leg_ref = float(_pos.mark_price)
                asyncio.create_task(
                    self._trader.check_trail_and_breakeven(
                        _pos, pair_cfg, leg_ref, iv.atr,
                    ),
                    name=f"scalp_trail_{pair_key}_{_pos.entry_cl_ord_id[:12]}",
                )

        # Paper mode: check stop/TP on every closed candle
        self._trader.check_paper_exits(pair_key, candle)

        # RSI reversion exit: close position when RSI crosses sell threshold
        eff_mode = self._resolved_active_mode(pair_key)
        if eff_mode == "rsi_reversion" and iv.rsi_sell_trigger:
            self._trader.check_rsi_exit(pair_key, candle.close)

        if eff_mode == "daviddtech_scalp":
            if not iv.optimized_ready or not iv.mode_ready:
                return
        elif not iv.ready or not iv.mode_ready:
            return

        if not self._cfg.enabled:
            return

        if self._state.scalp_entries_blocked():
            return

        # Scalp entries do not require spread-engine START — only OFF/SIM/LIVE on the scalp UI.

        pair_cfg = self._cfg.pairs[pair_key]
        symbol = pair_cfg.symbol
        mode_override = eff_mode
        shorts_enabled = bool(getattr(self._cfg, "shorts_enabled", False))

        for pos in self._trader.positions_for_pair(pair_key):
            if pos.status != "open":
                continue
            counter = self._signal_engine.evaluate_counter(
                pair_key, symbol, pair_cfg, iv,
                current_direction=pos.direction,
                mode_override=mode_override,
                shorts_enabled=shorts_enabled,
            )
            if counter is not None:
                asyncio.create_task(
                    self._counter_exit(counter, pair_cfg, iv, pos),
                    name=f"scalp_counter_{pair_key}_{pos.entry_cl_ord_id[:12]}",
                )

        signal = self._signal_engine.evaluate(
            pair_key,
            symbol,
            pair_cfg,
            iv,
            mode_override=mode_override,
            shorts_enabled=shorts_enabled,
            signal_cooldown_sec=self._effective_signal_cooldown_sec(pair_key, pair_cfg),
        )
        if signal is None:
            return

        if self._operator_standby:
            return

        if bool(getattr(self._cfg, "require_champion_to_trade", False)):
            if self._mode_source.get(pair_key) not in ("wfo_champion", "forward_demotion", "param_tuner_override", "nemesis_tuner"):
                LOG.debug("ScalpRuntime %s: bar entry blocked — no WFO champion yet (source=%s)", pair_key, self._mode_source.get(pair_key))
                return

        if self._trader.has_position(pair_key):
            return  # pending or open position already exists — skip bar-close entry

        # NM-014: prevent dual bar+tick race when tick entries are re-enabled
        if pair_key in self._entry_pending:
            return
        self._entry_pending.add(pair_key)
        # NM-013: record mode at entry so WFO champion switches don't change exit logic mid-trade
        self._pair_entry_mode[pair_key] = self._resolved_active_mode(pair_key)

        _erm = self._volatility_exec_risk_mult(pair_key)
        asyncio.create_task(
            self._open_position(signal, pair_cfg, execution_risk_mult=_erm),
            name=f"scalp_open_{pair_key}",
        )

    # ── Forward validation / auto-demotion ───────────────────────────────────

    def _check_champion_forward_validation(self) -> None:
        """Demote a WFO champion only when a concrete better alternative exists.

        The check has two gates that must BOTH pass before demotion fires:

        Gate 1 — champion is underperforming live:
          Either its forward PnL ratio is below ``wfo_forward_demotion_threshold``
          (relative to holdout expectancy × trades), or its holdout expectancy was
          non-positive (champion was expected to lose from the start).

        Gate 2 — a better replacement is ready (the key gate):
          The ``_strategy_lookback_snapshot`` (refreshed every 60s over the last
          ``strategy_lookback_hours``) must show at least one mode OTHER than the
          current champion that has:
            - positive expectancy in the lookback window
            - profit_factor >= 1.0
            - at least ``EXPECTANCY_MIN_TRADES`` trades in the window
            - expectancy at least ``wfo_forward_outperform_factor`` × the champion's
              live forward expectancy (or simply positive when champion is negative)

        If Gate 2 fails (no better alternative found), the champion is kept — a
        struggling strategy in a bad market is still better than an untested fallback.
        """
        from .strategy_lookback import EXPECTANCY_MIN_TRADES

        min_trades = int(getattr(self._cfg, "wfo_forward_min_trades", 10))
        ratio_threshold = float(getattr(self._cfg, "wfo_forward_demotion_threshold", -0.5))
        outperform_factor = float(self._cfg.wfo_forward_outperform_factor)

        for pair_key, pair_cfg in self._cfg.pairs.items():
            if self._mode_source.get(pair_key) != "wfo_champion":
                continue

            period_start = self._champion_period_start.get(pair_key, 0.0)
            if period_start == 0.0:
                continue

            forward_trades = self._trader.forward_trades_since(pair_key, period_start)
            if forward_trades < min_trades:
                continue

            champ = (self._champion_data or {}).get(pair_cfg.symbol, {})
            holdout_expectancy = float(
                (champ.get("holdout_metrics") or {}).get("expectancy", 0.0)
            )
            forward_pnl = self._trader.forward_pnl_since(pair_key, period_start)
            forward_expectancy = forward_pnl / forward_trades  # live $/trade

            # ── Gate 1: is the champion underperforming? ──────────────────────
            if holdout_expectancy <= 0:
                gate1 = True
                ratio = float("nan")
                expected_pnl = 0.0
            else:
                expected_pnl = holdout_expectancy * forward_trades
                ratio = forward_pnl / expected_pnl
                gate1 = ratio < ratio_threshold

            if not gate1:
                continue

            # ── Gate 2: is there a concrete better alternative? ───────────────
            snap = self._strategy_lookback_snapshot
            pair_snap: dict[str, dict] = {}
            if snap and isinstance(snap.get("pairs"), dict):
                pair_snap = snap["pairs"].get(pair_key) or {}

            current_mode = self._active_mode.get(pair_key, "")
            best_alt_mode: str | None = None
            best_alt_exp: float = -float("inf")

            for mode, metrics in pair_snap.items():
                if mode == current_mode:
                    continue
                alt_exp = float(metrics.get("expectancy", 0.0))
                alt_pf = float(metrics.get("profit_factor", 0.0))
                alt_trades = int(metrics.get("trades", 0))
                if alt_trades < EXPECTANCY_MIN_TRADES:
                    continue
                if alt_pf < 1.0:
                    continue
                if alt_exp <= 0:
                    continue
                # Must beat champion's live forward expectancy by the outperform factor,
                # or simply be positive when champion is already negative.
                if forward_expectancy >= 0 and alt_exp < forward_expectancy * outperform_factor:
                    continue
                if alt_exp > best_alt_exp:
                    best_alt_exp = alt_exp
                    best_alt_mode = mode

            if best_alt_mode is None:
                ratio_str = "n/a" if math.isnan(ratio) else f"{ratio:.2f}"
                LOG.info(
                    "ScalpRuntime %s: champion underperforming (ratio=%s fwd_exp=%.4f/trade)"
                    " but no better alternative found in lookback — keeping champion '%s'.",
                    pair_key, ratio_str, forward_expectancy, current_mode,
                )
                continue

            # Both gates passed — demote and switch to the better alternative.
            ratio_str = "n/a(non-positive holdout)" if math.isnan(ratio) else f"{ratio:.2f}"
            LOG.warning(
                "ScalpRuntime %s: CHAMPION DEMOTED — forward_exp=%.4f/trade ratio=%s"
                " | replacement '%s' lookback_exp=%.4f/trade (trades=%d, pf=%.2f).",
                pair_key, forward_expectancy, ratio_str,
                best_alt_mode, best_alt_exp,
                int(pair_snap.get(best_alt_mode, {}).get("trades", 0)),
                float(pair_snap.get(best_alt_mode, {}).get("profit_factor", 0.0)),
            )
            self._active_mode[pair_key] = best_alt_mode
            self._mode_source[pair_key] = "forward_demotion"
            self._champion_period_start.pop(pair_key, None)
            if self._session_log is not None:
                self._session_log.log_scalp(
                    "champion_forward_demoted",
                    pair_key=pair_key,
                    symbol=pair_cfg.symbol,
                    forward_pnl=round(forward_pnl, 6),
                    forward_expectancy=round(forward_expectancy, 6),
                    ratio=None if math.isnan(ratio) else round(ratio, 4),
                    demotion_reason=(
                        "non_positive_holdout_expectancy"
                        if math.isnan(ratio) else "ratio_below_threshold"
                    ),
                    forward_trades=forward_trades,
                    replacement_mode=best_alt_mode,
                    replacement_lookback_exp=round(best_alt_exp, 6),
                )
            remove_champion_for_symbol(pair_cfg.symbol)

    # ── CDE expiry guard ──────────────────────────────────────────────────────

    _EXPIRY_MONTH_MAP: dict[str, int] = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    _EXPIRY_RE = re.compile(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{1,2})", re.IGNORECASE)

    def _days_to_expiry(self, symbol: str) -> int | None:
        """Parse expiry date from CDE symbol (e.g. BIP-20DEC30-CDE → Dec 30).
        Returns days until expiry, or None if symbol has no parseable expiry date."""
        m = self._EXPIRY_RE.search(symbol.upper())
        if not m:
            return None
        month = self._EXPIRY_MONTH_MAP[m.group(1)]
        day = int(m.group(2))
        today = datetime.date.today()
        expiry = datetime.date(today.year, month, day)
        if expiry < today:
            expiry = datetime.date(today.year + 1, month, day)
        return (expiry - today).days

    async def _open_position(
        self,
        signal,
        pair_cfg,
        *,
        execution_risk_mult: float = 1.0,
    ) -> None:
        """Async wrapper for try_open — called via create_task from sync callback."""
        pair_key = getattr(signal, "pair_key", "")
        try:
            await self._open_position_inner(signal, pair_cfg, execution_risk_mult=execution_risk_mult)
        finally:
            # NM-014: always release the entry-pending guard so future entries are not blocked
            self._entry_pending.discard(pair_key)

    async def _open_position_inner(
        self,
        signal,
        pair_cfg,
        *,
        execution_risk_mult: float = 1.0,
    ) -> None:
        if self._operator_standby:
            LOG.info(
                "ScalpRuntime: entry suppressed — operator standby (%s)",
                getattr(signal, "pair_key", "?"),
            )
            return
        if self._state.scalp_entries_blocked():
            LOG.info(
                "ScalpRuntime: entry suppressed — portfolio risk halt (%s)",
                getattr(signal, "pair_key", "?"),
            )
            return

        # CDE expiry guard
        days = self._days_to_expiry(pair_cfg.symbol)
        if days is not None:
            warn_days = int(getattr(self._cfg, "expiry_guard_warning_days", 7))
            block_days = int(getattr(self._cfg, "expiry_guard_block_days", 3))
            if days <= block_days:
                LOG.warning(
                    "ScalpRuntime: entry BLOCKED — %s expires in %d day(s) (≤%d block threshold)."
                    " Roll contract or disable pair.",
                    pair_cfg.symbol, days, block_days,
                )
                return
            if days <= warn_days:
                LOG.warning(
                    "ScalpRuntime: %s expires in %d day(s) — approaching expiry guard threshold (%d).",
                    pair_cfg.symbol, days, block_days,
                )

        available = self._available_capital()
        await self._trader.try_open(
            signal,
            pair_cfg,
            available,
            execution_risk_mult=execution_risk_mult,
        )

    async def _counter_exit(self, counter_signal, pair_cfg, iv, position) -> None:
        """Async wrapper for check_counter_signal — called via create_task."""
        available = self._available_capital()
        _erm = self._volatility_exec_risk_mult(counter_signal.pair_key)
        # NM-012: reversal entries must pass the same champion gate as bar/tick entries
        pair_key = counter_signal.pair_key
        allow_reversal = True
        if bool(getattr(self._cfg, "require_champion_to_trade", False)):
            if self._mode_source.get(pair_key) not in (
                "wfo_champion", "forward_demotion", "param_tuner_override", "nemesis_tuner"
            ):
                allow_reversal = False
        await self._trader.check_counter_signal(
            pair_key,
            pair_cfg,
            counter_signal,
            iv,
            available,
            execution_risk_mult=_erm,
            position=position,
            allow_reversal=allow_reversal,
        )

    def _available_capital(self) -> float:
        """USD budget for scalp sizing: ``allocated_capital_usd``, optionally capped by exchange buying power."""
        cap = float(self._cfg.allocated_capital_usd)
        if not bool(getattr(self._cfg, "use_exchange_buying_power_cap", False)):
            return cap
        mgr = self._live_mgr
        if mgr is None or not hasattr(mgr, "balance_snapshot"):
            return cap
        stale_sec = float(getattr(self._cfg, "balance_stale_sec", 120.0) or 0.0)
        if stale_sec > 0:
            ts_fn = getattr(mgr, "futures_summary_last_ok_ts", None)
            if callable(ts_fn):
                last = float(ts_fn())
                if last <= 0 or (time.time() - last) > stale_sec:
                    return cap
        try:
            bal = mgr.balance_snapshot()
        except Exception:
            return cap
        fut = bal.get("futures") if isinstance(bal, dict) else None
        if not isinstance(fut, dict):
            return cap
        bp = float(fut.get("buying_power") or 0.0)
        buf = float(getattr(self._cfg, "buying_power_buffer_usd", 0.0) or 0.0)
        eff = max(0.0, bp - buf)
        return min(cap, eff)

    def _volatility_filter_armed(self, pair_key: str) -> bool:
        """True while post-confirm execution window is active for this pair."""
        if not bool(getattr(self._cfg, "volatility_filter_enabled", False)):
            return False
        return time.time() < float(self._vol_filt_armed_until.get(pair_key, 0.0))

    def _volatility_exec_risk_mult(self, pair_key: str) -> float:
        """Position sizing multiplier while volatility filter is armed (else 1.0)."""
        if not self._volatility_filter_armed(pair_key):
            return 1.0
        return max(1.0, float(getattr(self._cfg, "volatility_exec_risk_mult", 1.25)))

    def _effective_signal_cooldown_sec(self, pair_key: str, pair_cfg: ScalpPairConfig) -> float:
        """Bar-close path signal cooldown; scaled down while volatility filter armed."""
        base = float(pair_cfg.signal_cooldown_sec)
        if not self._volatility_filter_armed(pair_key):
            return base
        scale = float(getattr(self._cfg, "volatility_armed_signal_cooldown_scale", 0.5))
        floor = float(getattr(self._cfg, "volatility_armed_cooldown_floor_sec", 1.0))
        return max(floor, base * scale)

    def _effective_tick_signal_cooldown_sec(self, pair_key: str) -> float:
        """Tick-entry throttle; scaled down while volatility filter armed."""
        base = float(getattr(self._cfg, "tick_signal_cooldown_sec", 300.0))
        if not self._volatility_filter_armed(pair_key):
            return base
        scale = float(getattr(self._cfg, "volatility_armed_tick_cooldown_scale", 0.5))
        floor = float(getattr(self._cfg, "volatility_armed_cooldown_floor_sec", 1.0))
        return max(floor, base * scale)

    def _volatility_filter_snapshot(self) -> dict:
        if not bool(getattr(self._cfg, "volatility_filter_enabled", False)):
            return {"enabled": False, "pairs": {}}
        cfg = self._cfg
        now = time.time()
        pairs: dict[str, dict] = {}
        for pk in self._cfg.pairs:
            au = float(self._vol_filt_armed_until.get(pk, 0.0))
            armed = au > now
            pc = self._cfg.pairs.get(pk)
            entry: dict = {
                "armed": armed,
                "hold_remaining_sec": round(max(0.0, au - now), 1) if armed else 0.0,
                "pending_confirm": pk in self._vol_filt_pending,
                "last_event": self._vol_filt_last_event.get(pk, ""),
            }
            if armed and pc is not None:
                entry["effective_signal_cooldown_sec"] = round(
                    self._effective_signal_cooldown_sec(pk, pc), 2
                )
                entry["effective_tick_cooldown_sec"] = round(
                    self._effective_tick_signal_cooldown_sec(pk), 2
                )
            pairs[pk] = entry
        return {
            "enabled": True,
            "spike_volume_mult": float(getattr(cfg, "volatility_spike_volume_mult", 4.0)),
            "confirm_volume_mult": float(getattr(cfg, "volatility_confirm_min_volume_mult", 1.15)),
            "confirm_follow_atr_mult": float(getattr(cfg, "volatility_confirm_follow_atr_mult", 0.35)),
            "exec_risk_mult": float(getattr(cfg, "volatility_exec_risk_mult", 1.25)),
            "exec_hold_sec": float(getattr(cfg, "volatility_exec_hold_sec", 1800.0)),
            "armed_tick_cooldown_scale": float(
                getattr(cfg, "volatility_armed_tick_cooldown_scale", 0.5)
            ),
            "armed_signal_cooldown_scale": float(
                getattr(cfg, "volatility_armed_signal_cooldown_scale", 0.5)
            ),
            "armed_cooldown_floor_sec": float(
                getattr(cfg, "volatility_armed_cooldown_floor_sec", 1.0)
            ),
            "pairs": pairs,
        }

    def _update_volatility_filter(self, pair_key: str, candle: Candle, iv: IndicatorValues) -> None:
        from .volatility_filter import (
            climax_reject_spike_bar,
            confirm_spike,
            eligible_spike_prime,
        )

        cfg = self._cfg
        if not bool(getattr(cfg, "volatility_filter_enabled", False)):
            return

        now = time.time()
        au = float(self._vol_filt_armed_until.get(pair_key, 0.0))
        if au > 0 and now >= au:
            self._vol_filt_armed_until.pop(pair_key, None)
            self._vol_filt_last_event[pair_key] = "expired"

        if self._vol_filt_armed_until.get(pair_key, 0.0) > now:
            return

        pending = self._vol_filt_pending.pop(pair_key, None)
        if pending is not None:
            ok = confirm_spike(
                pending,
                candle,
                iv,
                confirm_vol_mult=float(getattr(cfg, "volatility_confirm_min_volume_mult", 1.15)),
                follow_atr_mult=float(getattr(cfg, "volatility_confirm_follow_atr_mult", 0.35)),
            )
            if ok:
                hold = float(getattr(cfg, "volatility_exec_hold_sec", 1800.0))
                self._vol_filt_armed_until[pair_key] = now + hold
                self._vol_filt_last_event[pair_key] = "armed"
                LOG.info(
                    "ScalpRuntime %s: volatility filter CONFIRMED — execution risk-on ×%.2f for %.0fs",
                    pair_key,
                    float(getattr(cfg, "volatility_exec_risk_mult", 1.25)),
                    hold,
                )
                return
            self._vol_filt_last_event[pair_key] = "confirm_failed"
            LOG.info(
                "ScalpRuntime %s: volatility filter confirm failed (need sustained vol or ATR follow-through)",
                pair_key,
            )

        if self._vol_filt_armed_until.get(pair_key, 0.0) > now:
            return

        spike_mult = float(getattr(cfg, "volatility_spike_volume_mult", 4.0))
        if not eligible_spike_prime(iv, candle, spike_mult):
            return

        if bool(getattr(cfg, "volatility_reject_bearish_climax", True)) or bool(
            getattr(cfg, "volatility_reject_bullish_exhaust", False)
        ):
            if climax_reject_spike_bar(
                candle,
                bearish_frac=float(getattr(cfg, "volatility_climax_bearish_range_frac", 0.22)),
                bullish_exhaust_frac=float(
                    getattr(cfg, "volatility_climax_bullish_exhaust_frac", 0.88)
                ),
                reject_bullish_exhaust=bool(getattr(cfg, "volatility_reject_bullish_exhaust", False)),
            ):
                self._vol_filt_last_event[pair_key] = "climax_reject"
                LOG.info(
                    "ScalpRuntime %s: volatility filter prime skipped (climax / one-print washout)",
                    pair_key,
                )
                return

        self._vol_filt_pending[pair_key] = Candle(
            timestamp=float(candle.timestamp),
            open=float(candle.open),
            high=float(candle.high),
            low=float(candle.low),
            close=float(candle.close),
            volume=float(candle.volume),
            vwap=float(candle.vwap),
            trades=int(candle.trades),
        )
        self._vol_filt_last_event[pair_key] = "pending_confirm"
        LOG.info(
            "ScalpRuntime %s: volatility filter PRIMED (spike ≥ %.2f× vol MA) — next bar must confirm",
            pair_key,
            spike_mult,
        )

    # ── Warmup logic ────────────────────────────────────────────────────────────

    def _check_warmup_bar_threshold_async(self) -> None:
        """Non-blocking wrapper: kicks off WFO in a background thread so the event loop stays free."""
        if self._warmup_phase not in (WarmupPhase.COLLECTING, WarmupPhase.OPTIMIZING):
            return
        if self._warmup_wfo_triggered:
            return  # already running or done

        min_bars = self._cfg.warmup_min_bars
        all_met = all(
            v >= min_bars for v in self._warmup_bars_collected.values()
        )
        if not all_met:
            return
        if self._operator_standby:
            return  # WFO gated — waiting for operator go-live

        # Auto-advance startup phase so the UI reflects that WFO is running
        if self._startup_phase == StartupPhase.STANDBY:
            self._startup_phase = StartupPhase.WARMING_UP
            self._warmup_start_ts = time.time()
            self._warmup_requested.set()
        self._warmup_phase = WarmupPhase.OPTIMIZING
        self._warmup_wfo_triggered = True
        LOG.info(
            "ScalpRuntime: warmup bar threshold met (%d bars) — running WFO in background thread",
            min_bars,
        )
        # Run WFO off the event loop so the UI stays responsive
        asyncio.ensure_future(self._run_warmup_wfo_in_thread())

    async def _run_warmup_wfo_in_thread(self) -> None:
        """Run the initial WFO pass in a thread, then finalize warmup on the event loop."""
        try:
            results = await asyncio.to_thread(self._wfo.run_once)
            for pk, r in results.items():
                if r is not None:
                    self._warmup_champion_found = True
                    LOG.info("ScalpRuntime: warmup WFO found champion for %s", pk)
                self._handle_wfo_result_pair(pk, r)
            self._startup_wfo_succeeded = True
        except Exception:
            LOG.exception("ScalpRuntime: warmup WFO pass failed")
            self._startup_wfo_succeeded = False
        self._startup_wfo_done = True
        self._check_warmup_complete()
        # Start the recurring WFO loop now that first pass is done
        self._wfo.start()

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
            # Deadlock fix: require_champion means "wait for WFO to validate the tape", not
            # "block forever if no mode passes gates". After a successful startup WFO with
            # zero champions, bootstrap is already applied — allow READY. If WFO is off,
            # bars + defaults are the validation surface. Only keep blocking when WFO is on
            # but the startup grid run failed (exceptions / retries exhausted).
            if self._cfg.wfo_enabled and not self._startup_wfo_succeeded:
                return False

        # All conditions met
        self._warmup_phase = WarmupPhase.READY
        if not self._operator_standby:
            self._state.running = True
            # Auto-advance startup phase to LIVE when no manual go-live is required
            if self._startup_phase in (StartupPhase.STANDBY, StartupPhase.WARMING_UP, StartupPhase.PRIMED):
                self._startup_phase = StartupPhase.LIVE
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

    def _scalp_wfo_roll_hours(self) -> float:
        return wfo_roll_span_hours(
            self._cfg.wfo_train_hours,
            self._cfg.wfo_holdout_hours,
            self._cfg.wfo_step_hours,
            max(1, int(self._cfg.wfo_max_roll_windows)),
        )

    def _scalp_wfo_backfill_hours(self) -> float:
        buf = float(getattr(self._cfg, "wfo_backfill_buffer_hours", 24.0) or 0.0)
        return self._scalp_wfo_roll_hours() + max(0.0, buf)

    def _verify_wfo_backfill_span(self, pair_key: str, pair_cfg: ScalpPairConfig) -> None:
        roll_h = self._scalp_wfo_roll_hours()
        span_h, ok = wfo_verify_stored_roll_coverage(pair_cfg.symbol, pair_cfg.interval, roll_h)
        if ok:
            return
        need = roll_h * 0.92
        LOG.error(
            "ScalpRuntime: WFO stored span shortfall %s (%s): span_h=%.1fh < %.1fh (92%% of roll %.1fh)",
            pair_key, pair_cfg.symbol, span_h, need, roll_h,
        )
        try:
            self._state.push_alert(
                "error",
                "WFO history shortfall",
                f"{pair_key} ({pair_cfg.symbol}): stored span {span_h:.1f}h < {need:.1f}h — check backfill.",
                "scalp",
            )
        except Exception:
            LOG.debug("push_alert span shortfall failed", exc_info=True)

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
            self._startup_wfo_succeeded = False
            self._warmup_champion_found = False
            self._warmup_requested.clear()

        # Clear stale champion so WFO always runs fresh — never inherit prior-session params
        try:
            CHAMPION_PATH.unlink(missing_ok=True)
            self._champion_data = None
            self._champion_mtime = 0.0
            LOG.info("ScalpRuntime: cleared stale champion file — WFO will select fresh champion")
        except OSError as exc:
            LOG.warning("ScalpRuntime: could not clear champion file: %s", exc)

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
        # If a prior build left warmup stuck in COLLECTING after PRIMED/LIVE, re-check gates.
        if self._warmup_phase in (WarmupPhase.COLLECTING, WarmupPhase.OPTIMIZING):
            self._check_warmup_complete()
        self._state.push_alert(
            "success",
            "Scalp: Go live",
            "Standby cleared — new entries allowed when signals and gates permit.",
            "scalp_operator",
        )
        LOG.info("ScalpRuntime: operator_go_live — entries armed, phase=LIVE")

    def _on_daily_loss_breach(self) -> None:
        """Operator-config policy when realized daily PnL crosses the loss limit (one-shot per UTC day)."""
        cfg = self._cfg
        if bool(getattr(cfg, "daily_loss_set_scalp_halt", True)):
            self.set_scalp_risk_halt("daily_loss_limit", "daily_loss_policy")
        enter_sb = bool(getattr(cfg, "daily_loss_enter_standby", False))
        cancel_open = bool(getattr(cfg, "daily_loss_cancel_open_orders", False))
        lim = float(cfg.allocated_capital_usd) * (float(cfg.daily_loss_limit_pct) / 100.0)
        if not enter_sb and not cancel_open:
            if self._session_log is not None:
                self._session_log.log_scalp(
                    "daily_loss_policy",
                    enter_standby=False,
                    cancel_open_orders=False,
                    scalp_halt_only=True,
                    daily_pnl=round(self._trader.daily_pnl, 6),
                    limit_usd=round(lim, 6),
                )
            return
        if self._session_log is not None:
            self._session_log.log_scalp(
                "daily_loss_policy",
                enter_standby=enter_sb,
                cancel_open_orders=cancel_open,
                daily_pnl=round(self._trader.daily_pnl, 6),
                limit_usd=round(lim, 6),
            )
        if enter_sb:
            self.operator_enter_standby()
            asyncio.create_task(
                self.operator_flow_standby_ui(),
                name="scalp_daily_loss_standby_ui",
            )
        if cancel_open:
            asyncio.create_task(
                self._run_daily_loss_cancels(),
                name="daily_loss_cancel_orders",
            )

    async def _run_daily_loss_cancels(self) -> None:
        mgr = self._live_mgr
        fn = getattr(mgr, "cancel_all_scalp_open_orders", None)
        if not callable(fn):
            return
        try:
            n = await fn()
            LOG.info("ScalpRuntime: daily_loss_policy — cancelled %s open order(s)", n)
        except Exception:
            LOG.exception("ScalpRuntime: daily_loss cancel_all_scalp_open_orders failed")

    def set_scalp_risk_halt(self, reason: str, source: str) -> None:
        """Block new scalp entries until ``clear_scalp_risk_halt`` (protectives/exits continue)."""
        was = self._state.scalp_risk_halted
        self._state.scalp_risk_halted = True
        self._state.scalp_risk_halt_reason = (reason or "halt").strip()[:240]
        if not was:
            self._state.scalp_risk_halted_ts = time.time()
        if self._session_log is not None:
            self._session_log.log_scalp(
                "scalp_risk_halt",
                reason=self._state.scalp_risk_halt_reason,
                source=str(source)[:120],
                was_already_halted=was,
            )
        if not was:
            self._state.push_alert(
                "warning",
                "Scalp portfolio halt",
                f"New entries blocked ({self._state.scalp_risk_halt_reason}). "
                "Open positions still manage stops/TP. Use scalp_risk_resume to clear.",
                "scalp_risk",
            )
        LOG.info(
            "ScalpRuntime: scalp_risk_halt set reason=%s source=%s",
            self._state.scalp_risk_halt_reason, source,
        )

    def clear_scalp_risk_halt(self, source: str) -> None:
        """Allow new scalp entries again (does not change operator standby)."""
        if not self._state.scalp_risk_halted:
            return
        self._state.scalp_risk_halted = False
        self._state.scalp_risk_halt_reason = ""
        self._state.scalp_risk_halted_ts = 0.0
        if self._session_log is not None:
            self._session_log.log_scalp("scalp_risk_resume", source=str(source)[:120])
        self._state.push_alert(
            "success",
            "Scalp portfolio halt cleared",
            f"Resume requested by {source}. New entries allowed when gates permit.",
            "scalp_risk",
        )
        LOG.info("ScalpRuntime: scalp_risk_halt cleared source=%s", source)

    async def emergency_flatten_all_positions(self, reason: str, *, source: str) -> int:
        """Halt new entries, cancel resting scalp orders, market-close all legs (reduce-only on perps).

        Returns the number of market exit orders submitted (0 if none / sim-only).
        """
        return await self._flatten_all_legs_core(reason, source, mode="emergency")

    async def manual_close_all_open_positions(self, reason: str, *, source: str) -> int:
        """Cancel protectives and submit reduce-only market exits for open legs.

        Does **not** portfolio-halt entries or enter operator standby — normal gates resume.
        """
        return await self._flatten_all_legs_core(reason, source, mode="manual")

    async def manual_cancel_all_open_orders(self, reason: str, *, source: str) -> int:
        """Best-effort cancel of resting scalp venue orders only (no flatten, no halt, no standby)."""
        r = (reason or "operator_manual_cancel").strip()[:240]
        mgr = self._live_mgr
        sim = bool(getattr(self._cfg, "sim_mode", False)) or mgr is None
        n = 0
        if not sim:
            cancel_fn = getattr(mgr, "cancel_all_scalp_open_orders", None)
            if callable(cancel_fn):
                try:
                    n = int(await cancel_fn())
                except Exception:
                    LOG.exception("ScalpRuntime: manual_cancel_all_open_orders failed")
        if self._session_log is not None:
            self._session_log.log_scalp(
                "scalp_operator_manual_cancel_orders",
                reason=r,
                source=str(source)[:120],
                simulated=sim,
                cancelled_orders=n,
            )
        self._state.push_alert(
            "info",
            "Manual: cancel resting orders",
            (
                "SIM: no venue cancels; session logged."
                if sim
                else f"Best-effort cancel on resting scalp orders (returned count={n})."
            )
            + f" reason={r}",
            "scalp_operator",
        )
        LOG.info(
            "ScalpRuntime: manual_cancel_all_open_orders n=%d sim=%s reason=%s",
            n, sim, r[:80],
        )
        return n

    async def _flatten_all_legs_core(
        self,
        reason: str,
        source: str,
        *,
        mode: Literal["emergency", "manual"],
    ) -> int:
        is_emergency = mode == "emergency"
        default_r = "emergency_flatten" if is_emergency else "operator_manual_close"
        r = (reason or default_r).strip()[:240]
        if is_emergency:
            self.set_scalp_risk_halt(r, source)
        close_reason = "emergency_flatten" if is_emergency else "user_manual_close"
        session_event = "scalp_emergency_flatten" if is_emergency else "scalp_operator_manual_close"
        prefix = "scalp_eflat_" if is_emergency else "scalp_mclose_"
        log_tag = "emergency_flatten" if is_emergency else "manual_close"
        n_markets = 0
        mgr = self._live_mgr
        sim = bool(getattr(self._cfg, "sim_mode", False)) or mgr is None

        if sim:
            for pk in list(self._cfg.pairs.keys()):
                for pos in list(self._trader.positions_for_pair(pk)):
                    if pos.status == "pending":
                        self._trader._release_reserved_for_position(pos)
                        try:
                            del self._trader._positions[pos.entry_cl_ord_id]
                        except KeyError:
                            pass
                    elif pos.status == "open":
                        mult = float(pos.contract_size or 1.0) if self._cfg.venue == "coinbase_perps" else 1.0
                        close_px = float(pos.entry_price)
                        if pos.direction == "long":
                            pnl = (close_px - pos.entry_price) * pos.qty * mult
                        else:
                            pnl = (pos.entry_price - close_px) * pos.qty * mult
                        self._trader._close_position(pos, pnl, close_reason, close_px)
            if self._session_log is not None:
                self._session_log.log_scalp(
                    session_event,
                    reason=r,
                    source=str(source)[:120],
                    simulated=True,
                    market_orders=0,
                )
            if is_emergency:
                self._state.push_alert(
                    "warning",
                    "Scalp emergency flatten (sim)",
                    "SIM: positions cleared locally; no venue orders sent.",
                    "scalp_risk",
                )
            else:
                self._state.push_alert(
                    "info",
                    "Manual close all legs (sim)",
                    "SIM: positions cleared locally (close_reason=user_manual_close); no venue orders.",
                    "scalp_operator",
                )
            return 0

        cancel_fn = getattr(mgr, "cancel_all_scalp_open_orders", None)
        if callable(cancel_fn):
            try:
                await cancel_fn()
            except Exception:
                LOG.exception("ScalpRuntime: %s cancel_all_scalp_open_orders failed", log_tag)

        for _pk in list(self._cfg.pairs.keys()):
            for pos in list(self._trader.positions_for_pair(_pk)):
                if pos.status == "pending":
                    try:
                        await mgr.cancel_order(pos.entry_cl_ord_id)
                    except Exception:
                        LOG.debug("%s: cancel pending entry failed", log_tag, exc_info=True)
                    self._trader._release_reserved_for_position(pos)
                    try:
                        del self._trader._positions[pos.entry_cl_ord_id]
                    except KeyError:
                        pass
                    continue
                if pos.status != "open":
                    continue
                for oid in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
                    if oid:
                        try:
                            await mgr.cancel_order(oid)
                        except Exception:
                            LOG.debug("%s: cancel protective failed", log_tag, exc_info=True)
                close_side = "sell" if pos.direction == "long" else "buy"
                close_id = f"{prefix}{uuid.uuid4().hex[:8]}"
                self._trader._link_market_exit_order(close_id, pos.entry_cl_ord_id)
                self._trader.register_pending_market_exit(
                    close_id, pos, close_reason, float(pos.entry_price),
                )
                oq = max(1, int(round(pos.qty))) if str(self._cfg.venue).lower() == "coinbase_perps" else round(
                    pos.qty, 8,
                )
                try:
                    flat_fn = getattr(mgr, "flatten_scalp_leg_market", None)
                    if callable(flat_fn):
                        res = await flat_fn(
                            symbol=pos.symbol,
                            side=close_side,
                            order_qty=float(oq),
                            cl_ord_id=close_id,
                            reduce_only=True,
                        )
                    else:
                        res = await mgr.add_order(
                            {
                                "symbol": pos.symbol,
                                "side": close_side,
                                "order_type": "market",
                                "order_qty": oq,
                                "cl_ord_id": close_id,
                                "reduce_only": True,
                            },
                        )
                    if res:
                        n_markets += 1
                except Exception:
                    LOG.exception("ScalpRuntime: %s market order failed for %s", log_tag, pos.pair_key)
                    continue
                mult = float(pos.contract_size or 1.0) if self._cfg.venue == "coinbase_perps" else 1.0
                close_px = float(pos.entry_price)
                if pos.direction == "long":
                    pnl = (close_px - pos.entry_price) * pos.qty * mult
                else:
                    pnl = (pos.entry_price - close_px) * pos.qty * mult
                self._trader._close_position(pos, pnl, close_reason, close_px)

        if self._session_log is not None:
            self._session_log.log_scalp(
                session_event,
                reason=r,
                source=str(source)[:120],
                simulated=False,
                market_orders=n_markets,
            )
        if is_emergency:
            self._state.push_alert(
                "warning",
                "Scalp emergency flatten",
                f"Submitted {n_markets} reduce-only market exit(s). reason={r}",
                "scalp_risk",
            )
        else:
            self._state.push_alert(
                "info",
                "Manual close: market exits",
                f"Submitted {n_markets} reduce-only market exit(s). Entries not halted. reason={r}",
                "scalp_operator",
            )
        return n_markets

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
                            for pk, r in results.items():
                                if r is not None:
                                    self._warmup_champion_found = True
                                self._handle_wfo_result_pair(pk, r)
                            self._startup_wfo_succeeded = True
                        except Exception:
                            LOG.exception("ScalpRuntime: prep WFO failed (warmup disabled)")
                            self._startup_wfo_succeeded = False
                    else:
                        self._startup_wfo_succeeded = True
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
            self._startup_wfo_succeeded = False

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
                        for pk, r in results.items():
                            if r is not None:
                                self._warmup_champion_found = True
                            self._handle_wfo_result_pair(pk, r)
                        self._startup_wfo_succeeded = True
                    except Exception:
                        LOG.exception("ScalpRuntime: prep WFO pass failed")
                        self._startup_wfo_succeeded = False
                else:
                    self._startup_wfo_succeeded = True
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
            # Avoid trading stale WFO state after file clear / parse failure / empty map.
            if self._champion_data:
                LOG.warning(
                    "ScalpRuntime: champion file has no rows (missing, empty, or invalid) — "
                    "clearing in-memory champion map; re-save champion or restart if unintended.",
                )
                self._champion_data = {}
                self._champion_apply_sig.clear()
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
            # Staleness-demotion guard: skip re-applying the old champion file entry until a
            # fresh WFO promotion clears this symbol (via _handle_wfo_result_pair on success).
            if pair_cfg.symbol in self._wfo_staleness_demoted:
                continue
            if not champion_row_matches_pair_interval(entry, pair_cfg.interval):
                LOG.warning(
                    "ScalpRuntime %s: skip champion apply — row interval %r != pair %sm",
                    pair_key,
                    entry.get("interval"),
                    pair_cfg.interval,
                )
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
            new_mode = str(entry.get("mode", params.get("mode", ""))).strip()
            if new_mode == "auto":
                new_mode = normalize_auto_mode_fallback(
                    getattr(pair_cfg, "auto_mode_fallback", None)
                    or getattr(self._cfg, "auto_mode_fallback", "sar_chop")
                )

            # If a position is open for this pair, queue the champion and defer the
            # mode/param switch until the pair is flat (checked on every bar-close).
            if new_mode and new_mode != self._active_mode.get(pair_key) and self._trader.has_position(pair_key):
                self._pending_champion[pair_key] = entry
                LOG.info(
                    "ScalpRuntime %s: WFO champion %s queued — position open, will apply when flat",
                    pair_key, new_mode,
                )
                continue

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
            if pair_has_wfo_champion(champ, pc.symbol, pc.interval):
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

    def _on_wfo_loop_results(self, results: "dict[str, dict | None]") -> None:
        """Callback fired by ScalpWFO._loop after each live pass (initial and scheduled).

        Called from the WFO background thread via the event loop; the callback itself is
        synchronous and must not await. Delegates per-pair handling to _handle_wfo_result_pair.
        """
        for pk, r in results.items():
            self._handle_wfo_result_pair(pk, r)

    def _handle_wfo_result_pair(self, pair_key: str, result: dict | None) -> None:
        """Update no_candidates streak and apply staleness demotion when threshold is hit.

        Called once per pair after every WFO ``run_once`` pass (warmup, prep, and live loop).
        On a fresh champion (result is not None) the streak resets and any staleness-demotion
        flag is cleared. On no_candidates the streak increments; if it reaches
        ``wfo_no_candidates_demotion_passes`` and the pair is currently running as
        ``wfo_champion``, the pair is demoted to bootstrap so it can keep trading while WFO
        continues searching.
        """
        threshold = int(getattr(self._cfg, "wfo_no_candidates_demotion_passes", 0))

        if result is not None:
            # Fresh champion promoted — reset streak and lift staleness block
            self._wfo_no_candidates_streak[pair_key] = 0
            pc = self._cfg.pairs.get(pair_key)
            if pc:
                self._wfo_staleness_demoted.discard(pc.symbol)
            return

        # No champion this pass — increment streak
        streak = self._wfo_no_candidates_streak.get(pair_key, 0) + 1
        self._wfo_no_candidates_streak[pair_key] = streak

        if threshold <= 0 or streak < threshold:
            return
        if self._mode_source.get(pair_key) != "wfo_champion":
            return  # already on bootstrap or other non-champion source — nothing to demote

        # Streak hit threshold: demote wfo_champion to bootstrap
        pc = self._cfg.pairs.get(pair_key)
        if pc is None:
            return
        self._wfo_staleness_demoted.add(pc.symbol)
        try:
            boot_mode = best_mode_bootstrap_no_champion(
                pc, self._cfg, lookback_hours=self._effective_bootstrap_hours(),
            )
        except Exception:
            boot_mode = str(getattr(self._cfg, "auto_mode_fallback", "sar_chop"))
        LOG.warning(
            "ScalpRuntime %s: WFO returned no_candidates for %d consecutive passes — "
            "demoting wfo_champion to bootstrap mode=%s",
            pair_key, streak, boot_mode,
        )
        self._active_mode[pair_key] = boot_mode
        self._mode_source[pair_key] = "bootstrap"
        self._wfo_no_candidates_streak[pair_key] = 0

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
                # vwap= field is display-only diagnostic; it is NOT an entry gate for any strategy
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

    # ── Fill routing — call these from LiveOrderManager.on_message ────────────

    async def on_entry_fill_authoritative(
        self,
        pair_key: str,
        fill_price: float,
        fill_qty: float,
        *,
        entry_cl_ord_id: str | None = None,
    ) -> None:
        """Apply entry from exchange order snapshot (average fill / filled size) — not fill-stream partials."""
        if entry_cl_ord_id:
            pos = self._trader.position_by_entry(entry_cl_ord_id)
        else:
            pend = [p for p in self._trader.positions_for_pair(pair_key) if p.status == "pending"]
            pos = pend[0] if len(pend) == 1 else None
        if pos is None or pos.status != "pending":
            return
        pos.entry_fill_cost = 0.0
        pos.entry_fill_qty = 0.0
        await self._trader.on_entry_filled(pos.entry_cl_ord_id, fill_price, fill_qty)

    async def on_fill(
        self,
        pair_key: str,
        cl_ord_id: str,
        fill_price: float,
        fill_qty: float,
        *,
        fee_usd: float | None = None,
    ) -> None:
        """Route a fill event to the appropriate position handler."""
        pos = self._trader.position_for_client_order(pair_key, cl_ord_id)
        market_exit = cl_ord_id.startswith(
            ("scalp_tstop_", "scalp_rsi_", "scalp_ctr_", "scalp_eflat_", "scalp_mclose_", "scalp_prot_"),
        )
        if pos is None and market_exit:
            eid = self._trader.take_market_exit_entry_link(cl_ord_id)
            if eid:
                pos = self._trader.position_by_entry(eid)
        if pos is None or pos.pair_key != pair_key:
            if market_exit:
                LOG.debug(
                    "ScalpRuntime on_fill: market exit with no tracked leg pair=%s id=%s",
                    pair_key, cl_ord_id[:28],
                )
            else:
                LOG.warning(
                    "ScalpRuntime on_fill: no position for pair=%s id=%s (orphan/late fill?)",
                    pair_key, cl_ord_id[:28],
                )
            return

        try:
            mult = float(pos.contract_size or 1.0)
            if str(getattr(self._cfg, "venue", "")).lower() == "coinbase_perps":
                self._fee_tier_note_fill_leg_usd(
                    abs(float(fill_price) * float(fill_qty) * mult),
                )
            else:
                self._fee_tier_note_fill_leg_usd(abs(float(fill_price) * float(fill_qty)))
        except Exception:
            pass

        if cl_ord_id == pos.entry_cl_ord_id:
            if pos.status != "pending":
                return
            if fee_usd is not None and float(fee_usd) != 0.0:
                pos.entry_fill_fee_usd += float(fee_usd)
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
            await self._trader.on_entry_filled(pos.entry_cl_ord_id, vwap, pos.entry_fill_qty)
        elif cl_ord_id in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
            await self._trader.on_exit_filled(
                pair_key, cl_ord_id, fill_price, fee_usd=fee_usd,
            )
        elif cl_ord_id.startswith(
            ("scalp_tstop_", "scalp_rsi_", "scalp_ctr_", "scalp_eflat_", "scalp_mclose_", "scalp_prot_"),
        ):
            self._trader.on_market_exit_fill(
                cl_ord_id, fill_price, fill_qty, fee_usd=fee_usd,
            )
            # Time/RSI/counter exit uses a separate market client id; position may already be closed
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
