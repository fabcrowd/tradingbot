"""Regression tests for continuous WFO skip paths, per-mode pick, adverse gate, fixtures."""

from __future__ import annotations

import numpy as np
import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig, wfo_continuous_span_hours
from scalp_bot.scalp_vec_backtest import BacktestMetrics, ParamSet
from scalp_bot.scalp_wfo import (
    WFOConfig,
    _mp_continuous_eval_one,
    run_adverse_wfo_holdout_check,
    optimize_pair,
)


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


def continuous_champion_dict(
    symbol: str,
    *,
    score: float,
    mode: str = "ema_momentum",
) -> dict:
    """Champion row shape written by ``optimize_pair`` (continuous tier)."""
    return {
        "symbol": symbol,
        "interval": 5,
        "timestamp": 0.0,
        "objective": "total_pnl",
        "score": score,
        "stability": 1.0,
        "baseline_score": None,
        "mode": mode,
        "evaluation_mode": "continuous",
        "eval_hours": 48.0,
        "warmup_hours": 24.0,
        "score_kind": "continuous_total_pnl",
        "period_rank_metric": "total_pnl",
        "windows_evaluated": 1,
        "windows_passed": 1,
        "wfo_promotion_tier": "continuous",
        "wfo_min_windows_used": 1,
        "holdout_rank_by_period": True,
        "exhaustive_grid_holdout": True,
        "sum_holdout_total_pnl": score,
        "params": {
            "mode": mode,
            "max_hold_bars": 15,
            "atr_stop_mult": 1.0,
            "atr_tp_mult": 1.5,
            "min_signals": 2,
            "ema_fast": 5,
            "ema_slow": 13,
        },
        "holdout_metrics": {},
        "holdout_metrics_mean": {},
        "grid_size": 3,
        "candidates_after_filter": 1,
    }


def test_wfo_continuous_span_hours_helper() -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_continuous_eval_hours=504.0,
        wfo_continuous_warmup_hours=120.0,
    )
    assert wfo_continuous_span_hours(cfg) == pytest.approx(624.0)


def test_optimize_pair_skips_no_bars_in_store(monkeypatch: pytest.MonkeyPatch) -> None:
    wfo = WFOConfig(continuous_eval_hours=48.0, continuous_warmup_hours=24.0)
    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *a, **kw: None)

    result, skip, diag = optimize_pair("T", 5, 0.0, 0.0, wfo)
    assert result is None
    assert skip == "no_bars_in_store"
    assert diag.get("skip") == "no_bars_in_store"


def test_optimize_pair_skips_insufficient_span(monkeypatch: pytest.MonkeyPatch) -> None:
    wfo = WFOConfig(
        continuous_eval_hours=672.0,
        continuous_warmup_hours=168.0,
        continuous_min_trades=5,
    )
    # ~100h span << 50% of 672h eval
    bars = _bars_5m(1200)
    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *a, **kw: bars)
    monkeypatch.setattr(
        "scalp_bot.scalp_wfo.build_default_grid",
        lambda **kw: [ParamSet(mode="ema_momentum")],
    )

    result, skip, diag = optimize_pair("T", 5, 0.0, 0.0, wfo)
    assert result is None
    assert skip is not None
    assert skip.startswith("insufficient_span:")
    assert diag["eval_hours"] == pytest.approx(672.0)


def test_optimize_pair_skips_no_strategies_met_min_trades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wfo = WFOConfig(
        continuous_eval_hours=48.0,
        continuous_warmup_hours=24.0,
        continuous_min_trades=50,
        pick_best_per_mode=False,
    )
    bars = _bars_5m(600)
    grid = [ParamSet(mode="ema_momentum"), ParamSet(mode="rsi_reversion")]
    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *a, **kw: bars)
    monkeypatch.setattr("scalp_bot.scalp_wfo.build_default_grid", lambda **kw: grid)
    monkeypatch.setattr(
        "scalp_bot.scalp_wfo.evaluate_params",
        lambda _b, p, **kw: _metrics(pnl=10.0, trades=5),
    )

    result, skip, diag = optimize_pair("T", 5, 0.0, 0.0, wfo)
    assert result is None
    assert skip == "no_strategies_met_min_trades"
    assert diag["min_trades"] == 50


def test_optimize_pair_safety_gate_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    wfo = WFOConfig(
        continuous_eval_hours=48.0,
        continuous_warmup_hours=24.0,
        continuous_min_trades=5,
        max_param_delta_stop=0.1,
        pick_best_per_mode=False,
        require_positive_latest_holdout=False,
    )
    bars = _bars_5m(600)
    proposed = ParamSet(mode="ema_momentum", atr_stop_mult=3.0, atr_tp_mult=3.0)
    current = ParamSet(mode="ema_momentum", atr_stop_mult=1.0, atr_tp_mult=1.5)
    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *a, **kw: bars)
    monkeypatch.setattr("scalp_bot.scalp_wfo.build_default_grid", lambda **kw: [proposed])
    monkeypatch.setattr(
        "scalp_bot.scalp_wfo.evaluate_params",
        lambda _b, p, **kw: _metrics(pnl=100.0, trades=25),
    )

    result, skip, _diag = optimize_pair(
        "T", 5, 0.0, 0.0, wfo, current_params=current,
    )
    assert result is None
    assert skip is not None
    assert skip.startswith("safety_gate:")


def test_optimize_pair_per_mode_diag_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wfo = WFOConfig(
        continuous_eval_hours=48.0,
        continuous_warmup_hours=24.0,
        continuous_min_trades=5,
        pick_best_per_mode=True,
        require_positive_latest_holdout=False,
    )
    bars = _bars_5m(600)
    grid = [ParamSet(mode="ema_momentum"), ParamSet(mode="rsi_reversion")]
    pnl = {"ema_momentum": 40.0, "rsi_reversion": 55.0}
    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *a, **kw: bars)
    monkeypatch.setattr("scalp_bot.scalp_wfo.build_default_grid", lambda **kw: grid)
    monkeypatch.setattr(
        "scalp_bot.scalp_wfo.evaluate_params",
        lambda _b, p, **kw: _metrics(pnl=pnl[p.mode], trades=25),
    )

    result, skip, diag = optimize_pair("T", 5, 0.0, 0.0, wfo)
    assert skip is None
    assert result is not None
    assert result["mode"] == "rsi_reversion"
    sort_diag = diag.get("holdout_sort_diag") or {}
    assert int(sort_diag.get("per_mode_count") or 0) == 2


def test_mp_continuous_eval_one_returns_eval_exception_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scalp_bot.scalp_wfo as wfo_mod

    wfo_mod._MP_BARS = _bars_5m(100)
    wfo_mod._MP_EVAL_KW = {}
    monkeypatch.setattr(
        "scalp_bot.scalp_wfo.evaluate_params",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    pi, metrics, err = _mp_continuous_eval_one((0, ParamSet(mode="ema_momentum"), {}))
    assert pi == 0
    assert metrics is None
    assert err == "eval_exception"


def test_adverse_check_fails_pnl_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    import scalp_bot.scalp_wfo as wfo_mod

    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_adverse_min_mean_holdout_pnl=10.0,
    )
    wfo = WFOConfig(continuous_eval_hours=48.0, continuous_warmup_hours=24.0)
    champ = continuous_champion_dict("X", score=50.0)
    monkeypatch.setattr(wfo_mod.bar_store, "load_bars", lambda *a, **kw: _bars_5m(600))
    monkeypatch.setattr(
        wfo_mod,
        "evaluate_params",
        lambda *_a, **_kw: _metrics(pnl=1.0, trades=25),
    )

    ok, reason, diag = run_adverse_wfo_holdout_check(
        cfg, wfo, cfg.pairs["p1"], champ,
    )
    assert ok is False
    assert reason == "adverse_pnl_below_threshold"
    assert diag["threshold_pnl"] == pytest.approx(10.0)


def test_adverse_check_fails_objective_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    import scalp_bot.scalp_wfo as wfo_mod

    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_adverse_min_objective_ratio_vs_primary=0.5,
    )
    wfo = WFOConfig(continuous_eval_hours=48.0, continuous_warmup_hours=24.0)
    champ = continuous_champion_dict("X", score=100.0)
    monkeypatch.setattr(wfo_mod.bar_store, "load_bars", lambda *a, **kw: _bars_5m(600))
    monkeypatch.setattr(
        wfo_mod,
        "evaluate_params",
        lambda *_a, **_kw: _metrics(pnl=10.0, trades=25),
    )

    ok, reason, _diag = run_adverse_wfo_holdout_check(
        cfg, wfo, cfg.pairs["p1"], champ,
    )
    assert ok is False
    assert reason == "adverse_objective_vs_primary"


def test_adverse_check_fails_mode_not_in_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    import scalp_bot.scalp_wfo as wfo_mod

    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
    )
    wfo = WFOConfig(continuous_eval_hours=48.0, continuous_warmup_hours=24.0)
    champ = continuous_champion_dict("X", score=10.0, mode="nonexistent_mode")
    monkeypatch.setattr(wfo_mod.bar_store, "load_bars", lambda *a, **kw: _bars_5m(600))
    monkeypatch.setattr(
        wfo_mod,
        "build_default_grid",
        lambda **kw: [ParamSet(mode="ema_momentum")],
    )

    ok, reason, diag = run_adverse_wfo_holdout_check(
        cfg, wfo, cfg.pairs["p1"], champ,
    )
    assert ok is False
    assert reason == "adverse_mode_not_in_grid"
    assert "nonexistent_mode" in str(diag.get("mode"))


def test_adverse_check_passes_with_sliced_window(monkeypatch: pytest.MonkeyPatch) -> None:
    import scalp_bot.scalp_wfo as wfo_mod

    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
        wfo_adverse_min_mean_holdout_pnl=5.0,
    )
    wfo = WFOConfig(continuous_eval_hours=48.0, continuous_warmup_hours=24.0)
    champ = continuous_champion_dict("X", score=80.0)
    monkeypatch.setattr(wfo_mod.bar_store, "load_bars", lambda *a, **kw: _bars_5m(600))
    captured: list[int | None] = []

    def _fake_eval(_bars: dict, params: ParamSet, **kwargs: object) -> BacktestMetrics:
        captured.append(kwargs.get("min_entry_bar"))  # type: ignore[arg-type]
        return _metrics(pnl=50.0, trades=25)

    monkeypatch.setattr(wfo_mod, "evaluate_params", _fake_eval)

    ok, reason, diag = run_adverse_wfo_holdout_check(
        cfg, wfo, cfg.pairs["p1"], champ,
    )
    assert ok is True
    assert reason is None
    assert captured and captured[0] is not None and captured[0] > 0
    assert diag.get("adverse_total_pnl") == pytest.approx(50.0)
