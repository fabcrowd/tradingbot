"""Tests for continuous full-grid WFO evaluation (Phase 2, 2026-05-19)."""

from __future__ import annotations

import numpy as np
import pytest

from scalp_bot.scalp_wfo import (
    WFOConfig,
    _mp_continuous_eval_one,
    wfo_data_readiness,
    wfo_effective_roll_span_hours,
)
from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_vec_backtest import ParamSet


def _dense_minute_bars(n_bars: int, t0: int = 1_000_000) -> dict[str, np.ndarray]:
    ts = np.arange(t0, t0 + n_bars * 60, 60, dtype=np.int64)
    price = np.ones(n_bars, dtype=np.float64)
    return {
        "timestamp": ts,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": price,
        "vwap": price,
        "trades": np.zeros(n_bars, dtype=np.int64),
    }


# ---------------------------------------------------------------------------
# wfo_effective_roll_span_hours — returns eval + warmup
# ---------------------------------------------------------------------------

def test_continuous_wfo_effective_roll_span_hours_default() -> None:
    """Default WFOConfig returns 672 + 168 = 840 hours."""
    wfo = WFOConfig()
    assert wfo_effective_roll_span_hours(wfo) == pytest.approx(840.0)


def test_continuous_wfo_effective_roll_span_hours_custom() -> None:
    """Custom eval+warmup values are correctly summed."""
    wfo = WFOConfig(continuous_eval_hours=504.0, continuous_warmup_hours=120.0)
    assert wfo_effective_roll_span_hours(wfo) == pytest.approx(624.0)


# ---------------------------------------------------------------------------
# WFOConfig field defaults
# ---------------------------------------------------------------------------

def test_continuous_wfo_config_defaults() -> None:
    """WFOConfig carries the correct continuous evaluation defaults."""
    wfo = WFOConfig()
    assert wfo.continuous_eval_hours == pytest.approx(672.0)
    assert wfo.continuous_warmup_hours == pytest.approx(168.0)
    assert wfo.continuous_min_trades == 20


# ---------------------------------------------------------------------------
# wfo_data_readiness — span-based progress, no rolling_windows call
# ---------------------------------------------------------------------------

def test_continuous_data_readiness_uses_span_not_windows(monkeypatch) -> None:
    """Progress is based on bar span vs required hours, not window count."""
    bot = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
    )
    wfo = WFOConfig(continuous_eval_hours=672.0, continuous_warmup_hours=168.0)
    required_hours = 840.0

    # Bars spanning exactly half the required window → ~50% progress
    half_bars = int(required_hours * 3600 / 60 / 2)  # half in 1-minute bars
    stub = _dense_minute_bars(half_bars, t0=1_000_000)
    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *_a, **_k: stub)

    out = wfo_data_readiness(bot, wfo)
    pct = out["pairs"]["p1"]["progress_pct"]
    assert 40.0 <= pct <= 60.0, f"Expected ~50% progress, got {pct}"
    assert out["eval_hours"] == pytest.approx(672.0)
    assert out["warmup_hours"] == pytest.approx(168.0)


def test_continuous_data_readiness_full_coverage(monkeypatch) -> None:
    """Full bar coverage yields 100% readiness."""
    bot = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
    )
    wfo = WFOConfig(continuous_eval_hours=672.0, continuous_warmup_hours=168.0)
    required_hours = 840.0

    # Bars spanning more than required → 100%
    full_bars = int(required_hours * 3600 / 60) + 100
    stub = _dense_minute_bars(full_bars, t0=1_000_000)
    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *_a, **_k: stub)

    out = wfo_data_readiness(bot, wfo)
    assert out["overall_progress_pct"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# _mp_continuous_eval_one — worker function shape
# ---------------------------------------------------------------------------

def test_mp_continuous_eval_one_returns_eval_exception_on_eval_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker maps evaluate_params failures to eval_exception (not AssertionError)."""
    import scalp_bot.scalp_wfo as _wfo_mod

    _wfo_mod._MP_BARS = _dense_minute_bars(200)
    _wfo_mod._MP_EVAL_KW = {}
    monkeypatch.setattr(
        "scalp_bot.scalp_wfo.evaluate_params",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    pi, metrics, err = _mp_continuous_eval_one((0, ParamSet(mode="ema_momentum"), {}))
    assert pi == 0
    assert metrics is None
    assert err == "eval_exception"
