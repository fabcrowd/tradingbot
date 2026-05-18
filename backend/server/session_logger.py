"""Session logger — writes structured JSONL events for overnight analysis.

One file per bot session: data/session_YYYYMMDD_HHMMSS.jsonl
Each line is a JSON event.

Event types:
  session_start   — bot started, config summary
  scalp           — scalp bot / WFO / backfill milestones (subtype in payload)
  scalp_snapshot  — periodic scalp summary (every 5 min when scalp enabled)
  session_summary — written at shutdown

Scalp subtypes used for analytics-style review:
  strategy_report_trade — one closed round-trip per strategy (TV-style list row: entry/exit,
    net / MFE / MAE / cumulative); emitted with subtype on each leg close.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import AppConfig
    from .state import BotState

LOG = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class SessionLogger:
    """Writes one JSONL event per line to a per-session file."""

    def __init__(self, state: "BotState", config: "AppConfig") -> None:
        self._state = state
        self._config = config
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._path = DATA_DIR / f"session_{ts}.jsonl"
        self._file = open(self._path, "a", encoding="utf-8", buffering=1)
        self._session_start = time.time()
        LOG.info("Session log: %s", self._path)

    def log_session_start(self) -> None:
        self._write({
            "event": "session_start",
            "mode": self._config.mode,
        })

    def log_scalp(self, subtype: str, **payload: Any) -> None:
        """Structured scalp / WFO / backfill events for overnight JSONL review."""
        self._write({
            "event": "scalp",
            "subtype": subtype,
            **payload,
        })

    def log_scalp_snapshot(self, snap: dict[str, Any]) -> None:
        """Periodic condensed scalp state. Drops ``candles`` to keep JSONL small."""
        lean = {k: v for k, v in snap.items() if k != "candles"}
        self._write({
            "event": "scalp_snapshot",
            "scalp": lean,
        })

    def log_snapshot(self) -> None:
        self._write({
            "event": "snapshot",
            "session_sec": round(time.time() - self._session_start, 0),
            "risk_halted": self._state.risk_halted,
            "risk_halt_reason": (self._state.risk_halt_reason or "")[:120] or None,
            "scalp_risk_halted": self._state.scalp_risk_halted,
            "scalp_risk_halt_reason": (self._state.scalp_risk_halt_reason or "")[:120] or None,
        })

    def write_summary(self) -> None:
        duration = time.time() - self._session_start
        self._write({
            "event": "session_summary",
            "duration_sec": round(duration, 0),
            "file": str(self._path),
        })
        LOG.info("Session summary written to %s", self._path)

    def close(self) -> None:
        try:
            self.write_summary()
        except Exception:
            LOG.debug("Error writing session summary", exc_info=True)
        try:
            self._file.close()
        except Exception:
            LOG.debug("Session log file close failed", exc_info=True)

    def _write(self, data: dict) -> None:
        row = {**data, "ts": round(time.time(), 3)}
        try:
            self._file.write(json.dumps(row) + "\n")
        except Exception:
            LOG.debug("Session log write failed", exc_info=True)
