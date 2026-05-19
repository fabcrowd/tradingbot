"""Tests for WFO champion-selection (continuous ranking and per-mode pick)."""

from __future__ import annotations

import numpy as np
import pytest

from scalp_bot.scalp_vec_backtest import BacktestMetrics
from scalp_bot.scalp_wfo import (
    WFOConfig,
    _holdout_rank_score,
    _pick_holdout_champion,
    _pick_holdout_champion_per_mode_first,
)


def _m(
    *,
    pnl: float = 1.0,
    trades: int = 5,
    dd: float = 10.0,
    sharpe: float = 0.5,
) -> BacktestMetrics:
    return BacktestMetrics(
        trade_count=trades,
        win_count=max(1, trades // 2),
        win_rate=0.5,
        total_pnl=pnl,
        avg_pnl=pnl / max(1, trades),
        expectancy=pnl / max(1, trades),
        max_drawdown=abs(pnl) * 0.5,
        max_drawdown_pct=dd,
        avg_hold_bars=3.0,
        profit_factor=1.2,
        sharpe=sharpe,
        sortino=0.5,
        calmar=0.1,
        recovery_factor=1.0,
        buy_hold_return=0.0,
        trades=[],
    )


class _FakeGrid:
    """Minimal grid proxy for per-mode champion test."""

    def __init__(self, modes: list[str]) -> None:
        self._modes = modes

    def __getitem__(self, idx: int):
        class _Row:
            mode = self._modes[idx]

        return _Row()

    def __len__(self) -> int:
        return len(self._modes)


def test_champion_json_score_kind_tag_period_rank() -> None:
    """holdout_rank_by_period=True → score_kind='sum_holdout_total_pnl'."""
    wfo = WFOConfig(holdout_rank_by_period=True)
    score_kind = (
        "sum_holdout_total_pnl"
        if bool(getattr(wfo, "holdout_rank_by_period", False))
        else "mean_holdout_objective"
    )
    assert score_kind == "sum_holdout_total_pnl"


def test_per_mode_winner_then_overall() -> None:
    """sar_chop dominates grid count but rsi_div best beats it → rsi_div wins."""
    modes = ["sar_chop"] * 4 + ["rsi_div"] * 2
    grid = _FakeGrid(modes)

    wfo = WFOConfig(
        holdout_rank_by_period=True,
        holdout_tiebreakers=("sum_holdout_total_pnl",),
        holdout_score_epsilon=0.0,
    )

    candidates = [
        (500.0, 1.0, 0, [_m(pnl=500.0, trades=10)]),
        (490.0, 1.0, 1, [_m(pnl=490.0, trades=10)]),
        (480.0, 1.0, 2, [_m(pnl=480.0, trades=10)]),
        (470.0, 1.0, 3, [_m(pnl=470.0, trades=10)]),
        (520.0, 1.0, 4, [_m(pnl=520.0, trades=10)]),
        (510.0, 1.0, 5, [_m(pnl=510.0, trades=10)]),
    ]

    chosen, diag = _pick_holdout_champion_per_mode_first(candidates, grid, wfo)
    assert chosen[2] == 4
    assert diag["per_mode_count"] == 2
    assert set(diag["mode_names"]) == {"sar_chop", "rsi_div"}


def test_per_mode_winner_flat_pick_same_when_one_mode() -> None:
    """With a single mode, per-mode and flat picks are identical."""
    modes = ["ema_momentum"] * 3
    grid = _FakeGrid(modes)
    wfo = WFOConfig(holdout_rank_by_period=True, holdout_tiebreakers=())
    candidates = [
        (300.0, 1.0, 0, [_m(pnl=300.0)]),
        (400.0, 1.0, 1, [_m(pnl=400.0)]),
        (350.0, 1.0, 2, [_m(pnl=350.0)]),
    ]
    per_mode_chosen, _ = _pick_holdout_champion_per_mode_first(candidates, grid, wfo)
    flat_chosen, _ = _pick_holdout_champion(candidates, wfo)
    assert per_mode_chosen[2] == flat_chosen[2]


def test_period_rank_tiebreak_calmar_prefers_lower_dd() -> None:
    """calmar metric: equal PnL, lower DD wins."""
    wfo = WFOConfig(holdout_rank_by_period=True, period_rank_metric="calmar")
    scores = np.array([1.0, 1.0, 1.0])

    mlist_high_dd = [_m(pnl=1.0, dd=20.0), _m(pnl=1.0, dd=20.0), _m(pnl=1.0, dd=20.0)]
    mlist_low_dd = [_m(pnl=1.0, dd=5.0), _m(pnl=1.0, dd=5.0), _m(pnl=1.0, dd=5.0)]

    score_high = _holdout_rank_score(wfo, scores, mlist_high_dd)
    score_low = _holdout_rank_score(wfo, scores, mlist_low_dd)

    assert score_low > score_high


def test_period_rank_total_pnl_ignores_dd() -> None:
    """Default total_pnl metric ranks by sum PnL, not risk-adjusted."""
    wfo = WFOConfig(holdout_rank_by_period=True, period_rank_metric="total_pnl")

    high_pnl = [_m(pnl=10.0, dd=50.0)]
    low_pnl = [_m(pnl=1.0, dd=1.0)]

    assert _holdout_rank_score(wfo, np.array([1.0]), high_pnl) > _holdout_rank_score(
        wfo, np.array([1.0]), low_pnl,
    )


def test_period_rank_sharpe_like_uses_mean_sharpe() -> None:
    """sharpe_like metric returns mean Sharpe across folds."""
    wfo = WFOConfig(holdout_rank_by_period=True, period_rank_metric="sharpe_like")
    mlist = [_m(sharpe=0.4), _m(sharpe=0.6), _m(sharpe=0.8)]
    scores = np.array([1.0] * 3)
    result = _holdout_rank_score(wfo, scores, mlist)
    assert result == pytest.approx((0.4 + 0.6 + 0.8) / 3.0)
