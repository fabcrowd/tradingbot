#!/usr/bin/env python3
"""Analyze live scalp_fill_execution slip_bps vs config/WFO defaults."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _iter_fill_events(path: Path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != "scalp_fill_execution":
            continue
        slip = row.get("slip_bps")
        if slip is None:
            continue
        try:
            bps = float(slip)
        except (TypeError, ValueError):
            continue
        if bps != bps:
            continue
        yield {
            "symbol": str(row.get("symbol", "")),
            "leg": str(row.get("leg", "")),
            "slip_bps": bps,
        }


def analyze_session(path: Path, config_bps: float) -> dict:
    rows = list(_iter_fill_events(path))
    if not rows:
        return {"count": 0, "message": "no scalp_fill_execution rows with slip_bps"}
    slips = [r["slip_bps"] for r in rows]
    med = statistics.median(slips)
    p95 = sorted(slips)[int(min(len(slips) - 1, max(0, int(0.95 * len(slips)) - 1)))]
    return {
        "count": len(slips),
        "median_bps": round(med, 4),
        "p95_bps": round(p95, 4),
        "mean_bps": round(statistics.mean(slips), 4),
        "config_bps": config_bps,
        "delta_median_vs_config": round(med - config_bps, 4),
        "recommendation": (
            f"Consider raising slippage_bps toward {round(max(config_bps, med), 2)}"
            if med > config_bps + 2.0
            else "Config slippage_bps is in line with live median"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", type=Path, required=False)
    ap.add_argument("--config-bps", type=float, default=1.0)
    args = ap.parse_args()
    session = args.session
    if session is None:
        candidates = sorted((REPO / "data").glob("session_*.jsonl"), key=lambda p: p.stat().st_mtime)
        session = candidates[-1] if candidates else None
    if session is None or not session.is_file():
        print("No session JSONL found under data/")
        return 1
    result = analyze_session(session, args.config_bps)
    print(f"session={session}")
    for k, v in result.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
