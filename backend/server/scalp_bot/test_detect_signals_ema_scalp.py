"""Regression tests for ``detect_signals_ema_scalp`` (vectorized cross + warmup)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from scalp_bot.indicator_warmup import vec_warmup_prefix_len
from scalp_bot.scalp_vec_backtest import detect_signals_ema_scalp


def test_ema_scalp_warmup_prefix_masks_signals() -> None:
    n = 80
    close = np.linspace(100.0, 120.0, n, dtype=np.float64)
    high = close + 0.5
    low = close - 0.5
    ema_p = 20
    atr_p = 14
    sr = 8
    long_m, short_m, _atr, _hn, _ln = detect_signals_ema_scalp(
        close, high, low, ema_period=ema_p, atr_period=atr_p, sr_bars=sr,
    )
    w = vec_warmup_prefix_len(
        "ema_scalp",
        SimpleNamespace(ema_scalp_period=ema_p, atr_period=atr_p, ema_scalp_sr_bars=sr),
    )
    assert not long_m[:w].any()
    assert not short_m[:w].any()


def test_ema_scalp_ohlc_length_mismatch_raises() -> None:
    close = np.ones(10, dtype=np.float64)
    high = np.ones(9, dtype=np.float64)
    low = np.ones(10, dtype=np.float64)
    with pytest.raises(ValueError, match="OHLC length mismatch"):
        detect_signals_ema_scalp(close, high, low, 20, 14, 8)


def test_ema_scalp_sr_series_returned_finite_after_warmup() -> None:
    n = 60
    close = np.linspace(100.0, 110.0, n, dtype=np.float64)
    high = close + 0.3
    low = close - 0.3
    _lm, _sm, _atr, hn, ln = detect_signals_ema_scalp(close, high, low, 20, 14, 8)
    assert np.isfinite(hn[-1])
    assert np.isfinite(ln[-1])
    assert hn[-1] >= ln[-1]
