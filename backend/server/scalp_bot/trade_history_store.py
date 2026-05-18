"""Persist closed scalp legs for ``trade_history`` (chart markers + UI across backend restarts).

Append-only JSONL: ``data/scalp_trade_history.jsonl`` — one JSON object per closed leg,
matching the dict shape appended to ``ScalpTrader._trade_history``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

LOG = logging.getLogger(__name__)

# scalp_bot/ → server/ → backend/ → repo root (matches ``session_logger.DATA_DIR`` depth from ``server/``).
DATA_DIR = Path(__file__).resolve().parents[3] / "data"
TRADE_HISTORY_FILE = "scalp_trade_history.jsonl"


def trade_history_path() -> Path:
    return DATA_DIR / TRADE_HISTORY_FILE


def row_from_position_closed_event(ev: dict[str, Any]) -> dict[str, Any] | None:
    """Build a trade-history row from a session ``position_closed`` scalp event."""
    eid = str(ev.get("entry_cl_ord_id") or "").strip()
    if not eid:
        return None
    try:
        exit_ts = float(ev.get("ts") or ev.get("exit_ts") or 0.0)
    except (TypeError, ValueError):
        exit_ts = 0.0
    row: dict[str, Any] = {
        "pair_key": str(ev.get("pair_key") or ""),
        "symbol": str(ev.get("symbol") or ""),
        "direction": str(ev.get("direction") or ""),
        "strategy_mode": str(ev.get("strategy_mode") or "unknown"),
        "entry_ts": float(ev.get("entry_ts") or 0.0) if ev.get("entry_ts") is not None else exit_ts,
        "exit_ts": exit_ts,
        "entry_price": float(ev.get("entry_price") or 0.0),
        "exit_price": float(ev.get("exit_price") or 0.0),
        "qty": float(ev.get("qty") or 0.0),
        "pnl": round(float(ev.get("pnl") or 0.0), 6),
        "reason": str(ev.get("reason") or ""),
        "simulated": bool(ev.get("simulated", False)),
        "entry_cl_ord_id": eid,
    }
    if not row["pair_key"]:
        return None
    return row


def _dedupe_key(row: dict[str, Any]) -> tuple[str, float]:
    eid = str(row.get("entry_cl_ord_id") or "").strip()
    if not eid:
        eid = f"_legacy|{row.get('pair_key')}|{float(row.get('entry_ts') or 0):.4f}"
    try:
        xts = float(row.get("exit_ts") or 0.0)
    except (TypeError, ValueError):
        xts = 0.0
    return (eid, round(xts, 4))


def append_trade_history_row(row: dict[str, Any]) -> None:
    """Append one closed-leg record (best-effort; failures are logged)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = trade_history_path()
    try:
        line = json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        LOG.exception("trade_history_store: append failed path=%s", path)


def upsert_trade_history_row(row: dict[str, Any]) -> bool:
    """Insert or replace a row by ``(entry_cl_ord_id, exit_ts)``; returns True if file changed."""
    path = trade_history_path()
    key = _dedupe_key(row)
    existing: dict[tuple[str, float], dict[str, Any]] = {}
    if path.is_file():
        for r in _read_all_rows(path):
            existing[_dedupe_key(r)] = r
    if key in existing and existing[key] == row:
        return False
    existing[key] = row
    ordered = sorted(existing.values(), key=lambda r: (float(r.get("exit_ts") or 0), float(r.get("entry_ts") or 0)))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            for r in ordered:
                f.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n")
    except OSError:
        LOG.exception("trade_history_store: upsert write failed path=%s", path)
        return False
    return True


def _read_all_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(o, dict) and o.get("pair_key"):
                    rows.append(o)
    except OSError:
        LOG.warning("trade_history_store: read failed path=%s", path, exc_info=True)
    return rows


def load_trade_history_tail(max_entries: int) -> list[dict[str, Any]]:
    """Load up to ``max_entries`` most recent closed-leg rows (chronological order).

    Dedupes by ``(entry_cl_ord_id, exit_ts)`` so accidental duplicate lines keep the last copy.
    """
    if max_entries <= 0:
        return []
    path = trade_history_path()
    if not path.is_file():
        return []

    dedup: dict[tuple[str, float], dict[str, Any]] = {}
    for o in _read_all_rows(path):
        dedup[_dedupe_key(o)] = o

    uniq = list(dedup.values())
    uniq.sort(key=lambda r: (float(r.get("exit_ts") or 0), float(r.get("entry_ts") or 0)))
    return uniq[-max_entries:]


def iter_position_closed_from_sessions(
    data_dir: Path | None = None,
    *,
    glob_pattern: str = "session_*.jsonl",
) -> Iterable[dict[str, Any]]:
    """Yield ``position_closed`` scalp dicts from session JSONL files (unsorted)."""
    root = data_dir or DATA_DIR
    for path in sorted(root.glob(glob_pattern)):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if o.get("event") != "scalp":
                        continue
                    if o.get("subtype") != "position_closed":
                        continue
                    if not isinstance(o, dict):
                        continue
                    yield o
        except OSError:
            LOG.warning("trade_history_store: skip unreadable session %s", path, exc_info=True)


def _entry_ts_from_sessions(
    data_dir: Path,
    entry_cl_ord_id: str,
) -> float | None:
    """Best-effort entry timestamp from ``scalp_fill_execution`` leg=entry in session logs."""
    eid = str(entry_cl_ord_id or "").strip()
    if not eid:
        return None
    for path in sorted(data_dir.glob("session_*.jsonl")):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or eid not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if o.get("event") != "scalp" or o.get("subtype") != "scalp_fill_execution":
                        continue
                    if str(o.get("leg") or "") != "entry":
                        continue
                    if str(o.get("cl_ord_id") or "") != eid:
                        continue
                    try:
                        return float(o.get("fill_ts") or o.get("placed_ts") or 0.0)
                    except (TypeError, ValueError):
                        return None
        except OSError:
            continue
    return None


def backfill_trade_history_from_sessions(
    data_dir: Path | None = None,
    *,
    dry_run: bool = False,
    include_simulated: bool = False,
) -> dict[str, int]:
    """Merge ``position_closed`` events from session logs into ``scalp_trade_history.jsonl``.

    Returns counts: ``found``, ``eligible``, ``inserted``, ``updated``, ``skipped``.
    """
    found = 0
    eligible = 0
    inserted = 0
    updated = 0
    skipped = 0
    path = trade_history_path()
    on_disk: dict[tuple[str, float], dict[str, Any]] = {}
    if path.is_file():
        for r in _read_all_rows(path):
            on_disk[_dedupe_key(r)] = r

    for ev in iter_position_closed_from_sessions(data_dir):
        found += 1
        if ev.get("simulated") and not include_simulated:
            skipped += 1
            continue
        row = row_from_position_closed_event(ev)
        if row is None:
            skipped += 1
            continue
        root = data_dir or DATA_DIR
        if float(row.get("entry_ts") or 0.0) <= 0.0:
            ets = _entry_ts_from_sessions(root, str(row.get("entry_cl_ord_id") or ""))
            if ets is not None and ets > 0:
                row["entry_ts"] = ets
        eligible += 1
        key = _dedupe_key(row)
        if dry_run:
            if key not in on_disk:
                inserted += 1
            elif on_disk[key] != row:
                updated += 1
            continue
        if key not in on_disk:
            append_trade_history_row(row)
            on_disk[key] = row
            inserted += 1
        elif on_disk[key] != row:
            upsert_trade_history_row(row)
            on_disk[key] = row
            updated += 1
    return {
        "found": found,
        "eligible": eligible,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }
