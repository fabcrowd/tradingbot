"""Tests for continuous WFO config mapping (legacy rolling promotion path removed)."""

from __future__ import annotations

import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_runtime import (
    _apply_vol_armed_wfo_overlay,
    _wfo_config_from_scalp_cfg,
)
from scalp_bot.scalp_wfo import WFOConfig, wfo_effective_roll_span_hours


def test_wfo_continuous_fields_map_to_wfo_config() -> None:
    """Continuous eval fields in ScalpBotConfig are forwarded to WFOConfig."""
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_continuous_eval_hours=504.0,
        wfo_continuous_warmup_hours=120.0,
        wfo_continuous_min_trades=15,
    )
    wfo = _wfo_config_from_scalp_cfg(cfg)
    assert wfo.continuous_eval_hours == pytest.approx(504.0)
    assert wfo.continuous_warmup_hours == pytest.approx(120.0)
    assert wfo.continuous_min_trades == 15
    assert wfo.holdout_rank_by_period is True
    assert wfo.period_rank_metric == "total_pnl"
    assert wfo_effective_roll_span_hours(wfo) == pytest.approx(624.0)


def test_vol_armed_overlay_is_noop_in_continuous_mode() -> None:
    """_apply_vol_armed_wfo_overlay always returns base unchanged (continuous mode)."""
    base = WFOConfig(continuous_eval_hours=672.0, continuous_warmup_hours=168.0)
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_vol_armed_disallow_promotion_relaxation=True,
    )
    out = _apply_vol_armed_wfo_overlay(base, cfg)
    assert out is base
