#!/usr/bin/env python3
"""PnL Feedback Lab — multi-window vector backtest sweep (no network).

Run from repo root:
  python .optimization/pnl-feedback-lab/scripts/run_multiwindow_lab.py
  python .../run_multiwindow_lab.py --intervals 5,15,60

Uses [scalp] from config.toml and coinbase_bars Parquet,
three disjoint bar-index windows (thirds of each series), all strategy modes.

Default: each pair's config ``interval`` only. With ``--intervals``, also runs
those minute intervals per symbol when a matching Parquet exists (discovery).

Outputs JSON lines to stdout; redirect to runs/<id>.jsonl, or pass ``--jsonl-out`` / ``--compare-md``
to write artifacts and the four-section compare markdown (PnL skill contract) in one run.
Use ``--export-pnl-details`` (optionally with ``--jsonl-out`` for auto paths) to emit a long CSV and
markdown matrices: every pair × interval × time window × strategy with ``total_pnl`` and trades.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[3]
_SCRIPTS = Path(__file__).resolve().parent
_SERVER = _REPO / "backend" / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import tomllib

from scalp_bot import bar_store
from scalp_bot.scalp_config import effective_scalp_fee_bps_per_leg, load_scalp_config
from scalp_bot.scalp_vec_backtest import evaluate_params
from scalp_bot.scalp_wfo import _params_from_config

from compare_report_generator import generate_compare_document, write_compare_from_jsonl_file
from lab_pnl_export import write_pnl_detail_artifacts

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


def _pair_focus_modes(bot) -> dict[str, str]:
    """Focus label for §2–§3: manual mode, else ``[scalp] auto_mode_fallback`` (not DaviddTech)."""
    out: dict[str, str] = {}
    fb = str(getattr(bot, "auto_mode_fallback", "ema_momentum") or "ema_momentum")
    for pk, pc in bot.pairs.items():
        m = (getattr(pc, "strategy_mode", None) or "auto").strip().lower()
        if m in ("", "auto"):
            out[pk] = str(getattr(pc, "auto_mode_fallback", None) or fb)
        else:
            out[pk] = str(pc.strategy_mode)
    return out


def _slice_bars(bars: dict, start: int, end: int) -> dict:
    return {k: np.asarray(v)[start:end].copy() for k, v in bars.items()}


def _bars_per_year(interval_minutes: int) -> float:
    return 365.25 * 24.0 * 60.0 / float(max(1, interval_minutes))


def _window_thirds(n: int) -> list[tuple[str, int, int]]:
    if n < 90:
        return [("full", 0, n)]
    t = n // 3
    return [
        ("early", 0, t),
        ("mid", t, 2 * t),
        ("late", 2 * t, n),
    ]


def _parse_intervals_arg(s: str | None) -> list[int] | None:
    if not s or not str(s).strip():
        return None
    out: list[int] = []
    for part in str(s).split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out or None


def main() -> int:
    ap = argparse.ArgumentParser(description="PnL lab multi-window (and optional multi-interval) backtest sweep.")
    ap.add_argument(
        "--intervals",
        type=str,
        default=None,
        help="Comma-separated bar intervals in minutes to sweep per symbol (e.g. 5,15,60). "
        "Default: each pair's config interval only.",
    )
    ap.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.toml (default: repo root config.toml).",
    )
    ap.add_argument(
        "--jsonl-out",
        type=str,
        default=None,
        help="Also write contract + row JSONL to this path (UTF-8).",
    )
    ap.add_argument(
        "--compare-md",
        type=str,
        default=None,
        help="After the sweep, write the four-section compare markdown here (skill contract).",
    )
    ap.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Label for the compare doc (default: UTC pnl_lab_YYYYMMDD_HHMMSS).",
    )
    ap.add_argument(
        "--lens-b",
        type=str,
        default=None,
        help="Optional path to Lens B report for §3 cross-link (shown in compare header).",
    )
    ap.add_argument(
        "--export-pnl-details",
        nargs="?",
        const="__auto__",
        default=None,
        metavar="PREFIX",
        help="Write PREFIX_pnl_long.csv, PREFIX_pnl_matrix.md, PREFIX_profit_factor.md, PREFIX_best_per_pair.md "
        "(includes best bar size + strategy per pair). "
        "With --jsonl-out and no PREFIX, uses the JSONL path without .jsonl.",
    )
    ap.add_argument(
        "--fee-bps",
        type=float,
        default=None,
        metavar="BPS",
        help="Override fee_bps_per_leg from config (e.g. 4.0 for CDE estimate). "
        "Default: use config value (often 0). Run with realistic fees before promoting any strategy.",
    )
    ap.add_argument(
        "--min-trades-per-window",
        type=int,
        default=0,
        metavar="N",
        help="Flag rows with fewer than N trades per window with [LOW_N] in matrix output "
        "and exclude them from the best-per-pair ranking. Default: 0 (no filter).",
    )
    args = ap.parse_args()
    extra_intervals = _parse_intervals_arg(args.intervals)

    cfg_path = Path(args.config).resolve() if args.config else (_REPO / "config.toml")
    if not cfg_path.exists():
        print("config.toml not found", cfg_path, file=sys.stderr)
        return 2

    with cfg_path.open("rb") as f:
        raw = tomllib.load(f)
    bot = load_scalp_config(raw)
    if not bot.pairs:
        print("No [scalp].pairs in config", file=sys.stderr)
        return 2

    venue = (bot.venue or "coinbase_perps").strip().lower()
    bar_store.set_bar_store_venue("coinbase_perps")

    fill_model = getattr(bot, "backtest_fill_model", "next_open") or "next_open"
    fee_override = (args.fee_bps / 10_000.0) if args.fee_bps is not None else None
    slip = bot.slippage_bps / 10_000.0
    min_trades_per_window = int(args.min_trades_per_window)

    rows: list[dict] = []
    skipped_msgs: list[str] = []

    for pk, pc in sorted(bot.pairs.items(), key=lambda kv: kv[1].symbol):
        symbol = pc.symbol
        cfg_iv = int(pc.interval)
        if extra_intervals is not None:
            interval_list = sorted(set(extra_intervals) | {cfg_iv})
        else:
            interval_list = [cfg_iv]

        base_params = _params_from_config(pc, bot)
        if fee_override is not None:
            base_params = replace(base_params, fee_pct=fee_override, slippage_pct=slip, fill_model=fill_model)
        else:
            base_params = replace(base_params, slippage_pct=slip, fill_model=fill_model)

        for interval in interval_list:
            bars = bar_store.load_bars(symbol, interval)
            if bars is None or len(bars.get("close", [])) < 40:
                msg = f"# skip {pk} {symbol} {interval}m: insufficient or missing parquet"
                skipped_msgs.append(msg)
                print(msg, file=sys.stderr)
                continue

            n = len(bars["close"])
            ts0 = int(bars["timestamp"][0])
            ts1 = int(bars["timestamp"][-1])
            windows = _window_thirds(n)
            bpy = _bars_per_year(interval)

            for wname, a, b in windows:
                seg = _slice_bars(bars, a, b)
                if len(seg["close"]) < 40:
                    continue
                for mode in STRATEGIES:
                    p = replace(base_params, mode=mode)
                    m = evaluate_params(seg, p, bars_per_year=bpy)
                    score = float(m.expectancy) * math.sqrt(max(1, int(m.trade_count)))
                    n_trades = int(m.trade_count)
                    low_n = min_trades_per_window > 0 and n_trades < min_trades_per_window
                    rows.append({
                        "pair_key": pk,
                        "symbol": symbol,
                        "interval_m": interval,
                        "config_interval_m": cfg_iv,
                        "window": wname,
                        "bar_start": a,
                        "bar_end": b,
                        "n_bars": b - a,
                        "ts_first": int(seg["timestamp"][0]),
                        "ts_last": int(seg["timestamp"][-1]),
                        "strategy": mode,
                        "trades": n_trades,
                        "low_n": low_n,
                        "win_rate": round(m.win_rate, 4),
                        "total_pnl": round(m.total_pnl, 6),
                        "expectancy": round(m.expectancy, 8),
                        "profit_factor": float(m.profit_factor) if not math.isinf(m.profit_factor) else None,
                        "max_dd_pct": round(m.max_drawdown_pct, 4),
                        "sharpe": round(m.sharpe, 4),
                        "sortino": round(m.sortino, 4),
                        "score_exp_sqrt_n": round(score, 6),
                    })

            print(
                f"# {pk} {symbol} {interval}m full_span bars={n} ts={ts0}..{ts1}",
                file=sys.stderr,
            )

    fee_bps_used = (args.fee_bps if args.fee_bps is not None else effective_scalp_fee_bps_per_leg(bot))
    contract_obj = {
        "venue": venue,
        "fill_model": fill_model,
        "fee_bps_per_leg": fee_bps_used,
        "fee_bps_source": "cli_override" if args.fee_bps is not None else "config_effective_maker_or_taker",
        "fee_usd_per_contract_per_leg": getattr(bot, "fee_usd_per_contract_per_leg", 0.0),
        "order_type": bot.order_type,
        "slippage_bps": bot.slippage_bps,
        "windows": "thirds_of_series_bar_index",
        "intervals_swept": extra_intervals if extra_intervals is not None else "config_only",
        "min_trades_per_window": min_trades_per_window if min_trades_per_window > 0 else None,
    }
    contract_line = json.dumps({"contract": contract_obj}, indent=2)
    print(contract_line)
    for r in rows:
        print(json.dumps(r))

    jsonl_out = Path(args.jsonl_out).resolve() if args.jsonl_out else None
    if jsonl_out is not None:
        jsonl_out.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_out.open("w", encoding="utf-8") as jf:
            jf.write(contract_line + "\n")
            for r in rows:
                jf.write(json.dumps(r) + "\n")
        print(f"# wrote JSONL {jsonl_out}", file=sys.stderr)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("pnl_lab_%Y%m%d_%H%M%S")
    compare_md = Path(args.compare_md).resolve() if args.compare_md else None
    if compare_md is not None:
        pair_modes = _pair_focus_modes(bot)
        cmd = " ".join(sys.argv)
        jsonl_rel = None
        if jsonl_out is not None:
            try:
                jsonl_rel = str(jsonl_out.relative_to(_REPO))
            except ValueError:
                jsonl_rel = str(jsonl_out)
            write_compare_from_jsonl_file(
                jsonl_out,
                repo=_REPO,
                pair_focus_modes=pair_modes,
                run_id=run_id,
                command_line=cmd,
                out_md_path=compare_md,
                skipped_messages=skipped_msgs,
                lens_b_path=args.lens_b,
            )
        else:
            md = generate_compare_document(
                contract=contract_obj,
                rows=rows,
                pair_focus_modes=pair_modes,
                run_id=run_id,
                repo=_REPO,
                command_line=cmd,
                jsonl_relative=jsonl_rel,
                skipped_messages=skipped_msgs,
                lens_b_path=args.lens_b,
            )
            compare_md.parent.mkdir(parents=True, exist_ok=True)
            compare_md.write_text(md, encoding="utf-8")
        print(f"# wrote compare markdown {compare_md}", file=sys.stderr)

    pnl_prefix: Path | None = None
    if args.export_pnl_details is not None:
        if args.export_pnl_details == "__auto__":
            if jsonl_out is not None:
                pnl_prefix = jsonl_out.with_suffix("")
            else:
                print(
                    "# --export-pnl-details: pass PREFIX or use with --jsonl-out for auto path",
                    file=sys.stderr,
                )
        else:
            pnl_prefix = Path(args.export_pnl_details).resolve()
    if pnl_prefix is not None and rows:
        c_csv, c_md, c_pf, c_best = write_pnl_detail_artifacts(rows, pnl_prefix, contract=contract_obj)
        print(f"# wrote PnL grid CSV {c_csv}", file=sys.stderr)
        print(f"# wrote PnL grid MD {c_md}", file=sys.stderr)
        print(f"# wrote profit factor MD {c_pf}", file=sys.stderr)
        print(f"# wrote best per pair MD {c_best}", file=sys.stderr)

    # Summary: best strategy per pair_key + interval + window
    by_key: dict[tuple, list[dict]] = {}
    for r in rows:
        k = (r["pair_key"], int(r["interval_m"]), r["window"])
        by_key.setdefault(k, []).append(r)
    print("\n# SUMMARY best score_exp_sqrt_n per pair_key+interval+window", file=sys.stderr)
    for k in sorted(by_key.keys()):
        best = max(by_key[k], key=lambda x: x["score_exp_sqrt_n"])
        print(
            f"{k[0]:12} {k[1]:3}m {k[2]:5} -> {best['strategy']:16} "
            f"pnl={best['total_pnl']:.4f} trades={best['trades']} score={best['score_exp_sqrt_n']:.4f}",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
