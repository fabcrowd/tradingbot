"""WFO holdout champion tie-break after mean score."""

from __future__ import annotations

from scalp_bot.scalp_vec_backtest import BacktestMetrics
from scalp_bot.scalp_wfo import WFOConfig, _pick_holdout_champion


def _m(
    *,
    dd: float,
    trades: int = 10,
    pnl: float = 1.0,
) -> BacktestMetrics:
    return BacktestMetrics(
        trade_count=trades,
        win_count=max(1, trades // 2),
        win_rate=0.5,
        total_pnl=pnl,
        avg_pnl=pnl / max(1, trades),
        expectancy=0.1,
        max_drawdown=1.0,
        max_drawdown_pct=dd,
        avg_hold_bars=3.0,
        profit_factor=1.2,
        sharpe=0.5,
        sortino=0.5,
        calmar=0.1,
        recovery_factor=1.0,
        buy_hold_return=0.0,
        trades=[],
    )


def test_tiebreak_prefers_stability_when_mean_tied() -> None:
    wfo = WFOConfig(
        holdout_score_epsilon=0.01,
        holdout_tiebreakers=("stability", "neg_mean_max_dd_pct", "min_holdout_trade_count"),
    )
    # Same mean score, different stability / DD
    cands = [
        (1.0, 2.0, 0, [_m(dd=10.0, trades=5)]),
        (1.0, 5.0, 1, [_m(dd=20.0, trades=5)]),
    ]
    chosen, diag = _pick_holdout_champion(cands, wfo)
    assert chosen[2] == 1  # higher stability wins
    assert diag["holdout_chosen_pi"] == 1


def test_epsilon_includes_runner_up_then_tiebreak() -> None:
    wfo = WFOConfig(
        holdout_score_epsilon=0.5,
        holdout_tiebreakers=("neg_mean_max_dd_pct",),
    )
    cands = [
        (2.0, 1.0, 0, [_m(dd=30.0)]),
        (1.6, 1.0, 1, [_m(dd=5.0)]),  # better (less negative) mean DD
    ]
    chosen, _diag = _pick_holdout_champion(cands, wfo)
    assert chosen[2] == 1
