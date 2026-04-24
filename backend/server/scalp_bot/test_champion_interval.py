"""Champion row interval matching (5m migration guardrails)."""

from __future__ import annotations

from scalp_bot.strategy_lookback import champion_row_matches_pair_interval, pair_has_wfo_champion


def test_champion_row_matches_pair_interval_legacy_no_key() -> None:
    assert champion_row_matches_pair_interval({"mode": "ema_momentum"}, 5) is True


def test_champion_row_matches_pair_interval_match() -> None:
    assert champion_row_matches_pair_interval({"interval": 5}, 5) is True


def test_champion_row_matches_pair_interval_mismatch() -> None:
    assert champion_row_matches_pair_interval({"interval": 15}, 5) is False


def test_pair_has_wfo_champion_respects_interval() -> None:
    store = {"SLP-20DEC30-CDE": {"interval": 15, "mode": "macd_scalp", "params": {}}}
    assert pair_has_wfo_champion(store, "SLP-20DEC30-CDE", 5) is False
    assert pair_has_wfo_champion(store, "SLP-20DEC30-CDE", 15) is True
