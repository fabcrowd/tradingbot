#!/usr/bin/env python3
"""Which bar interval (1m/5m/15m/60m) scores best on a **fixed calendar lookback** — **WFO champion only**.

Loads ``data/scalp_champion.json`` per pair ``symbol``, builds ``ParamSet`` via
``param_set_from_champion_row`` (same merge as live: champion params over
``_params_from_config``, fees/slip from ``config.toml`` unless ``--fee-bps``), then
runs ``evaluate_params`` on ``bar_store.load_bars(symbol, interval, last_n_days=...)``.

No per-interval strategy tournament: **one mode + one param vector per pair** (the champion).

Optional ``--oracle-all-modes`` restores the old exploratory sweep (all STRATEGIES).

Run from repo root::

  python .optimization/timeframe_3d_pnl_sweep.py --days 3 --intervals 1,5,15,60 --fetch-hours 96
  python .optimization/timeframe_3d_pnl_sweep.py --days 3 --oracle-all-modes   # legacy grid
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import tomllib

from backend.server.scalp_bot import bar_store
from backend.server.scalp_bot.scalp_config import effective_scalp_fee_bps_per_leg, load_scalp_config
from backend.server.scalp_bot.scalp_vec_backtest import evaluate_params
from backend.server.scalp_bot.scalp_wfo import (
    CHAMPION_PATH,
    _params_from_config,
    load_champion,
    param_set_from_champion_row,
)

STRATEGIES = (
    "daviddtech_scalp",
    "ema_momentum",
    "rsi_reversion",
    "ema_scalp",
    "supertrend",
    "squeeze_momentum",
    "qqe_mod",
    "utbot_alert",
    "hull_suite",
    "macd_scalp",
    "sar_chop",
)


def _bars_per_year(interval_minutes: int) -> float:
    return 365.25 * 24.0 * 60.0 / float(max(1, interval_minutes))


async def _backfill_all(
    bot,
    intervals: list[int],
    hours: float,
    *,
    rate_limit: float,
) -> None:
    from backend.server.scalp_bot.bar_store import backfill_from_rest

    venue = (bot.venue or "coinbase_perps").strip().lower()
    for _pk, pc in sorted(bot.pairs.items(), key=lambda kv: kv[1].symbol):
        sym = pc.symbol
        for iv in intervals:
            n = await backfill_from_rest(
                sym,
                iv,
                hours,
                venue=venue,
                rate_limit_sec=rate_limit,
            )
            print(f"# backfill {sym} {iv}m hours={hours:.1f} new_rows={n}", file=sys.stderr)


def _run_champion_sweep(
    bot,
    intervals: list[int],
    lookback: float,
    min_bars: int,
    fill_model: str,
    slip: float,
    fee_override: float | None,
    champion_store: dict[str, dict] | None,
    rows: list[dict],
    skipped: list[str],
) -> dict[tuple[str, int], dict]:
    """Returns champion_pnl_by (pair_key, interval_m) -> row dict."""
    out: dict[tuple[str, int], dict] = {}
    for pk, pc in sorted(bot.pairs.items(), key=lambda kv: kv[1].symbol):
        symbol = pc.symbol
        row = (champion_store or {}).get(symbol)
        pset = param_set_from_champion_row(row, pc, bot)
        if pset is None:
            skipped.append(f"{pk} {symbol}: no WFO champion row in file for this symbol")
            continue
        if fee_override is not None:
            pset = replace(pset, fee_pct=fee_override, slippage_pct=slip, fill_model=fill_model)
        else:
            pset = replace(pset, slippage_pct=slip, fill_model=fill_model)
        champ_mode = str(pset.mode)

        for interval in intervals:
            bars = bar_store.load_bars(symbol, interval, last_n_days=lookback)
            if bars is None:
                skipped.append(f"{pk} {symbol} {interval}m: no parquet")
                continue
            n = len(bars["close"])
            if n < min_bars:
                skipped.append(f"{pk} {symbol} {interval}m: only {n} bars (<{min_bars})")
                continue

            bpy = _bars_per_year(interval)
            m = evaluate_params(bars, pset, bars_per_year=bpy)
            tp = float(m.total_pnl)
            tc = int(m.trade_count)
            rec = {
                "pair_key": pk,
                "symbol": symbol,
                "interval_m": interval,
                "lookback_days": lookback,
                "champion_mode": champ_mode,
                "wfo_champion_interval_m": int(row["interval"]) if row and row.get("interval") is not None else None,
                "n_bars": n,
                "trades": tc,
                "total_pnl": round(tp, 6),
                "win_rate": round(m.win_rate, 4),
                "profit_factor": float(m.profit_factor)
                if not math.isinf(m.profit_factor)
                else None,
            }
            rows.append(rec)
            out[(pk, interval)] = rec
    return out


def _run_oracle_sweep(
    bot,
    intervals: list[int],
    lookback: float,
    min_bars: int,
    fill_model: str,
    slip: float,
    fee_override: float | None,
    rows: list[dict],
    skipped: list[str],
) -> dict[tuple[str, int], tuple[float, str, int]]:
    oracle: dict[tuple[str, int], tuple[float, str, int]] = {}
    for pk, pc in sorted(bot.pairs.items(), key=lambda kv: kv[1].symbol):
        symbol = pc.symbol
        for interval in intervals:
            bars = bar_store.load_bars(symbol, interval, last_n_days=lookback)
            if bars is None:
                skipped.append(f"{pk} {symbol} {interval}m: no parquet")
                continue
            n = len(bars["close"])
            if n < min_bars:
                skipped.append(f"{pk} {symbol} {interval}m: only {n} bars (<{min_bars})")
                continue

            base = _params_from_config(pc, bot)
            if fee_override is not None:
                base = replace(base, fee_pct=fee_override, slippage_pct=slip, fill_model=fill_model)
            else:
                base = replace(base, slippage_pct=slip, fill_model=fill_model)

            bpy = _bars_per_year(interval)
            best_pnl = -1e100
            best_mode = ""
            best_trades = 0

            for mode in STRATEGIES:
                p = replace(base, mode=mode)
                m = evaluate_params(bars, p, bars_per_year=bpy)
                tp = float(m.total_pnl)
                tc = int(m.trade_count)
                rows.append(
                    {
                        "pair_key": pk,
                        "symbol": symbol,
                        "interval_m": interval,
                        "lookback_days": lookback,
                        "strategy": mode,
                        "trades": tc,
                        "total_pnl": round(tp, 6),
                        "win_rate": round(m.win_rate, 4),
                        "profit_factor": float(m.profit_factor)
                        if not math.isinf(m.profit_factor)
                        else None,
                    }
                )
                if tp > best_pnl:
                    best_pnl = tp
                    best_mode = mode
                    best_trades = tc
            oracle[(pk, interval)] = (best_pnl, best_mode, best_trades)
    return oracle


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bar-interval PnL sweep: WFO champion params (default) or all modes (--oracle-all-modes)."
    )
    ap.add_argument("--config", type=str, default=None, help="config.toml path (default: repo root)")
    ap.add_argument("--days", type=float, default=3.0, help="Calendar lookback on bar timestamps (default 3)")
    ap.add_argument(
        "--intervals",
        type=str,
        default="1,5,15,60",
        help="Comma-separated minute intervals (default 1,5,15,60)",
    )
    ap.add_argument(
        "--fetch-hours",
        type=float,
        default=0.0,
        help="If >0, backfill this many hours of history per (symbol, interval) before sweep.",
    )
    ap.add_argument("--min-bars", type=int, default=40, help="Skip slice if fewer bars after trim")
    ap.add_argument("--fee-bps", type=float, default=None, help="Override fee_bps_per_leg")
    ap.add_argument("--rate-limit", type=float, default=0.25, help="Seconds between Coinbase backfill pages")
    ap.add_argument("--output-json", type=str, default=None, help="Write full result rows JSON here")
    ap.add_argument(
        "--champion-path",
        type=str,
        default=None,
        help=f"scalp_champion.json path (default: {CHAMPION_PATH})",
    )
    ap.add_argument(
        "--oracle-all-modes",
        action="store_true",
        help="Sweep every strategy mode per interval (legacy exploratory; not WFO champion).",
    )
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve() if args.config else (_ROOT / "config.toml")
    if not cfg_path.exists():
        print("config not found:", cfg_path, file=sys.stderr)
        return 2

    with cfg_path.open("rb") as f:
        raw = tomllib.load(f)
    bot = load_scalp_config(raw)
    if not bot.pairs:
        print("No scalp pairs in config", file=sys.stderr)
        return 2

    venue = (bot.venue or "coinbase_perps").strip().lower()
    bar_store.set_bar_store_venue("coinbase_perps")

    intervals = [int(x.strip()) for x in args.intervals.split(",") if x.strip()]
    lookback = float(args.days)
    min_bars = int(args.min_bars)
    fill_model = getattr(bot, "backtest_fill_model", "next_open") or "next_open"
    slip = bot.slippage_bps / 10_000.0
    fee_override = (args.fee_bps / 10_000.0) if args.fee_bps is not None else None

    if args.fetch_hours and args.fetch_hours > 0:
        need_h = max(float(args.fetch_hours), lookback * 24.0 + 6.0)
        asyncio.run(
            _backfill_all(bot, intervals, need_h, rate_limit=float(args.rate_limit)),
        )

    rows: list[dict] = []
    skipped: list[str] = []

    champ_path = Path(args.champion_path).resolve() if args.champion_path else CHAMPION_PATH
    champion_store = load_champion(champ_path) if not args.oracle_all_modes else None

    if args.oracle_all_modes:
        oracle = _run_oracle_sweep(
            bot, intervals, lookback, min_bars, fill_model, slip, fee_override, rows, skipped
        )
        champion_by_pair_interval = None
    else:
        champion_by_pair_interval = _run_champion_sweep(
            bot,
            intervals,
            lookback,
            min_bars,
            fill_model,
            slip,
            fee_override,
            champion_store,
            rows,
            skipped,
        )
        oracle = {}

    fee_bps = args.fee_bps if args.fee_bps is not None else effective_scalp_fee_bps_per_leg(bot)
    meta = {
        "git_invoked": "timeframe_3d_pnl_sweep",
        "venue": venue,
        "lookback_days": lookback,
        "intervals": intervals,
        "fee_bps_per_leg": fee_bps,
        "fill_model": fill_model,
        "slippage_bps": bot.slippage_bps,
        "mode": "oracle_all_strategies" if args.oracle_all_modes else "wfo_champion_only",
        "champion_path": str(champ_path),
        "utc": datetime.now(timezone.utc).isoformat(),
    }
    if not args.oracle_all_modes:
        meta["champion_symbols_on_disk"] = sorted(champion_store.keys()) if champion_store else []

    print("", file=sys.stderr)
    if args.oracle_all_modes:
        print("## Oracle (best of all modes) per pair × interval", file=sys.stderr)
        winners: dict[str, list[tuple[int, str, float, int]]] = defaultdict(list)
        for (pk, iv), (tp, mode, tc) in sorted(oracle.items()):
            winners[pk].append((iv, mode, tp, tc))
        for pk in sorted(winners.keys()):
            xs = winners[pk]
            best_iv, best_mode, best_pnl, best_tc = max(xs, key=lambda t: t[2])
            print(f"### {pk}", file=sys.stderr)
            for iv, mode, tp, tc in sorted(xs, key=lambda t: t[0]):
                mark = "  **WIN**" if iv == best_iv else ""
                print(f"  - {iv}m: pnl={tp:.4f}  trades={tc}  mode={mode}{mark}", file=sys.stderr)
            print(
                f"  => Best interval: **{best_iv}m** ({best_mode})  pnl={best_pnl:.4f}  trades={best_tc}",
                file=sys.stderr,
            )
    else:
        print(
            f"## WFO champion only — best bar interval by total_pnl (last {lookback:.2f} days)",
            file=sys.stderr,
        )
        by_pair: dict[str, list[tuple[int, float, int, str]]] = defaultdict(list)
        for (pk, iv), rec in sorted(champion_by_pair_interval.items()):
            by_pair[pk].append(
                (iv, float(rec["total_pnl"]), int(rec["trades"]), str(rec["champion_mode"])),
            )
        for pk in sorted(by_pair.keys()):
            xs = by_pair[pk]
            best_iv, best_pnl, best_tc, _ = max(xs, key=lambda t: t[1])
            cmode = xs[0][3]
            print(f"### {pk}  (champion mode={cmode})", file=sys.stderr)
            for iv, tp, tc, _m in sorted(xs, key=lambda t: t[0]):
                mark = "  **WIN**" if iv == best_iv else ""
                print(f"  - {iv}m: pnl={tp:.4f}  trades={tc}{mark}", file=sys.stderr)
            print(
                f"  => Best interval for this champion: **{best_iv}m**  pnl={best_pnl:.4f}  trades={best_tc}",
                file=sys.stderr,
            )

    if skipped:
        print("\n# Skipped", file=sys.stderr)
        for s in skipped:
            print(f"  - {s}", file=sys.stderr)

    out_obj: dict = {
        "meta": meta,
        "skipped": skipped,
        "rows": rows,
    }
    if args.oracle_all_modes:
        out_obj["oracle_by_pair_interval"] = {
            f"{a}|{b}m": {"pnl": p, "mode": m, "trades": t} for (a, b), (p, m, t) in oracle.items()
        }
    else:
        out_obj["champion_by_pair_interval"] = {
            f"{a}|{b}m": v for (a, b), v in champion_by_pair_interval.items()
        }

    print(json.dumps(meta, indent=2))

    if args.output_json:
        out_path = Path(args.output_json).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # JSON keys must be strings
        if champion_by_pair_interval is not None:
            out_obj["champion_by_pair_interval"] = {
                f"{a}|{b}m": v for (a, b), v in champion_by_pair_interval.items()
            }
        out_path.write_text(json.dumps(out_obj, indent=2), encoding="utf-8")
        print(f"# wrote {out_path}", file=sys.stderr)

    ok = bool(oracle) if args.oracle_all_modes else bool(champion_by_pair_interval)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
