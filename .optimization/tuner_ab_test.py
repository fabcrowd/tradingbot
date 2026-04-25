"""Walk-forward A/B test: param tuner always-on variants vs baseline.

Three arms, same bar data, deterministic:
  Baseline: tuner runs, but only applies params if tuner.best_mode == active_mode
  Variant A: tuner applies params even on mode mismatch (cross-mode param bleed)
  Variant B: tuner overrides active mode to its best + applies params

Walk-forward structure:
  - First `lookback_bars` bars are the initial tuner lookback (no trading).
  - Every `tune_every` bars, re-run the tuner on trailing `lookback_bars`.
  - Between tuner runs, evaluate forward performance with the chosen mode+params.
  - Sum PnL across all forward segments.

Usage:
    python .optimization/tuner_ab_test.py
"""

from __future__ import annotations

import copy
import sys
import time
from dataclasses import replace
from pathlib import Path

# Ensure project root is on sys.path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np

from backend.server.scalp_bot import bar_store
from backend.server.scalp_bot.param_tuner import (
    STRATEGY_MODES,
    TunerResult,
    _params_from_pair_config,
    run_tuner_cycle,
    apply_tuner_result,
)
from backend.server.scalp_bot.scalp_vec_backtest import (
    ParamSet,
    evaluate_params,
    apply_param_dict_overrides,
)
from backend.server.scalp_bot.scalp_config import load_scalp_config

# Load real config
import tomllib

CONFIG_PATH = _root / "config.toml"
with open(CONFIG_PATH, "rb") as f:
    _raw = tomllib.load(f)

scalp_cfg = load_scalp_config(_raw)

# Pairs to test
PAIRS = {
    "BTC_USD": scalp_cfg.pairs["BTC_USD"],
    "SOL_USD": scalp_cfg.pairs["SOL_USD"],
    "XRP_USD": scalp_cfg.pairs["XRP_USD"],
}

# Walk-forward params
TUNE_EVERY_BARS = 96  # retune every 96 bars = 24h at 15m
LOOKBACK_BARS = 128   # ~32h lookback for tuner (train+holdout)
MIN_FORWARD_BARS = 20  # need at least this many bars to evaluate a forward segment


def _slice_bars(bars: dict[str, np.ndarray], start: int, end: int) -> dict[str, np.ndarray]:
    """Slice bar arrays by index."""
    return {k: v[start:end] for k, v in bars.items()}


def _run_tuner_on_bars(
    pair_key: str,
    pair_cfg,
    bot_cfg,
    bars_slice: dict[str, np.ndarray],
) -> TunerResult | None:
    """Run the tuner's core logic directly on a bar slice (bypass bar_store.load_bars)."""
    n_bars = len(bars_slice["close"])
    if n_bars < 50:
        return None

    half_life = max(10.0, n_bars / 3.0)
    all_modes: dict[str, dict] = {}
    scored_rows = []
    all_adjustments = []
    overall_frozen = True

    from backend.server.scalp_bot.param_tuner import (
        _aggressiveness_from_pf,
        tune_strategy_params,
        EXPECTANCY_MIN_TRADES,
    )

    for mode in STRATEGY_MODES:
        base = _params_from_pair_config(pair_cfg, bot_cfg, mode)
        baseline_m = evaluate_params(bars_slice, base, recency_half_life_bars=half_life)

        agg = _aggressiveness_from_pf(baseline_m.profit_factor, baseline_m.trade_count)
        if agg != "frozen":
            overall_frozen = False

        tuned, adjustments = tune_strategy_params(
            bars_slice, base, mode, agg, recency_half_life_bars=half_life,
        )
        tuned_m = evaluate_params(bars_slice, tuned, recency_half_life_bars=half_life)
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

    # Pick best mode (same logic as run_tuner_cycle)
    best_mode = ""
    best_wr = -1.0
    best_pnl = -float("inf")
    best_pf = -float("inf")
    best_trades = 0
    min_t = EXPECTANCY_MIN_TRADES

    def _pf_num(tm):
        p = float(tm.profit_factor)
        return p if p != float("inf") else 999.0

    # First pass: best by expectancy among modes with enough trades
    for m, tm in scored_rows:
        if tm.trade_count >= min_t and float(tm.expectancy) > best_pnl:
            best_mode = m
            best_wr = float(tm.win_rate)
            best_pnl = float(tm.expectancy)
            best_pf = _pf_num(tm)
            best_trades = int(tm.trade_count)

    if not best_mode:
        # Fallback: best PnL among modes with any trades
        best_pnl = -float("inf")
        for m, tm in scored_rows:
            if tm.trade_count >= 1 and float(tm.total_pnl) > best_pnl:
                best_mode = m
                best_wr = float(tm.win_rate)
                best_pnl = float(tm.total_pnl)
                best_pf = _pf_num(tm)
                best_trades = int(tm.trade_count)

    if not best_mode:
        return None

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


def _param_names_for_mode(mode: str) -> list[str]:
    from backend.server.scalp_bot.param_tuner import TUNABLE_PARAMS
    tunables = TUNABLE_PARAMS.get(mode, [])
    return [t[0] for t in tunables]


def _build_paramset(pair_cfg, bot_cfg, mode: str) -> ParamSet:
    """Build a ParamSet from the current pair_cfg state for the given mode."""
    return _params_from_pair_config(pair_cfg, bot_cfg, mode)


def _apply_params_to_cfg(pair_cfg, result: TunerResult):
    """Apply tuner result params to pair_cfg (mutating). Returns list of changes."""
    return apply_tuner_result(result, pair_cfg)


def walk_forward_test(
    pair_key: str,
    pair_cfg,
    bot_cfg,
    bars: dict[str, np.ndarray],
    config_default_mode: str,
) -> dict[str, dict]:
    """Run the three-arm walk-forward test for one pair.

    The first tuner cycle selects the "WFO champion" mode — this is the starting
    active_mode for all three arms (simulates WFO picking a mode at startup).
    After that, the arms diverge:
      Baseline:   tuner applies params ONLY when tuner.best_mode == initial champion mode
      Variant A:  tuner applies params regardless of mode mismatch (cross-mode bleed)
      Variant B:  tuner overrides active mode to its best + applies params

    Returns dict: arm_name -> {pnl, trades, segments, mode_changes, details}
    """
    n_bars = len(bars["timestamp"])

    # Run initial tuner to pick the "WFO champion" mode for all arms
    init_bars = _slice_bars(bars, 0, LOOKBACK_BARS)
    init_result = _run_tuner_on_bars(pair_key, copy.deepcopy(pair_cfg), bot_cfg, init_bars)
    if init_result is None:
        champion_mode = config_default_mode
        print(f"  (initial tuner returned None, using config default: {champion_mode})")
    else:
        champion_mode = init_result.best_mode
        print(f"  (initial tuner selected champion mode: {champion_mode})")

    results = {}

    for arm_name in ["baseline", "variant_a", "variant_b"]:
        # Deep copy pair_cfg so each arm starts fresh
        pc = copy.deepcopy(pair_cfg)
        active_mode = champion_mode  # all arms start from the same WFO champion
        total_pnl = 0.0
        total_trades = 0
        segments = 0
        mode_changes = 0
        segment_details = []

        cursor = LOOKBACK_BARS  # start after initial lookback
        while cursor < n_bars:
            # Lookback window for tuner
            lb_start = max(0, cursor - LOOKBACK_BARS)
            lb_bars = _slice_bars(bars, lb_start, cursor)

            # Run tuner on lookback
            result = _run_tuner_on_bars(pair_key, pc, bot_cfg, lb_bars)

            if result is not None:
                tuner_best = result.best_mode

                if arm_name == "baseline":
                    # Only apply if tuner best == active mode (the champion)
                    if tuner_best == active_mode:
                        _apply_params_to_cfg(pc, result)
                    # Mode stays locked to champion

                elif arm_name == "variant_a":
                    # Apply params regardless of mode match (cross-mode param bleed)
                    _apply_params_to_cfg(pc, result)
                    # Mode stays locked to champion

                elif arm_name == "variant_b":
                    # Override mode AND apply params
                    if tuner_best != active_mode:
                        active_mode = tuner_best
                        mode_changes += 1
                    _apply_params_to_cfg(pc, result)

            # Forward segment: evaluate next TUNE_EVERY_BARS bars with current mode+params
            fwd_end = min(cursor + TUNE_EVERY_BARS, n_bars)
            if fwd_end - cursor < MIN_FORWARD_BARS:
                break

            fwd_bars = _slice_bars(bars, cursor, fwd_end)
            ps = _build_paramset(pc, bot_cfg, active_mode)
            m = evaluate_params(fwd_bars, ps)

            total_pnl += float(m.total_pnl)
            total_trades += int(m.trade_count)
            segments += 1
            segment_details.append({
                "start_bar": cursor,
                "end_bar": fwd_end,
                "mode": active_mode,
                "pnl": round(float(m.total_pnl), 6),
                "trades": int(m.trade_count),
                "pf": round(float(m.profit_factor), 3) if m.profit_factor != float("inf") else 999.0,
            })

            cursor = fwd_end

        results[arm_name] = {
            "pnl": round(total_pnl, 6),
            "trades": total_trades,
            "segments": segments,
            "mode_changes": mode_changes,
            "details": segment_details,
        }

    return results


def main():
    print("=" * 80)
    print("PARAM TUNER A/B TEST — Walk-Forward Simulation")
    print("=" * 80)
    print(f"Tuner lookback: {LOOKBACK_BARS} bars ({LOOKBACK_BARS * 15 / 60:.0f}h)")
    print(f"Retune every:   {TUNE_EVERY_BARS} bars ({TUNE_EVERY_BARS * 15 / 60:.0f}h)")
    print(f"Strategies:     {', '.join(STRATEGY_MODES)}")
    print(f"Fee:            {scalp_cfg.fee_bps_per_leg} bps/leg")
    print(f"Fill model:     {scalp_cfg.backtest_fill_model}")
    print()
    print("Arms:")
    print("  Baseline:   Tuner applies params ONLY when best_mode == active_mode")
    print("  Variant A:  Tuner applies params regardless of mode mismatch")
    print("  Variant B:  Tuner overrides active mode to its best + applies params")
    print()

    all_results = {}
    for pair_key, pair_cfg in PAIRS.items():
        symbol = pair_cfg.symbol
        interval = pair_cfg.interval
        config_mode = pair_cfg.strategy_mode

        bars = bar_store.load_bars(symbol, interval, last_n_days=90)
        if bars is None:
            print(f"[{pair_key}] No bar data for {symbol}@{interval}m — skipping")
            continue

        n = len(bars["timestamp"])
        span_d = (float(bars["timestamp"][-1]) - float(bars["timestamp"][0])) / 86400
        print(f"[{pair_key}] {symbol} @{interval}m: {n} bars, {span_d:.1f}d span, config_mode={config_mode}")

        if n < LOOKBACK_BARS + MIN_FORWARD_BARS:
            print(f"  Not enough bars (need {LOOKBACK_BARS + MIN_FORWARD_BARS}, have {n}) — skipping")
            continue

        results = walk_forward_test(pair_key, pair_cfg, scalp_cfg, bars, config_mode)
        all_results[pair_key] = results

        # Per-pair summary
        print()
        print(f"  {'Arm':<14} {'PnL':>12} {'Trades':>8} {'Segments':>10} {'Mode Chg':>10}")
        print(f"  {'-'*14} {'-'*12} {'-'*8} {'-'*10} {'-'*10}")
        for arm, r in results.items():
            print(f"  {arm:<14} {r['pnl']:>12.4f} {r['trades']:>8} {r['segments']:>10} {r['mode_changes']:>10}")

        # Per-segment detail for each arm
        for arm, r in results.items():
            print(f"\n  [{arm}] segment detail:")
            for s in r["details"]:
                print(f"    bars {s['start_bar']:>5}-{s['end_bar']:>5} | mode={s['mode']:<20} | pnl={s['pnl']:>10.4f} | trades={s['trades']:>3} | pf={s['pf']:>7.3f}")
        print()

    # Grand summary
    print("=" * 80)
    print("GRAND SUMMARY")
    print("=" * 80)
    print(f"\n{'Pair':<10} {'Arm':<14} {'PnL':>12} {'Trades':>8} {'D vs Base':>12} {'Mode Chg':>10}")
    print(f"{'-'*10} {'-'*14} {'-'*12} {'-'*8} {'-'*12} {'-'*10}")
    for pair_key, results in all_results.items():
        base_pnl = results["baseline"]["pnl"]
        for arm, r in results.items():
            delta = r["pnl"] - base_pnl if arm != "baseline" else 0.0
            delta_str = f"{delta:>+12.4f}" if arm != "baseline" else f"{'--':>12}"
            print(f"{pair_key:<10} {arm:<14} {r['pnl']:>12.4f} {r['trades']:>8} {delta_str} {r['mode_changes']:>10}")
        print()

    # Aggregate
    totals = {}
    for arm in ["baseline", "variant_a", "variant_b"]:
        totals[arm] = {
            "pnl": sum(r[arm]["pnl"] for r in all_results.values()),
            "trades": sum(r[arm]["trades"] for r in all_results.values()),
        }

    print(f"{'TOTAL':<10} {'Arm':<14} {'PnL':>12} {'Trades':>8} {'D vs Base':>12}")
    print(f"{'-'*10} {'-'*14} {'-'*12} {'-'*8} {'-'*12}")
    base_total = totals["baseline"]["pnl"]
    for arm, t in totals.items():
        delta = t["pnl"] - base_total if arm != "baseline" else 0.0
        delta_str = f"{delta:>+12.4f}" if arm != "baseline" else f"{'—':>12}"
        print(f"{'TOTAL':<10} {arm:<14} {t['pnl']:>12.4f} {t['trades']:>8} {delta_str}")

    # Verdict
    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)
    for arm in ["variant_a", "variant_b"]:
        delta = totals[arm]["pnl"] - base_total
        pct = (delta / abs(base_total) * 100) if base_total != 0 else float("inf")
        if abs(delta) < 0.001:
            verdict = "NEUTRAL"
        elif delta > 0:
            verdict = "NET POSITIVE vs deployed"
        else:
            verdict = "NET NEGATIVE vs deployed"
        label = "A (cross-mode params)" if arm == "variant_a" else "B (mode override + params)"
        print(f"  {label}: {verdict} (D={delta:+.4f}, {pct:+.1f}%)")


if __name__ == "__main__":
    main()
