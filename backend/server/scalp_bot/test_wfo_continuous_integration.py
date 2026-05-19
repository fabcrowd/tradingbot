"""Integration / e2e tests for continuous WFO (mocked bars + grid + run_once)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_runtime import ScalpRuntime, _wfo_config_from_scalp_cfg
from scalp_bot.scalp_vec_backtest import BacktestMetrics, ParamSet
from scalp_bot.scalp_wfo import (
    ScalpWalkForwardOptimizer,
    WFOConfig,
    _wfo_continuous_mode_scoreboard_rows,
    load_champion_for_symbol,
    optimize_pair,
    wfo_effective_roll_span_hours,
)
from state import BotState


def _bars_5m(n_bars: int, *, t_end: int = 2_000_000_000) -> dict[str, np.ndarray]:
    step = 300
    ts = np.arange(t_end - (n_bars - 1) * step, t_end + 1, step, dtype=np.int64)
    price = np.linspace(100.0, 110.0, n_bars, dtype=np.float64)
    z = np.zeros(n_bars, dtype=np.int64)
    return {
        "timestamp": ts,
        "open": price,
        "high": price + 0.5,
        "low": price - 0.5,
        "close": price,
        "volume": np.full(n_bars, 1000.0),
        "vwap": price,
        "trades": z,
    }


def _metrics(*, pnl: float, trades: int = 25) -> BacktestMetrics:
    return BacktestMetrics(
        trade_count=trades,
        win_count=max(1, trades // 2),
        win_rate=0.5,
        total_pnl=pnl,
        avg_pnl=pnl / max(1, trades),
        expectancy=pnl / max(1, trades),
        max_drawdown=1.0,
        max_drawdown_pct=5.0,
        avg_hold_bars=3.0,
        profit_factor=1.2,
        sharpe=0.5,
        sortino=0.5,
        calmar=0.1,
        recovery_factor=1.0,
        buy_hold_return=0.0,
        trades=[],
    )


def test_scalp_runtime_backfill_hours_matches_continuous_span() -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_continuous_eval_hours=672.0,
        wfo_continuous_warmup_hours=168.0,
        wfo_backfill_buffer_hours=24.0,
    )
    rt = ScalpRuntime(BotState(), cfg, live_mgr=None, session_logger=None)
    wfo = _wfo_config_from_scalp_cfg(cfg)
    assert rt._scalp_wfo_roll_hours() == pytest.approx(840.0)
    assert rt._scalp_wfo_backfill_hours() == pytest.approx(864.0)
    assert wfo_effective_roll_span_hours(wfo) == pytest.approx(840.0)


def test_continuous_scoreboard_one_row_per_mode() -> None:
    grid = [
        ParamSet(mode="ema_momentum"),
        ParamSet(mode="rsi_reversion"),
        ParamSet(mode="ema_momentum"),
    ]
    results = {0: _metrics(pnl=10.0), 1: _metrics(pnl=30.0), 2: _metrics(pnl=20.0)}
    wfo = WFOConfig(continuous_min_trades=5, holdout_rank_by_period=True)
    rows = _wfo_continuous_mode_scoreboard_rows(grid, results, wfo, champion_pi=1)
    assert len(rows) == 2
    modes = {r["mode"] for r in rows}
    assert modes == {"ema_momentum", "rsi_reversion"}
    rsi = next(r for r in rows if r["mode"] == "rsi_reversion")
    assert rsi["sum_holdout_total_pnl"] == pytest.approx(30.0)


def test_optimize_pair_continuous_selects_highest_pnl(monkeypatch: pytest.MonkeyPatch) -> None:
    wfo = WFOConfig(
        continuous_eval_hours=48.0,
        continuous_warmup_hours=24.0,
        continuous_min_trades=5,
        holdout_rank_by_period=True,
        period_rank_metric="total_pnl",
        pick_best_per_mode=False,
        require_positive_latest_holdout=False,
    )
    n_bars = 600
    bars = _bars_5m(n_bars)
    grid = [
        ParamSet(mode="ema_momentum"),
        ParamSet(mode="rsi_reversion"),
        ParamSet(mode="sar_chop"),
    ]
    pnl_by_mode = {"ema_momentum": 40.0, "rsi_reversion": 120.0, "sar_chop": 5.0}

    monkeypatch.setattr("scalp_bot.scalp_wfo.build_default_grid", lambda **kw: grid)
    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *a, **kw: bars)

    def fake_eval(_bars: dict, params: ParamSet, **kwargs: object) -> BacktestMetrics:
        return _metrics(pnl=pnl_by_mode[params.mode], trades=25)

    monkeypatch.setattr("scalp_bot.scalp_wfo.evaluate_params", fake_eval)

    result, skip, diag = optimize_pair(
        "TEST-SYM",
        5,
        fee_pct=0.0,
        slippage_pct=0.0,
        wfo_cfg=wfo,
    )
    assert skip is None
    assert result is not None
    assert result["evaluation_mode"] == "continuous"
    assert result["mode"] == "rsi_reversion"
    assert result["score"] == pytest.approx(120.0)
    assert len(result["wfo_mode_scoreboard"]) >= 2
    assert diag["strategies_passed_min_trades"] == 3


def test_optimize_pair_rejects_negative_when_positive_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wfo = WFOConfig(
        continuous_eval_hours=48.0,
        continuous_warmup_hours=24.0,
        continuous_min_trades=5,
        require_positive_latest_holdout=True,
        pick_best_per_mode=False,
    )
    bars = _bars_5m(600)
    grid = [ParamSet(mode="ema_momentum"), ParamSet(mode="rsi_reversion")]

    monkeypatch.setattr("scalp_bot.scalp_wfo.build_default_grid", lambda **kw: grid)
    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *a, **kw: bars)
    monkeypatch.setattr(
        "scalp_bot.scalp_wfo.evaluate_params",
        lambda _b, p, **kw: _metrics(pnl=-10.0, trades=25),
    )

    result, skip, _diag = optimize_pair("T", 5, 0.0, 0.0, wfo)
    assert result is None
    assert skip == "negative_continuous_eval_pnl"


def test_run_once_e2e_saves_continuous_champion(tmp_path: Path) -> None:
    """Full path: ScalpWalkForwardOptimizer.run_once → optimize_pair → champion on disk."""
    sym = "E2E-CONT"
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol=sym, interval=5)},
        wfo_continuous_eval_hours=48.0,
        wfo_continuous_warmup_hours=24.0,
        wfo_continuous_min_trades=5,
        wfo_require_holdout_beat_prior=False,
        wfo_champion_cooldown_sec=0.0,
        wfo_require_positive_holdout=False,
    )
    wfo = _wfo_config_from_scalp_cfg(cfg)
    champ_path = tmp_path / "champ.json"
    log_path = tmp_path / "promo.jsonl"
    meta_path = tmp_path / "meta.json"
    opt = ScalpWalkForwardOptimizer(
        cfg,
        wfo,
        champion_path=champ_path,
        promotion_log_path=log_path,
        promotion_meta_path=meta_path,
    )

    n_bars = 600
    bars = _bars_5m(n_bars)
    grid = [
        ParamSet(mode="ema_momentum"),
        ParamSet(mode="rsi_reversion"),
    ]
    pnl_by_mode = {"ema_momentum": 25.0, "rsi_reversion": 90.0}

    with (
        patch("scalp_bot.scalp_wfo.build_default_grid", lambda **kw: grid),
        patch("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *a, **kw: bars),
        patch(
            "scalp_bot.scalp_wfo.evaluate_params",
            lambda _b, p, **kw: _metrics(pnl=pnl_by_mode[p.mode], trades=25),
        ),
        patch("scalp_bot.scalp_wfo.wfo_verify_stored_roll_coverage", return_value=(900.0, True)),
    ):
        results = opt.run_once()

    assert results["p1"] is not None
    row = results["p1"]
    assert row is not None
    assert row["evaluation_mode"] == "continuous"
    assert row["mode"] == "rsi_reversion"
    assert row["wfo_promotion_tier"] == "continuous"
    assert float(row["score"]) == pytest.approx(90.0)

    on_disk = load_champion_for_symbol(sym, path=champ_path)
    assert on_disk is not None
    assert on_disk["mode"] == "rsi_reversion"

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["outcome"] == "champion_saved"
    assert rec["candidate_mode"] == "rsi_reversion"
