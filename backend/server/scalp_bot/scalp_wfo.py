"""Walk-forward optimizer for scalp bot parameters.

**Division of labor:** WFO is the coarse layer — it chooses **which strategy mode**
(and broad parameter set) wins a continuous full-grid backtest, then writes
``data/scalp_champion.json``. The **param tuner** (``param_tuner.py``) is the fine
layer: it perturbs tunables around the **current** mode's operating point. While
a champion applies to a pair's symbol, the runtime lets WFO own mode selection;
the tuner adjusts parameters without fighting that decision.

**Continuous evaluation:** every grid row is backtested on one uninterrupted window
(``continuous_eval_hours`` of scored history plus ``continuous_warmup_hours`` for
indicator warm-up). The best performer after per-mode normalization becomes champion.

Historical bars are backfilled from Coinbase REST on startup (paginated),
so the WFO can run its first full pass within seconds of boot — no
multi-day wait for candle accumulation.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import numpy as np

from . import bar_store
from .scalp_config import wfo_fee_bps_per_leg
from .scalp_mode_resolution import normalize_auto_mode_fallback
from .scalp_vec_backtest import (
    BacktestMetrics,
    ParamSet,
    WFO_REGISTERED_STRATEGY_MODES,
    apply_param_dict_overrides,
    build_default_grid,
    evaluate_params,
)

if TYPE_CHECKING:
    from ..session_logger import SessionLogger
    from .scalp_config import ScalpBotConfig, ScalpPairConfig

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Multiprocessing worker for parallel WFO train scoring
# ---------------------------------------------------------------------------
# Process-pool workers need a picklable top-level function.  To avoid
# re-serializing the (large) ``bars`` dict with every task, we stash it
# in a module-level global that the worker initializer populates once per
# worker process.

_MP_BARS: dict[str, np.ndarray] | None = None
_MP_EVAL_KW: dict[str, Any] = {}

_WFO_WORKER_COUNT: int = max(1, (os.cpu_count() or 1) - 1)


def _mp_init(bars: dict[str, np.ndarray], eval_kw: dict[str, Any]) -> None:
    """Called once per worker process to stash shared state."""
    global _MP_BARS, _MP_EVAL_KW  # noqa: PLW0603
    _MP_BARS = bars
    _MP_EVAL_KW = eval_kw


def _mp_continuous_eval_one(
    args: tuple[int, "ParamSet", dict[str, Any]],
) -> tuple[int, "BacktestMetrics | None", str | None]:
    """Continuous full-window backtest of one grid row (uses shared bars from _mp_init)."""
    pi, params, eval_kw = args
    bars = _MP_BARS
    assert bars is not None
    try:
        m = evaluate_params(bars, params, recency_half_life_bars=0, **eval_kw)
    except Exception:
        return (pi, None, "eval_exception")
    return (pi, m, None)


DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
CHAMPION_PATH = DATA_DIR / "scalp_champion.json"
PROMOTION_LOG_PATH = DATA_DIR / "wfo_champion_promotions.jsonl"
PROMOTION_META_PATH = DATA_DIR / "wfo_champion_promotion_meta.json"


# ---------------------------------------------------------------------------
# WFO configuration
# ---------------------------------------------------------------------------

@dataclass
class WFOConfig:
    enabled: bool = True
    interval_sec: float = 3600.0
    min_trades: int = 3
    objective: str = "sharpe"
    min_profit_factor: float = 0.8
    min_win_rate: float = 0.20
    max_drawdown_pct: float = 30.0
    max_avg_dd_pct: float = 50.0
    max_param_delta_hold: int = 48
    max_param_delta_stop: float = 1.0
    max_param_delta_tp: float = 1.5
    min_stability_ratio: float = 0.15
    min_mean_score: float = 0.0
    require_positive_latest_holdout: bool = True
    min_latest_holdout_pf: float = 1.0
    champion_cooldown_sec: float = 0.0
    require_holdout_beat_prior: bool = False
    prior_beat_epsilon: float = 1e-6
    holdout_tiebreakers: tuple[str, ...] = (
        "stability",
        "neg_mean_max_dd_pct",
        "min_holdout_trade_count",
    )
    holdout_score_epsilon: float = 0.0
    holdout_rank_by_period: bool = True
    period_rank_metric: str = "total_pnl"
    pick_best_per_mode: bool = True
    continuous_eval_hours: float = 672.0
    continuous_warmup_hours: float = 168.0
    continuous_min_trades: int = 20


# ---------------------------------------------------------------------------
# Lookback span (load / backfill / readiness)
# ---------------------------------------------------------------------------

def wfo_effective_roll_span_hours(wfo_cfg: WFOConfig) -> float:
    """Hours of bar history required: continuous eval window plus indicator warmup prefix."""
    return float(wfo_cfg.continuous_eval_hours) + float(wfo_cfg.continuous_warmup_hours)


def _mean_neg_max_dd_pct(metrics_list: list[BacktestMetrics]) -> float:
    if not metrics_list:
        return 0.0
    return -float(np.mean([float(m.max_drawdown_pct) for m in metrics_list]))


def _holdout_candidate_rank_tuple(
    cand: tuple[float, float, int, list[BacktestMetrics]],
    wfo_cfg: WFOConfig,
) -> tuple[float, ...]:
    """Sort key (descending): tie-breakers first, then mean holdout score as final split.

    Candidates are pre-filtered to within ``holdout_score_epsilon`` of the top mean score so
    near-ties defer to stability / drawdown / trade-count metrics instead of a tiny mean edge.
    """
    mean_score, stability, _pi, mlist = cand
    keys: list[float] = []
    for name in getattr(wfo_cfg, "holdout_tiebreakers", ()):
        n = str(name).strip()
        if n == "stability":
            keys.append(float(stability))
        elif n in ("neg_mean_max_dd_pct", "mean_neg_max_dd_pct"):
            keys.append(_mean_neg_max_dd_pct(mlist))
        elif n in ("min_holdout_trade_count", "min_window_trade_count"):
            keys.append(float(min((m.trade_count for m in mlist), default=0)))
        elif n == "mean_holdout_trade_count":
            keys.append(float(np.mean([m.trade_count for m in mlist])) if mlist else 0.0)
        elif n == "mean_holdout_total_pnl":
            keys.append(float(np.mean([m.total_pnl for m in mlist])) if mlist else 0.0)
        elif n in ("sum_holdout_total_pnl", "holdout_total_pnl_sum"):
            keys.append(float(sum(float(m.total_pnl) for m in mlist)) if mlist else 0.0)
        else:
            LOG.debug("scalp_wfo: unknown holdout tiebreaker %r — ignored", name)
    keys.append(float(mean_score))
    return tuple(keys)


def _pick_holdout_champion(
    candidates: list[tuple[float, float, int, list[BacktestMetrics]]],
    wfo_cfg: WFOConfig,
) -> tuple[
    tuple[float, float, int, list[BacktestMetrics]],
    dict[str, object],
]:
    """Highest mean score; within ``holdout_score_epsilon`` of the top, apply tie-breakers."""
    best_mean = max(c[0] for c in candidates)
    eps = float(getattr(wfo_cfg, "holdout_score_epsilon", 0.0) or 0.0)
    tol = max(eps, 1e-12)
    pool = [c for c in candidates if c[0] >= best_mean - tol]
    pool.sort(key=lambda c: _holdout_candidate_rank_tuple(c, wfo_cfg), reverse=True)
    chosen = pool[0]
    runner = pool[1] if len(pool) > 1 else None
    diag: dict[str, object] = {
        "holdout_top_mean_score": float(best_mean),
        "holdout_score_epsilon": float(eps),
        "holdout_tie_pool_size": len(pool),
        "holdout_chosen_pi": int(chosen[2]),
        "holdout_chosen_sort_tuple": list(_holdout_candidate_rank_tuple(chosen, wfo_cfg)),
        "holdout_tiebreakers": list(getattr(wfo_cfg, "holdout_tiebreakers", ())),
    }
    if runner is not None:
        diag["holdout_runner_up_pi"] = int(runner[2])
        diag["holdout_runner_up_sort_tuple"] = list(_holdout_candidate_rank_tuple(runner, wfo_cfg))
    return chosen, diag


def _pick_holdout_champion_per_mode_first(
    candidates: list[tuple[float, float, int, list[BacktestMetrics]]],
    grid: list[ParamSet],
    wfo_cfg: WFOConfig,
) -> tuple[tuple[float, float, int, list[BacktestMetrics]], dict[str, object]]:
    """Two-stage ranking: elect best candidate per mode, then best across modes.

    Prevents high-row-count modes (e.g. sar_chop ≈ 46% of grid) from flooding the
    candidate pool and winning on sheer representation rather than per-strategy quality.
    """
    by_mode: dict[str, tuple[float, float, int, list[BacktestMetrics]]] = {}
    for cand in candidates:
        mode = str(grid[cand[2]].mode)
        cur = by_mode.get(mode)
        if cur is None or _holdout_candidate_rank_tuple(cand, wfo_cfg) > _holdout_candidate_rank_tuple(cur, wfo_cfg):
            by_mode[mode] = cand
    mode_winners = list(by_mode.values())
    chosen, diag = _pick_holdout_champion(mode_winners, wfo_cfg)
    diag["per_mode_count"] = len(by_mode)
    diag["mode_names"] = list(by_mode.keys())
    return chosen, diag


def _holdout_rank_score(
    wfo_cfg: WFOConfig,
    scores: np.ndarray,
    metrics_list: list[BacktestMetrics],
) -> float:
    """Primary sort key for champion pool: period total PnL (optionally risk-adjusted)."""
    if bool(getattr(wfo_cfg, "holdout_rank_by_period", False)):
        metric = str(getattr(wfo_cfg, "period_rank_metric", "total_pnl") or "total_pnl")
        if metric == "calmar":
            pnl_sum = float(sum(m.total_pnl for m in metrics_list))
            dd_max = float(max((m.max_drawdown_pct for m in metrics_list), default=1.0))
            return pnl_sum / (dd_max / 100.0) if dd_max > 0 else pnl_sum
        if metric == "sharpe_like":
            return float(np.mean([m.sharpe for m in metrics_list])) if metrics_list else 0.0
        return float(sum(float(m.total_pnl) for m in metrics_list))
    return float(scores.mean())


def _continuous_rank_score(wfo_cfg: WFOConfig, m: BacktestMetrics) -> float:
    """Rank key for one continuous eval window (delegates to period metric rules)."""
    return _holdout_rank_score(wfo_cfg, np.array([0.0]), [m])


def _wfo_continuous_mode_scoreboard_rows(
    grid: list[ParamSet],
    results: dict[int, BacktestMetrics],
    wfo_cfg: WFOConfig,
    *,
    champion_pi: int | None,
    objective: str = "total_pnl",
) -> list[dict[str, Any]]:
    """Best row per strategy mode from continuous full-grid eval (dashboard)."""
    champ_mode: str | None = None
    if champion_pi is not None and 0 <= int(champion_pi) < len(grid):
        champ_mode = str(grid[int(champion_pi)].mode)

    by_mode: dict[str, dict[str, Any]] = {}
    min_trades = int(getattr(wfo_cfg, "continuous_min_trades", 0) or 0)
    for pi, m in results.items():
        if pi < 0 or pi >= len(grid):
            continue
        mode = str(grid[pi].mode)
        rank = _continuous_rank_score(wfo_cfg, m)
        row = {
            "pi": int(pi),
            "mode": mode,
            "holdout_windows": 1,
            "mean_holdout_score": round(float(m.total_pnl), 6),
            "holdout_rank_score": round(rank, 6),
            "mean_holdout_total_pnl": round(float(m.total_pnl), 6),
            "sum_holdout_total_pnl": round(float(m.total_pnl), 6),
            "mean_max_drawdown_pct": round(float(m.max_drawdown_pct), 4),
            "mean_holdout_trades": float(m.trade_count),
            "qualified_champion_pool": int(m.trade_count) >= min_trades,
            "is_wfo_champion_row": bool(champion_pi is not None and int(pi) == int(champion_pi)),
            "is_wfo_champion_mode": bool(champ_mode is not None and mode == champ_mode),
            "objective": objective,
        }
        cur = by_mode.get(mode)
        if cur is None or float(row["holdout_rank_score"]) > float(cur["holdout_rank_score"]):
            by_mode[mode] = row

    rows = list(by_mode.values())
    rows.sort(key=lambda r: float(r["holdout_rank_score"]), reverse=True)
    return rows


def _slice_bars_to_roll_span(
    bars: dict[str, np.ndarray],
    roll_hours: float,
) -> dict[str, np.ndarray]:
    """Keep only bars with timestamp >= (latest − roll_hours)."""
    ts = bars["timestamp"]
    if len(ts) == 0 or roll_hours <= 0:
        return bars
    latest = float(ts[-1])
    cutoff = latest - float(roll_hours) * 3600.0
    mask = ts >= cutoff
    if mask.all():
        return bars
    return {k: v[mask] for k, v in bars.items()}


def wfo_verify_stored_roll_coverage(
    symbol: str,
    interval: int,
    roll_hours: float,
    *,
    min_span_fraction: float = 0.92,
) -> tuple[float, bool]:
    """After Parquet load + roll slice, return ``(span_hours, ok)``.

    ``ok`` is True when ``(ts[-1]-ts[0])/3600 >= roll_hours * min_span_fraction``.
    Used post-backfill to detect partial REST history.
    """
    load_days = roll_hours / 24.0 + 1.0
    bars = bar_store.load_bars(
        symbol, interval, last_n_days=load_days, trim_anchor="latest_bar",
    )
    if bars is None or len(bars["timestamp"]) < 2:
        return 0.0, False
    bars = _slice_bars_to_roll_span(bars, roll_hours)
    ts = bars["timestamp"]
    if len(ts) < 2:
        return 0.0, False
    span_h = (float(ts[-1]) - float(ts[0])) / 3600.0
    need = float(roll_hours) * float(min_span_fraction)
    return span_h, bool(span_h >= need)


# ---------------------------------------------------------------------------
# Scoring — objective dispatch (see WFOConfig.objective) + hard gates
# ---------------------------------------------------------------------------

def _score_profit_factor(m: BacktestMetrics) -> float:
    if m.profit_factor == float("inf"):
        return 0.0
    return float(m.profit_factor)


_OBJECTIVES: dict[str, Callable[[BacktestMetrics], float]] = {
    "sharpe": lambda m: m.sharpe,
    "sortino": lambda m: m.sortino,
    "calmar": lambda m: m.calmar,
    "expectancy": lambda m: m.expectancy,
    "expectancy_sqrt_n": lambda m: m.expectancy * math.sqrt(float(m.trade_count)),
    "profit_factor": _score_profit_factor,
    "total_pnl": lambda m: m.total_pnl,
}


def score_strategy(m: BacktestMetrics, objective: str = "sharpe") -> float:
    """Primary score for WFO ranking; ``objective`` selects the metric (default Sharpe)."""
    if m.trade_count == 0:
        return -float("inf")
    fn = _OBJECTIVES.get(objective) or _OBJECTIVES["sharpe"]
    return fn(m)


def _gate_fail_reason(m: BacktestMetrics, cfg: WFOConfig) -> str:
    """Return first failing gate label (for diagnostics)."""
    if m.trade_count < cfg.min_trades:
        return f"too_few_trades({m.trade_count}<{cfg.min_trades})"
    if m.profit_factor < cfg.min_profit_factor and m.profit_factor != float("inf"):
        return f"low_pf({m.profit_factor:.2f}<{cfg.min_profit_factor})"
    if m.win_rate < cfg.min_win_rate:
        return f"low_wr({m.win_rate:.2f}<{cfg.min_win_rate})"
    if m.max_drawdown_pct > cfg.max_drawdown_pct:
        return f"high_dd({m.max_drawdown_pct:.1f}>{cfg.max_drawdown_pct})"
    return "passed"


# ---------------------------------------------------------------------------
# Safety gates
# ---------------------------------------------------------------------------

def safety_gate(
    proposed: ParamSet,
    current: ParamSet | None,
    cfg: WFOConfig,
) -> tuple[bool, str]:
    """Check whether proposed params are within acceptable delta of current."""
    if current is None:
        return True, "no_current"
    if proposed.mode != current.mode:
        return True, "mode_switch"

    if abs(proposed.max_hold_bars - current.max_hold_bars) > cfg.max_param_delta_hold:
        return False, f"hold_delta={abs(proposed.max_hold_bars - current.max_hold_bars)}"
    if abs(proposed.atr_stop_mult - current.atr_stop_mult) > cfg.max_param_delta_stop:
        return False, f"stop_delta={abs(proposed.atr_stop_mult - current.atr_stop_mult):.2f}"
    if abs(proposed.atr_tp_mult - current.atr_tp_mult) > cfg.max_param_delta_tp:
        return False, f"tp_delta={abs(proposed.atr_tp_mult - current.atr_tp_mult):.2f}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Champion I/O — per-symbol map on disk (legacy single-object supported)
# ---------------------------------------------------------------------------

def _champion_store_from_raw(raw: dict | None) -> dict[str, dict]:
    """Normalize file JSON to ``symbol -> champion_row`` (may be empty)."""
    if not raw or not isinstance(raw, dict):
        return {}
    # Legacy: one object with top-level symbol + params blob
    if "symbol" in raw and isinstance(raw.get("params"), dict):
        sym = str(raw.get("symbol", "")).strip()
        if sym:
            return {sym: raw}
        return {}
    out: dict[str, dict] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        if not (isinstance(v.get("params"), dict) or "holdout_metrics" in v or "mode" in v):
            continue
        sym = str(v.get("symbol", k)).strip()
        if sym:
            out[sym] = v
    return out


def load_champion(path: Path = CHAMPION_PATH) -> dict[str, dict] | None:
    """Load champion file as ``symbol -> champion_dict``. None if missing/unreadable."""
    if not path.exists():
        return None
    try:
        with path.open("r") as f:
            raw = json.load(f)
    except Exception:
        LOG.warning("scalp_wfo: failed to read champion", exc_info=True)
        return None
    store = _champion_store_from_raw(raw if isinstance(raw, dict) else None)
    return store if store else None


def load_champion_for_symbol(symbol: str, path: Path = CHAMPION_PATH) -> dict | None:
    """Return the champion row for ``symbol``, or None."""
    store = load_champion(path)
    if not store:
        return None
    return store.get(str(symbol))


def save_champion(result: dict, path: Path = CHAMPION_PATH) -> None:
    """Merge ``result`` under ``result['symbol']`` into the on-disk map (atomic write)."""
    sym = str(result.get("symbol", "")).strip()
    if not sym:
        LOG.warning("scalp_wfo: save_champion skipped — missing symbol in result")
        return
    mode = str(result.get("mode", "") or "").strip()
    if mode not in WFO_REGISTERED_STRATEGY_MODES:
        raise ValueError(
            f"scalp_wfo: save_champion refused — unknown strategy mode {mode!r} for {sym}. "
            f"Registered: {sorted(WFO_REGISTERED_STRATEGY_MODES)}",
        )
    # NM-006: validate params for NaN/inf/negative before writing to disk
    params = result.get("params")
    if isinstance(params, dict):
        import math
        bad = [
            f"{k}={v}" for k, v in params.items()
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v))
        ]
        if bad:
            raise ValueError(
                f"scalp_wfo: save_champion refused — NaN/inf params for {sym}: {bad}"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_raw: dict | None = None
    if path.exists():
        try:
            with path.open("r") as f:
                existing_raw = json.load(f)
        except Exception:
            LOG.warning("scalp_wfo: could not read existing champion for merge", exc_info=True)
            existing_raw = None
    store = _champion_store_from_raw(existing_raw if isinstance(existing_raw, dict) else None)
    store[sym] = result
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(store, f, indent=2, default=str)
    tmp.replace(path)
    LOG.info("scalp_wfo: saved champion for %s to %s (%d symbols)", sym, path, len(store))


def remove_champion_for_symbol(symbol: str, path: Path = CHAMPION_PATH) -> bool:
    """Remove the champion entry for ``symbol`` from the on-disk map. Returns True if removed."""
    if not path.exists():
        return False
    try:
        with path.open("r") as f:
            raw = json.load(f)
    except Exception:
        LOG.warning("scalp_wfo: remove_champion_for_symbol — could not read file", exc_info=True)
        return False
    store = _champion_store_from_raw(raw if isinstance(raw, dict) else None)
    if symbol not in store:
        return False
    del store[symbol]
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(store, f, indent=2, default=str)
    tmp.replace(path)
    LOG.info("scalp_wfo: removed champion for %s (%d symbols remain)", symbol, len(store))
    return True


def wfo_champion_fingerprint(row: dict | None, *, fee_revision: int = 0) -> str | None:
    """Stable hash of mode + params + objective (+ fee revision) for promotion comparisons."""
    if not row or not isinstance(row, dict):
        return None
    params = row.get("params")
    if not isinstance(params, dict):
        return None
    ordered_params = {k: params[k] for k in sorted(params.keys())}
    payload = {
        "fee_revision": int(fee_revision),
        "mode": str(row.get("mode", "") or ""),
        "objective": str(row.get("objective", "") or ""),
        "params": ordered_params,
    }
    canon = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def load_wfo_promotion_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        LOG.warning("scalp_wfo: failed to read promotion meta %s", path, exc_info=True)
        return {}


def save_wfo_promotion_meta(path: Path, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)
    tmp.replace(path)


def append_wfo_promotion_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Core optimisation for one pair — continuous full-grid WFO
# ---------------------------------------------------------------------------

def optimize_pair(
    symbol: str,
    interval: int,
    fee_pct: float,
    slippage_pct: float,
    wfo_cfg: WFOConfig,
    current_params: ParamSet | None = None,
    fill_model: str = "close_slip",
    *,
    contract_size: float = 1.0,
    fee_usd_per_contract_per_leg: float = 0.0,
    backtest_funding_enabled: bool = False,
    backtest_funding_bps_per_hour: float = 0.0,
    progress_hook: Callable[[int, int], None] | None = None,
    breakeven_atr_trigger: float = 0.0,
    trail_atr_trigger: float = 0.0,
    trail_atr_distance: float = 0.0,
    counter_signal_exit: bool = False,
) -> tuple[dict | None, str | None, dict]:
    """Continuous full-grid evaluation for a single pair.

    Evaluates every grid row on one uninterrupted window of ``continuous_eval_hours``
    (preceded by ``continuous_warmup_hours`` for indicator initialization).  The best
    28-day performer, after per-mode normalization and the safety gate, becomes champion.

    Returns ``(champion_dict_or_none, skip_reason_or_none, diagnostics)``.
    ``skip_reason`` is set when no new champion is produced (for logs / JSONL).
    ``diagnostics`` is always a JSON-friendly dict (bar counts, eval hours, etc.).
    """
    eval_hours = float(wfo_cfg.continuous_eval_hours)
    warmup_hours = float(wfo_cfg.continuous_warmup_hours)
    min_trades = int(wfo_cfg.continuous_min_trades)
    total_hours = eval_hours + warmup_hours

    load_days = total_hours / 24.0 + 1.0
    bars = bar_store.load_bars(
        symbol, interval, last_n_days=load_days, trim_anchor="latest_bar",
    )
    if bars is None:
        msg = "no_bars_in_store"
        LOG.info("scalp_wfo: %s/%dm — %s (load_days=%.2f)", symbol, interval, msg, load_days)
        if progress_hook:
            try:
                progress_hook(1, 1)
            except Exception:
                pass
        return None, msg, {"skip": msg, "symbol": symbol, "interval": interval}

    bars = _slice_bars_to_roll_span(bars, total_hours)
    ts = bars["timestamp"]
    n_bars = int(len(ts))
    span_h = (float(ts[-1]) - float(ts[0])) / 3600.0 if n_bars > 1 else 0.0

    if span_h < eval_hours * 0.5:
        msg = f"insufficient_span:span_h={span_h:.2f},required={eval_hours:.2f}"
        LOG.info("scalp_wfo: %s/%dm — %s", symbol, interval, msg)
        if progress_hook:
            try:
                progress_hook(1, 1)
            except Exception:
                pass
        return None, msg, {
            "skip": msg,
            "symbol": symbol,
            "interval": interval,
            "n_bars": n_bars,
            "span_hours": round(span_h, 2),
            "eval_hours": eval_hours,
            "warmup_hours": warmup_hours,
        }

    # n_prefix: first bar of the evaluation window; bars[:n_prefix] initialize indicators only.
    eval_start_ts = float(ts[-1]) - eval_hours * 3600.0
    n_prefix = int(np.searchsorted(ts, eval_start_ts, side="left"))

    grid = build_default_grid(fee_pct=fee_pct, fill_model=fill_model)
    fund_on = bool(backtest_funding_enabled)
    fund_bps = float(backtest_funding_bps_per_hour or 0.0)
    for p in grid:
        p.fee_pct = fee_pct
        p.slippage_pct = slippage_pct
        p.fill_model = fill_model
        p.contract_size = float(contract_size)
        p.fee_usd_per_contract_per_leg = float(fee_usd_per_contract_per_leg)
        p.backtest_funding_enabled = fund_on
        p.backtest_funding_bps_per_hour = fund_bps

    mode_counts: dict[str, int] = {}
    for p in grid:
        mode_counts[p.mode] = mode_counts.get(p.mode, 0) + 1
    LOG.info(
        "scalp_wfo: %s/%dm — continuous eval: grid=%d combos (%s), span=%.1fh "
        "eval_h=%.0f warmup_h=%.0f n_prefix=%d",
        symbol, interval, len(grid),
        ", ".join(f"{m}:{c}" for m, c in sorted(mode_counts.items())),
        span_h, eval_hours, warmup_hours, n_prefix,
    )

    # Live-matching backtest kwargs forwarded to every evaluate_params call.
    _eval_live_kw: dict[str, Any] = {}
    if breakeven_atr_trigger > 0.0:
        _eval_live_kw["breakeven_atr_trigger"] = breakeven_atr_trigger
    if trail_atr_trigger > 0.0 and trail_atr_distance > 0.0:
        _eval_live_kw["trail_atr_trigger"] = trail_atr_trigger
        _eval_live_kw["trail_atr_distance"] = trail_atr_distance
    if counter_signal_exit:
        _eval_live_kw["counter_signal_exit"] = True

    _eval_kw: dict[str, Any] = {"min_entry_bar": n_prefix, **_eval_live_kw}

    # Parallel evaluation: each worker evaluates one grid row on the shared bars.
    n_workers = min(_WFO_WORKER_COUNT, len(grid))
    use_mp = n_workers > 1 and len(grid) >= 20
    raw_results: dict[int, BacktestMetrics] = {}
    eval_exceptions = 0

    if use_mp:
        LOG.info(
            "scalp_wfo: %s/%dm — parallel continuous eval: %d workers × %d grid rows",
            symbol, interval, n_workers, len(grid),
        )
        tasks = [(pi, grid[pi], _eval_kw) for pi in range(len(grid))]
        chunksize = max(1, len(tasks) // (n_workers * 4))
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_mp_init,
            initargs=(bars, {}),
        ) as pool:
            for pi, m, err_key in pool.map(_mp_continuous_eval_one, tasks, chunksize=chunksize):
                if err_key is not None:
                    eval_exceptions += 1
                    continue
                assert m is not None
                raw_results[pi] = m
    else:
        for pi, params in enumerate(grid):
            try:
                m = evaluate_params(bars, params, recency_half_life_bars=0, **_eval_kw)
            except Exception:
                eval_exceptions += 1
                continue
            raw_results[pi] = m

    if progress_hook:
        try:
            progress_hook(0, 2)
        except Exception:
            pass

    # Gate: keep only strategies with enough eval-window trades.
    results: dict[int, BacktestMetrics] = {
        pi: m for pi, m in raw_results.items() if m.trade_count >= min_trades
    }

    if not results:
        msg = "no_strategies_met_min_trades"
        LOG.info(
            "scalp_wfo: %s/%dm — %s (eval_exceptions=%d, raw=%d, min_trades=%d)",
            symbol, interval, msg, eval_exceptions, len(raw_results), min_trades,
        )
        return None, msg, {
            "skip": msg,
            "symbol": symbol,
            "interval": interval,
            "n_bars": n_bars,
            "span_hours": round(span_h, 2),
            "eval_hours": eval_hours,
            "warmup_hours": warmup_hours,
            "n_prefix": n_prefix,
            "grid_size": len(grid),
            "eval_exceptions": eval_exceptions,
            "raw_scored": len(raw_results),
            "min_trades": min_trades,
        }

    rank_metric = str(getattr(wfo_cfg, "period_rank_metric", "total_pnl") or "total_pnl")
    candidates: list[tuple[float, float, int, list[BacktestMetrics]]] = [
        (_continuous_rank_score(wfo_cfg, m), 1.0, pi, [m]) for pi, m in results.items()
    ]

    if progress_hook:
        try:
            progress_hook(1, 2)
        except Exception:
            pass

    if bool(getattr(wfo_cfg, "pick_best_per_mode", True)):
        (best_score, best_stability, best_pi, best_metrics), holdout_sort_diag = (
            _pick_holdout_champion_per_mode_first(candidates, grid, wfo_cfg)
        )
    else:
        (best_score, best_stability, best_pi, best_metrics), holdout_sort_diag = (
            _pick_holdout_champion(candidates, wfo_cfg)
        )

    LOG.info(
        "scalp_wfo: %s/%dm — champion pick pi=%d pnl=%.4f sort_tuple=%s tie_pool=%d",
        symbol, interval, int(best_pi), float(best_score),
        holdout_sort_diag.get("holdout_chosen_sort_tuple"),
        int(holdout_sort_diag.get("holdout_tie_pool_size") or 0),
    )
    params = grid[best_pi]

    m = best_metrics[0]
    if bool(getattr(wfo_cfg, "require_positive_latest_holdout", False)) and float(m.total_pnl) < 0:
        LOG.warning(
            "scalp_wfo: %s/%dm — top candidate rejected: continuous eval pnl=%.4f < 0",
            symbol, interval, float(m.total_pnl),
        )
        return None, "negative_continuous_eval_pnl", {
            "symbol": symbol,
            "interval": interval,
            "n_bars": n_bars,
            "span_hours": round(span_h, 2),
            "eval_hours": eval_hours,
            "warmup_hours": warmup_hours,
            "grid_size": len(grid),
            "best_pi": int(best_pi),
            "continuous_total_pnl": round(float(m.total_pnl), 6),
            "candidates_after_filter": len(candidates),
        }

    # Safety gate: reject if champion params diverge too far from current (within-mode continuity).
    passed, reason = safety_gate(params, current_params, wfo_cfg)
    if not passed:
        LOG.warning("scalp_wfo: safety gate blocked: %s", reason)
        return None, f"safety_gate:{reason}", {
            "symbol": symbol,
            "interval": interval,
            "n_bars": n_bars,
            "span_hours": round(span_h, 2),
            "eval_hours": eval_hours,
            "warmup_hours": warmup_hours,
            "grid_size": len(grid),
            "best_pi": int(best_pi),
        }

    pf = float(m.profit_factor) if m.profit_factor != float("inf") else 999.0
    score_kind = f"continuous_{rank_metric}"
    wfo_mode_scoreboard = _wfo_continuous_mode_scoreboard_rows(
        grid, results, wfo_cfg, champion_pi=int(best_pi), objective="total_pnl",
    )

    if progress_hook:
        try:
            progress_hook(2, 2)
        except Exception:
            pass

    result = {
        "symbol": symbol,
        "interval": interval,
        "timestamp": time.time(),
        "objective": "total_pnl",
        "score": round(float(best_score), 6),
        "stability": 1.0,
        "baseline_score": None,
        "mode": params.mode,
        "evaluation_mode": "continuous",
        "eval_hours": eval_hours,
        "warmup_hours": warmup_hours,
        "score_kind": score_kind,
        "period_rank_metric": rank_metric,
        "windows_evaluated": 1,
        "windows_passed": 1,
        "wfo_promotion_tier": "continuous",
        "wfo_min_windows_used": 1,
        "holdout_rank_by_period": True,
        "exhaustive_grid_holdout": True,
        "sum_holdout_total_pnl": round(float(m.total_pnl), 6),
        "final_holdout_metrics": {},
        "params": {
            "mode": params.mode,
            "max_hold_bars": params.max_hold_bars,
            "atr_stop_mult": params.atr_stop_mult,
            "atr_tp_mult": params.atr_tp_mult,
            "min_signals": params.min_signals,
            "ema_fast": params.ema_fast,
            "ema_slow": params.ema_slow,
            "rsi_period": params.rsi_period,
            "atr_period": params.atr_period,
            "vol_ma_period": params.vol_ma_period,
            "vol_mult": params.vol_mult,
            "rsi_buy_threshold": params.rsi_buy_threshold,
            "rsi_sell_threshold": params.rsi_sell_threshold,
            "rsi_short_threshold": params.rsi_short_threshold,
            "ema_scalp_period": params.ema_scalp_period,
            "ema_scalp_sr_bars": params.ema_scalp_sr_bars,
            "macd_fast_len": params.macd_fast_len,
            "macd_slow_len": params.macd_slow_len,
            "macd_signal_len": params.macd_signal_len,
            "t3_length": params.t3_length,
            "t3_vfactor": params.t3_vfactor,
            "hlc_close_period": params.hlc_close_period,
            "hlc_low_period": params.hlc_low_period,
            "hlc_high_period": params.hlc_high_period,
            "adx_period": params.adx_period,
            "adx_threshold": params.adx_threshold,
            "wae_sensitivity": params.wae_sensitivity,
            "wae_fast_len": params.wae_fast_len,
            "wae_slow_len": params.wae_slow_len,
            "wae_bb_len": params.wae_bb_len,
            "wae_bb_mult": params.wae_bb_mult,
            "supertrend_period": params.supertrend_period,
            "supertrend_factor": params.supertrend_factor,
            "squeeze_bb_period": params.squeeze_bb_period,
            "squeeze_bb_mult": params.squeeze_bb_mult,
            "squeeze_kc_mult": params.squeeze_kc_mult,
            "squeeze_mom_period": params.squeeze_mom_period,
            "qqe_rsi_period": params.qqe_rsi_period,
            "qqe_factor": params.qqe_factor,
            "qqe_smoothing": params.qqe_smoothing,
            "utbot_atr_period": params.utbot_atr_period,
            "utbot_atr_mult": params.utbot_atr_mult,
            "hull_period": params.hull_period,
            "sar_start": params.sar_start,
            "sar_increment": params.sar_increment,
            "sar_max": params.sar_max,
            "sar_chop_ma_fast_period": params.sar_chop_ma_fast_period,
            "sar_chop_ma_long_period": params.sar_chop_ma_long_period,
            "sar_chop_ma_short_period": params.sar_chop_ma_short_period,
            "sar_chop_chop_period": params.sar_chop_chop_period,
            "sar_chop_chop_threshold": params.sar_chop_chop_threshold,
            "sar_chop_macd_fast": params.sar_chop_macd_fast,
            "sar_chop_macd_slow": params.sar_chop_macd_slow,
            "sar_chop_macd_signal": params.sar_chop_macd_signal,
            "sar_chop_use_lucid": params.sar_chop_use_lucid,
            "sar_chop_use_utbot_trail": params.sar_chop_use_utbot_trail,
            "sar_chop_utbot_atr_period": params.sar_chop_utbot_atr_period,
            "sar_chop_utbot_mult": params.sar_chop_utbot_mult,
        },
        "holdout_metrics": {
            "trade_count": m.trade_count,
            "win_count": m.win_count,
            "win_rate": round(float(m.win_rate), 4),
            "total_pnl": round(float(m.total_pnl), 6),
            "expectancy": round(float(m.expectancy), 6),
            "max_drawdown": round(float(m.max_drawdown), 6),
            "max_drawdown_pct": round(float(m.max_drawdown_pct), 2),
            "avg_hold_bars": round(float(m.avg_hold_bars), 2),
            "profit_factor": round(pf, 4),
            "sharpe": round(float(m.sharpe), 4),
            "sortino": round(float(m.sortino), 4),
            "calmar": round(float(m.calmar), 4),
            "recovery_factor": round(float(m.recovery_factor), 4),
            "buy_hold_return": round(float(m.buy_hold_return), 4),
        },
        "holdout_metrics_mean": {
            "total_pnl": round(float(m.total_pnl), 6),
            "profit_factor": round(pf, 4),
            "win_rate": round(float(m.win_rate), 4),
            "avg_trades": float(m.trade_count),
            "windows": 1,
        },
        "grid_size": len(grid),
        "candidates_after_filter": len(candidates),
        "windows_skipped_insufficient_bars": 0,
        "holdout_sort_diag": holdout_sort_diag,
        "wfo_mode_scoreboard": wfo_mode_scoreboard,
    }

    LOG.info(
        "scalp_wfo: %s/%dm — champion mode=%s pnl=%.4f trades=%d eval_h=%.0f",
        symbol, interval, params.mode, float(m.total_pnl), m.trade_count, eval_hours,
    )

    diag_ok = {
        "symbol": symbol,
        "interval": interval,
        "n_bars": n_bars,
        "span_hours": round(span_h, 2),
        "eval_hours": eval_hours,
        "warmup_hours": warmup_hours,
        "n_prefix": n_prefix,
        "grid_size": len(grid),
        "eval_exceptions": eval_exceptions,
        "raw_scored": len(raw_results),
        "strategies_passed_min_trades": len(results),
        "candidates_after_filter": len(candidates),
        "holdout_sort_diag": holdout_sort_diag,
        "wfo_mode_scoreboard": wfo_mode_scoreboard,
    }
    return result, None, diag_ok


def run_adverse_wfo_holdout_check(
    cfg: "ScalpBotConfig",
    wfo_cfg: WFOConfig,
    pair_cfg: "ScalpPairConfig",
    champion_result: dict,
    *,
    slippage_bps: float | None = None,
) -> tuple[bool, str | None, dict]:
    """Re-evaluate the champion on the full continuous eval window with stricter fill/fees."""
    symbol = str(champion_result.get("symbol") or pair_cfg.symbol)
    interval = int(champion_result.get("interval") or pair_cfg.interval)
    mode = str(champion_result.get("mode") or "")

    eval_hours = float(wfo_cfg.continuous_eval_hours)
    warmup_hours = float(wfo_cfg.continuous_warmup_hours)
    total_hours = eval_hours + warmup_hours
    load_days = total_hours / 24.0 + 1.0

    bars = bar_store.load_bars(
        symbol, interval, last_n_days=load_days, trim_anchor="latest_bar",
    )
    if bars is None:
        return True, None, {"adverse_skipped": "no_bars_in_store"}
    bars = _slice_bars_to_roll_span(bars, total_hours)
    ts = bars["timestamp"]
    if len(ts) < 2:
        return True, None, {"adverse_skipped": "insufficient_bars"}

    eval_start_ts = float(ts[-1]) - eval_hours * 3600.0
    n_prefix = int(np.searchsorted(ts, eval_start_ts, side="left"))

    taker_fee = bool(getattr(cfg, "wfo_adverse_assume_taker_fee", True))
    fee_pct = (
        float(getattr(cfg, "fee_bps_taker_per_leg", 7.0) or 0.0) / 10_000.0
        if taker_fee
        else wfo_fee_bps_per_leg(cfg) / 10_000.0
    )
    slip_b = float(slippage_bps) if slippage_bps is not None else float(
        getattr(cfg, "slippage_bps", 0.0) or 0.0,
    )
    slip_pct = slip_b / 10_000.0
    fill_model = str(getattr(cfg, "wfo_adverse_fill_model", "next_open") or "next_open").strip()

    grid = build_default_grid(fee_pct=fee_pct, fill_model=fill_model)
    base_ps = next((p for p in grid if p.mode == mode), None)
    if base_ps is None:
        return False, "adverse_mode_not_in_grid", {
            "mode": mode,
            "grid_modes": sorted({p.mode for p in grid}),
        }

    ov = champion_result.get("params")
    if not isinstance(ov, dict):
        ov = {}
    params = apply_param_dict_overrides(base_ps, ov)
    cs = float(getattr(pair_cfg, "contract_size", 1.0) or 1.0)
    fund_on = bool(getattr(cfg, "backtest_funding_enabled", False))
    fund_bps = float(getattr(cfg, "backtest_funding_bps_per_hour", 0.0) or 0.0)
    fee_usd = float(getattr(cfg, "fee_usd_per_contract_per_leg", 0.0) or 0.0)
    params = replace(
        params,
        fee_pct=fee_pct,
        slippage_pct=slip_pct,
        fill_model=fill_model,
        contract_size=cs,
        fee_usd_per_contract_per_leg=fee_usd,
        backtest_funding_enabled=fund_on,
        backtest_funding_bps_per_hour=fund_bps,
    )

    _adv_live_kw: dict[str, Any] = {}
    _be = float(getattr(pair_cfg, "breakeven_atr_trigger", 0.0) or 0.0)
    if _be > 0.0:
        _adv_live_kw["breakeven_atr_trigger"] = _be
    _tt = float(getattr(pair_cfg, "trail_atr_trigger", 0.0) or 0.0)
    _td = float(getattr(pair_cfg, "trail_atr_distance", 0.0) or 0.0)
    if _tt > 0.0 and _td > 0.0:
        _adv_live_kw["trail_atr_trigger"] = _tt
        _adv_live_kw["trail_atr_distance"] = _td

    try:
        m = evaluate_params(
            bars, params,
            recency_half_life_bars=0,
            min_entry_bar=n_prefix,
            **_adv_live_kw,
        )
    except Exception:
        return True, None, {"adverse_skipped": "eval_exception"}

    diag: dict[str, Any] = {
        "adverse_fill_model": fill_model,
        "adverse_taker_fee": taker_fee,
        "adverse_trade_count": m.trade_count,
        "adverse_total_pnl": round(float(m.total_pnl), 6),
    }

    min_pnl = float(getattr(cfg, "wfo_adverse_min_mean_holdout_pnl", 0.0) or 0.0)
    if min_pnl > 0 and float(m.total_pnl) < min_pnl:
        return False, "adverse_pnl_below_threshold", {**diag, "threshold_pnl": min_pnl}

    ratio = float(getattr(cfg, "wfo_adverse_min_objective_ratio_vs_primary", 0.0) or 0.0)
    primary = float(champion_result.get("score") or 0.0)
    if ratio > 0 and primary > 0 and float(m.total_pnl) < primary * ratio:
        return False, "adverse_objective_vs_primary", {
            **diag,
            "primary_score": primary,
            "min_ratio": ratio,
        }
    return True, None, diag


# ---------------------------------------------------------------------------
# Async loop (runs in scalp runtime)
# ---------------------------------------------------------------------------

def wfo_data_readiness(bot_cfg: "ScalpBotConfig", wfo_cfg: WFOConfig) -> dict:
    """How close stored bars are to a full continuous WFO evaluation (worst pair wins)."""
    eval_hours = float(wfo_cfg.continuous_eval_hours)
    warmup_hours = float(wfo_cfg.continuous_warmup_hours)
    required_hours = eval_hours + warmup_hours
    load_days = required_hours / 24.0 + 1.0

    pairs: dict[str, dict] = {}
    worst_pct = 100.0
    if not bot_cfg.pairs:
        return {
            "overall_progress_pct": 0.0,
            "required_span_hours": round(eval_hours, 1),
            "total_load_hours": round(required_hours, 1),
            "eval_hours": eval_hours,
            "warmup_hours": warmup_hours,
            "pairs": {},
        }

    for pk, pc in bot_cfg.pairs.items():
        bars = bar_store.load_bars(
            pc.symbol, pc.interval, last_n_days=load_days, trim_anchor="latest_bar",
        )
        if bars is None or len(bars["timestamp"]) == 0:
            pairs[pk] = {
                "span_hours": 0.0,
                "bar_count": 0,
                "windows_skipped_insufficient_bars": 0,
                "progress_pct": 0.0,
            }
            worst_pct = 0.0
            continue
        bars = _slice_bars_to_roll_span(bars, required_hours)
        ts = bars["timestamp"]
        span_hours = (float(ts[-1]) - float(ts[0])) / 3600.0
        pct = min(100.0, 100.0 * span_hours / max(required_hours, 0.01))
        pairs[pk] = {
            "span_hours": round(span_hours, 2),
            "bar_count": int(len(ts)),
            "windows_skipped_insufficient_bars": 0,
            "progress_pct": round(pct, 1),
        }
        worst_pct = min(worst_pct, pct)

    return {
        "overall_progress_pct": round(worst_pct, 1),
        "required_span_hours": round(eval_hours, 1),
        "total_load_hours": round(required_hours, 1),
        "eval_hours": eval_hours,
        "warmup_hours": warmup_hours,
        "pairs": pairs,
    }


class ScalpWalkForwardOptimizer:
    """Periodically re-optimizes scalp parameters and writes champion.json."""

    def __init__(
        self,
        cfg: "ScalpBotConfig",
        wfo_cfg: WFOConfig | None = None,
        *,
        session_logger: "SessionLogger | None" = None,
        interval_sec_resolver: Callable[[], float] | None = None,
        wfo_pass_cfg_resolver: Callable[[], WFOConfig] | None = None,
        slippage_bps_resolver: Callable[[], float] | None = None,
        results_callback: "Callable[[dict[str, dict | None]], None] | None" = None,
        champion_path: Path | None = None,
        promotion_log_path: Path | None = None,
        promotion_meta_path: Path | None = None,
    ) -> None:
        self._cfg = cfg
        self._wfo = wfo_cfg or WFOConfig()
        self._session_log = session_logger
        self._interval_sec_resolver = interval_sec_resolver
        self._wfo_pass_cfg_resolver = wfo_pass_cfg_resolver
        self._slippage_bps_resolver = slippage_bps_resolver
        self._results_callback = results_callback
        self._champion_path = Path(champion_path) if champion_path is not None else CHAMPION_PATH
        self._promotion_log_path = (
            Path(promotion_log_path) if promotion_log_path is not None else PROMOTION_LOG_PATH
        )
        self._promotion_meta_path = (
            Path(promotion_meta_path) if promotion_meta_path is not None else PROMOTION_META_PATH
        )
        self._task: asyncio.Task | None = None
        self._loop_started_at: float = 0.0
        self._last_run_ts: float = 0.0
        self._run_progress_lock = threading.Lock()
        self._run_progress_pct: float = 0.0
        self._run_progress_detail: str = ""
        # Wall time of last _set_run_progress (for UI: seconds since a window milestone)
        self._run_progress_at: float = 0.0
        self._wfo_action_log: deque[str] = deque(maxlen=120)
        self._last_wfo_pass: dict | None = None

    def _set_run_progress(self, pct: float, detail: str) -> None:
        with self._run_progress_lock:
            self._run_progress_pct = min(99.0, max(0.0, float(pct)))
            self._run_progress_detail = detail
            self._run_progress_at = time.time()

    def get_run_progress(self) -> tuple[float, str, float]:
        """Returns (pct 0–99, detail, last_milestone_unix_ts)."""
        with self._run_progress_lock:
            return self._run_progress_pct, self._run_progress_detail, float(self._run_progress_at)

    def _sleep_interval_sec(self) -> float:
        """Seconds to wait after a pass; resolver overrides static ``interval_sec`` (e.g. regime risk-on)."""
        if self._interval_sec_resolver is not None:
            try:
                v = float(self._interval_sec_resolver())
                return max(60.0, v)
            except Exception:
                pass
        return max(60.0, float(self._wfo.interval_sec))

    def start(self) -> None:
        if not self._wfo.enabled:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="scalp_wfo")
            LOG.info("ScalpWFO: started (interval=%.0fs)", self._wfo.interval_sec)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def run_once(self, wfo_override: WFOConfig | None = None) -> dict[str, dict | None]:
        """Synchronous single pass — useful for testing or CLI invocation."""
        if wfo_override is not None:
            wfo = wfo_override
        elif self._wfo_pass_cfg_resolver is not None:
            try:
                wfo = self._wfo_pass_cfg_resolver()
            except Exception:
                LOG.exception("ScalpWFO: wfo_pass_cfg_resolver failed; using static WFOConfig")
                wfo = self._wfo
        else:
            wfo = self._wfo
        results: dict[str, dict | None] = {}
        sl = self._session_log
        if sl is not None:
            sl.log_scalp(
                "wfo_pass_start",
                pairs=list(self._cfg.pairs.keys()),
                eval_hours=wfo.continuous_eval_hours,
                warmup_hours=wfo.continuous_warmup_hours,
                interval_sec=wfo.interval_sec,
                objective=wfo.objective,
            )
        pass_ts = time.time()
        self._wfo_action_log.append(
            f"[{time.strftime('%H:%M:%S', time.localtime(pass_ts))}] WFO pass start "
            f"pairs={list(self._cfg.pairs.keys())} objective={wfo.objective}",
        )
        pair_summaries: list[dict] = []
        by_skip: dict[str, int] = {}
        LOG.info(
            "ScalpWFO: run_once start — pairs=%s eval=%.0fh warmup=%.0fh objective=%s",
            list(self._cfg.pairs.keys()),
            wfo.continuous_eval_hours,
            wfo.continuous_warmup_hours,
            wfo.objective,
        )
        n_pairs = len(self._cfg.pairs)
        self._set_run_progress(0.0, "WFO: preparing…")
        pair_items = list(self._cfg.pairs.items())
        for pi, (pair_key, pair_cfg) in enumerate(pair_items):
            eff_slip_bps = float(getattr(self._cfg, "slippage_bps", 0.0) or 0.0)
            if self._slippage_bps_resolver is not None:
                try:
                    eff_slip_bps = float(self._slippage_bps_resolver())
                except Exception:
                    LOG.debug("ScalpWFO: slippage_bps_resolver failed", exc_info=True)

            def _pair_progress_hook(
                done_w: int, total_w: int, *, _pi: int = pi, _pk: str = pair_key
            ) -> None:
                frac = float(done_w) / max(float(total_w), 1.0)
                pct = (_pi + frac) / max(float(n_pairs), 1.0) * 100.0
                label = str(_pk).replace("_", "/")
                if total_w <= 2:
                    phase = (
                        "warming up"
                        if done_w < 1
                        else "scoring grid"
                        if done_w < total_w
                        else "done"
                    )
                    detail = f"{label} · continuous eval · {phase}"
                else:
                    detail = f"{label} · step {done_w}/{total_w}"
                self._set_run_progress(pct, detail)

            current = _params_from_config(pair_cfg, self._cfg, slippage_bps=eff_slip_bps)
            fee_pct = wfo_fee_bps_per_leg(self._cfg) / 10_000.0
            slippage_pct = eff_slip_bps / 10_000.0
            fill_model = getattr(self._cfg, "backtest_fill_model", "close_slip")

            roll_pre_h = wfo_effective_roll_span_hours(wfo)
            span_pre_h, span_pre_ok = wfo_verify_stored_roll_coverage(
                pair_cfg.symbol, pair_cfg.interval, roll_pre_h,
            )
            if not span_pre_ok:
                buf_h = float(getattr(self._cfg, "wfo_backfill_buffer_hours", 24.0) or 0.0)
                need_h = roll_pre_h + max(0.0, buf_h)
                LOG.warning(
                    "ScalpWFO: stored span short for %s (%.1fh vs ≥%.1fh of roll) — REST top-up %.1fh",
                    pair_cfg.symbol,
                    span_pre_h,
                    roll_pre_h * 0.92,
                    need_h,
                )
                bar_store.notify_ui_alert(
                    "warning",
                    "WFO history shorter than roll window",
                    f"{pair_cfg.symbol}: stored ~{span_pre_h:.1f}h vs ≥{roll_pre_h * 0.92:.0f}h needed — "
                    f"running REST backfill (~{need_h:.0f}h). Check bar files if this repeats.",
                    "scalp_wfo",
                )
                try:
                    wr = asyncio.run(
                        bar_store.backfill_from_rest(
                            pair_cfg.symbol,
                            pair_cfg.interval,
                            need_h,
                            venue=getattr(self._cfg, "venue", "coinbase_perps"),
                        ),
                    )
                    LOG.info("ScalpWFO: REST top-up for %s — new_rows=%d", pair_cfg.symbol, int(wr))
                    if int(wr) > 0:
                        bar_store.notify_ui_alert(
                            "info",
                            "WFO tape backfill wrote rows",
                            f"{pair_cfg.symbol}: +{int(wr)} candles from REST.",
                            "scalp_wfo",
                        )
                except RuntimeError as e:
                    LOG.warning("ScalpWFO: REST top-up skipped (event loop): %s", e)
                except Exception:
                    LOG.exception("ScalpWFO: REST top-up failed for %s", pair_cfg.symbol)
                    bar_store.notify_ui_alert(
                        "error",
                        "WFO REST backfill failed",
                        f"{pair_cfg.symbol}: could not refill bars — see server log.",
                        "scalp_wfo",
                    )

            result, skip, diag = optimize_pair(
                symbol=pair_cfg.symbol,
                interval=pair_cfg.interval,
                fee_pct=fee_pct,
                slippage_pct=slippage_pct,
                wfo_cfg=wfo,
                current_params=current,
                fill_model=fill_model,
                contract_size=float(getattr(pair_cfg, "contract_size", 1.0) or 1.0),
                fee_usd_per_contract_per_leg=float(
                    getattr(self._cfg, "fee_usd_per_contract_per_leg", 0.0) or 0.0
                ),
                backtest_funding_enabled=bool(
                    getattr(self._cfg, "backtest_funding_enabled", False)
                ),
                backtest_funding_bps_per_hour=float(
                    getattr(self._cfg, "backtest_funding_bps_per_hour", 0.0) or 0.0
                ),
                progress_hook=_pair_progress_hook if n_pairs else None,
                breakeven_atr_trigger=float(
                    getattr(pair_cfg, "breakeven_atr_trigger", 0.0) or 0.0
                ),
                trail_atr_trigger=float(
                    getattr(pair_cfg, "trail_atr_trigger", 0.0) or 0.0
                ),
                trail_atr_distance=float(
                    getattr(pair_cfg, "trail_atr_distance", 0.0) or 0.0
                ),
                counter_signal_exit=True,
            )
            fee_rev = int(getattr(self._cfg, "scalp_fee_assumption_revision", 0) or 0)
            prior = load_champion_for_symbol(pair_cfg.symbol, path=self._champion_path)
            prior_fp = wfo_champion_fingerprint(prior, fee_revision=fee_rev)
            grid_size = int(diag.get("grid_size") or 0)
            candidate_fp = wfo_champion_fingerprint(result, fee_revision=fee_rev) if result else None
            champion_changed = bool(
                result is not None and candidate_fp is not None and candidate_fp != prior_fp,
            )
            gate_reason: str | None = None
            saved = False
            adv_diag: dict | None = None
            if result is not None:
                cd = float(getattr(wfo, "champion_cooldown_sec", 0.0) or 0.0)
                if cd > 0:
                    meta = load_wfo_promotion_meta(self._promotion_meta_path)
                    sym_key = str(pair_cfg.symbol).strip()
                    sym_meta = meta.get(sym_key) if isinstance(meta.get(sym_key), dict) else {}
                    last_ts = float(sym_meta.get("last_promoted_ts") or 0.0)
                    if last_ts > 0 and (time.time() - last_ts) < cd:
                        gate_reason = "champion_cooldown"
                if gate_reason is None and bool(
                    getattr(wfo, "require_holdout_beat_prior", False),
                ) and prior:
                    new_s = float(result.get("score") or 0.0)
                    old_s = float(prior.get("score") or 0.0)
                    eps = float(getattr(wfo, "prior_beat_epsilon", 1e-6) or 0.0)
                    if new_s < old_s + eps:
                        gate_reason = "champion_not_better_than_prior"
                if gate_reason is None and float(
                    getattr(self._cfg, "wfo_min_champion_score_delta", 0.0) or 0.0,
                ) > 0 and prior:
                    new_s = float(result.get("score") or 0.0)
                    old_s = float(prior.get("score") or 0.0)
                    delta_req = float(getattr(self._cfg, "wfo_min_champion_score_delta", 0.0) or 0.0)
                    if new_s < old_s + delta_req:
                        gate_reason = "champion_min_score_delta"
                if gate_reason is None and bool(
                    getattr(self._cfg, "wfo_adverse_check_enabled", False),
                ):
                    ok_adv, adv_reason, adv_diag = run_adverse_wfo_holdout_check(
                        self._cfg, wfo, pair_cfg, result, slippage_bps=eff_slip_bps,
                    )
                    if not ok_adv:
                        gate_reason = adv_reason or "wfo_adverse_failed"
                if gate_reason is None:
                    save_champion(result, path=self._champion_path)
                    saved = True
                    meta = load_wfo_promotion_meta(self._promotion_meta_path)
                    sym_key = str(pair_cfg.symbol).strip()
                    sym_m = dict(meta.get(sym_key) or {}) if isinstance(meta.get(sym_key), dict) else {}
                    sym_m["last_promoted_ts"] = time.time()
                    sym_m["last_saved_fingerprint"] = candidate_fp
                    meta[sym_key] = sym_m
                    save_wfo_promotion_meta(self._promotion_meta_path, meta)

            if saved:
                outcome = "champion_saved"
                results[pair_key] = result
            elif result is not None and gate_reason:
                outcome = "champion_gated"
                results[pair_key] = None
                by_skip[gate_reason] = by_skip.get(gate_reason, 0) + 1
            else:
                outcome = "no_champion"
                results[pair_key] = None
                if skip:
                    by_skip[skip] = by_skip.get(skip, 0) + 1

            promo_rec = {
                "ts": time.time(),
                "pair_key": pair_key,
                "symbol": pair_cfg.symbol,
                "interval": pair_cfg.interval,
                "outcome": outcome,
                "skip_reason": skip,
                "gate_reason": gate_reason,
                "grid_size": grid_size,
                "champion_fingerprint_prior": prior_fp,
                "champion_fingerprint_candidate": candidate_fp,
                "champion_changed": champion_changed,
                "fee_assumption_revision": fee_rev,
                "objective": wfo.objective,
                "wfo_promotion_tier": diag.get("wfo_promotion_tier")
                or diag.get("wfo_promotion_tier_attempted"),
                "min_windows_primary": diag.get("min_windows_primary"),
                "min_windows_effective": diag.get("min_windows_effective"),
                "windows_skipped_insufficient_bars": diag.get("windows_skipped_insufficient_bars"),
            }
            if result is not None:
                promo_rec["candidate_mode"] = result.get("mode")
                promo_rec["candidate_score"] = result.get("score")
                hsd = result.get("holdout_sort_diag")
                if isinstance(hsd, dict):
                    promo_rec["holdout_sort_diag"] = hsd
            if adv_diag is not None:
                promo_rec["wfo_adverse_diag"] = adv_diag
            try:
                append_wfo_promotion_record(self._promotion_log_path, promo_rec)
            except Exception:
                LOG.exception("ScalpWFO: append promotion log failed")

            ps = {
                "pair_key": pair_key,
                "outcome": outcome,
                "skip_reason": skip,
                "gate_reason": gate_reason,
                "n_windows": diag.get("n_windows"),
                "windows_skipped_insufficient_bars": diag.get("windows_skipped_insufficient_bars"),
                "wfo_promotion_tier": diag.get("wfo_promotion_tier")
                or diag.get("wfo_promotion_tier_attempted"),
                "min_windows_primary": diag.get("min_windows_primary"),
                "min_windows_effective": diag.get("min_windows_effective"),
                "span_hours": diag.get("span_hours"),
                "bar_count": diag.get("n_bars"),
                "grid_size": grid_size,
                "champion_changed": champion_changed,
                "wfo_mode_scoreboard": diag.get("wfo_mode_scoreboard") or [],
            }
            pair_summaries.append(ps)
            log_line = (
                f"[{time.strftime('%H:%M:%S', time.localtime())}] {pair_key} "
                f"{pair_cfg.symbol}: {outcome}"
                + (f" skip={skip}" if skip else "")
                + (f" gate={gate_reason}" if gate_reason else "")
                + (
                    f" mode={result.get('mode') if result else ''}"
                    if outcome == "champion_saved"
                    else ""
                )
            )
            self._wfo_action_log.append(log_line)
            if sl is not None:
                row = {
                    "pair_key": pair_key,
                    "symbol": pair_cfg.symbol,
                    "interval": pair_cfg.interval,
                    "outcome": outcome,
                    "wfo_diag": diag,
                    "grid_size": grid_size,
                    "champion_fingerprint_prior": prior_fp,
                    "champion_fingerprint_candidate": candidate_fp,
                    "champion_changed": champion_changed,
                    "fee_assumption_revision": fee_rev,
                }
                if skip:
                    row["skip_reason"] = skip
                if gate_reason:
                    row["gate_reason"] = gate_reason
                if result is not None:
                    row["mode"] = result.get("mode")
                    row["score"] = result.get("score")
                    row["windows_evaluated"] = result.get("windows_evaluated")
                    row["holdout_metrics"] = result.get("holdout_metrics")
                    row["objective"] = result.get("objective", wfo.objective)
                sl.log_scalp("wfo_pair_result", **row)
        self._set_run_progress(99.0, "WFO: saving results…")
        champs = [k for k, v in results.items() if v is not None]
        LOG.info(
            "ScalpWFO: run_once complete — champions=%s",
            champs if champs else "(none)",
        )
        self._last_wfo_pass = {
            "ts": pass_ts,
            "objective": wfo.objective,
            "champion_pairs": champs,
            "n_pairs": len(results),
            "champion_count": len(champs),
            "by_skip_reason": dict(sorted(by_skip.items(), key=lambda x: -x[1])),
            "pairs": pair_summaries,
        }
        self._wfo_action_log.append(
            f"[{time.strftime('%H:%M:%S', time.localtime())}] WFO pass complete "
            f"champions={len(champs)}/{len(results)} by_skip={by_skip!r}",
        )
        if sl is not None:
            sl.log_scalp(
                "wfo_pass_complete",
                champion_pairs=champs,
                n_pairs=len(results),
                champion_count=len(champs),
                by_skip_reason=dict(sorted(by_skip.items(), key=lambda x: -x[1])),
                pairs=pair_summaries,
            )
        try:
            from .scalp_fee_assumptions import fee_assumption_snapshot, save_fee_assumption_state

            save_fee_assumption_state(fee_assumption_snapshot(self._cfg))
        except Exception:
            LOG.exception("ScalpWFO: failed to persist fee assumption snapshot")
        return results

    def scheduler_status(self) -> dict:
        """Seconds until the next scheduled WFO pass (sleep-then-run loop)."""
        if not self._wfo.enabled:
            return {"enabled": False, "seconds_until_next": 0, "interval_sec": 0.0}
        now = time.time()
        interval = self._sleep_interval_sec()
        if self._last_run_ts > 0:
            next_at = self._last_run_ts + interval
        elif self._loop_started_at > 0:
            next_at = self._loop_started_at + interval
        else:
            next_at = now + interval
        sec_left = max(0.0, next_at - now)
        return {
            "enabled": True,
            "interval_sec": interval,
            "seconds_until_next": int(sec_left),
            "last_run_ts": self._last_run_ts,
            "loop_started_at": self._loop_started_at,
        }

    def ui_snapshot(self, bot_cfg: "ScalpBotConfig", *, champion_active: bool) -> dict:
        data = wfo_data_readiness(bot_cfg, self._wfo)
        sched = self.scheduler_status()
        data_pct = float(data.get("overall_progress_pct", 0.0))
        last = self._last_wfo_pass
        log_lines = list(self._wfo_action_log)
        return {
            **data,
            **sched,
            "champion_active": champion_active,
            "data_progress_pct": round(data_pct, 1),
            "ui_progress_pct": 100.0 if champion_active else round(data_pct, 1),
            "last_wfo_pass": last,
            "wfo_action_log": "\n".join(log_lines),
        }

    async def _loop(self) -> None:
        self._loop_started_at = time.time()
        self._last_run_ts = 0.0

        try:
            LOG.info("ScalpWFO: running initial pass (in thread)...")
            _results = await asyncio.to_thread(self.run_once)
            self._last_run_ts = time.time()
            if self._results_callback is not None:
                try:
                    self._results_callback(_results)
                except Exception:
                    LOG.warning("ScalpWFO: results_callback raised on initial pass", exc_info=True)
            _next = self._sleep_interval_sec()
            LOG.info(
                "ScalpWFO: initial pass finished at %s, next in %.0fs",
                time.strftime("%H:%M:%S", time.localtime(self._last_run_ts)),
                _next,
            )
        except Exception:
            LOG.exception("ScalpWFO: initial pass failed — will retry on schedule")

        while True:
            try:
                await asyncio.sleep(self._sleep_interval_sec())
                _results = await asyncio.to_thread(self.run_once)
                self._last_run_ts = time.time()
                if self._results_callback is not None:
                    try:
                        self._results_callback(_results)
                    except Exception:
                        LOG.warning("ScalpWFO: results_callback raised on scheduled pass", exc_info=True)
                _next = self._sleep_interval_sec()
                LOG.info(
                    "ScalpWFO: scheduled pass finished at %s, next in %.0fs",
                    time.strftime("%H:%M:%S", time.localtime(self._last_run_ts)),
                    _next,
                )
            except asyncio.CancelledError:
                break
            except Exception:
                LOG.exception("ScalpWFO: error in optimization loop")
                await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def param_set_from_champion_row(
    champion_row: dict | None,
    pair_cfg: "ScalpPairConfig",
    bot_cfg: "ScalpBotConfig",
) -> ParamSet | None:
    """Build a ParamSet from a WFO champion entry (``data/scalp_champion.json`` row).

    Returns ``None`` if the row has no ``params`` dict. Merges champion params over
    ``_params_from_config`` so missing keys keep live pair defaults.
    """
    if not champion_row or not isinstance(champion_row.get("params"), dict):
        return None
    ci = champion_row.get("interval")
    if ci is not None:
        try:
            if int(ci) != int(pair_cfg.interval):
                LOG.warning(
                    "scalp_wfo: champion interval %s != pair interval %sm — ignoring champion params for %s",
                    ci,
                    pair_cfg.interval,
                    pair_cfg.symbol,
                )
                return None
        except (TypeError, ValueError):
            LOG.warning(
                "scalp_wfo: invalid champion interval %r for %s — ignoring champion params",
                ci,
                pair_cfg.symbol,
            )
            return None
    base = _params_from_config(pair_cfg, bot_cfg)
    champ_params = dict(champion_row["params"])
    for _k in (
        "fee_pct",
        "slippage_pct",
        "contract_size",
        "fee_usd_per_contract_per_leg",
        "backtest_funding_enabled",
        "backtest_funding_bps_per_hour",
    ):
        champ_params.pop(_k, None)
    merged = apply_param_dict_overrides(base, champ_params)
    mode = str(champion_row.get("mode") or merged.mode).strip()
    if mode == "auto":
        fb = getattr(pair_cfg, "auto_mode_fallback", None) or getattr(
            bot_cfg, "auto_mode_fallback", "sar_chop"
        )
        mode = normalize_auto_mode_fallback(fb)
    # NM-007: reject champion rows whose mode was removed from the registry
    if mode not in WFO_REGISTERED_STRATEGY_MODES:
        LOG.error(
            "scalp_wfo: champion row for %s has unregistered mode %r — ignoring champion",
            pair_cfg.symbol, mode,
        )
        return None
    return replace(merged, mode=mode)


def _params_from_config(
    pair_cfg: "ScalpPairConfig",
    bot_cfg: "ScalpBotConfig",
    *,
    slippage_bps: float | None = None,
) -> ParamSet:
    raw_mode = str(getattr(pair_cfg, "strategy_mode", "auto"))
    if raw_mode == "auto":
        fb = getattr(pair_cfg, "auto_mode_fallback", None) or getattr(
            bot_cfg, "auto_mode_fallback", "sar_chop"
        )
        mode = normalize_auto_mode_fallback(fb)
    else:
        mode = raw_mode
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
        max_hold_bars=getattr(pair_cfg, "max_hold_bars", 15),
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
        rsi_buy_threshold=getattr(pair_cfg, "rsi_buy_threshold", 10.0),
        rsi_sell_threshold=getattr(pair_cfg, "rsi_sell_threshold", 50.0),
        rsi_short_threshold=float(getattr(pair_cfg, "rsi_short_threshold", 70.0)),
        ema_scalp_period=getattr(pair_cfg, "ema_scalp_period", 20),
        ema_scalp_sr_bars=getattr(pair_cfg, "ema_scalp_sr_bars", 8),
        macd_fast_len=getattr(pair_cfg, "macd_fast_len", 8),
        macd_slow_len=getattr(pair_cfg, "macd_slow_len", 10),
        macd_signal_len=getattr(pair_cfg, "macd_signal_len", 8),
        t3_length=getattr(pair_cfg, "t3_length", 7),
        t3_vfactor=float(getattr(pair_cfg, "t3_vfactor", 0.7)),
        hlc_close_period=getattr(pair_cfg, "hlc_close_period", 5),
        hlc_low_period=getattr(pair_cfg, "hlc_low_period", 13),
        hlc_high_period=getattr(pair_cfg, "hlc_high_period", 34),
        adx_period=getattr(pair_cfg, "adx_period", 14),
        adx_threshold=float(getattr(pair_cfg, "adx_threshold", 20.0)),
        wae_sensitivity=float(getattr(pair_cfg, "wae_sensitivity", 150.0)),
        wae_fast_len=getattr(pair_cfg, "wae_fast_len", 20),
        wae_slow_len=getattr(pair_cfg, "wae_slow_len", 40),
        wae_bb_len=getattr(pair_cfg, "wae_bb_len", 20),
        wae_bb_mult=float(getattr(pair_cfg, "wae_bb_mult", 2.0)),
        fill_model=getattr(bot_cfg, "backtest_fill_model", "close_slip"),
        backtest_funding_enabled=bool(getattr(bot_cfg, "backtest_funding_enabled", False)),
        backtest_funding_bps_per_hour=float(
            getattr(bot_cfg, "backtest_funding_bps_per_hour", 0.0) or 0.0
        ),
        supertrend_period=getattr(pair_cfg, "supertrend_period", 10),
        supertrend_factor=float(getattr(pair_cfg, "supertrend_factor", 3.0)),
        squeeze_bb_period=getattr(pair_cfg, "squeeze_bb_period", 20),
        squeeze_bb_mult=float(getattr(pair_cfg, "squeeze_bb_mult", 2.0)),
        squeeze_kc_mult=float(getattr(pair_cfg, "squeeze_kc_mult", 1.5)),
        squeeze_mom_period=getattr(pair_cfg, "squeeze_mom_period", 12),
        qqe_rsi_period=getattr(pair_cfg, "qqe_rsi_period", 14),
        qqe_factor=float(getattr(pair_cfg, "qqe_factor", 4.238)),
        qqe_smoothing=getattr(pair_cfg, "qqe_smoothing", 5),
        utbot_atr_period=getattr(pair_cfg, "utbot_atr_period", 10),
        utbot_atr_mult=float(getattr(pair_cfg, "utbot_atr_mult", 1.0)),
        hull_period=getattr(pair_cfg, "hull_period", 38),
        sar_start=float(getattr(pair_cfg, "sar_start", 0.02)),
        sar_increment=float(getattr(pair_cfg, "sar_increment", 0.02)),
        sar_max=float(getattr(pair_cfg, "sar_max", 0.2)),
        sar_chop_ma_fast_period=int(getattr(pair_cfg, "sar_chop_ma_fast_period", 7)),
        sar_chop_ma_long_period=int(getattr(pair_cfg, "sar_chop_ma_long_period", 200)),
        sar_chop_ma_short_period=int(getattr(pair_cfg, "sar_chop_ma_short_period", 50)),
        sar_chop_chop_period=int(getattr(pair_cfg, "sar_chop_chop_period", 14)),
        sar_chop_chop_threshold=float(getattr(pair_cfg, "sar_chop_chop_threshold", 68.0)),
        sar_chop_macd_fast=int(getattr(pair_cfg, "sar_chop_macd_fast", 12)),
        sar_chop_macd_slow=int(getattr(pair_cfg, "sar_chop_macd_slow", 26)),
        sar_chop_macd_signal=int(getattr(pair_cfg, "sar_chop_macd_signal", 9)),
        sar_chop_use_lucid=bool(getattr(pair_cfg, "sar_chop_use_lucid", True)),
        sar_chop_use_utbot_trail=bool(getattr(pair_cfg, "sar_chop_use_utbot_trail", True)),
        sar_chop_utbot_atr_period=int(getattr(pair_cfg, "sar_chop_utbot_atr_period", 10)),
        sar_chop_utbot_mult=float(getattr(pair_cfg, "sar_chop_utbot_mult", 2.0)),
    )
