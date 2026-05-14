"""Verify sar_chop short entries fire in a golden-cross market (MA50 > MA200).

Before the fix, shorts required MA(50) <= MA(200) — a death cross that almost
never forms on 5-min bars. The fix replaces that gate with `close < MA(50)`,
so shorts fire whenever price is below all three MAs and MACD is negative.

Tests:
  1. Shorts fire during a sharp downmove in a golden-cross market.
  2. Longs still fire normally (fix did not break longs).
  3. Shorts do NOT fire when price is above MA(50) (new gate still filters noise).
"""

from __future__ import annotations

import numpy as np
import pytest

from scalp_bot.scalp_vec_backtest import detect_signals_sar_chop


def _base_series(n: int = 600, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Steady uptrend so MA(50) >> MA(200) — golden cross firmly in place."""
    rng = np.random.default_rng(seed)
    # Slow upward drift + small noise → close stays above MA(200), MA(50) > MA(200)
    drift = np.linspace(100.0, 130.0, n)
    noise = rng.standard_normal(n) * 0.1
    close = (drift + noise).astype(np.float64)
    high = close + 0.3
    low = close - 0.3
    return close, high, low


def test_shorts_fire_in_golden_cross_market() -> None:
    """Append a sharp crash to a golden-cross uptrend — shorts must fire."""
    n_base = 600
    close_base, high_base, low_base = _base_series(n_base)

    # Append 80-bar crash: price drops well below MA(50) and MA(200)
    crash = np.linspace(close_base[-1], close_base[-1] * 0.88, 80).astype(np.float64)
    close = np.concatenate([close_base, crash])
    high = close + 0.3
    low = close - 0.3

    long_m, short_m, _ = detect_signals_sar_chop(close, high, low)

    # MA(50) is still well above MA(200) at bar 600 (golden cross holds for a while)
    # Shorts must fire somewhere in the crash segment
    assert short_m[n_base:].any(), (
        "No short signals in crash segment — sar_chop short gate is still broken. "
        f"short_m crash sum={short_m[n_base:].sum()}, long_m crash sum={long_m[n_base:].sum()}"
    )


def test_longs_still_fire_in_uptrend() -> None:
    """Longs must fire during the uptrend section (fix must not break longs)."""
    close, high, low = _base_series(600)
    long_m, short_m, _ = detect_signals_sar_chop(close, high, low)
    assert long_m.any(), (
        f"No long signals in uptrend — fix accidentally broke longs. "
        f"long_m sum={long_m.sum()}"
    )


def test_shorts_blocked_when_price_above_ma50() -> None:
    """In a strong uptrend where price stays above MA(50), shorts should not fire."""
    close, high, low = _base_series(600)
    long_m, short_m, _ = detect_signals_sar_chop(close, high, low)
    # Price is consistently above MAs in this uptrend — short gate should filter all
    assert not short_m.any(), (
        f"Short fired while price was above MA(50) in uptrend — new gate is too loose. "
        f"short_m sum={short_m.sum()} at bars={np.where(short_m)[0].tolist()[:10]}"
    )


def test_short_signal_count_reasonable() -> None:
    """After a crash, short signal count should be non-trivial but not every bar."""
    n_base = 600
    close_base, _, _ = _base_series(n_base)
    crash = np.linspace(close_base[-1], close_base[-1] * 0.85, 100).astype(np.float64)
    close = np.concatenate([close_base, crash])
    high = close + 0.3
    low = close - 0.3

    long_m, short_m, _ = detect_signals_sar_chop(close, high, low)
    n_shorts = int(short_m[n_base:].sum())
    n_bars = 100

    # Expect at least 1 short, but not >50% of crash bars (PSAR flips are sparse)
    assert n_shorts >= 1, f"Expected ≥1 short in crash, got {n_shorts}"
    assert n_shorts < n_bars * 0.5, f"Too many shorts ({n_shorts}/{n_bars}) — check PSAR flip logic"
