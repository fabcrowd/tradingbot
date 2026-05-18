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

# ----- scalp_vec_backtest.py lines 1737–1826 -----
    )
    i = len(close) - 1
    return {"qqe_long": bool(long_m[i]), "qqe_short": bool(short_m[i])}


# ---------------------------------------------------------------------------
# New strategy: UT Bot Alert (Chandelier Exit trailing stop)
# ---------------------------------------------------------------------------

def detect_signals_utbot(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    atr_period: int = 10,
    atr_mult: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """UT Bot Alert: ATR trailing stop with direction flips (edge detector).

    Sparse masks: entries only on bull/bear flips, not every bar in a trend.
    ``cooldown_bars`` is not load-bearing. Mode is **off** the default WFO grid
    (manual pin, bootstrap, no-champion tuner).

    At ``loop_warm``, ``direction`` is seeded **bullish (1)** and ``trail`` to
    ``close[loop_warm]`` — Pine ``var int udir = 1`` at ``bar_index == utAtrLen``.
    Same window-start asymmetry as ``detect_signals_supertrend`` (TV parity).
    ``mask_prefix`` (= ``loop_warm + 1``) clears bars ``0..loop_warm``; first flip
    may occur at ``loop_warm + 1``.

    Ratchet uses strict ``>`` / ``<``; ``close == trail`` falls through to ``c + loss``
    (Pine ternary else branch). Direction flips use strict inequalities on ``pc`` vs
    prior trail and ``c`` vs new trail.

    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    if not (len(high) == n and len(low) == n):
        raise ValueError(
            f"detect_signals_utbot: OHLC length mismatch close={n} high={len(high)} low={len(low)}",
        )

    atr_v = atr(high, low, close, atr_period)

    trail = np.full(n, np.nan)
    direction = np.ones(n, dtype=np.int8)  # 1=bull, -1=bear; pre-warmup matches seed

    mask_prefix = vec_warmup_prefix_len(
        "utbot_alert",
        SimpleNamespace(utbot_atr_period=atr_period),
    )
    loop_warm = mask_prefix - 1  # == utbot_atr_period; synced via vec_warmup_prefix_len
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    if loop_warm >= n:
        _scalp_vec_bt_diag_warn(
            f"utbot:warm:{n}:{loop_warm}",
            f"detect_signals_utbot: len(close)={n} < recurrence start {loop_warm}; no signals.",
        )
        return long_mask, short_mask, atr_v

    trail[loop_warm] = close[loop_warm]
    direction[loop_warm] = 1

    for i in range(loop_warm + 1, n):
        if np.isnan(atr_v[i]):
            trail[i] = trail[i - 1]
            direction[i] = direction[i - 1]
            continue
        loss = atr_mult * atr_v[i]
        c = close[i]
        pc = close[i - 1]
        pt = trail[i - 1]
        # Ratchet (Pine utbot_alert ternary chain; c == pt → else branch c + loss)
        if c > pt and pc > pt:
            trail[i] = max(pt, c - loss)
        elif c < pt and pc < pt:
            trail[i] = min(pt, c + loss)
        elif c > pt:
            trail[i] = c - loss
        else:
            trail[i] = c + loss
        # Direction (pc vs prior trail, c vs ratcheted trail[i])
        if pc < trail[i - 1] and c > trail[i]:
            direction[i] = 1
        elif pc > trail[i - 1] and c < trail[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        if direction[i - 1] != 1 and direction[i] == 1:
            long_mask[i] = True
