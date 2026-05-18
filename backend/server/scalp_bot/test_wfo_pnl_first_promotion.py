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
from scalp_bot.scalp_vec_backtest import BacktestMetrics
from scalp_bot.scalp_wfo import (
    WFOConfig,
    _aggregate_holdout_candidates,
    _holdout_grid_indices,
    _min_holdout_windows_from_fraction,
)
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
    assert wfo.holdout_rank_by_period is True
    assert wfo.exhaustive_grid_holdout is True
    assert wfo.min_period_holdout_trades == 3
    assert _min_holdout_windows_from_fraction(21, wfo) == 1


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
    assert wpass.min_window_fraction == pytest.approx(0.0)
    assert wpass.holdout_rank_by_period is True


def _holdout_m(*, pnl: float, trades: int = 2) -> BacktestMetrics:
    return BacktestMetrics(
        trade_count=trades,
        win_count=max(0, trades // 2),
        win_rate=0.5,
        total_pnl=pnl,
        avg_pnl=pnl / max(1, trades),
        expectancy=pnl / max(1, trades),
        max_drawdown=1.0,
        max_drawdown_pct=10.0,
        avg_hold_bars=3.0,
        profit_factor=1.0,
        sharpe=0.1,
        sortino=0.1,
        calmar=0.1,
        recovery_factor=1.0,
        buy_hold_return=0.0,
        trades=[],
    )


def test_holdout_grid_indices_exhaustive_is_full_grid() -> None:
    wfo = WFOConfig(exhaustive_grid_holdout=True, top_k=80)
    train = [(1.0, 0), (2.0, 1)]
    assert _holdout_grid_indices(wfo, train, 5019) == list(range(5019))


def test_holdout_grid_indices_top_k_when_not_exhaustive() -> None:
    wfo = WFOConfig(exhaustive_grid_holdout=False, top_k=2)
    train = [(3.0, 5), (2.0, 1), (1.0, 9)]
    assert _holdout_grid_indices(wfo, train, 100) == [5, 1]


def test_period_rank_prefers_total_pnl_over_fold_count() -> None:
    """More holdout folds does not beat higher cumulative OOS $ when period ranking is on."""
    wfo = WFOConfig(
        objective="total_pnl",
        holdout_rank_by_period=True,
        min_period_holdout_trades=3,
        min_mean_score=-999.0,
        min_stability_ratio=-999.0,
        max_avg_dd_pct=999.0,
    )
    # pi0: 6 folds × +2 = +12; pi1: 7 folds × +1 = +7 (old cross-window rule favored pi1)
    scores = {
        0: [(2.0, _holdout_m(pnl=2.0)) for _ in range(6)],
        1: [(1.0, _holdout_m(pnl=1.0)) for _ in range(7)],
    }
    cands = _aggregate_holdout_candidates(scores, wfo, min_windows=1)
    assert cands
    best = max(cands, key=lambda c: c[0])
    assert best[2] == 0
    assert best[0] == pytest.approx(12.0)
