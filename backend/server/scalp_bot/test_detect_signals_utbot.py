"""Regression tests for ``detect_signals_utbot`` (flip detector + warmup)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from scalp_bot.indicator_warmup import vec_warmup_prefix_len
from scalp_bot.scalp_vec_backtest import detect_signals_utbot


def test_utbot_warmup_prefix_masks_signals() -> None:
    n = 200
    close = np.linspace(100.0, 120.0, n, dtype=np.float64)
    high = close + 0.5
    low = close - 0.5
    atr_p = 10
    long_m, short_m, _ = detect_signals_utbot(close, high, low, atr_period=atr_p)
    mask_prefix = vec_warmup_prefix_len(
        "utbot_alert",
        SimpleNamespace(utbot_atr_period=atr_p),
    )
    assert not long_m[:mask_prefix].any()
    assert not short_m[:mask_prefix].any()


def test_utbot_masks_mutually_exclusive_per_bar() -> None:
    n = 300
    rng = np.random.default_rng(11)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + 0.5
    low = close - 0.5
    long_m, short_m, _ = detect_signals_utbot(close, high, low)
    assert not (long_m & short_m).any()


def test_utbot_sparse_flips_not_dense_state() -> None:
    n = 300
    close = np.linspace(100.0, 150.0, n, dtype=np.float64)
    high = close + 0.5
    low = close - 0.5
    long_m, short_m, _ = detect_signals_utbot(close, high, low, atr_period=10)
    mask_prefix = vec_warmup_prefix_len(
        "utbot_alert",
        SimpleNamespace(utbot_atr_period=10),
    )
    tail = long_m[mask_prefix:] | short_m[mask_prefix:]
    assert tail.sum() < len(tail) * 0.25


def test_utbot_loop_warm_derived_from_warmup_helper() -> None:
    atr_p = 10
    mask_prefix = vec_warmup_prefix_len(
        "utbot_alert",
        SimpleNamespace(utbot_atr_period=atr_p),
    )
    assert mask_prefix == atr_p + 1


def test_utbot_ohlc_length_mismatch_raises() -> None:
    close = np.ones(10, dtype=np.float64)
    with pytest.raises(ValueError, match="OHLC length mismatch"):
        detect_signals_utbot(close, np.ones(9), close)


def test_utbot_short_series_returns_empty_masks() -> None:
    close = np.ones(5, dtype=np.float64)
    long_m, short_m, atr_v = detect_signals_utbot(close, close, close, atr_period=10)
    assert not long_m.any()
    assert not short_m.any()
    assert len(atr_v) == 5
