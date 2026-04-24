"""``wfo_pnl_first_promotion`` relaxes WFO gates toward mean holdout USD."""

from __future__ import annotations

import time

import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_runtime import (
    ScalpRuntime,
    _apply_vol_armed_wfo_overlay,
    _wfo_config_from_scalp_cfg,
)
from scalp_bot.scalp_wfo import WFOConfig
from state import BotState


def test_wfo_pnl_first_forces_total_pnl_and_relaxes_gates() -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_objective="sharpe",
        wfo_require_positive_holdout=True,
        wfo_min_holdout_pf=1.0,
        wfo_min_mean_score=0.0,
        wfo_min_stability_ratio=0.5,
        wfo_require_holdout_beat_prior=True,
        wfo_pnl_first_promotion=True,
    )
    wfo = _wfo_config_from_scalp_cfg(cfg)
    assert wfo.objective == "total_pnl"
    assert wfo.require_positive_latest_holdout is False
    assert wfo.min_latest_holdout_pf == 0.0
    assert wfo.min_mean_score == -999.0
    assert wfo.min_stability_ratio == -999.0
    assert wfo.require_holdout_beat_prior is False
    assert wfo.max_avg_dd_pct == pytest.approx(999.0)
    assert wfo.max_param_delta_hold >= 10_000


def test_vol_armed_overlay_skipped_when_pnl_first() -> None:
    base = WFOConfig(
        min_window_fraction=0.48,
        min_latest_holdout_pf=1.0,
        allow_promotion_relaxation=True,
    )
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_pnl_first_promotion=True,
        wfo_vol_armed_min_window_fraction=0.62,
        wfo_vol_armed_min_latest_holdout_pf=1.15,
        wfo_vol_armed_disallow_promotion_relaxation=True,
    )
    out = _apply_vol_armed_wfo_overlay(base, cfg)
    assert out is base


def test_wfo_pass_config_no_vol_overlay_when_pnl_first() -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_pnl_first_promotion=True,
        wfo_min_window_fraction=0.35,
        volatility_filter_enabled=True,
        wfo_vol_armed_min_window_fraction=0.99,
        wfo_vol_armed_disallow_promotion_relaxation=True,
    )
    st = BotState()
    rt = ScalpRuntime(st, cfg, live_mgr=None, session_logger=None)
    rt._vol_filt_armed_until["p1"] = time.time() + 60.0
    wpass = rt._wfo_pass_config()
    assert wpass.min_window_fraction == pytest.approx(0.35)
