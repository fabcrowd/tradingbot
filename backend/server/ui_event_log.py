"""In-memory ring buffer of dashboard UI events (alerts, actions) for Logs tab + WS tail."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any


class UiEventLog:
    """Thread-safe append; tail() returns a shallow copy of the last K entries for snapshots."""

    def __init__(self, max_entries: int = 15000, tail_for_snapshot: int = 500) -> None:
        self._max = max(10, int(max_entries))
        self._tail_k = max(10, min(int(tail_for_snapshot), self._max))
        self._entries: deque[dict[str, Any]] = deque(maxlen=self._max)
        self._lock = threading.Lock()
        self._seq = 0

    def append(
        self,
        *,
        kind: str,
        level: str,
        title: str,
        detail: str = "",
        source: str = "",
        ts: float | None = None,
        exchange_error_id: str | None = None,
        persistent: bool = False,
        meta: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            eid = entry_id or f"ui-{self._seq}-{uuid.uuid4().hex[:10]}"
            row: dict[str, Any] = {
                "id": eid,
                "ts": float(ts if ts is not None else time.time()),
                "kind": kind,
                "level": level,
                "title": title,
                "detail": detail or "",
                "source": source or "",
                "persistent": bool(persistent),
            }
            if exchange_error_id:
                row["exchange_error_id"] = exchange_error_id
            if meta:
                row["meta"] = dict(meta)
            self._entries.append(row)
            return dict(row)

    def tail(self) -> list[dict[str, Any]]:
        with self._lock:
            if len(self._entries) <= self._tail_k:
                return [dict(x) for x in self._entries]
            return [dict(x) for x in list(self._entries)[-self._tail_k :]]
