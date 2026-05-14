"""Self-tuning parameter optimizer — continuously adjusts individual indicator
params based on their measured effect on backtest win rate and profit.

**WFO vs tuner:** Walk-forward optimization (``scalp_wfo.py``) picks the **overall**
strategy mode and champion parameters from a broad grid. This tuner **refines**
settings for the active mode via local one-parameter perturbations. When the
on-disk champion's ``symbol`` matches a pair, the runtime keeps the tuner from
switching mode (WFO stays authoritative); the tuner still nudges tunables for
the selected mode when not frozen. With ``param_tuner_require_wfo_champion`` (default
True), the tuner does not run until a champion exists for the symbol, then it
scores only the pair's active mode unless mode override is enabled.

Design principles:
  - Perturbs ONE parameter at a time (scientific method — isolate variables)
  - Measures win rate delta via vector backtest on stored bars
  - Adjustment aggressiveness scales with current win rate:
      ≥ 80% → frozen (don't touch what's working)
      50-80% → slow tuning (small perturbations, long intervals)
      20-50% → moderate tuning
      < 20% → aggressive tuning (bigger steps, shorter intervals)
  - Each strategy mode has its own tunable parameter set
  - Picks the best mode across registered strategies by **expectancy** (min trade count),
    then profit factor / win rate / PnL as tie-breakers (see ``run_tuner_cycle``).

The tuner is complementary to the WFO grid search: WFO does broad exploration
across the full parameter grid, while the tuner does fine-grained local search
around the current operating point.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from . import bar_store
from .scalp_config import wfo_fee_bps_per_leg
from .scalp_vec_backtest import ParamSet, apply_param_dict_overrides, evaluate_params
from .strategy_lookback import EXPECTANCY_MIN_TRADES

if TYPE_CHECKING:
    from .scalp_config import ScalpBotConfig, ScalpPairConfig
    from .scalp_vec_backtest import BacktestMetrics

LOG = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
TUNER_STATE_PATH = DATA_DIR / "scalp_tuner_state.json"

STRATEGY_MODES = (
    "daviddtech_scalp", "ema_momentum", "rsi_reversion", "ema_scalp",
    "supertrend", "squeeze_momentum", "qqe_mod", "utbot_alert", "hull_suite",
    "macd_scalp", "sar_chop",
)


def champion_tuner_mode_resolution(
    *,
    wfo_champion_active: bool,
    allow_mode_override_champion: bool,
    current_active_mode: str,
    tuner_best_mode: str,
) -> tuple[str, str]:
    """When WFO champion locks the pair, decide effective mode before ``apply_tuner_result``.

    Returns ``(mode_to_use, reason_tag)``. ``mode_to_use`` is ``tuner_best_mode`` only when
    override is enabled and differs from current; otherwise ``current_active_mode``.
    """
    cur = str(current_active_mode or "").strip()
    best = str(tuner_best_mode or "").strip()
    if not wfo_champion_active:
        return cur, "no_champion"
    if not allow_mode_override_champion:
        return cur, "champion_lock"
    if not best or cur == best:
        return cur, "champion_lock_aligned"
    return best, "override_champion"

# Which params are tunable per strategy mode, with (min, max, step) ranges.
# Keep aligned with ``ParamSet`` + ``evaluate_params`` / ``detect_signals_*`` for that mode.
TUNABLE_PARAMS: dict[str, list[tuple[str, float, float, float]]] = {
    "daviddtech_scalp": [
        ("t3_length", 3, 15, 2),
        ("t3_vfactor", 0.35, 0.95, 0.05),
        ("adx_threshold", 15.0, 30.0, 2.5),
        ("hlc_close_period", 3, 8, 1),
        ("hlc_low_period", 8, 21, 2),
        ("hlc_high_period", 21, 55, 5),
        ("wae_sensitivity", 100.0, 250.0, 25.0),
        ("wae_fast_len", 12, 28, 2),
        ("wae_slow_len", 28, 55, 3),
        ("wae_bb_len", 14, 28, 2),
        ("wae_bb_mult", 1.5, 3.0, 0.25),
        ("atr_period", 7, 28, 2),
        ("atr_stop_mult", 1.5, 5.0, 0.5),
        ("atr_tp_mult", 1.5, 5.0, 0.5),
        ("max_hold_bars", 5, 40, 5),
        ("adx_period", 7, 28, 2),
    ],
    # ema_momentum is MA-cross-only in vec + signal_engine: tune cross periods, ATR
    # length, risk multiples, and hold — not rsi/vol/min_signals (no effect on entries).
    "ema_momentum": [
        ("atr_stop_mult", 0.75, 4.0, 0.25),
        ("atr_tp_mult", 1.5, 5.0, 0.25),
        ("max_hold_bars", 8, 32, 2),
        ("ema_fast", 5, 15, 1),
        ("ema_slow", 12, 34, 1),
        ("atr_period", 10, 21, 1),
    ],
    "rsi_reversion": [
        ("atr_stop_mult", 0.5, 3.0, 0.25),
        ("atr_tp_mult", 1.5, 10.0, 0.5),
        ("max_hold_bars", 5, 40, 5),
        ("rsi_period", 5, 25, 2),
        ("atr_period", 7, 28, 2),
        ("rsi_buy_threshold", 5.0, 35.0, 2.5),
        ("rsi_sell_threshold", 40.0, 70.0, 5.0),
        ("rsi_short_threshold", 60.0, 88.0, 2.0),
    ],
    "ema_scalp": [
        ("atr_stop_mult", 0.5, 3.0, 0.25),
        ("atr_tp_mult", 1.5, 10.0, 0.5),
        ("max_hold_bars", 5, 40, 5),
        ("ema_scalp_period", 8, 40, 2),
        ("ema_scalp_sr_bars", 4, 16, 2),
        ("atr_period", 7, 28, 2),
    ],
    "macd_scalp": [
        ("atr_stop_mult", 0.5, 3.0, 0.25),
        ("atr_tp_mult", 1.5, 10.0, 0.5),
        ("max_hold_bars", 5, 40, 5),
        ("macd_fast_len", 3, 15, 1),
        ("macd_slow_len", 6, 30, 2),
        ("macd_signal_len", 3, 15, 2),
        ("atr_period", 7, 28, 2),
    ],
    "supertrend": [
        ("atr_stop_mult", 0.5, 3.0, 0.25),
        ("atr_tp_mult", 1.5, 10.0, 0.5),
        ("max_hold_bars", 5, 40, 5),
        ("supertrend_period", 5, 20, 2),
        ("supertrend_factor", 1.5, 5.0, 0.5),
        ("atr_period", 7, 28, 2),
    ],
    "squeeze_momentum": [
        ("atr_stop_mult", 0.5, 3.0, 0.25),
        ("atr_tp_mult", 1.5, 10.0, 0.5),
        ("max_hold_bars", 5, 40, 5),
        ("squeeze_bb_period", 10, 30, 2),
        ("squeeze_bb_mult", 1.5, 3.0, 0.25),
        ("squeeze_kc_mult", 1.0, 2.5, 0.25),
        ("squeeze_mom_period", 6, 20, 2),
        ("atr_period", 7, 28, 2),
    ],
    "qqe_mod": [
        ("atr_stop_mult", 0.5, 3.0, 0.25),
        ("atr_tp_mult", 1.5, 10.0, 0.5),
        ("max_hold_bars", 5, 40, 5),
        ("qqe_rsi_period", 7, 21, 2),
        ("qqe_factor", 2.0, 8.0, 0.5),
        ("qqe_smoothing", 3, 10, 1),
        ("atr_period", 7, 28, 2),
    ],
    "utbot_alert": [
        ("atr_stop_mult", 0.5, 3.0, 0.25),
        ("atr_tp_mult", 1.5, 10.0, 0.5),
        ("max_hold_bars", 5, 40, 5),
        ("utbot_atr_period", 5, 20, 2),
        ("utbot_atr_mult", 0.5, 3.0, 0.25),
        ("atr_period", 7, 28, 2),
    ],
    # hull_period: set from pair TOML / WFO champion (TV-validated length); do not auto-tune —
    # vec backtest fees/fills differ from TradingView; nudging length here fights your preset.
    "hull_suite": [
        ("atr_stop_mult", 0.5, 3.0, 0.25),
        ("atr_tp_mult", 1.5, 10.0, 0.5),
        ("max_hold_bars", 5, 40, 5),
        ("atr_period", 7, 28, 2),
    ],
    # sar_chop: PSAR + Lucid SAR + CHOP regime + MA/MACD filters + UT Bot ATR-trail gate
    "sar_chop": [
        ("atr_stop_mult", 0.75, 3.0, 0.25),
        ("atr_tp_mult", 1.5, 6.0, 0.5),
        ("max_hold_bars", 5, 40, 5),
        ("sar_increment", 0.01, 0.05, 0.01),
        ("sar_max", 0.1, 0.4, 0.05),
        ("sar_chop_chop_threshold", 30.0, 70.0, 5.0),
        ("sar_chop_ma_long_period", 50, 300, 25),
        ("sar_chop_utbot_mult", 0.5, 4.0, 0.25),
        ("sar_chop_utbot_atr_period", 5, 20, 1),
        ("atr_period", 7, 28, 2),
    ],
}


@dataclass
class TunerResult:
    """Result of a single tuning cycle for one pair."""
    pair_key: str
    best_mode: str
    best_win_rate: float
    best_pnl: float
    best_trades: int
    adjustments_made: list[str]
    frozen: bool
    aggressiveness: str           # "frozen", "slow", "moderate", "aggressive"
    all_modes: dict[str, dict]    # mode -> {win_rate, pnl, trades, params_changed}
    timestamp: float = 0.0


def param_set_for_tuned_mode(
    pair_cfg: "ScalpPairConfig",
    bot_cfg: "ScalpBotConfig",
    mode: str,
    all_modes: dict[str, dict],
) -> ParamSet:
    """ParamSet for ``mode`` after applying ``params_changed`` from a tuner cycle."""
    base = _params_from_pair_config(pair_cfg, bot_cfg, mode)
    info = all_modes.get(mode) or {}
    changed = info.get("params_changed") or {}
    if not changed:
        return base
    return apply_param_dict_overrides(base, changed)


def _params_from_pair_config(
    pair_cfg: "ScalpPairConfig",
    bot_cfg: "ScalpBotConfig",
    mode: str,
    *,
    slippage_bps: float | None = None,
) -> ParamSet:
    """Build a ParamSet for a specific mode from current pair config."""
    return ParamSet(
        mode=mode,
        ema_fast=pair_cfg.ema_fast,
        ema_slow=pair_cfg.ema_slow,
        rsi_period=pair_cfg.rsi_period,
        atr_period=pair_cfg.atr_period,
        vol_ma_period=pair_cfg.volume_ma_period,
        vol_mult=pair_cfg.volume_mult,
        min_signals=pair_cfg.min_signals,
        atr_stop_mult=pair_cfg.atr_stop_mult,
        atr_tp_mult=pair_cfg.atr_tp_mult,
        max_hold_bars=pair_cfg.max_hold_bars,
        fee_pct=wfo_fee_bps_per_leg(bot_cfg) / 10_000.0,
        contract_size=float(getattr(pair_cfg, "contract_size", 1.0) or 1.0),
        fee_usd_per_contract_per_leg=float(
            getattr(bot_cfg, "fee_usd_per_contract_per_leg", 0.0) or 0.0
        ),
        slippage_pct=(
            float(slippage_bps) / 10_000.0
            if slippage_bps is not None
            else float(getattr(bot_cfg, "slippage_bps", 0.0) or 0.0) / 10_000.0
        ),
        fill_model=getattr(bot_cfg, "backtest_fill_model", "close_slip"),
        backtest_funding_enabled=bool(getattr(bot_cfg, "backtest_funding_enabled", False)),
        backtest_funding_bps_per_hour=float(
            getattr(bot_cfg, "backtest_funding_bps_per_hour", 0.0) or 0.0
        ),
        rsi_buy_threshold=pair_cfg.rsi_buy_threshold,
        rsi_sell_threshold=pair_cfg.rsi_sell_threshold,
        rsi_short_threshold=float(getattr(pair_cfg, "rsi_short_threshold", 70.0)),
        ema_scalp_period=pair_cfg.ema_scalp_period,
        ema_scalp_sr_bars=pair_cfg.ema_scalp_sr_bars,
        macd_fast_len=pair_cfg.macd_fast_len,
        macd_slow_len=pair_cfg.macd_slow_len,
        macd_signal_len=pair_cfg.macd_signal_len,
        t3_length=pair_cfg.t3_length,
        t3_vfactor=pair_cfg.t3_vfactor,
        hlc_close_period=pair_cfg.hlc_close_period,
        hlc_low_period=pair_cfg.hlc_low_period,
        hlc_high_period=pair_cfg.hlc_high_period,
        adx_threshold=pair_cfg.adx_threshold,
        wae_sensitivity=pair_cfg.wae_sensitivity,
        wae_fast_len=pair_cfg.wae_fast_len,
        wae_slow_len=pair_cfg.wae_slow_len,
        wae_bb_len=pair_cfg.wae_bb_len,
        wae_bb_mult=pair_cfg.wae_bb_mult,
        adx_period=getattr(pair_cfg, "adx_period", 14),
        supertrend_period=getattr(pair_cfg, "supertrend_period", 10),
        supertrend_factor=getattr(pair_cfg, "supertrend_factor", 3.0),
        squeeze_bb_period=getattr(pair_cfg, "squeeze_bb_period", 20),
        squeeze_bb_mult=getattr(pair_cfg, "squeeze_bb_mult", 2.0),
        squeeze_kc_mult=getattr(pair_cfg, "squeeze_kc_mult", 1.5),
        squeeze_mom_period=getattr(pair_cfg, "squeeze_mom_period", 12),
        qqe_rsi_period=getattr(pair_cfg, "qqe_rsi_period", 14),
        qqe_factor=getattr(pair_cfg, "qqe_factor", 4.238),
        qqe_smoothing=getattr(pair_cfg, "qqe_smoothing", 5),
        utbot_atr_period=getattr(pair_cfg, "utbot_atr_period", 10),
        utbot_atr_mult=getattr(pair_cfg, "utbot_atr_mult", 1.0),
        hull_period=getattr(pair_cfg, "hull_period", 38),
        sar_start=float(getattr(pair_cfg, "sar_start", 0.02)),
        sar_increment=float(getattr(pair_cfg, "sar_increment", 0.02)),
        sar_max=float(getattr(pair_cfg, "sar_max", 0.2)),
        sar_chop_ma_fast_period=int(getattr(pair_cfg, "sar_chop_ma_fast_period", 7)),
        sar_chop_ma_long_period=int(getattr(pair_cfg, "sar_chop_ma_long_period", 200)),
        sar_chop_ma_short_period=int(getattr(pair_cfg, "sar_chop_ma_short_period", 50)),
        sar_chop_chop_period=int(getattr(pair_cfg, "sar_chop_chop_period", 14)),
        sar_chop_chop_threshold=float(getattr(pair_cfg, "sar_chop_chop_threshold", 38.2)),
        sar_chop_macd_fast=int(getattr(pair_cfg, "sar_chop_macd_fast", 12)),
        sar_chop_macd_slow=int(getattr(pair_cfg, "sar_chop_macd_slow", 26)),
        sar_chop_macd_signal=int(getattr(pair_cfg, "sar_chop_macd_signal", 9)),
        sar_chop_use_lucid=bool(getattr(pair_cfg, "sar_chop_use_lucid", True)),
        sar_chop_use_utbot_trail=bool(getattr(pair_cfg, "sar_chop_use_utbot_trail", True)),
        sar_chop_utbot_atr_period=int(getattr(pair_cfg, "sar_chop_utbot_atr_period", 10)),
        sar_chop_utbot_mult=float(getattr(pair_cfg, "sar_chop_utbot_mult", 2.0)),
    )


_MIN_TRADES_TO_FREEZE = 10  # need a meaningful sample before we stop tuning


def _aggressiveness_from_pf(pf: float, n_trades: int = 0) -> str:
    """Determine tuning aggressiveness based on profit factor.

    Profit factor (gross_wins / gross_losses) measures *magnitude* of winners
    vs losers — unlike win rate which only counts wins and misses size entirely.
    A PF of 2.0 means $2 won for every $1 lost, regardless of whether the
    win rate is 30% or 80%.

    Freeze requires both a high PF AND a minimum trade count so that a single
    lucky outsized win doesn't silence the tuner.
    """
    pf_capped = min(pf, 999.0)  # inf when no losses
    if pf_capped >= 3.0 and n_trades >= _MIN_TRADES_TO_FREEZE:
        return "frozen"
    if pf_capped >= 1.5:
        return "slow"
    if pf_capped >= 0.8:
        return "moderate"
    return "aggressive"


def _perturbation_count(aggressiveness: str) -> int:
    """How many params to try perturbing per cycle.

    Aggressive is capped at 3 (was 6) — large simultaneous jumps caused 15-36 point
    PnL crashes that took 3-5 recovery cycles to undo. One parameter at a time
    in slow mode, up to 3 in moderate/aggressive is the right isolation granularity.
    """
    return {"frozen": 0, "slow": 1, "moderate": 3, "aggressive": 3}.get(aggressiveness, 2)


def _step_scale(aggressiveness: str) -> float:
    """Multiplier on the base step size."""
    return {"frozen": 0.0, "slow": 0.5, "moderate": 1.0, "aggressive": 2.0}.get(aggressiveness, 1.0)


def _validate_param_constraints(params: ParamSet) -> ParamSet:
    """Enforce structural constraints (e.g., fast EMA < slow EMA)."""
    kw = {}
    if params.ema_fast >= params.ema_slow:
        kw["ema_slow"] = params.ema_fast + 3
    if params.macd_fast_len >= params.macd_slow_len:
        kw["macd_slow_len"] = params.macd_fast_len + 2
    if params.wae_fast_len >= params.wae_slow_len:
        kw["wae_slow_len"] = params.wae_fast_len + 2
    if params.hlc_low_period > params.hlc_high_period:
        kw["hlc_high_period"] = params.hlc_low_period
    if str(getattr(params, "mode", "")) == "rsi_reversion":
        buy = float(params.rsi_buy_threshold)
        sell = float(params.rsi_sell_threshold)
        short_th = float(params.rsi_short_threshold)
        if buy >= sell:
            kw["rsi_sell_threshold"] = buy + 5.0
            sell = float(kw.get("rsi_sell_threshold", sell))
        if short_th <= sell:
            kw["rsi_short_threshold"] = sell + 5.0
    if kw:
        return replace(params, **kw)
    return params


def _perturb_param(
    base: ParamSet,
    param_name: str,
    step: float,
    direction: int,    # +1 or -1
    lo: float,
    hi: float,
) -> ParamSet | None:
    """Create a new ParamSet with one param nudged by step*direction, clamped to [lo, hi]."""
    current = getattr(base, param_name)
    new_val = current + step * direction
    new_val = max(lo, min(hi, new_val))
    if isinstance(current, int):
        new_val = int(round(new_val))
    if new_val == current:
        return None
    result = replace(base, **{param_name: new_val})
    return _validate_param_constraints(result)


def tune_strategy_params(
    bars: dict[str, np.ndarray],
    base_params: ParamSet,
    mode: str,
    aggressiveness: str,
    recency_half_life_bars: float = 0.0,
) -> tuple[ParamSet, list[str]]:
    """Try perturbations on individual params; keep those that improve total PnL.

    Primary objective: total PnL (what actually grows the account).
    Secondary tiebreaker: profit factor (quality of wins vs losses).
    Win rate is tracked for diagnostics only — it does not drive decisions.

    Uses recency-weighted metrics so recent trade performance counts more.
    Returns (best_params, list_of_adjustment_descriptions).
    """
    if aggressiveness == "frozen":
        return base_params, []

    tunables = TUNABLE_PARAMS.get(mode, [])
    if not tunables:
        return base_params, []

    hl = recency_half_life_bars
    n_try = _perturbation_count(aggressiveness)
    scale = _step_scale(aggressiveness)
    current = replace(base_params, mode=mode)
    baseline = evaluate_params(bars, current, recency_half_life_bars=hl)
    adjustments: list[str] = []

    priority = {
        "atr_stop_mult": 0, "atr_tp_mult": 0, "atr_period": 0,
        "max_hold_bars": 1,
        "ema_fast": 2, "ema_slow": 2, "rsi_period": 2,
        "macd_fast_len": 2, "macd_slow_len": 2, "macd_signal_len": 2,
        "t3_length": 2, "t3_vfactor": 2, "adx_threshold": 2, "adx_period": 2,
        "hlc_close_period": 2, "hlc_low_period": 2, "hlc_high_period": 2,
        "wae_sensitivity": 2, "wae_fast_len": 2, "wae_slow_len": 2, "wae_bb_len": 2, "wae_bb_mult": 2,
        "rsi_buy_threshold": 2, "rsi_sell_threshold": 2, "rsi_short_threshold": 2,
        "supertrend_period": 2, "supertrend_factor": 2,
        "squeeze_bb_period": 2, "squeeze_bb_mult": 2, "squeeze_kc_mult": 2, "squeeze_mom_period": 2,
        "qqe_rsi_period": 2, "qqe_factor": 2, "qqe_smoothing": 2,
        "utbot_atr_period": 2, "utbot_atr_mult": 2,
        "hull_period": 2,
        "sar_increment": 2, "sar_max": 2,
        "sar_chop_chop_threshold": 2, "sar_chop_ma_long_period": 2,
        "sar_chop_utbot_mult": 2, "sar_chop_utbot_atr_period": 2,
    }
    sorted_tunables = sorted(tunables, key=lambda t: priority.get(t[0], 3))

    tried = 0
    step_multipliers = [1, 2, 3] if aggressiveness == "aggressive" else [1]

    for param_name, lo, hi, base_step in sorted_tunables:
        if tried >= n_try:
            break

        step = base_step * scale
        best_for_param = current
        best_pnl = baseline.total_pnl
        best_pf = baseline.profit_factor if baseline.profit_factor != float("inf") else 999.0
        improved = False

        for mult in step_multipliers:
            for direction in (+1, -1):
                candidate = _perturb_param(current, param_name, step * mult, direction, lo, hi)
                if candidate is None:
                    continue
                m = evaluate_params(bars, candidate, recency_half_life_bars=hl)
                m_pf = m.profit_factor if m.profit_factor != float("inf") else 999.0
                if (m.total_pnl > best_pnl) or (m.total_pnl == best_pnl and m_pf > best_pf):
                    best_for_param = candidate
                    best_pnl = m.total_pnl
                    best_pf = m_pf
                    improved = True

        if improved:
            old_val = getattr(current, param_name)
            new_val = getattr(best_for_param, param_name)
            adjustments.append(
                f"{param_name}: {old_val} -> {new_val} "
                f"(pnl {baseline.total_pnl:.4f} -> {best_pnl:.4f})"
            )
            current = best_for_param
            baseline = evaluate_params(bars, current, recency_half_life_bars=hl)

        tried += 1

    return current, adjustments


def run_tuner_cycle(
    pair_key: str,
    pair_cfg: "ScalpPairConfig",
    bot_cfg: "ScalpBotConfig",
    lookback_hours: float,
    *,
    modes_only: tuple[str, ...] | None = None,
    slippage_bps: float | None = None,
) -> TunerResult | None:
    """Run one full tuning cycle for a single pair across all strategy modes.

    For each mode:
      1. Evaluate current params (baseline win rate)
      2. Based on aggressiveness, try perturbations
      3. Keep the best params found

    Then pick the overall best mode by **expectancy** (min trades), then profit factor,
    win rate, total PnL; if no mode has enough trades, fall back to PnL among modes
    with at least one trade.
    """
    load_days = lookback_hours / 24.0 + 0.5
    bars = bar_store.load_bars(pair_cfg.symbol, pair_cfg.interval, last_n_days=load_days)
    if bars is None or len(bars["timestamp"]) < 50:
        return None

    # Slice to lookback window
    ts = bars["timestamp"]
    cutoff = float(ts[-1]) - lookback_hours * 3600.0
    mask = ts >= cutoff
    if int(mask.sum()) < 50:
        return None
    bars = {k: v[mask] for k, v in bars.items()}

    all_modes: dict[str, dict] = {}
    scored_rows: list[tuple[str, "BacktestMetrics"]] = []  # mode, tuned_m
    all_adjustments: list[str] = []
    overall_frozen = True

    mode_list = modes_only if modes_only else STRATEGY_MODES
    for mode in mode_list:
        base = _params_from_pair_config(
            pair_cfg, bot_cfg, mode, slippage_bps=slippage_bps,
        )
        baseline_m = evaluate_params(bars, base)

        agg = _aggressiveness_from_pf(baseline_m.profit_factor, baseline_m.trade_count)
        if agg != "frozen":
            overall_frozen = False

        tuned, adjustments = tune_strategy_params(bars, base, mode, agg)
        tuned_m = evaluate_params(bars, tuned)
        tuned_pf = tuned_m.profit_factor if tuned_m.profit_factor != float("inf") else 999.0

        mode_info: dict = {
            "win_rate": round(float(tuned_m.win_rate), 4),
            "pnl": round(float(tuned_m.total_pnl), 6),
            "expectancy": round(float(tuned_m.expectancy), 6),
            "trades": int(tuned_m.trade_count),
            "profit_factor": round(tuned_pf, 4),
            "aggressiveness": agg,
            "adjustments": adjustments,
        }

        if adjustments:
            mode_info["params_changed"] = {
                attr: getattr(tuned, attr)
                for attr in _param_names_for_mode(mode)
                if getattr(tuned, attr) != getattr(base, attr)
            }
            all_adjustments.extend([f"[{mode}] {a}" for a in adjustments])

        all_modes[mode] = mode_info
        scored_rows.append((mode, tuned_m))

    best_mode = ""
    best_wr = -1.0
    best_pnl = -float("inf")
    best_pf = -float("inf")
    best_trades = 0
    min_t = EXPECTANCY_MIN_TRADES

    def _pf_num(tm: "BacktestMetrics") -> float:
        p = float(tm.profit_factor)
        return 999.0 if p == float("inf") else p

    qualified = [(m, tm) for m, tm in scored_rows if int(tm.trade_count) >= min_t]
    if qualified:
        m_best, tm_best = max(
            qualified,
            key=lambda x: (
                float(x[1].expectancy),
                _pf_num(x[1]),
                float(x[1].win_rate),
                float(x[1].total_pnl),
            ),
        )
        best_mode = m_best
        best_wr = float(tm_best.win_rate)
        best_pnl = float(tm_best.total_pnl)
        best_pf = _pf_num(tm_best)
        best_trades = int(tm_best.trade_count)
    else:
        at_least_one = [(m, tm) for m, tm in scored_rows if int(tm.trade_count) >= 1]
        if at_least_one:
            m_best, tm_best = max(
                at_least_one,
                key=lambda x: (float(x[1].total_pnl), _pf_num(x[1]), float(x[1].expectancy)),
            )
            best_mode = m_best
            best_wr = float(tm_best.win_rate)
            best_pnl = float(tm_best.total_pnl)
            best_pf = _pf_num(tm_best)
            best_trades = int(tm_best.trade_count)
        elif scored_rows:
            m_best, tm_best = scored_rows[0]
            best_mode = m_best
            best_wr = float(tm_best.win_rate)
            best_pnl = float(tm_best.total_pnl)
            best_pf = _pf_num(tm_best)
            best_trades = int(tm_best.trade_count)

    return TunerResult(
        pair_key=pair_key,
        best_mode=best_mode,
        best_win_rate=best_wr,
        best_pnl=best_pnl,
        best_trades=best_trades,
        adjustments_made=all_adjustments,
        frozen=overall_frozen,
        aggressiveness=_aggressiveness_from_pf(best_pf, best_trades),
        all_modes=all_modes,
        timestamp=time.time(),
    )


def apply_tuner_result(
    result: TunerResult,
    pair_cfg: "ScalpPairConfig",
    *,
    apply_mode: str | None = None,
) -> list[str]:
    """Apply tuned params from the best mode back to the live pair config.

    ``apply_mode`` selects which entry in ``result.all_modes`` to use (defaults to
    ``result.best_mode``). Use the pair's active WFO mode when the tuner only ran
    a single-mode grid.

    Returns list of changed param descriptions.
    """
    mode_key = (apply_mode or result.best_mode or "").strip()
    mode_info = result.all_modes.get(mode_key, {})
    changed_params = mode_info.get("params_changed", {})
    if not changed_params:
        return []

    applied: list[str] = []
    attr_map = {
        "ema_fast": "ema_fast",
        "ema_slow": "ema_slow",
        "rsi_period": "rsi_period",
        "atr_period": "atr_period",
        "vol_ma_period": "volume_ma_period",
        "vol_mult": "volume_mult",
        "min_signals": "min_signals",
        "atr_stop_mult": "atr_stop_mult",
        "atr_tp_mult": "atr_tp_mult",
        "max_hold_bars": "max_hold_bars",
        "rsi_buy_threshold": "rsi_buy_threshold",
        "rsi_sell_threshold": "rsi_sell_threshold",
        "rsi_short_threshold": "rsi_short_threshold",
        "ema_scalp_period": "ema_scalp_period",
        "ema_scalp_sr_bars": "ema_scalp_sr_bars",
        "macd_fast_len": "macd_fast_len",
        "macd_slow_len": "macd_slow_len",
        "macd_signal_len": "macd_signal_len",
        "t3_length": "t3_length",
        "t3_vfactor": "t3_vfactor",
        "hlc_close_period": "hlc_close_period",
        "hlc_low_period": "hlc_low_period",
        "hlc_high_period": "hlc_high_period",
        "adx_period": "adx_period",
        "adx_threshold": "adx_threshold",
        "wae_sensitivity": "wae_sensitivity",
        "wae_fast_len": "wae_fast_len",
        "wae_slow_len": "wae_slow_len",
        "wae_bb_len": "wae_bb_len",
        "wae_bb_mult": "wae_bb_mult",
        "supertrend_period": "supertrend_period",
        "supertrend_factor": "supertrend_factor",
        "squeeze_bb_period": "squeeze_bb_period",
        "squeeze_bb_mult": "squeeze_bb_mult",
        "squeeze_kc_mult": "squeeze_kc_mult",
        "squeeze_mom_period": "squeeze_mom_period",
        "qqe_rsi_period": "qqe_rsi_period",
        "qqe_factor": "qqe_factor",
        "qqe_smoothing": "qqe_smoothing",
        "utbot_atr_period": "utbot_atr_period",
        "utbot_atr_mult": "utbot_atr_mult",
        "hull_period": "hull_period",
        "sar_start": "sar_start",
        "sar_increment": "sar_increment",
        "sar_max": "sar_max",
        "sar_chop_ma_fast_period": "sar_chop_ma_fast_period",
        "sar_chop_ma_long_period": "sar_chop_ma_long_period",
        "sar_chop_ma_short_period": "sar_chop_ma_short_period",
        "sar_chop_chop_period": "sar_chop_chop_period",
        "sar_chop_chop_threshold": "sar_chop_chop_threshold",
        "sar_chop_macd_fast": "sar_chop_macd_fast",
        "sar_chop_macd_slow": "sar_chop_macd_slow",
        "sar_chop_macd_signal": "sar_chop_macd_signal",
        "sar_chop_use_lucid": "sar_chop_use_lucid",
        "sar_chop_use_utbot_trail": "sar_chop_use_utbot_trail",
        "sar_chop_utbot_atr_period": "sar_chop_utbot_atr_period",
        "sar_chop_utbot_mult": "sar_chop_utbot_mult",
    }

    for param_name, new_val in changed_params.items():
        cfg_attr = attr_map.get(param_name)
        if cfg_attr is None:
            continue
        old_val = getattr(pair_cfg, cfg_attr, None)
        if old_val is not None and old_val != new_val:
            setattr(pair_cfg, cfg_attr, type(old_val)(new_val))
            applied.append(f"{cfg_attr}: {old_val} -> {new_val}")

    return applied


def _param_names_for_mode(mode: str) -> list[str]:
    """Param attribute names relevant to a given mode."""
    tunables = TUNABLE_PARAMS.get(mode, [])
    return [t[0] for t in tunables]


def save_tuner_state(results: dict[str, TunerResult]) -> None:
    """Persist tuner results to disk for debugging / UI."""
    TUNER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = {}
    for pk, r in results.items():
        out[pk] = {
            "best_mode": r.best_mode,
            "best_win_rate": r.best_win_rate,
            "best_pnl": r.best_pnl,
            "best_trades": r.best_trades,
            "frozen": r.frozen,
            "aggressiveness": r.aggressiveness,
            "adjustments": r.adjustments_made,
            "all_modes": r.all_modes,
            "timestamp": r.timestamp,
        }
    tmp = TUNER_STATE_PATH.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(out, f, indent=2, default=str)
    tmp.replace(TUNER_STATE_PATH)


def load_tuner_state() -> dict | None:
    """Load persisted tuner state (for UI display on restart)."""
    if not TUNER_STATE_PATH.exists():
        return None
    try:
        with TUNER_STATE_PATH.open() as f:
            return json.load(f)
    except Exception:
        return None
