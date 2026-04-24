"""Structural A/B for param_tuner vs WFO champion mode lock (proposed-changes-pnl-test harness)."""

from __future__ import annotations

from scalp_bot.param_tuner import champion_tuner_mode_resolution


def test_baseline_champion_keeps_mode_on_mismatch() -> None:
    """Deployed default: champion present, tuner best != active → no mode sync from helper."""
    eff, tag = champion_tuner_mode_resolution(
        wfo_champion_active=True,
        allow_mode_override_champion=False,
        current_active_mode="daviddtech_scalp",
        tuner_best_mode="rsi_reversion",
    )
    assert eff == "daviddtech_scalp"
    assert tag == "champion_lock"


def test_proposed_override_aligns_mode_for_apply_path() -> None:
    """Proposed: same inputs with flag True → effective mode follows tuner grid."""
    eff, tag = champion_tuner_mode_resolution(
        wfo_champion_active=True,
        allow_mode_override_champion=True,
        current_active_mode="daviddtech_scalp",
        tuner_best_mode="rsi_reversion",
    )
    assert eff == "rsi_reversion"
    assert tag == "override_champion"


def test_no_champion_branch_tag() -> None:
    eff, tag = champion_tuner_mode_resolution(
        wfo_champion_active=False,
        allow_mode_override_champion=True,
        current_active_mode="ema_momentum",
        tuner_best_mode="rsi_reversion",
    )
    assert eff == "ema_momentum"
    assert tag == "no_champion"


def test_champion_already_aligned() -> None:
    eff, tag = champion_tuner_mode_resolution(
        wfo_champion_active=True,
        allow_mode_override_champion=True,
        current_active_mode="daviddtech_scalp",
        tuner_best_mode="daviddtech_scalp",
    )
    assert eff == "daviddtech_scalp"
    assert tag == "champion_lock_aligned"
