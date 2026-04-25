#!/usr/bin/env python3
"""Backfill Coinbase perp Parquet bars for PnL lab / multi-timeframe charts (5m, 15m, 60m).

Uses Coinbase Advanced Trade REST ``get_public_candles``. Loads ``.env`` from the repo root:
if ``COINBASE_API_KEY`` and ``COINBASE_API_SECRET`` are set (same as live scalp), the client
is authenticated — otherwise an unauthenticated public client is used.

Requires ``coinbase-advanced-py`` (see ``backend/requirements.txt``).

Run from repo root::

  python .optimization/pnl-feedback-lab/scripts/backfill_coinbase_lab_intervals.py
  python .../backfill_coinbase_lab_intervals.py --hours 2160 --intervals 5,15,60
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SERVER = _REPO / "backend" / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

import tomllib

from scalp_bot import bar_store
from scalp_bot.scalp_config import load_scalp_config


def _parse_intervals(s: str) -> list[int]:
    out: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out


async def _run() -> int:
    ap = argparse.ArgumentParser(description="Backfill Coinbase candles for lab interval discovery.")
    ap.add_argument("--config", default=str(_REPO / "config.toml"), help="Path to config.toml")
    ap.add_argument(
        "--hours",
        type=float,
        default=None,
        help="History depth in hours (default: max(1440, span of existing 15m + 48h), i.e. >=60d or longer if already on disk)",
    )
    ap.add_argument(
        "--intervals",
        type=str,
        default="5,15,60",
        help="Comma-separated bar sizes in minutes (default: 5,15,60 for lab charts)",
    )
    ns = ap.parse_args()

    with Path(ns.config).open("rb") as f:
        raw = tomllib.load(f)
    bot = load_scalp_config(raw)
    if (bot.venue or "").strip().lower() != "coinbase_perps":
        print("backfill_coinbase_lab_intervals: [scalp].venue must be coinbase_perps", file=sys.stderr)
        return 2

    bar_store.set_bar_store_venue("coinbase_perps")
    intervals = _parse_intervals(ns.intervals)
    if not intervals:
        print("no intervals parsed", file=sys.stderr)
        return 2

    hours = ns.hours
    if hours is None:
        hours = 1440.0  # 60d minimum target for a usable multi-window lab view
        for pc in bot.pairs.values():
            iv = int(pc.interval)
            b = bar_store.load_bars(pc.symbol, iv)
            if b is not None and len(b.get("timestamp", [])) >= 2:
                ts = b["timestamp"]
                span_h = float(int(ts[-1]) - int(ts[0])) / 3600.0 + 48.0
                hours = max(hours, span_h)
                break
        print(f"# hours_needed={hours:.1f} (override with --hours)")

    symbols = sorted({pc.symbol for pc in bot.pairs.values()})
    total_new = 0
    for sym in symbols:
        for iv in intervals:
            print(f"# backfill {sym} {iv}m …", flush=True)
            n = await bar_store.backfill_coinbase_public_candles(sym, iv, hours)
            total_new += n
            print(f"#   new_rows={n}", flush=True)

    print(f"# total_new_rows={total_new}", flush=True)
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
