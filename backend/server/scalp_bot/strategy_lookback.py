"""Per-strategy vector backtest over a fixed lookback window (UI).

Uses the same ``evaluate_params`` path as WFO, with each mode and the pair's
current config (stops, TP, RSI thresholds, etc.). Trades are simulated on the
full loaded bar series; **reported** metrics keep only entries in the last
``strategy_lookback_hours`` via ``min_entry_bar`` (matches Analytics subtitle
and no-champion bootstrap windowing).
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

from . import bar_store
from .scalp_vec_backtest import (
    evaluate_params,
    min_entry_bar_for_last_hours,
)
from .scalp_mode_resolution import resolve_auto_mode
from .scalp_wfo import _params_from_config

if TYPE_CHECKING:
    from .scalp_config import ScalpBotConfig, ScalpPairConfig

LOG = logging.getLogger(__name__)

STRATEGY_MODES = (
    "daviddtech_scalp", "ema_momentum", "rsi_reversion", "ema_scalp",
    "supertrend", "squeeze_momentum", "qqe_mod", "utbot_alert", "hull_suite",
    "macd_scalp", "sar_chop",
)

# Modes need at least this many trades in the lookback snapshot to rank by expectancy.
EXPECTANCY_MIN_TRADES = 2

# Fixed window for mode selection when no WFO champion exists for the pair's symbol.
NO_CHAMPION_BOOTSTRAP_HOURS = 168.0


def _return_pct_vs_capital(total_pnl: float, allocated_usd: float) -> float:
    """Percent of configured scalp capital (PnL / allocated), not vs spot price."""
    cap = float(allocated_usd)
    if not np.isfinite(cap) or cap <= 0:
        cap = 1000.0
    return 100.0 * float(total_pnl) / cap


def champion_row_matches_pair_interval(row: dict, pair_interval: int) -> bool:
    """True if champion row may be used for this bar size.

    Missing ``interval`` on the row (legacy JSON) is treated as a match.
    """
    ci = row.get("interval")
    if ci is None:
        return True
    try:
        return int(ci) == int(pair_interval)
    except (TypeError, ValueError):
        return False


def pair_has_wfo_champion(
    champion: dict | None,
    symbol: str,
    pair_interval: int | None = None,
) -> bool:
    """True if champion store has a row for this exchange symbol.

    Accepts normalized ``symbol -> champion_row`` map from ``load_champion()``,
    or a legacy single champion dict (top-level ``symbol`` field).

    When ``pair_interval`` is set and the row includes ``interval``, both must match
    or this returns False (stale champion after changing bar size).
    """
    if not champion or not isinstance(champion, dict):
        return False
    sym = str(symbol)
    row = champion.get(sym)
    if not isinstance(row, dict):
        if str(champion.get("symbol", "")) == sym:
            row = champion
        else:
            return False
    if pair_interval is not None and not champion_row_matches_pair_interval(row, pair_interval):
        return False
    return True


# Mode sources that may open new legs when ``require_champion_to_trade`` is on and a
# champion row exists on disk for this pair's symbol/interval.
CHAMPION_GATED_ENTRY_SOURCES: frozenset[str] = frozenset({
    "wfo_champion",
    "forward_demotion",
    "param_tuner_override",
})


def live_entry_allowed_champion_gate(
    bot_cfg: "ScalpBotConfig",
    champion: dict | None,
    pair_cfg: "ScalpPairConfig",
    mode_source: str,
) -> bool:
    """True when a new live entry is allowed under ``require_champion_to_trade``.

  Requires a persisted WFO champion row for the pair *and* a WFO-backed
  ``mode_source`` label. Bootstrap/tuner/config never qualify even if a stale
  in-memory label still says ``wfo_champion``.
    """
    if not bool(getattr(bot_cfg, "require_champion_to_trade", False)):
        return True
    if not pair_has_wfo_champion(champion, pair_cfg.symbol, pair_cfg.interval):
        return False
    return str(mode_source or "") in CHAMPION_GATED_ENTRY_SOURCES


def _slice_bars_to_hours(
    bars: dict[str, np.ndarray],
    lookback_hours: float,
) -> dict[str, np.ndarray]:
    """Keep only rows whose timestamp is within ``lookback_hours`` of the last bar."""
    ts = bars["timestamp"]
    if len(ts) == 0:
        return bars
    latest = float(ts[-1])
    cutoff = latest - lookback_hours * 3600.0
    mask = ts >= cutoff
    if int(mask.sum()) < 4:
        return bars
    return {k: v[mask] for k, v in bars.items()}


def per_strategy_lookback_metrics(
    pair_cfg: "ScalpPairConfig",
    bot_cfg: "ScalpBotConfig",
    *,
    lookback_hours: float,
) -> dict[str, dict] | None:
    """Backtest each mode on stored bars; return mode -> metrics dict.

    Simulates on the **full** loaded series (indicator warmup), but **metrics**
    (trades, PnL, win rate, expectancy) include only trades whose **entry bar**
    falls in the last ``lookback_hours`` — same rule as ``min_entry_bar`` in
    ``evaluate_params`` / no-champion bootstrap.

    Computes flat and recency-weighted win rate / PnL, plus expectancy and
    return_pct vs ``[scalp].allocated_capital_usd`` (UI / diagnostics).
    """
    if lookback_hours <= 0:
        return None
    load_days = max(lookback_hours / 24.0 + 1.0, 2.0)
    bars = bar_store.load_bars(pair_cfg.symbol, pair_cfg.interval, last_n_days=load_days)
    if bars is None or len(bars["timestamp"]) < 4:
        return None

    min_bar = min_entry_bar_for_last_hours(bars, lookback_hours)
    close = bars["close"]
    cap_usd = float(getattr(bot_cfg, "allocated_capital_usd", 1000.0) or 1000.0)

    n_bars = len(close)
    half_life = max(10.0, n_bars / 3.0)

    base = _params_from_config(pair_cfg, bot_cfg)
    out: dict[str, dict] = {}
    for mode in STRATEGY_MODES:
        params = replace(base, mode=mode)
        m_flat = evaluate_params(bars, params, min_entry_bar=min_bar)
        m_weighted = evaluate_params(
            bars, params, recency_half_life_bars=half_life, min_entry_bar=min_bar,
        )
        pf = m_flat.profit_factor
        pf_out = round(float(pf), 4) if pf != float("inf") else 999.0
        out[mode] = {
            "win_rate": round(float(m_flat.win_rate), 4),
            "trades": int(m_flat.trade_count),
            "pnl": round(float(m_flat.total_pnl), 6),
            "expectancy": round(float(m_flat.expectancy), 6),
            "return_pct": round(_return_pct_vs_capital(m_flat.total_pnl, cap_usd), 6),
            "weighted_win_rate": round(float(m_weighted.win_rate), 4),
            "weighted_pnl": round(float(m_weighted.total_pnl), 6),
            "weighted_return_pct": round(_return_pct_vs_capital(m_weighted.total_pnl, cap_usd), 6),
            "profit_factor": pf_out,
        }
    return out


def best_mode_bootstrap_no_champion(
    pair_cfg: "ScalpPairConfig",
    bot_cfg: "ScalpBotConfig",
    *,
    lookback_hours: float = NO_CHAMPION_BOOTSTRAP_HOURS,
) -> str:
    """Pick strategy mode using only trades that *open* in the last ``lookback_hours``.

    Ranks by **return_pct** (PnL vs price at window start bar). Used when no WFO
    champion exists for this pair's symbol. Falls back to ``pair_cfg.strategy_mode``.
    """
    if lookback_hours <= 0:
        fb = getattr(pair_cfg, "auto_mode_fallback", None) or getattr(
            bot_cfg, "auto_mode_fallback", "sar_chop"
        )
        return resolve_auto_mode(pair_cfg.strategy_mode, champion_row=None, auto_mode_fallback=fb)
    load_days = max(lookback_hours / 24.0 + 1.0, 2.0)
    bars = bar_store.load_bars(pair_cfg.symbol, pair_cfg.interval, last_n_days=load_days)
    if bars is None or len(bars["timestamp"]) < 4:
        fb = getattr(pair_cfg, "auto_mode_fallback", None) or getattr(
            bot_cfg, "auto_mode_fallback", "sar_chop"
        )
        resolved = resolve_auto_mode(
            pair_cfg.strategy_mode, champion_row=None, auto_mode_fallback=fb,
        )
        LOG.info(
            "strategy_bootstrap %s: no bars for %s/%dm — fallback '%s'",
            pair_cfg.symbol, pair_cfg.symbol, pair_cfg.interval, resolved,
        )
        return resolved

    min_bar = min_entry_bar_for_last_hours(bars, lookback_hours)

    base = _params_from_config(pair_cfg, bot_cfg)
    fb = getattr(pair_cfg, "auto_mode_fallback", None) or getattr(
        bot_cfg, "auto_mode_fallback", "ema_momentum"
    )
    best_mode = resolve_auto_mode(
        pair_cfg.strategy_mode, champion_row=None, auto_mode_fallback=fb,
    )
    best_rp = -float("inf")
    stats: dict[str, tuple[float, int, float]] = {}

    for mode in STRATEGY_MODES:
        params = replace(base, mode=mode)
        m = evaluate_params(bars, params, min_entry_bar=min_bar)
        rp = _return_pct_vs_capital(m.total_pnl, float(getattr(bot_cfg, "allocated_capital_usd", 1000.0) or 1000.0))
        stats[mode] = (rp, int(m.trade_count), float(m.expectancy))
        if m.trade_count < 1:
            continue
        if rp > best_rp:
            best_rp = rp
            best_mode = mode

    if best_rp == -float("inf"):
        LOG.info(
            "strategy_bootstrap %s: 0 in-window trades in %.1fh — fallback '%s' | %s",
            pair_cfg.symbol, lookback_hours, best_mode,
            {m: (s[1], round(s[0], 4)) for m, s in stats.items()},
        )
        return best_mode

    LOG.info(
        "strategy_bootstrap %s: best mode=%s return_pct=%.4f%% (%.1fh window) | all: %s",
        pair_cfg.symbol,
        best_mode,
        best_rp,
        lookback_hours,
        {m: (s[1], round(s[0], 4), round(s[2], 4)) for m, s in stats.items()},
    )
    return best_mode


def nemesis_advisory_champion_vs_bootstrap(
    pair_cfg: "ScalpPairConfig",
    bot_cfg: "ScalpBotConfig",
    *,
    champion_mode: str,
    bootstrap_lookback_hours: float | None = None,
) -> dict:
    """Compare WFO champion mode to short-horizon bootstrap (Nemesis lens A vs lens B).

    **Does not change trading mode.** WFO champion remains authoritative for execution;
    bootstrap is a *regime* read on ``NO_CHAMPION_BOOTSTRAP_HOURS``. When they differ,
    operators get a structured signal to schedule re-WFO or watch for drift.

    This mirrors Nemesis Phase 4: surface the gap between coupled decisions
    (OOS champion vs recent microstructure) instead of silently ignoring one side.
    """
    cm = str(champion_mode or "").strip()
    if not cm:
        return {"aligned": True, "champion_mode": "", "bootstrap_mode": "", "note": "no_champion_mode"}
    bl_h = (
        float(NO_CHAMPION_BOOTSTRAP_HOURS)
        if bootstrap_lookback_hours is None
        else float(bootstrap_lookback_hours)
    )
    boot = best_mode_bootstrap_no_champion(pair_cfg, bot_cfg, lookback_hours=bl_h)
    if boot == cm:
        return {
            "aligned": True,
            "champion_mode": cm,
            "bootstrap_mode": boot,
            "resolution": "nemesis_converged",
        }
    return {
        "aligned": False,
        "champion_mode": cm,
        "bootstrap_mode": boot,
        "resolution": "wfo_authoritative",
        "note": (
            "Active mode stays WFO champion; bootstrap reflects recent-window return% only. "
            "Re-run WFO or validate before switching."
        ),
    }


def nemesis_resolve_bootstrap_vs_tuner(
    *,
    bootstrap_mode: str,
    tuner_best_mode: str,
    tuner_all_modes: dict[str, dict],
    expectancy_slack: float = 0.0,
    tuner_min_pf: float = 1.0,
) -> tuple[str, str, dict]:
    """Resolve disagreement between no-champion bootstrap and param tuner (dual feedback).

    Nemesis-style loop (two lenses, one convergence):
      - **Lens A (bootstrap):** short-window return% winner — regime-sensitive.
      - **Lens B (tuner):** expectancy-ranked mode on train+holdout lookback with tuned params.

    **Agreement:** use shared mode.
    **Disagreement:** tuner overrides bootstrap only if tuner mode shows strictly better
    expectancy *and* profit factor ≥ 1 *and* enough trades — i.e. both lenses favor the
    tuner outcome on risk-adjusted grounds. Otherwise bootstrap holds (conservative).

    Returns ``(resolved_mode, reason_code, meta_dict)`` for logging and UI.
    """
    es = max(0.0, float(expectancy_slack))
    pf_floor = float(tuner_min_pf)
    meta: dict = {
        "bootstrap_mode": bootstrap_mode,
        "tuner_nominated": tuner_best_mode,
        "expectancy_slack": es,
        "tuner_min_pf": pf_floor,
    }
    b_mode = str(bootstrap_mode or "").strip()
    t_mode = str(tuner_best_mode or "").strip()
    if not t_mode:
        return b_mode, "nemesis_no_tuner_mode", meta
    if not b_mode:
        meta["loop"] = "tuner_only"
        return t_mode, "nemesis_bootstrap_empty", meta
    if b_mode == t_mode:
        meta["loop"] = "converged_agree"
        return b_mode, "nemesis_agree", meta

    b = tuner_all_modes.get(b_mode) or {}
    t = tuner_all_modes.get(t_mode) or {}
    b_exp = float(b.get("expectancy", -1e18))
    t_exp = float(t.get("expectancy", -1e18))
    b_pf = float(b.get("profit_factor", 0.0))
    t_pf = float(t.get("profit_factor", 0.0))
    b_tr = int(b.get("trades", 0))
    t_tr = int(t.get("trades", 0))
    meta["lens_a_bootstrap_metrics"] = {"expectancy": b_exp, "profit_factor": b_pf, "trades": b_tr}
    meta["lens_b_tuner_pick_metrics"] = {"expectancy": t_exp, "profit_factor": t_pf, "trades": t_tr}

    min_t = EXPECTANCY_MIN_TRADES
    bootstrap_under_sampled = b_tr < min_t
    threshold = b_exp - es
    tuner_strong = (
        t_tr >= min_t
        and t_pf >= pf_floor
        and t_exp > threshold
        and (
            bootstrap_under_sampled
            or t_exp > threshold + 1e-9
            or t_pf >= max(pf_floor, b_pf * 1.02)
        )
    )
    if tuner_strong:
        meta["loop"] = "pass2_tuner_dual_confirm"
        return t_mode, "nemesis_tuner_wins_dual_gate", meta

    meta["loop"] = "pass1_bootstrap_prior"
    return b_mode, "nemesis_bootstrap_holds", meta


def best_mode_from_lookback(bot_cfg: "ScalpBotConfig") -> dict[str, str]:
    """Return the best strategy mode per pair based on lookback performance.

    Ranking: among modes with **≥ EXPECTANCY_MIN_TRADES** trades, highest
    **expectancy**, then profit_factor, win_rate, then total_pnl.
    Falls back to pair config default if no mode qualifies.
    """
    look_h = float(bot_cfg.strategy_lookback_hours)
    LOG.info("strategy_lookback: selecting best modes (expectancy) from %.1fh label window", look_h)
    result: dict[str, str] = {}
    min_t = EXPECTANCY_MIN_TRADES

    for pk, pc in bot_cfg.pairs.items():
        try:
            row = per_strategy_lookback_metrics(pc, bot_cfg, lookback_hours=look_h)
            if row is None:
                LOG.info(
                    "strategy_lookback %s: no bars for %s/%dm in %.1fh — fallback to config '%s'",
                    pk, pc.symbol, pc.interval, look_h, pc.strategy_mode,
                )
                result[pk] = pc.strategy_mode
                continue
            candidates = [
                (
                    mode,
                    float(m["expectancy"]),
                    float(m["profit_factor"]),
                    float(m["win_rate"]),
                    float(m["pnl"]),
                    int(m["trades"]),
                )
                for mode, m in row.items()
                if int(m["trades"]) >= min_t
            ]
            if not candidates:
                trade_summary = {mm: r["trades"] for mm, r in row.items()}
                LOG.info(
                    "strategy_lookback %s: no mode with >=%d trades in %.1fh — fallback to config '%s' | %s",
                    pk, min_t, look_h, pc.strategy_mode, trade_summary,
                )
                result[pk] = pc.strategy_mode
                continue
            best = max(
                candidates,
                key=lambda x: (x[1], x[2], x[3], x[4]),
            )
            result[pk] = best[0]
            LOG.info(
                "strategy_lookback %s: best mode=%s expectancy=%.6f trades=%d pf=%.2f | all: %s",
                pk,
                best[0],
                best[1],
                best[5],
                best[2],
                {mm: (r["trades"], r["expectancy"], round(r["pnl"], 4)) for mm, r in row.items()},
            )
        except Exception:
            LOG.warning("strategy_lookback: best_mode failed for %s", pk, exc_info=True)
            result[pk] = pc.strategy_mode
    return result


def build_strategy_lookback_snapshot(bot_cfg: "ScalpBotConfig") -> dict:
    """Snapshot for ``ScalpRuntime.snapshot()`` — all configured pairs."""
    look_h = float(bot_cfg.strategy_lookback_hours)
    pairs_out: dict[str, dict[str, dict]] = {}
    for pk, pc in bot_cfg.pairs.items():
        try:
            row = per_strategy_lookback_metrics(pc, bot_cfg, lookback_hours=look_h)
            if row is not None:
                pairs_out[pk] = row
        except Exception:
            LOG.warning(
                "strategy_lookback: failed for %s — %s/%dm",
                pk, pc.symbol, pc.interval,
                exc_info=True,
            )
    return {
        "lookback_hours": round(look_h, 2),
        "ranking": "expectancy",
        "expectancy_min_trades": EXPECTANCY_MIN_TRADES,
        "bootstrap_hours": NO_CHAMPION_BOOTSTRAP_HOURS,
        "bootstrap_ranking": "return_pct",
        "updated_ts": time.time(),
        "pairs": pairs_out,
    }
