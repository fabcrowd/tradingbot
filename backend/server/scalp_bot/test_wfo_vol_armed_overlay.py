"""WFO vol-armed overlay and risk-on WFO sleep floor."""

from __future__ import annotations

import time

import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_runtime import ScalpRuntime, _apply_vol_armed_wfo_overlay, _wfo_config_from_scalp_cfg
from scalp_bot.scalp_wfo import WFOConfig


def test_apply_vol_armed_wfo_overlay_is_noop() -> None:
    """In continuous evaluation mode the overlay always returns base unchanged."""
    base = WFOConfig(
        continuous_eval_hours=672.0,
        continuous_warmup_hours=168.0,
        min_latest_holdout_pf=1.0,
    )
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_vol_armed_min_latest_holdout_pf=1.15,
        wfo_vol_armed_disallow_promotion_relaxation=True,
    )
    out = _apply_vol_armed_wfo_overlay(base, cfg)
    assert out is base


def test_risk_on_wfo_sleep_uses_base_frac() -> None:
    from state import BotState

    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_interval_sec=200.0,
        regime_risk_on_enabled=True,
        risk_on_wfo_interval_scale=0.25,
        risk_on_wfo_min_interval_sec=60.0,
        risk_on_wfo_min_base_interval_frac=0.5,
    )
    st = BotState()
    rt = ScalpRuntime(st, cfg, live_mgr=None, session_logger=None)
    rt._regime_risk_on_until = time.time() + 3600.0
    # scaled eff=50 → max(60,50)=60; min_from_base=100 → max(60,60,100)=100
    assert rt._effective_wfo_sleep_sec() == pytest.approx(100.0)


def test_wfo_pass_config_returns_base_when_vol_armed() -> None:
    """Vol-armed overlay is a no-op in continuous mode; _wfo_pass_config returns base unchanged."""
    from state import BotState

    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        volatility_filter_enabled=True,
        wfo_continuous_min_trades=20,
        wfo_vol_armed_disallow_promotion_relaxation=True,
    )
    st = BotState()
    rt = ScalpRuntime(st, cfg, live_mgr=None, session_logger=None)
    base_only = _wfo_config_from_scalp_cfg(cfg)
    assert base_only.continuous_min_trades == 20

    rt._vol_filt_armed_until["p1"] = time.time() + 60.0
    wpass = rt._wfo_pass_config()
    assert wpass.continuous_min_trades == 20
