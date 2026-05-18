#!/usr/bin/env python3
"""Offline report: live forward PnL vs holdout expectancy per champion symbol."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend" / "server"))

from scalp_bot.forward_reconciliation import reconciliation_from_champion_row  # noqa: E402


def _load_champions(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _forward_from_session(path: Path, symbol: str, since_ts: float) -> tuple[float, int]:
    pnl = 0.0
    trades = 0
    if not path.is_file():
        return pnl, trades
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != "scalp_trade_closed":
            continue
        if str(row.get("symbol", "")) != symbol:
            continue
        ex = float(row.get("exit_ts") or row.get("timestamp") or 0.0)
        if ex < since_ts:
            continue
        pnl += float(row.get("pnl") or 0.0)
        trades += 1
    return pnl, trades


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--champion",
        type=Path,
        default=REPO / "data" / "scalp_champion.json",
    )
    ap.add_argument(
        "--session",
        type=Path,
        default=None,
        help="Session JSONL for live closed trades (optional)",
    )
    ap.add_argument("--alert-pct", type=float, default=0.30)
    ap.add_argument("--since-ts", type=float, default=0.0)
    args = ap.parse_args()

    champs = _load_champions(args.champion)
    if not champs:
        print(f"No champions at {args.champion}")
        return 1

    session = args.session
    if session is None:
        data_dir = REPO / "data"
        candidates = sorted(data_dir.glob("session_*.jsonl"), key=lambda p: p.stat().st_mtime)
        session = candidates[-1] if candidates else None

    print(f"champion_file={args.champion}")
    print(f"session={session or '(none)'}")
    print(f"{'symbol':<22} {'mode':<18} {'fwd_pnl':>10} {'trades':>6} {'ratio':>8} {'div%':>8} {'alert':>5}")
    print("-" * 90)

    any_alert = False
    for _key, row in sorted(champs.items()):
        sym = str(row.get("symbol", _key))
        fwd_pnl, fwd_tr = 0.0, 0
        if session is not None:
            fwd_pnl, fwd_tr = _forward_from_session(session, sym, args.since_ts)
        rec = reconciliation_from_champion_row(
            row,
            forward_pnl=fwd_pnl,
            forward_trades=fwd_tr,
            period_start=args.since_ts,
            alert_pct=args.alert_pct,
        )
        ratio = rec.get("forward_ratio")
        div = rec.get("divergence_pct")
        alert = rec.get("alert", False)
        if alert:
            any_alert = True
        print(
            f"{sym:<22} {str(rec.get('mode', '')):<18} "
            f"{fwd_pnl:>10.4f} {fwd_tr:>6} "
            f"{(f'{ratio:.4f}' if ratio is not None else 'n/a'):>8} "
            f"{(f'{div:.2%}' if div is not None else 'n/a'):>8} "
            f"{str(alert):>5}"
        )
    return 2 if any_alert else 0


if __name__ == "__main__":
    raise SystemExit(main())
