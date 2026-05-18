# AUTO-GENERATED review excerpt from scalp_vec_backtest.py
# Not intended as a runnable module alone — imports below match typical usage in the full file.
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from indicator_warmup import vec_warmup_prefix_len


# ----- scalp_vec_backtest.py lines 143–159 -----
def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Average True Range. First `period` values are NaN."""
    out = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period:
        return out
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    tr = np.concatenate([[high[0] - low[0]], tr])
    out[period - 1] = np.mean(tr[:period])
    for i in range(period, len(close)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out

# ----- scalp_vec_backtest.py lines 1353–1442 -----
def detect_signals_supertrend(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    period: int = 10,
    factor: float = 3.0,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Supertrend ATR-channel trend following (edge detector: flip bars only).

    Bands: hl2 ± factor×ATR, tightened each bar (``close[i-1]`` vs prior band — standard lag).
    Long/short masks fire on direction flips only (sparse); ``cooldown_bars`` is not load-bearing.

    At ``loop_warm``, direction is seeded **bullish (1)** to match Pine ``var int stDir = 1``.
    That TV-parity choice means uptrends miss an initial long until the first bear→bull flip;
    downtrends can short on bar ``loop_warm + 1`` from the forced seed (Pine ``bar_index > loopWarm``).
    ``mask_prefix`` (= ``loop_warm + 1``) clears bars ``0..loop_warm`` only; first flip bar is not masked.

    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    if not (len(high) == n and len(low) == n):
        raise ValueError(
            f"detect_signals_supertrend: OHLC length mismatch close={n} high={len(high)} low={len(low)}",
        )

    atr_v = atr(high, low, close, atr_period)
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + factor * atr_v
    basic_lower = hl2 - factor * atr_v

    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.ones(n, dtype=np.int8)  # 1=bull, -1=bear; pre-warmup filled to match seed

    mask_prefix = vec_warmup_prefix_len(
        "supertrend",
        SimpleNamespace(atr_period=atr_period, supertrend_period=period),
    )
    loop_warm = mask_prefix - 1  # == max(atr_period, period); synced via vec_warmup_prefix_len
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    if loop_warm >= n:
        _scalp_vec_bt_diag_warn(
            f"supertrend:warm:{n}:{loop_warm}",
            f"detect_signals_supertrend: len(close)={n} < recurrence start {loop_warm}; no signals.",
        )
        return long_mask, short_mask, atr_v

    final_upper[loop_warm] = basic_upper[loop_warm]
    final_lower[loop_warm] = basic_lower[loop_warm]
    direction[loop_warm] = 1

    for i in range(loop_warm + 1, n):
        if np.isnan(atr_v[i]):
            final_upper[i] = final_upper[i - 1]
            final_lower[i] = final_lower[i - 1]
            direction[i] = direction[i - 1]
            continue
        # Tighten bands: upper only drops; lower only rises
        fu = (
            min(basic_upper[i], final_upper[i - 1])
            if close[i - 1] <= final_upper[i - 1]
            else basic_upper[i]
        )
        fl = (
            max(basic_lower[i], final_lower[i - 1])
            if close[i - 1] >= final_lower[i - 1]
            else basic_lower[i]
        )
        final_upper[i] = fu
        final_lower[i] = fl
        # Direction flip logic
        if direction[i - 1] == -1:
            direction[i] = 1 if close[i] > fu else -1
        else:
            direction[i] = -1 if close[i] < fl else 1
        # Signals on direction change
        if direction[i - 1] != 1 and direction[i] == 1:
            long_mask[i] = True
        elif direction[i - 1] != -1 and direction[i] == -1:
            short_mask[i] = True

    ok = ~np.isnan(atr_v) & (atr_v > 0)
    long_mask &= ok
    short_mask &= ok
    long_mask[:mask_prefix] = False
    short_mask[:mask_prefix] = False
    return long_mask, short_mask, atr_v
