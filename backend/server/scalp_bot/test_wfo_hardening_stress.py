"""Stress / integration checks for WFO hardening (coverage verify, config maps, readiness shape)."""

from __future__ import annotations

import numpy as np
import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig, load_scalp_config
from scalp_bot.scalp_runtime import _wfo_config_from_scalp_cfg
from scalp_bot.scalp_wfo import (
    WFOConfig,
    wfo_data_readiness,
    wfo_effective_roll_span_hours,
    wfo_verify_stored_roll_coverage,
)


def _dense_hourly_bars(t0: int, n_bars: int) -> dict[str, np.ndarray]:
    ts = np.arange(t0, t0 + n_bars * 3600, 3600, dtype=np.int64)
    o = np.ones(n_bars, dtype=np.float64)
    z = np.zeros(n_bars, dtype=np.int64)
    return {
        "timestamp": ts,
        "open": o,
        "high": o,
        "low": o,
        "close": o,
        "volume": o,
        "vwap": o,
        "trades": z,
    }


def test_wfo_verify_stored_roll_coverage_passes_dense_tape(monkeypatch) -> None:
    """Sliced span should meet 92% of roll_hours for contiguous hourly bars."""
    roll_h = 120.0
    n_need = int(roll_h) + 5
    bars = _dense_hourly_bars(2_000_000, n_need)

    def _fake_load(*_a, **_k):
        return bars

    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", _fake_load)
    span_h, ok = wfo_verify_stored_roll_coverage("SYM-X", 5, roll_h)
    assert ok
    assert span_h >= roll_h * 0.92 - 1e-6


def test_wfo_verify_stored_roll_coverage_fails_short_tape(monkeypatch) -> None:
    roll_h = 500.0
    bars = _dense_hourly_bars(2_000_000, 50)  # far less than roll_h

    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *_a, **_k: bars)
    span_h, ok = wfo_verify_stored_roll_coverage("SYM-X", 5, roll_h)
    assert not ok
    assert span_h < roll_h * 0.92


def test_wfo_config_maps_continuous_min_trades_from_scalp_cfg() -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"a": ScalpPairConfig(symbol="S", interval=5)},
        wfo_continuous_min_trades=12,
    )
    wfo = _wfo_config_from_scalp_cfg(cfg)
    assert wfo.continuous_min_trades == 12


def test_wfo_data_readiness_pair_shape_continuous(monkeypatch) -> None:
    """Readiness output uses continuous eval/warmup hours and span-based progress."""
    bot = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="LAB-TEST", interval=5)},
    )
    wfo = WFOConfig(continuous_eval_hours=672.0, continuous_warmup_hours=168.0)
    # Provide enough bars to register non-zero span
    ts = np.array([1_700_000_000 + i * 300 for i in range(400)], dtype=np.int64)
    o = np.ones(400, dtype=np.float64)
    z = np.zeros(400, dtype=np.int64)
    stub = {
        "timestamp": ts,
        "open": o, "high": o, "low": o, "close": o,
        "volume": o, "vwap": o, "trades": z,
    }

    monkeypatch.setattr("scalp_bot.scalp_wfo.bar_store.load_bars", lambda *_a, **_k: stub)

    out = wfo_data_readiness(bot, wfo)
    assert "pairs" in out
    assert "eval_hours" in out
    assert "warmup_hours" in out
    p1 = out["pairs"]["p1"]
    assert "span_hours" in p1
    assert "bar_count" in p1
    assert "progress_pct" in p1
    assert "windows_skipped_insufficient_bars" in p1
    assert isinstance(p1["windows_skipped_insufficient_bars"], int)


def test_load_scalp_config_backfill_buffer_roundtrip() -> None:
    raw = {
        "scalp": {
            "enabled": True,
            "pairs": {"x": {"symbol": "Z", "interval": 5}},
            "wfo_backfill_buffer_hours": 12.5,
        }
    }
    cfg = load_scalp_config(raw)
    assert cfg.wfo_backfill_buffer_hours == 12.5


def test_effective_roll_span_positive() -> None:
    w = WFOConfig(continuous_eval_hours=672.0, continuous_warmup_hours=168.0)
    h = wfo_effective_roll_span_hours(w)
    assert h == pytest.approx(840.0)
