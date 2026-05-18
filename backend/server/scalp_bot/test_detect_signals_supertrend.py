"""Regression tests for ``detect_signals_supertrend`` (flip detector + warmup)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from scalp_bot.indicator_warmup import vec_warmup_prefix_len
from scalp_bot.scalp_vec_backtest import detect_signals_supertrend


def test_supertrend_warmup_prefix_masks_signals() -> None:
    n = 200
    close = np.linspace(100.0, 120.0, n, dtype=np.float64)
    high = close + 0.5
    low = close - 0.5
    period, atr_p = 10, 14
    long_m, short_m, _ = detect_signals_supertrend(
        close, high, low, period=period, atr_period=atr_p,
    )
    mask_prefix = vec_warmup_prefix_len(
        "supertrend",
        SimpleNamespace(atr_period=atr_p, supertrend_period=period),
    )
    assert not long_m[:mask_prefix].any()
    assert not short_m[:mask_prefix].any()


def test_supertrend_masks_mutually_exclusive_per_bar() -> None:
    n = 300
    rng = np.random.default_rng(7)
    close = 100.0 + np.cumsum(rng.normal(0, 0.4, n))
    high = close + 0.5
    low = close - 0.5
    long_m, short_m, _ = detect_signals_supertrend(close, high, low)
    assert not (long_m & short_m).any()


def test_supertrend_sparse_flips_not_dense_state() -> None:
    n = 300
    close = np.linspace(100.0, 150.0, n, dtype=np.float64)
    high = close + 0.5
    low = close - 0.5
    long_m, short_m, _ = detect_signals_supertrend(close, high, low)
    mask_prefix = vec_warmup_prefix_len(
        "supertrend",
        SimpleNamespace(atr_period=14, supertrend_period=10),
    )
    tail = long_m[mask_prefix:] | short_m[mask_prefix:]
    assert tail.sum() < len(tail) * 0.2


def test_supertrend_loop_warm_derived_from_warmup_helper() -> None:
    atr_p, period = 14, 10
    w = max(atr_p, period)
    mask_prefix = vec_warmup_prefix_len(
        "supertrend",
        SimpleNamespace(atr_period=atr_p, supertrend_period=period),
    )
    assert mask_prefix == w + 1


def test_supertrend_ohlc_length_mismatch_raises() -> None:
    close = np.ones(10, dtype=np.float64)
    with pytest.raises(ValueError, match="OHLC length mismatch"):
        detect_signals_supertrend(close, np.ones(9), close)


def test_supertrend_short_series_returns_empty_masks() -> None:
    close = np.ones(5, dtype=np.float64)
    long_m, short_m, atr_v = detect_signals_supertrend(close, close, close, period=10, atr_period=14)
    assert not long_m.any()
    assert not short_m.any()
    assert len(atr_v) == 5
