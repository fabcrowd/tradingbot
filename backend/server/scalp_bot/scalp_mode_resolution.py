"""Resolve ``strategy_mode == "auto"`` to a concrete mode.

``auto`` means: use the WFO champion row's ``mode`` for that symbol when present;
otherwise use ``auto_mode_fallback`` (default ``sar_chop``). This replaces the
legacy behavior of treating ``auto`` as ``daviddtech_scalp`` everywhere.
"""

from __future__ import annotations

import logging

LOG = logging.getLogger(__name__)


def normalize_auto_mode_fallback(fallback: str | None) -> str:
    """Return a safe non-auto mode string for ParamSet / signal dispatch.

    Validates the fallback against WFO_REGISTERED_STRATEGY_MODES at call time
    (NM-005: catch typos at config load rather than mid-execution).
    """
    fb = str(fallback or "sar_chop").strip()
    if not fb or fb == "auto":
        return "sar_chop"
    # Lazy import to avoid circular dependency at module load time
    try:
        from .scalp_vec_backtest import WFO_REGISTERED_STRATEGY_MODES
        if fb not in WFO_REGISTERED_STRATEGY_MODES:
            LOG.error(
                "scalp_mode_resolution: auto_mode_fallback %r is not a registered strategy — "
                "falling back to 'sar_chop'. Registered: %s",
                fb, sorted(WFO_REGISTERED_STRATEGY_MODES),
            )
            return "sar_chop"
    except Exception:
        pass  # registry unavailable — allow the value through; WFO will catch it later
    return fb


def resolve_auto_mode(
    strategy_mode: str,
    *,
    champion_row: dict | None,
    auto_mode_fallback: str | None = None,
) -> str:
    """Resolve config/dashboard ``strategy_mode`` to a concrete execution mode.

    - If ``strategy_mode`` is not ``"auto"``, return it unchanged (manual pin).
    - If ``"auto"`` and ``champion_row`` has a non-empty ``mode`` other than
      ``"auto"``, return that (WFO champion is authoritative).
    - Otherwise return ``normalize_auto_mode_fallback(auto_mode_fallback)``.
    """
    raw = str(strategy_mode or "").strip()
    if raw != "auto":
        return raw
    if champion_row and isinstance(champion_row, dict):
        m = str(champion_row.get("mode") or "").strip()
        if m and m != "auto":
            return m
    return normalize_auto_mode_fallback(auto_mode_fallback)
