"""Dump ``sar_chop`` per-bar diagnostics for TradingView / chart parity checks.

Loads OHLC from ``data/coinbase_bars/{SYMBOL}_{INTERVAL}m.parquet`` via ``bar_store`` (same as WFO),
runs ``sar_chop_diagnostic_frame``, then prints rows where an entry fired or (optionally) PSAR
flipped.

Examples (repo root)::

    python -m scalp_bot.sar_chop_signal_dump --symbol SLP-20DEC30-CDE --interval 5 --last-n-days 3

    python -m scalp_bot.sar_chop_signal_dump --symbol SLP-20DEC30-CDE --interval 5 \\
        --since 1745107200 --until 1745280000 --include-flips --csv out.csv

Requires ``pyarrow`` for Parquet (same as the running bot).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

import numpy as np

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from scalp_bot import bar_store
from scalp_bot.scalp_vec_backtest import sar_chop_diagnostic_frame


def _fmt_ts(ts: float) -> str:
    return dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S UTC")


def main() -> int:
    p = argparse.ArgumentParser(description="Dump sar_chop signals + internals from bar_store Parquet.")
    p.add_argument("--symbol", default="SLP-20DEC30-CDE", help="CDE product_id as stored in Parquet filename")
    p.add_argument("--interval", type=int, default=5, help="Candle interval minutes")
    p.add_argument("--last-n-days", type=float, default=None, help="Trim to this many days (see trim-anchor)")
    p.add_argument(
        "--trim-anchor",
        choices=("wall", "latest_bar"),
        default="latest_bar",
        help="Match WFO: latest_bar trims from last stored candle",
    )
    p.add_argument("--since", type=float, default=None, help="Unix seconds: first bar to include (inclusive)")
    p.add_argument("--until", type=float, default=None, help="Unix seconds: last bar to include (inclusive)")
    p.add_argument("--include-flips", action="store_true", help="Also print bars with PSAR flip but no entry")
    p.add_argument("--max-rows", type=int, default=500, help="Cap printed rows (signals + flips)")
    p.add_argument("--csv", type=str, default=None, help="Write matching rows to this CSV path")
    p.add_argument("--no-lucid", action="store_true", help="Set use_lucid_sar=False")
    p.add_argument("--no-utbot", action="store_true", help="Set use_utbot_trail=False")
    args = p.parse_args()

    bar_store.set_bar_store_venue("coinbase_perps")
    bars = bar_store.load_bars(
        args.symbol,
        args.interval,
        last_n_days=args.last_n_days,
        trim_anchor=args.trim_anchor,
    )
    if bars is None:
        print("No bars loaded (missing Parquet or empty window).", file=sys.stderr)
        return 1

    ts = bars["timestamp"].astype(np.float64)
    op = bars["open"].astype(np.float64)
    hi = bars["high"].astype(np.float64)
    lo = bars["low"].astype(np.float64)
    cl = bars["close"].astype(np.float64)

    if args.since is not None:
        m = ts >= float(args.since)
        ts, op, hi, lo, cl = ts[m], op[m], hi[m], lo[m], cl[m]
    if args.until is not None:
        m = ts <= float(args.until)
        ts, op, hi, lo, cl = ts[m], op[m], hi[m], lo[m], cl[m]
    if len(cl) < 3:
        print("Too few bars after since/until filter.", file=sys.stderr)
        return 1

    diag = sar_chop_diagnostic_frame(
        cl, hi, lo,
        use_lucid_sar=not args.no_lucid,
        use_utbot_trail=not args.no_utbot,
    )
    warmup = int(diag["warmup"])
    long_m = diag["long_mask"]
    short_m = diag["short_mask"]
    plf = diag["psar_long_flip"]
    psf = diag["psar_short_flip"]
    chop = diag["chop"]
    mh = diag["macd_hist"]
    ut = diag["ut_dir"]
    lb = diag["lucid_bull"]

    n = len(cl)
    print(f"symbol={args.symbol} interval={args.interval}m bars={n} warmup={warmup}")
    print(f"long_signals={int(long_m.sum())} short_signals={int(short_m.sum())}")

    rows_out: list[dict[str, object]] = []
    printed = 0

    def want_row(i: int) -> bool:
        if long_m[i] or short_m[i]:
            return True
        if args.include_flips and (plf[i] or psf[i]):
            return True
        return False

    for i in range(warmup, n):
        if not want_row(i):
            continue
        row = {
            "i": i,
            "timestamp": int(ts[i]),
            "time_utc": _fmt_ts(ts[i]),
            "open": float(op[i]),
            "high": float(hi[i]),
            "low": float(lo[i]),
            "close": float(cl[i]),
            "long": bool(long_m[i]),
            "short": bool(short_m[i]),
            "psar_long_flip": bool(plf[i]),
            "psar_short_flip": bool(psf[i]),
            "chop": float(chop[i]) if np.isfinite(chop[i]) else "",
            "chop_ok": bool(np.isfinite(chop[i]) and chop[i] < float(diag["chop_threshold"])),
            "macd_hist": float(mh[i]) if np.isfinite(mh[i]) else "",
            "ut_dir": int(ut[i]),
            "lucid_bull": int(lb[i]),
        }
        rows_out.append(row)
        if printed < args.max_rows:
            tag = []
            if long_m[i]:
                tag.append("LONG")
            if short_m[i]:
                tag.append("SHORT")
            if args.include_flips:
                if plf[i] and not long_m[i]:
                    tag.append("psar_L_flip_only")
                if psf[i] and not short_m[i]:
                    tag.append("psar_S_flip_only")
            print(
                f"{row['time_utc']} i={i} {'/'.join(tag) or '-'} "
                f"chop={row['chop']} macd_h={row['macd_hist']} ut={row['ut_dir']} lucid={row['lucid_bull']} "
                f"cl={row['close']:.4f}"
            )
            printed += 1

    if args.csv and rows_out:
        keys = list(rows_out[0].keys())
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows_out)
        print(f"Wrote {len(rows_out)} rows to {args.csv}")

    if len(rows_out) > printed:
        print(f"(suppressed {len(rows_out) - printed} additional rows; use --max-rows or --csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
