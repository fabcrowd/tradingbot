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

# ----- scalp_vec_backtest.py lines 162–174 -----
def wma(close: np.ndarray, period: int) -> np.ndarray:
    """Weighted moving average — linearly weighted, newest bar has highest weight."""
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if period < 1:
        return out
    if period > n:
        _scalp_vec_bt_diag_warn(
            f"wma:{period}:{n}",
            f"wma: period={period} > len(close)={n}; returning all-NaN.",
        )
        return out
    weights = np.arange(1, period + 1, dtype=np.float64)

# ----- scalp_vec_backtest.py lines 1886–1965 -----
        "utbot_short": bool(short_m[i]),
        "utbot_bull": bool(direction[i] == 1),
    }


# ---------------------------------------------------------------------------
# Hull Suite — TradingView "Hull Suite Strategy" (Hma path) entry semantics
# ---------------------------------------------------------------------------

def detect_signals_hull(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    hull_period: int = 38,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hull Suite (**registered mode:** ``hull_suite`` — DashTrader / InSilico Hma path).

    HMA = WMA(2×WMA(n/2) − WMA(n), round(√n)) on ``close``. Long when ``HMA[i] > HMA[i-2]``,
    short when ``HMA[i] < HMA[i-2]`` (Pine ``longSig`` / ``shortSig``).

    **State classifier (not edge-only):** the mask is **True on every bar** the inequality holds,
    not only on direction flips. Pine and live only act when flat; WFO ``simulate_trades_bidir``
    dedupes via ``next_allowed`` + ``cooldown_bars``. ``counter_signal_exit`` is effectively inert
    here (opposite mask cannot be True while HMA stays monotonic vs lag-2).
    """
    n = len(close)
    if not (len(high) == n and len(low) == n):
        raise ValueError(
            f"detect_signals_hull: OHLC length mismatch close={n} high={len(high)} low={len(low)}",
        )

    atr_v = atr(high, low, close, atr_period)

    half = max(1, hull_period // 2)
    sqrtn = max(1, int(round(np.sqrt(hull_period))))

    wma_half = wma(close, half)
    wma_full = wma(close, hull_period)
    diff = 2.0 * wma_half - wma_full
    hma = wma(diff, sqrtn)

    hma_lag2 = np.full(n, np.nan, dtype=np.float64)
    hma_lag2[2:] = hma[:-2]
    # isfinite (not isnan): HMA/WMA differencing can theoretically yield ±inf on bad inputs.
    valid = np.isfinite(hma) & np.isfinite(hma_lag2)
    long_mask = (hma > hma_lag2) & valid
    short_mask = (hma < hma_lag2) & valid

    ok = ~np.isnan(atr_v) & (atr_v > 0)
    long_mask &= ok
    short_mask &= ok
    warmup = vec_warmup_prefix_len(
        "hull_suite",
        SimpleNamespace(hull_period=hull_period, atr_period=atr_period),
    )
    if warmup >= n:
        _scalp_vec_bt_diag_warn(
            f"hull_suite:warm:{n}:{warmup}",
            f"detect_signals_hull: window len={n} < warmup prefix {warmup}; no signals.",
        )
        long_mask[:] = False
        short_mask[:] = False
        return long_mask, short_mask, atr_v
    long_mask[:warmup] = False
    short_mask[:warmup] = False
    return long_mask, short_mask, atr_v


def hull_live_bundle(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    hull_period: int = 38,
    atr_period: int = 14,
) -> dict[str, bool]:
    """Last-bar Hull Suite flags (same semantics as ``detect_signals_hull``)."""
    if len(close) < 3:
