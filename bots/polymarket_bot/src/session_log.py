"""JSONL session event logging for observability and replay."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger("polymarket_bot.session")


class SessionLogger:
    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._path = log_dir / f"session_{ts}.jsonl"
        self._f = self._path.open("a", encoding="utf-8")
        LOG.info("Session log: %s", self._path)

    def log(self, event: str, **kwargs: Any) -> None:
        row = {"ts": time.time(), "event": event, **kwargs}
        self._f.write(json.dumps(row) + "\n")
        self._f.flush()

    def log_tick(self, **kwargs: Any) -> None:
        self.log("tick", **kwargs)

    def log_fill(self, **kwargs: Any) -> None:
        self.log("fill", **kwargs)

    def log_resolution(self, **kwargs: Any) -> None:
        self.log("resolution", **kwargs)

    def log_risk(self, **kwargs: Any) -> None:
        self.log("risk", **kwargs)

    def log_signal(self, **kwargs: Any) -> None:
        self.log("taker_signal", **kwargs)

    def close(self) -> None:
        self._f.close()
