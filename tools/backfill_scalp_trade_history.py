#!/usr/bin/env python3
"""Backfill ``data/scalp_trade_history.jsonl`` from session ``position_closed`` events."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend" / "server"))

from scalp_bot import trade_history_store as store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge session position_closed events into scalp_trade_history.jsonl",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=store.DATA_DIR,
        help=f"Directory with session_*.jsonl (default: {store.DATA_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts only; do not write scalp_trade_history.jsonl",
    )
    parser.add_argument(
        "--include-simulated",
        action="store_true",
        help="Include rows where position_closed.simulated is true",
    )
    args = parser.parse_args()
    counts = store.backfill_trade_history_from_sessions(
        args.data_dir,
        dry_run=args.dry_run,
        include_simulated=args.include_simulated,
    )
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
