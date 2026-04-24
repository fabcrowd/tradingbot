"""Mirror stdlib logging into dashboard ``UiEventLog`` via ``DashboardServer.log_action`` (async-safe)."""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .ws_server import DashboardServer

_MAX_DETAIL = 16000


class UiMirrorLogHandler(logging.Handler):
    """Forwards log records to ``DashboardServer.log_action`` on the server's asyncio loop."""

    def __init__(self, server_ref: Callable[[], Any]) -> None:
        super().__init__()
        self._server_ref = server_ref

    def emit(self, record: logging.LogRecord) -> None:
        try:
            srv = self._server_ref()
            if srv is None:
                return
            loop = getattr(srv, "_asyncio_loop", None)
            if loop is None or loop.is_closed():
                return
            if not self.filter(record):
                return
            msg = record.getMessage()
            if record.exc_info:
                msg = msg + "\n" + "".join(traceback.format_exception(*record.exc_info))
            if len(msg) > _MAX_DETAIL:
                msg = msg[:_MAX_DETAIL] + "…"
            title = f"{record.name} [{record.levelname}]"
            if len(title) > 500:
                title = title[:500] + "…"
            lvl = (
                "error"
                if record.levelno >= logging.ERROR
                else "warning"
                if record.levelno >= logging.WARNING
                else "info"
            )
            fut = asyncio.run_coroutine_threadsafe(
                srv.log_action(lvl, title, msg, "python_log", kind="server_log"),
                loop,
            )

            def _swallow_done(f: asyncio.Future) -> None:
                try:
                    f.result()
                except BaseException:
                    pass

            fut.add_done_callback(_swallow_done)
        except Exception:
            self.handleError(record)


def install_ui_mirror_log_handlers(server: DashboardServer) -> UiMirrorLogHandler:
    """Attach one handler to ``backend.server`` and ``arceus`` loggers; return it for ``removeHandler`` on shutdown."""
    dbg = os.environ.get("UI_LOG_MIRROR_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    h = UiMirrorLogHandler(lambda: server)
    h.setLevel(logging.DEBUG if dbg else logging.INFO)
    logging.getLogger("backend.server").addHandler(h)
    logging.getLogger("arceus").addHandler(h)
    return h
