"""Windows: keep the system from idle-sleeping while the bot process runs.

Uses SetThreadExecutionState(ES_SYSTEM_REQUIRED). Does not block manual sleep,
lid close, or forced shutdown. No-op on non-Windows.
"""

from __future__ import annotations

import logging
import sys

_LOG = logging.getLogger(__name__)

if sys.platform == "win32":
    import ctypes

    _ES_CONTINUOUS = 0x80000000
    _ES_SYSTEM_REQUIRED = 0x00000001

    def prevent_system_sleep() -> bool:
        prev = ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        )
        if prev == 0:
            _LOG.warning(
                "SetThreadExecutionState failed — Windows may still suspend on idle"
            )
            return False
        _LOG.info("Windows: preventing system idle sleep while bot runs")
        return True

    def allow_system_sleep() -> None:
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)

else:

    def prevent_system_sleep() -> bool:
        return False

    def allow_system_sleep() -> None:
        pass
