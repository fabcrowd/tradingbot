"""Verify sar_chop short entries can fire still under a bullish MA stack.

Previously, shorts mistakenly required MA(50) <= MA(200), which seldom happens on a
few-hundred-bar window. Entries now mirror the detector: shorts need a PSAR
**bull→bear** flip on the bar plus close below MA(fast)/MA(50)/MA(200), MACD
hist `< 0`, CHOP regime filter, plus optional Lucid/UT gates.

Important nuance tested here: SAR entries attach to **flip** bars only. A smooth
meltdown stays bear without another flip → no extra short entries — so fixtures
must include at least modest oscillation so another bearish flip aligns with MA +
MACD + CHOP.
"""

from __future__ import annotations

import numpy as np
import pytest

from scalp_bot.scalp_vec_backtest import detect_signals_sar_chop, sar_chop_diagnostic_frame


def _base_uptrend(n: int = 600, *, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Slow drift + tight noise so MA(50) stays above MA(200) throughout."""
    rng = np.random.default_rng(seed)
    drift = np.linspace(100.0, 130.0, n)
    noise = rng.standard_normal(n) * 0.1
    close = (drift + noise).astype(np.float64)
    high = close + 0.3
    low = close - 0.3
    return close, high, low


def _golden_uptrend_with_wavy_rollover(
    n_base: int = 600,
    crash_len: int = 220,
    *,
    seed: int = 42,
    depth: float = 42.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Append a waved net-down stretch after a golden-cross uptrend.

    Returns OHLC concatenation and ``n_base`` (first index of the post-base segment).
    """
    close_b, hi_b, lo_b = _base_uptrend(n_base, seed=seed)
    peak = float(close_b[-1])
    t = np.arange(crash_len, dtype=np.float64)
    span = float(max(np.ptp(t), 1.0))
    wave = (peak - depth * t / span + 2.8 * np.sin(0.45 * t) + 0.55 * np.sin(0.93 * t)).astype(
        np.float64
    )
    close = np.concatenate([close_b, wave])
    high = close + 0.3
    low = close - 0.3
    return close, high, low, n_base


@pytest.mark.parametrize("crash_len", [150, 220])
def test_shorts_fire_in_golden_cross_market(crash_len: int) -> None:
    """Bearish SAR flips in the waved rollover must occasionally pass all short gates."""
    close, high, low, n_base = _golden_uptrend_with_wavy_rollover(crash_len=crash_len)

    _, short_m, _ = detect_signals_sar_chop(close, high, low)

    assert short_m[n_base:].any(), (
        "No short signals after the waved rollover — check PSAR-flip fixtures or CHOP veto. "
        f"crash shorts={short_m[n_base:].sum()}, uptrend shorts={short_m[:n_base].sum()}"
    )


def test_longs_still_fire_in_uptrend() -> None:
    """Longs still appear on the uplift section."""
    close, high, low = _base_uptrend(600, seed=0)
    long_m, short_m, _ = detect_signals_sar_chop(close, high, low)
    assert long_m.any(), (
        "No long signals in uptrend — regression in sar_chop long path. "
        f"long_m sum={long_m.sum()}, short_m sum={short_m.sum()}"
    )


def test_shorts_blocked_when_price_above_ma50() -> None:
    """During a tidy uptrend, shorts should remain absent."""
    close, high, low = _base_uptrend(600, seed=0)
    long_m, short_m, _ = detect_signals_sar_chop(close, high, low)
    assert not short_m.any(), (
        "Short fired despite price hugging highs above slower MAs — entry filter too permissive? "
        f"short indices={np.where(short_m)[0].tolist()[:10]}"
    )


def test_short_signal_count_reasonable() -> None:
    """After the rollover begins, shorts should appear but remain sparse versus bars."""
    close, high, low, n_base = _golden_uptrend_with_wavy_rollover(crash_len=220)
    long_m, short_m, _ = detect_signals_sar_chop(close, high, low)
    n_crash_bars = len(close) - n_base
    n_shorts = int(short_m[n_base:].sum())

    assert n_shorts >= 1, f"Expected ≥1 short in waved crash, got {n_shorts}"
    assert n_shorts < n_crash_bars * 0.5, (
        f"Too many shorts ({n_shorts}/{n_crash_bars}) versus bars — revisit fixture amplitudes"
    )


def test_golden_cross_holds_through_early_rollover() -> None:
    """Sanity: MA stack stays bullish for the first wedge of waved rollover."""
    close, high, low, _n_base = _golden_uptrend_with_wavy_rollover()
    diag = sar_chop_diagnostic_frame(close, high, low)
    # Empirically crosses under ~ bar 643 (fixture-specific); enforce a stable prefix window.
    for i in range(599, min(642, len(close))):
        assert diag["ma_short"][i] >= diag["ma_long"][i]
