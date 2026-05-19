"""Compare 5m vs 15m vectorized backtests for Coinbase INTX perp pairs.

Loads [scalp] from config.toml, uses each pair's current parameters, runs all
five strategy modes on both intervals, backfills 5m bars via Coinbase REST if
needed, and prints a ranked table + per-pair recommendation.

**Lab contract:** to match live WFO bar coverage, any script that replays WFO-style logic must
load or backfill at least ``wfo_effective_roll_span_hours(WFOConfig)`` for the
same ``[scalp]`` settings (see ``scalp_wfo.wfo_effective_roll_span_hours`` / runtime backfill).

Run from repo root (requires coinbase-advanced-py for REST backfill):

    python backend/server/scalp_bot/compare_intervals.py

Or from backend/server:

    python -m scalp_bot.compare_intervals
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from dataclasses import replace
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

_REPO_ROOT = Path(__file__).resolve().parents[3]

import tomllib

from scalp_bot import bar_store
from scalp_bot.scalp_config import load_scalp_config
from scalp_bot.scalp_vec_backtest import evaluate_params
from scalp_bot.scalp_wfo import _params_from_config

STRATEGIES = (
    "daviddtech_scalp",
    "macd_scalp",
    "ema_scalp",
    "ema_momentum",
    "rsi_reversion",
)


def _bars_per_year(interval_minutes: int) -> float:
    return 365.25 * 24.0 * 60.0 / float(max(1, interval_minutes))


def _hours_span_from_bars(bars: dict | None, interval_minutes: int) -> float:
    if bars is None or len(bars.get("timestamp", [])) < 2:
        return 30.0 * 24.0
    ts = bars["timestamp"]
    span_sec = float(int(ts[-1]) - int(ts[0]))
    hours = span_sec / 3600.0
    return max(hours, float(interval_minutes) / 60.0)


def _load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _composite_score(metrics) -> float:
    n = max(1, int(metrics.trade_count))
    return float(metrics.expectancy) * math.sqrt(float(n))


def _fmt_row(pair: str, interval: int, strategy: str, m, score: float) -> tuple[str, ...]:
    wr = m.win_rate * 100.0
    pf = m.profit_factor
    pf_s = "inf" if math.isinf(pf) else f"{pf:.2f}"
    return (
        pair[:18].ljust(18),
        f"{interval:>3}m",
        strategy[:18].ljust(18),
        f"{m.trade_count:>6}",
        f"{wr:>6.1f}%",
        f"{m.total_pnl:>10.2f}",
        f"{pf_s:>6}",
        f"{m.sharpe:>7.2f}",
        f"{score:>8.3f}",
    )


async def _ensure_5m_history(product_id: str, hours_needed: float, skip_backfill: bool) -> None:
    if skip_backfill:
        return
    bar_store.set_bar_store_venue("coinbase_perps")
    n = bar_store.bar_count(product_id, 5)
    if n < 10:
        print(f"  backfill 5m {product_id} (~{hours_needed:.0f}h history)...")
        await bar_store.backfill_coinbase_public_candles(product_id, 5, hours_needed)
        return
    b = bar_store.load_bars(product_id, 5)
    h = _hours_span_from_bars(b, 5)
    if h + 1.0 < hours_needed * 0.85:
        print(f"  extend 5m {product_id} (have ~{h:.0f}h, want ~{hours_needed:.0f}h)...")
        await bar_store.backfill_coinbase_public_candles(product_id, 5, hours_needed)


async def run_compare(args: argparse.Namespace) -> int:
    cfg_path = Path(args.config).resolve()
    raw = _load_toml(cfg_path)
    bot_cfg = load_scalp_config(raw)

    if (bot_cfg.venue or "").strip().lower() != "coinbase_perps":
        print("compare_intervals: [scalp].venue is not coinbase_perps — aborting.")
        return 2

    bar_store.set_bar_store_venue("coinbase_perps")

    rows: list[tuple[str, int, str, object, float]] = []

    for _key, pair_cfg in sorted(bot_cfg.pairs.items(), key=lambda kv: kv[1].symbol):
        symbol = pair_cfg.symbol
        base = _params_from_config(pair_cfg, bot_cfg)

        bars_15 = bar_store.load_bars(symbol, 15, last_n_days=args.last_n_days)
        hours = float(args.hours) if args.hours is not None else _hours_span_from_bars(bars_15, 15)
        hours = max(hours, 24.0)

        await _ensure_5m_history(symbol, hours, args.skip_backfill)

        for interval in (15, 5):
            bars = bar_store.load_bars(symbol, interval, last_n_days=args.last_n_days)
            if bars is None or len(bars["close"]) < 50:
                print(f"skip {symbol} {interval}m: insufficient bars")
                continue
            bpy = _bars_per_year(interval)

            for mode in STRATEGIES:
                params = replace(base, mode=mode)
                m = evaluate_params(bars, params, bars_per_year=bpy)
                sc = _composite_score(m)
                rows.append((symbol, interval, mode, m, sc))

    hdr = (
        "Pair".ljust(18),
        "Iv",
        "Strategy".ljust(18),
        "Trades",
        "WR%",
        "PnL",
        "PF",
        "Sharpe",
        "Score",
    )
    print("\n" + " | ".join(hdr))
    print("-" * 96)

    rows.sort(key=lambda r: (r[0], -r[4]))

    by_pair: dict[str, list[tuple[int, str, object, float]]] = {}
    for sym, iv, mode, m, sc in rows:
        by_pair.setdefault(sym, []).append((iv, mode, m, sc))

    for sym, iv, mode, m, sc in rows:
        print(" | ".join(_fmt_row(sym, iv, mode, m, sc)))

    print("\n--- Per-pair recommendation (best expectancy*sqrt(n)) ---\n")
    for sym in sorted(by_pair.keys()):
        best_iv, best_mode, best_m, best_sc = max(by_pair[sym], key=lambda x: x[3])
        print(
            f"  {sym}:  {best_iv}m + {best_mode}  "
            f"(trades={best_m.trade_count}, WR={best_m.win_rate*100:.1f}%, "
            f"PnL={best_m.total_pnl:.2f}, Sharpe={best_m.sharpe:.2f}, score={best_sc:.3f})"
        )

    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Compare 5m vs 15m scalp backtests (Coinbase INTX).")
    p.add_argument("--config", default=str(_REPO_ROOT / "config.toml"), help="Path to config.toml")
    p.add_argument("--skip-backfill", action="store_true", help="Skip Coinbase REST 5m backfill")
    p.add_argument("--hours", type=float, default=None, help="Hours of history for 5m backfill")
    p.add_argument("--last-n-days", type=float, default=None, help="Trim bars to recent N days")
    args = p.parse_args()
    raise SystemExit(asyncio.run(run_compare(args)))


if __name__ == "__main__":
    main()