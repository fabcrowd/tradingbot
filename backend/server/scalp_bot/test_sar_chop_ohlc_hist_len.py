"""§1b: sar_chop bundle callable on long series vs 320-bar tail (smoke + bounded diff)."""

from __future__ import annotations

import math

import numpy as np

from scalp_bot.scalp_vec_backtest import sar_chop_live_bundle


def test_sar_chop_last_bar_bundle_runs_full_vs_320_tail() -> None:
    n = 400
    rng = np.random.default_rng(1)
    close = 100.0 + np.cumsum(rng.standard_normal(n).astype(np.float64) * 0.05)
    high = close + 0.15
    low = close - 0.15
    full = sar_chop_live_bundle(high, low, close)
    tail = sar_chop_live_bundle(high[-320:], low[-320:], close[-320:])
    assert full.keys() == tail.keys()
    for k in full:
        a, b = full[k], tail[k]
        assert type(a) is type(b)
        if isinstance(a, float) and isinstance(b, float) and math.isfinite(a) and math.isfinite(b):
            assert abs(a - b) < 50.0  # bundle state can drift vs cap; spot-check magnitude only
