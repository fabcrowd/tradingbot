"""Walk-forward A/B test: WFO train window sizing — 12h vs 24h (current) vs 48h.

Each arm uses a different lookback window for champion mode selection. Everything else
is identical: same bars, same candidate modes, same scoring (expectancy_sqrt_n with
recency half-life = n_bars/3, matching WFO production logic).

Walk-forward structure:
  - Warm up = largest lookback (48h = 192 bars) so all arms start from the same bar.
  - Every SEGMENT_BARS, pick champion mode from trailing train_bars, evaluate forward.
  - Recency half-life scales with window size (n_bars/3) — mirrors scalp_wfo.py:380.

Arms:
  12h  =  48 bars @ 15m  (hl=16  bars = 4h recency)
  24h  =  96 bars @ 15m  (hl=32  bars = 8h recency)  ← current config
  48h  = 192 bars @ 15m  (hl=64  bars = 16h recency)

Also reports: mode stability (how often champion switches), trade count per segment.

**Stage 2 (production WFO):** For full `optimize_pair` train/holdout + gates, use
``.optimization/wfo_knob_suite.py`` (see `proposed-changes-pnl-test` skill Track B).

Usage:
  python .optimization/wfo_window_ab_test.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np

from backend.server.scalp_bot import bar_store
from backend.server.scalp_bot.scalp_vec_backtest import evaluate_params
from backend.server.scalp_bot.scalp_wfo import score_strategy
from backend.server.scalp_bot.scalp_config import load_scalp_config
from backend.server.scalp_bot.param_tuner import STRATEGY_MODES, _params_from_pair_config

import tomllib

CONFIG_PATH = _root / "config.toml"
with open(CONFIG_PATH, "rb") as f:
    _raw = tomllib.load(f)

scalp_cfg = load_scalp_config(_raw)

PAIRS = {
    "BTC_USD": scalp_cfg.pairs["BTC_USD"],
    "SOL_USD": scalp_cfg.pairs["SOL_USD"],
    "XRP_USD": scalp_cfg.pairs["XRP_USD"],
}

BARS_PER_HOUR = 4          # 15m bars
SEGMENT_BARS  = 96         # 24h forward evaluation window
MIN_FWD_BARS  = 20
WARMUP_BARS   = 192        # 48h — largest window; all arms start here
OBJECTIVE     = scalp_cfg.wfo_objective  # "expectancy_sqrt_n"

TRAIN_CONFIGS = [
    ("12h",  48),   # shorter: more reactive, noisier
    ("24h",  96),   # current config
    ("48h", 192),   # longer: more stable, slower to adapt
]


def _slice(bars: dict, start: int, end: int) -> dict:
    return {k: v[start:end] for k, v in bars.items()}


def _pick_champion(pair_key: str, pair_cfg, bot_cfg, bars: dict) -> str:
    """Select best mode from bars using production WFO scoring (recency hl = n_bars/3)."""
    n = len(bars["close"])
    half_life = max(10.0, n / 3.0)
    best_mode = pair_cfg.strategy_mode
    best_score = -float("inf")
    for mode in STRATEGY_MODES:
        params = _params_from_pair_config(pair_cfg, bot_cfg, mode)
        m = evaluate_params(bars, params, recency_half_life_bars=half_life)
        s = score_strategy(m, OBJECTIVE)
        if s > best_score:
            best_score = s
            best_mode = mode
    return best_mode


def walk_forward(
    pair_key: str,
    pair_cfg,
    bot_cfg,
    bars: dict,
    train_bars: int,
    label: str,
) -> dict:
    n = len(bars["timestamp"])
    pc = copy.deepcopy(pair_cfg)

    total_pnl = 0.0
    total_trades = 0
    segments = 0
    mode_switches = 0
    active_mode = None
    details = []

    cursor = WARMUP_BARS  # all arms start at same position
    while cursor < n:
        fwd_end = min(cursor + SEGMENT_BARS, n)
        if fwd_end - cursor < MIN_FWD_BARS:
            break

        # Pick champion from trailing train_bars
        lb_start = max(0, cursor - train_bars)
        lb_bars = _slice(bars, lb_start, cursor)
        champion = _pick_champion(pair_key, pc, bot_cfg, lb_bars)

        if active_mode is not None and champion != active_mode:
            mode_switches += 1
        active_mode = champion

        # Evaluate forward with chosen mode
        fwd_bars = _slice(bars, cursor, fwd_end)
        params = _params_from_pair_config(pc, bot_cfg, active_mode)
        m = evaluate_params(fwd_bars, params)

        total_pnl += float(m.total_pnl)
        total_trades += int(m.trade_count)
        segments += 1
        details.append({
            "start": cursor,
            "end": fwd_end,
            "mode": active_mode,
            "pnl": round(float(m.total_pnl), 6),
            "trades": int(m.trade_count),
            "pf": round(float(m.profit_factor), 3) if m.profit_factor != float("inf") else 999.0,
        })
        cursor = fwd_end

    return {
        "label": label,
        "train_bars": train_bars,
        "pnl": round(total_pnl, 6),
        "trades": total_trades,
        "segments": segments,
        "mode_switches": mode_switches,
        "details": details,
    }


def main():
    print("=" * 80)
    print("WFO TRAIN WINDOW A/B TEST — 12h vs 24h vs 48h")
    print("=" * 80)
    print(f"Warmup:    {WARMUP_BARS} bars ({WARMUP_BARS // BARS_PER_HOUR}h) — all arms start here")
    print(f"Segments:  {SEGMENT_BARS} bars ({SEGMENT_BARS // BARS_PER_HOUR}h forward each)")
    print(f"Scoring:   {OBJECTIVE}, recency hl = n_bars/3 (mirrors scalp_wfo.py:380)")
    print(f"Fee:       {scalp_cfg.fee_bps_per_leg} bps/leg | Fill: {scalp_cfg.backtest_fill_model}")
    print()
    print("Recency half-life per arm (bars / hours):")
    for label, tb in TRAIN_CONFIGS:
        hl = max(10.0, tb / 3.0)
        print(f"  {label}: train={tb} bars, hl={hl:.0f} bars ({hl / BARS_PER_HOUR:.1f}h)")
    print()

    all_results: dict[str, list[dict]] = {}

    for pair_key, pair_cfg in PAIRS.items():
        bars = bar_store.load_bars(pair_cfg.symbol, pair_cfg.interval, last_n_days=90)
        if bars is None:
            print(f"[{pair_key}] No bars — skipping")
            continue
        n = len(bars["timestamp"])
        span_d = (float(bars["timestamp"][-1]) - float(bars["timestamp"][0])) / 86400
        print(f"[{pair_key}] {pair_cfg.symbol} @{pair_cfg.interval}m: {n} bars, {span_d:.1f}d")

        arm_results = []
        for label, train_bars in TRAIN_CONFIGS:
            r = walk_forward(pair_key, pair_cfg, scalp_cfg, bars, train_bars, label)
            arm_results.append(r)

        all_results[pair_key] = arm_results

        # Per-pair table
        print(f"\n  {'Window':<8} {'PnL':>12} {'Trades':>8} {'Segments':>10} {'Mode Switches':>15} {'D vs 24h':>12}")
        print(f"  {'-'*8} {'-'*12} {'-'*8} {'-'*10} {'-'*15} {'-'*12}")
        base_pnl = next(r["pnl"] for r in arm_results if r["label"] == "24h")
        for r in arm_results:
            d = r["pnl"] - base_pnl if r["label"] != "24h" else 0.0
            d_str = f"{d:>+12.4f}" if r["label"] != "24h" else f"{'--':>12}"
            print(f"  {r['label']:<8} {r['pnl']:>12.4f} {r['trades']:>8} {r['segments']:>10} {r['mode_switches']:>15} {d_str}")

        # Mode distribution per arm
        print()
        for r in arm_results:
            mode_counts: dict[str, int] = {}
            for seg in r["details"]:
                mode_counts[seg["mode"]] = mode_counts.get(seg["mode"], 0) + 1
            modes_str = ", ".join(f"{m}×{c}" for m, c in sorted(mode_counts.items(), key=lambda x: -x[1]))
            print(f"  [{r['label']}] modes used: {modes_str}")

        # Segment-level comparison
        print(f"\n  Segment detail (all three arms):")
        print(f"  {'Bars':<13} {'12h mode':<22} {'12h PnL':>9} {'24h mode':<22} {'24h PnL':>9} {'48h mode':<22} {'48h PnL':>9}")
        print(f"  {'-'*13} {'-'*22} {'-'*9} {'-'*22} {'-'*9} {'-'*22} {'-'*9}")
        for segs in zip(*[r["details"] for r in arm_results]):
            s0, s1, s2 = segs
            print(
                f"  {s0['start']:>5}-{s0['end']:>5}   "
                f"{s0['mode']:<22} {s0['pnl']:>9.4f}   "
                f"{s1['mode']:<22} {s1['pnl']:>9.4f}   "
                f"{s2['mode']:<22} {s2['pnl']:>9.4f}"
            )
        print()

    # Grand summary
    print("=" * 80)
    print("GRAND SUMMARY")
    print("=" * 80)
    print(f"\n{'Pair':<10} {'Window':<8} {'PnL':>12} {'Trades':>8} {'Switches':>10} {'D vs 24h':>12}")
    print(f"{'-'*10} {'-'*8} {'-'*12} {'-'*8} {'-'*10} {'-'*12}")

    totals = {label: {"pnl": 0.0, "trades": 0, "switches": 0} for label, _ in TRAIN_CONFIGS}
    for pair_key, arm_results in all_results.items():
        base_pnl = next(r["pnl"] for r in arm_results if r["label"] == "24h")
        for r in arm_results:
            d = r["pnl"] - base_pnl if r["label"] != "24h" else 0.0
            d_str = f"{d:>+12.4f}" if r["label"] != "24h" else f"{'--':>12}"
            print(f"{pair_key:<10} {r['label']:<8} {r['pnl']:>12.4f} {r['trades']:>8} {r['mode_switches']:>10} {d_str}")
            totals[r["label"]]["pnl"] += r["pnl"]
            totals[r["label"]]["trades"] += r["trades"]
            totals[r["label"]]["switches"] += r["mode_switches"]
        print()

    base_total = totals["24h"]["pnl"]
    print(f"\n{'TOTAL':<10} {'Window':<8} {'PnL':>12} {'Trades':>8} {'Switches':>10} {'D vs 24h':>12}")
    print(f"{'-'*10} {'-'*8} {'-'*12} {'-'*8} {'-'*10} {'-'*12}")
    for label, _ in TRAIN_CONFIGS:
        t = totals[label]
        d = t["pnl"] - base_total if label != "24h" else 0.0
        d_str = f"{d:>+12.4f}" if label != "24h" else f"{'--':>12}"
        print(f"{'TOTAL':<10} {label:<8} {t['pnl']:>12.4f} {t['trades']:>8} {t['switches']:>10} {d_str}")

    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)
    for label, _ in TRAIN_CONFIGS:
        if label == "24h":
            continue
        delta = totals[label]["pnl"] - base_total
        pct = (delta / abs(base_total) * 100) if base_total != 0 else float("inf")
        if abs(delta) < 0.5 and abs(pct) < 5.0:
            verdict = "NEUTRAL"
        elif delta > 0:
            verdict = "NET POSITIVE vs 24h"
        else:
            verdict = "NET NEGATIVE vs 24h"
        print(f"  {label} train window: {verdict} (D={delta:+.4f}, {pct:+.1f}%, switches={totals[label]['switches']} vs {totals['24h']['switches']})")

    print()
    print("  Limits:")
    print("  - Deterministic backtest, 30d 15m bars. No holdout gate applied (train-only champion pick).")
    print("  - Recency hl scales with window (n_bars/3): 12h=hl4h, 24h=hl8h, 48h=hl16h.")
    print("  - WFO production also uses a holdout gate; this test is train-window sensitivity only.")
    print("  - Mode switches show instability, not necessarily worse P&L.")


if __name__ == "__main__":
    main()
