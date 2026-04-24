"""Slip calibration EMA → WFO / tuner effective slippage bps."""

from __future__ import annotations

import dataclasses

import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_runtime import ScalpRuntime


@pytest.fixture
def bot_state():
    from state import BotState

    return BotState()


def _base_cfg() -> ScalpBotConfig:
    return ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        sim_mode=False,
        warmup_enabled=False,
        pairs={"p1": ScalpPairConfig(symbol="X-CDE", interval=5)},
        slippage_bps=2.0,
        slip_calibration_enabled=False,
    )


def test_slip_calibration_disabled_uses_config_only(bot_state) -> None:
    rt = ScalpRuntime(bot_state, _base_cfg(), None, None)
    assert rt.effective_slippage_bps_for_sim() == 2.0


def test_slip_calibration_max_with_config_after_min_samples(bot_state) -> None:
    cfg = dataclasses.replace(
        _base_cfg(),
        slip_calibration_enabled=True,
        slip_calibration_min_samples=2,
        slip_calibration_ema_alpha=0.5,
        slip_calibration_mode="max_with_config",
    )
    rt = ScalpRuntime(bot_state, cfg, None, None)
    rt._note_slip_calibration_sample(10.0)
    assert rt.effective_slippage_bps_for_sim() == 2.0  # not enough samples
    rt._note_slip_calibration_sample(10.0)
    assert rt.effective_slippage_bps_for_sim() == 10.0


def test_slip_calibration_replace_mode(bot_state) -> None:
    cfg = dataclasses.replace(
        _base_cfg(),
        slippage_bps=8.0,
        slip_calibration_enabled=True,
        slip_calibration_min_samples=1,
        slip_calibration_ema_alpha=1.0,
        slip_calibration_mode="replace",
        slip_calibration_floor_bps=0.0,
        slip_calibration_cap_bps=100.0,
    )
    rt = ScalpRuntime(bot_state, cfg, None, None)
    rt._note_slip_calibration_sample(3.0)
    assert rt.effective_slippage_bps_for_sim() == 3.0
