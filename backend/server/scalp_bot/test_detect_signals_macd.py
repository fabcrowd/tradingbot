"""Regression tests for ``detect_signals_macd`` and touch-cross helpers."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from scalp_bot.indicator_warmup import vec_warmup_prefix_len
from scalp_bot.scalp_vec_backtest import (
    _touch_crossover,
    _touch_crossunder,
    detect_signals_macd,
    super_smooth,
)


def test_touch_crossunder_fires_when_prev_equals_signal() -> None:
    """``_rising_edge(~above)`` would miss this; Pine crossunder must fire."""
    a = np.array([0.0, 1.0, 0.5, 0.2], dtype=np.float64)
    b = np.array([0.0, 1.0, 1.0, 0.8], dtype=np.float64)
    cu = _touch_crossunder(a, b)
    assert not cu[0]
    assert not cu[1]
    assert cu[2]
    assert not cu[3]


def test_touch_crossover_fires_when_prev_equals_signal() -> None:
    a = np.array([0.0, 1.0, 1.0, 1.2], dtype=np.float64)
    b = np.array([0.0, 1.0, 0.8, 1.0], dtype=np.float64)
    co = _touch_crossover(a, b)
    assert co[2]


def test_macd_warmup_prefix_masks_signals() -> None:
    n = 120
    close = np.linspace(100.0, 130.0, n, dtype=np.float64)
    high = close + 0.5
    low = close - 0.5
    fast, slow, sig, atr_p = 8, 10, 8, 14
    long_m, short_m, _, _, _ = detect_signals_macd(
        close, high, low, fast, slow, sig, atr_p,
    )
    w = vec_warmup_prefix_len(
        "macd_scalp",
        SimpleNamespace(
            macd_fast_len=fast,
            macd_slow_len=slow,
            macd_signal_len=sig,
            atr_period=atr_p,
        ),
    )
    assert not long_m[:w].any()
    assert not short_m[:w].any()


def test_macd_masks_mutually_exclusive_per_bar() -> None:
    n = 200
    rng = np.random.default_rng(42)
    close = 100.0 + np.cumsum(rng.normal(0, 0.3, n))
    high = close + 0.5
    low = close - 0.5
    long_m, short_m, _, _, _ = detect_signals_macd(close, high, low, 8, 10, 8, 14)
    assert not (long_m & short_m).any()


def test_macd_ohlc_length_mismatch_raises() -> None:
    close = np.ones(10, dtype=np.float64)
    with pytest.raises(ValueError, match="OHLC length mismatch"):
        detect_signals_macd(close, np.ones(9), close, 8, 10, 8, 14)


def test_super_smooth_invalid_period_returns_nan() -> None:
    data = np.ones(5, dtype=np.float64)
    out = super_smooth(data, 0)
    assert np.isnan(out).all()
