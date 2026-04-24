#!/usr/bin/env python3
"""Summarize a session JSONL for overnight / morning review.

Usage (from repo root):
  python tools/session_report.py
  python tools/session_report.py data/session_20260404_022014.jsonl

Picks newest data/session_*.jsonl if no path given.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _load_lines(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _newest_session() -> Path | None:
    if not DATA.is_dir():
        return None
    files = sorted(DATA.glob("session_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def main() -> int:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        p = _newest_session()
        if p is None:
            print("No data/session_*.jsonl found.", file=sys.stderr)
            return 1
        path = p

    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 1

    rows = _load_lines(path)
    print(f"File: {path}")
    print(f"Lines: {len(rows)}")
    if not rows:
        return 0

    kinds = Counter(r.get("event") for r in rows)
    print("\nEvents:")
    for k, n in kinds.most_common():
        print(f"  {k}: {n}")

    fills = [r for r in rows if r.get("event") == "fill"]
    if fills:
        buy_n = sum(1 for r in fills if r.get("side") == "buy")
        sell_n = sum(1 for r in fills if r.get("side") == "sell")
        last = fills[-1]
        print("\nFills:")
        print(f"  total={len(fills)} buy={buy_n} sell={sell_n}")
        print(
            f"  last: {last.get('side')} {last.get('pair')} "
            f"qty={last.get('qty')} @ {last.get('price')} "
            f"total_pnl={last.get('total_pnl')}"
        )

    halts = [r for r in rows if r.get("event") == "risk_halt"]
    if halts:
        print("\nRisk halts:")
        for h in halts:
            print(f"  {h.get('reason')} (total_pnl={h.get('total_pnl')})")

    snaps = [r for r in rows if r.get("event") == "snapshot"]
    if snaps:
        first, last = snaps[0], snaps[-1]
        print("\nSnapshots (first -> last):")
        print(
            f"  session_sec {first.get('session_sec')} -> {last.get('session_sec')} | "
            f"pnl {first.get('total_pnl')} -> {last.get('total_pnl')}"
        )
        print(
            f"  engine_running={last.get('engine_running')} "
            f"risk_halted={last.get('risk_halted')} "
            f"open_orders={last.get('open_orders_total')}"
        )
        pairs_last = last.get("pairs") or {}
        for pk, pv in pairs_last.items():
            print(
                f"  {pk}: spread_bps={pv.get('spread_bps')} "
                f"fills={pv.get('fills')} open_b/s={pv.get('open_buys')}/{pv.get('open_sells')} "
                f"mid={pv.get('mid')} mkt_spread_bps={pv.get('market_spread_bps')}"
            )

    summary = [r for r in rows if r.get("event") == "session_summary"]
    if summary:
        s = summary[-1]
        print("\nSession summary (shutdown):")
        print(f"  duration_sec={s.get('duration_sec')} total_pnl={s.get('total_pnl')}")
        for pk, pv in (s.get("pairs") or {}).items():
            print(
                f"  {pk}: fills={pv.get('fills')} pnl={pv.get('total_pnl')} "
                f"fills/hr={pv.get('fills_per_hour')}"
            )

    learners = [r for r in rows if r.get("event") == "learner"]
    if learners:
        print(f"\nLearner adjustments: {len(learners)}")
        adj = learners[-3:]
        for r in adj:
            print(
                f"  {r.get('pair')}: {r.get('action')} "
                f"{r.get('spread_old')}->{r.get('spread_new')} ({r.get('reason')})"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
