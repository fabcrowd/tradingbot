"""Tests for auto mode resolution."""

from __future__ import annotations

from scalp_bot.scalp_mode_resolution import normalize_auto_mode_fallback, resolve_auto_mode


def test_resolve_manual_unchanged() -> None:
    assert resolve_auto_mode("macd_scalp", champion_row=None, auto_mode_fallback="ema_momentum") == "macd_scalp"


def test_resolve_auto_uses_champion() -> None:
    row = {"mode": "rsi_reversion", "params": {}}
    assert (
        resolve_auto_mode("auto", champion_row=row, auto_mode_fallback="ema_momentum")
        == "rsi_reversion"
    )


def test_resolve_auto_no_champion_fallback() -> None:
    assert (
        resolve_auto_mode("auto", champion_row=None, auto_mode_fallback="ema_momentum")
        == "ema_momentum"
    )


def test_normalize_rejects_auto() -> None:
    # Default fallback is sar_chop (WFO will still promote a better-scoring mode as champion).
    assert normalize_auto_mode_fallback("auto") == "sar_chop"
    assert normalize_auto_mode_fallback(None) == "sar_chop"
    assert normalize_auto_mode_fallback("") == "sar_chop"
