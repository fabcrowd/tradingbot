"""Regression tests for ``detect_signals_hull`` (state mask + warmup)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from scalp_bot.indicator_warmup import vec_warmup_prefix_len
from scalp_bot.scalp_vec_backtest import detect_signals_hull


def test_hull_warmup_prefix_masks_signals() -> None:
    n = 120
    close = np.linspace(100.0, 130.0, n, dtype=np.float64)
    high = close + 0.5
    low = close - 0.5
    hull_p = 38
    atr_p = 14
    long_m, short_m, _ = detect_signals_hull(
        close, high, low, hull_period=hull_p, atr_period=atr_p,
    )
    w = vec_warmup_prefix_len(
        "hull_suite",
        SimpleNamespace(hull_period=hull_p, atr_period=atr_p),
    )
    assert not long_m[:w].any()
    assert not short_m[:w].any()


def test_hull_masks_mutually_exclusive_per_bar() -> None:
    n = 120
    close = np.linspace(100.0, 130.0, n, dtype=np.float64)
    high = close + 0.5
    low = close - 0.5
    long_m, short_m, _ = detect_signals_hull(close, high, low)
    assert not (long_m & short_m).any()


def test_hull_uptrend_has_dense_long_mask_after_warmup() -> None:
    """State classifier: sustained rise should yield many consecutive long True bars."""
    n = 200
    close = np.linspace(100.0, 200.0, n, dtype=np.float64)
    high = close + 0.5
    low = close - 0.5
    long_m, short_m, _ = detect_signals_hull(close, high, low, hull_period=20, atr_period=14)
    w = vec_warmup_prefix_len(
        "hull_suite",
        SimpleNamespace(hull_period=20, atr_period=14),
    )
    tail = long_m[w:]
    assert tail.sum() > len(tail) * 0.5
    assert short_m[w:].sum() < tail.sum() * 0.1


def test_hull_ohlc_length_mismatch_raises() -> None:
    close = np.ones(10, dtype=np.float64)
    with pytest.raises(ValueError, match="OHLC length mismatch"):
        detect_signals_hull(close, np.ones(9), close)
