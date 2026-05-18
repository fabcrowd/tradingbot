# AUTO-GENERATED review excerpt from scalp_vec_backtest.py
# Not intended as a runnable module alone — imports below match typical usage in the full file.
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from indicator_warmup import vec_warmup_prefix_len


# ----- scalp_vec_backtest.py lines 61–159 -----
def ema(close: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average. NaN-safe: skips leading NaNs before seeding."""
    out = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period:
        return out
    alpha = 2.0 / (period + 1)
    start = 0
    while start < len(close) and np.isnan(close[start]):
        start += 1
    if start + period > len(close):
        return out
    seed = np.nanmean(close[start : start + period])
    if np.isnan(seed):
        return out
    idx = start + period - 1
    out[idx] = seed
    for i in range(idx + 1, len(close)):
        v = close[i]
        if np.isnan(v):
            out[i] = out[i - 1]
        else:
            out[i] = alpha * v + (1 - alpha) * out[i - 1]
    return out


def _rising_edge(mask: np.ndarray) -> np.ndarray:
    """True at ``i`` when ``mask[i]`` is True and ``mask[i-1]`` was False.

    ``mask`` must be **boolean**. Integer 0/1 arrays are unsafe — ``~`` breaks for ints.
    """
    n = int(mask.shape[0])
    out = np.zeros(n, dtype=bool)
    if n <= 1:
        return out
    out[1:] = mask[1:] & (~mask[:-1])
    return out


def _touch_crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossover(a, b)``: ``a[i-1] <= b[i-1]`` and ``a[i] > b[i]``."""
    n = len(a)
    out = np.zeros(n, dtype=bool)
    if n <= 1:
        return out
    out[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
    return out


def _touch_crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossunder(a, b)``: ``a[i-1] >= b[i-1]`` and ``a[i] < b[i]``."""
    n = len(a)
    out = np.zeros(n, dtype=bool)
    if n <= 1:
        return out
    out[1:] = (a[:-1] >= b[:-1]) & (a[1:] < b[1:])
    return out


def rsi(close: np.ndarray, period: int) -> np.ndarray:
    """Wilder RSI. First `period` values are NaN."""
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    out = np.full(len(close), np.nan, dtype=np.float64)
    avg_gain = np.mean(gain[:period])
    avg_loss = np.mean(loss[:period])
    if avg_loss == 0:
        out[period] = 100.0
    else:
        out[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period
        if avg_loss == 0:
            out[i + 1] = 100.0
        else:
            out[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


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

# ----- scalp_vec_backtest.py lines 1978–2551 -----


# ---------------------------------------------------------------------------
# New strategy: SAR + CHOP (TV "5 min bot scalper" decode)
#   Parabolic SAR (flip trigger) + optional close-based Lucid SAR
#   + MA(200) / MA(50) trend filter + Choppiness Index regime filter
#   + MACD(12,26,9) histogram confirmation + UT Bot ATR-trail exit.
# ---------------------------------------------------------------------------

def _parabolic_sar(
    high: np.ndarray,
    low: np.ndarray,
    *,
    start: float = 0.02,
    step: float = 0.02,
    max_af: float = 0.2,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Classic Wilder Parabolic SAR (price-derived seed on bars 0–1).

    Returns (sar, bull_dir, long_flip_mask, short_flip_mask) where bull_dir is
    +1 when price is above SAR and -1 when below. ``long_flip_mask[i]`` is True
    on the bar the direction flipped from -1 to +1 (mirror for short_flip).

    Also used for Lucid SAR when ``high is low`` (same close array passed twice);
    no ``high >= low`` check in that case — intentional for close-based PSAR.
    """
    n = len(high)
    sar = np.full(n, np.nan, dtype=np.float64)
    bull = np.zeros(n, dtype=np.int8)
    long_flip = np.zeros(n, dtype=bool)
    short_flip = np.zeros(n, dtype=bool)
    if n < 3:
        return sar, bull, long_flip, short_flip
    # Seed with a simple direction heuristic from the first two bars.
    if high[1] >= high[0]:
        direction = 1
        sar[1] = float(low[0])
        ep = float(high[1])
    else:
        direction = -1
        sar[1] = float(high[0])
        ep = float(low[1])
    af = float(start)
    bull[1] = direction
    for i in range(2, n):
        prev_sar = sar[i - 1]
        new_sar = prev_sar + af * (ep - prev_sar)
        if direction == 1:
            # SAR can't exceed the prior two bars' lows.
            new_sar = min(new_sar, float(low[i - 1]), float(low[i - 2]))
            if low[i] < new_sar:
                # Flip down
                direction = -1
                new_sar = ep
                ep = float(low[i])
                af = float(start)
                short_flip[i] = True
            else:
                if high[i] > ep:
                    ep = float(high[i])
                    af = min(af + step, max_af)
        else:
            new_sar = max(new_sar, float(high[i - 1]), float(high[i - 2]))
            if high[i] > new_sar:
                # Flip up
                direction = 1
                new_sar = ep
                ep = float(high[i])
                af = float(start)
                long_flip[i] = True
            else:
                if low[i] < ep:
                    ep = float(low[i])
                    af = min(af + step, max_af)
        sar[i] = new_sar
        bull[i] = direction
    return sar, bull, long_flip, short_flip


def _chop_index(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Choppiness Index — 100 * log10(sum(TR)/range) / log10(period).

    Classic read: ``< ~38.2`` trending-side, ``> ~61.8`` chop-side — between is a gray zone.
    ``sar_chop`` default gate uses ``sar_chop_chop_threshold`` (repo default ``68``, looser than
    strict Fib caps so routine moderate-chop regimes are not vetoed). First ``period`` values are NaN.
    """
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if period <= 1 or n <= period:
        return out
    tr = np.empty(n, dtype=np.float64)
    tr[0] = float(high[0] - low[0])
    for i in range(1, n):
        tr[i] = max(
            float(high[i] - low[i]),
            abs(float(high[i] - close[i - 1])),
            abs(float(low[i] - close[i - 1])),
        )
    log_p = math.log10(float(period))
    hi_roll = rolling_max_arr(high, period)
    lo_roll = rolling_min_arr(low, period)
    for i in range(period, n):
        tr_sum = float(np.sum(tr[i - period + 1 : i + 1]))
        rng = float(hi_roll[i] - lo_roll[i])
        if rng <= 0 or tr_sum <= 0 or log_p <= 0:
            continue
        out[i] = 100.0 * math.log10(tr_sum / rng) / log_p
    return out


def _macd_hist(
    close: np.ndarray,
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> np.ndarray:
    """Standard (Appel) MACD histogram = (EMA_fast - EMA_slow) - signal EMA.

    Uses the ``ema`` helper above (first bars NaN until warmup).
    """
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line - sig


def _utbot_trail_flips(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    atr_period: int,
    atr_mult: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run the UT Bot ATR-trail state machine.

    Returns (direction, long_flip_mask, short_flip_mask) — direction is +1 bull,
    -1 bear, same ratchet mechanics as ``detect_signals_utbot``. Used for exits
    in the sar_chop mode without re-detecting entries.
    """
    n = len(close)
    atr_v = atr(high, low, close, atr_period)
    trail = np.full(n, np.nan, dtype=np.float64)
    direction = np.ones(n, dtype=np.int8)
    long_flip = np.zeros(n, dtype=bool)
    short_flip = np.zeros(n, dtype=bool)
    warmup = atr_period
    if warmup >= n:
        return direction, long_flip, short_flip
    trail[warmup] = float(close[warmup])
    direction[warmup] = 1
    for i in range(warmup + 1, n):
        if np.isnan(atr_v[i]):
            trail[i] = trail[i - 1]
            direction[i] = direction[i - 1]
            continue
        loss = atr_mult * atr_v[i]
        c = float(close[i])
        pc = float(close[i - 1])
        pt = float(trail[i - 1])
        if c > pt and pc > pt:
            trail[i] = max(pt, c - loss)
        elif c < pt and pc < pt:
            trail[i] = min(pt, c + loss)
        elif c > pt:
            trail[i] = c - loss
        else:
            trail[i] = c + loss
        if pc < trail[i - 1] and c > trail[i]:
            direction[i] = 1
        elif pc > trail[i - 1] and c < trail[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        if direction[i - 1] != 1 and direction[i] == 1:
            long_flip[i] = True
        elif direction[i - 1] != -1 and direction[i] == -1:
            short_flip[i] = True
    return direction, long_flip, short_flip


def _sar_chop_common_mats(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    sar_start: float,
    sar_increment: float,
    sar_max: float,
    ma_fast_period: int,
    ma_long_period: int,
    ma_short_period: int,
    chop_period: int,
    macd_fast: int,
    macd_slow: int,
    macd_signal: int,
    use_lucid_sar: bool,
    use_utbot_trail: bool,
    utbot_atr_period: int,
    utbot_atr_mult: float,
    atr_period: int,
) -> tuple[
    int,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
] | None:
    """Shared PSAR/CHOP/MA/MACD/UT arrays for sar_chop entry logic.

    Consumers: ``detect_signals_sar_chop``, ``sar_chop_diagnostic_frame`` (CSV/TV
    parity), ``sar_chop_signal_dump``. Split from ``_sar_chop_fill_masks`` so
    diagnostics export per-bar gates without duplicating indicator math.

    Returns None if ``len(close) < 3``.
    """
    n = len(close)
    atr_v = atr(high, low, close, atr_period)
    if n < 3:
        return None
    _, _, psar_long_flip, psar_short_flip = _parabolic_sar(
        high, low, start=sar_start, step=sar_increment, max_af=sar_max,
    )
    if use_lucid_sar:
        _, lucid_bull, _, _ = _parabolic_sar(
            close, close, start=sar_start, step=sar_increment, max_af=sar_max,
        )
    else:
        lucid_bull = np.ones(n, dtype=np.int8)
    chop = _chop_index(high, low, close, chop_period)
    ma_fast = ema(close, ma_fast_period)
    ma_long = ema(close, ma_long_period)
    ma_short = ema(close, ma_short_period)
    macd_h = _macd_hist(close, fast=macd_fast, slow=macd_slow, signal=macd_signal)
    if use_utbot_trail:
        ut_dir, _, _ = _utbot_trail_flips(
            close, high, low, atr_period=utbot_atr_period, atr_mult=utbot_atr_mult,
        )
    else:
        ut_dir = np.ones(n, dtype=np.int8)
    warmup = vec_warmup_prefix_len(
        "sar_chop",
        SimpleNamespace(
            sar_chop_ma_long_period=ma_long_period,
            sar_chop_macd_slow=macd_slow,
            sar_chop_macd_signal=macd_signal,
            sar_chop_chop_period=chop_period,
            atr_period=atr_period,
            sar_chop_utbot_atr_period=utbot_atr_period,
        ),
    )
    return (
        warmup,
        atr_v,
        psar_long_flip,
        psar_short_flip,
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    )


def _sar_chop_fill_masks(
    close: np.ndarray,
    warmup: int,
    chop_threshold: float,
    use_lucid_sar: bool,
    use_utbot_trail: bool,
    atr_v: np.ndarray,
    psar_long_flip: np.ndarray,
    psar_short_flip: np.ndarray,
    lucid_bull: np.ndarray,
    chop: np.ndarray,
    ma_fast: np.ndarray,
    ma_long: np.ndarray,
    ma_short: np.ndarray,
    macd_h: np.ndarray,
    ut_dir: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    n = len(close)
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    for i in range(warmup, n):
        if np.isnan(chop[i]) or np.isnan(ma_long[i]) or np.isnan(ma_short[i]) or np.isnan(macd_h[i]):
            continue
        if not (np.isfinite(atr_v[i]) and atr_v[i] > 0):
            continue
        if chop[i] >= chop_threshold:
            continue
        c = float(close[i])
        if psar_long_flip[i]:
            if c > ma_fast[i] and c > ma_long[i] and ma_short[i] >= ma_long[i] and macd_h[i] > 0:
                if (not use_lucid_sar or lucid_bull[i] == 1) and (not use_utbot_trail or ut_dir[i] == 1):
                    long_mask[i] = True
        elif psar_short_flip[i]:
            if c < ma_fast[i] and c < ma_long[i] and c < ma_short[i] and macd_h[i] < 0:
                if (not use_lucid_sar or lucid_bull[i] == -1) and (not use_utbot_trail or ut_dir[i] == -1):
                    short_mask[i] = True
    return long_mask, short_mask


def detect_signals_sar_chop(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    sar_start: float = 0.02,
    sar_increment: float = 0.02,
    sar_max: float = 0.2,
    ma_fast_period: int = 7,
    ma_long_period: int = 200,
    ma_short_period: int = 50,
    chop_period: int = 14,
    chop_threshold: float = 68.0,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    use_lucid_sar: bool = True,
    use_utbot_trail: bool = True,
    utbot_atr_period: int = 10,
    utbot_atr_mult: float = 2.0,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Combined SAR + CHOP entry, UT Bot ATR-trail exit.

    Entry conditions (long):
      - primary PSAR flips from bear to bull on this bar (``psar_long_flip``)
      - Choppiness Index < ``chop_threshold`` (regime gate; default ``68`` is looser than strict fib caps — see `_chop_index` doc.)
      - close > MA(7) (fast MA momentum confirmation)
      - close > MA(200) AND MA(50) >= MA(200) (bullish trend stack)
      - MACD histogram > 0
      - if ``use_lucid_sar``: Lucid (close-based) SAR is in bull state
      - if ``use_utbot_trail``: UT Bot trail is bull (prevents entry while exit
        trail still says bear)

    Short (intentionally not a mirror of the long MA stack):
      - PSAR bull→bear flip; same CHOP regime gate
      - close < MA(7), close < MA(50), close < MA(200); MACD hist < 0
      - optional Lucid bear / UT bear gates

    Exits via simulate_trades_bidir ATR stops/TPs — the
    ``utbot_atr_mult`` choice is reflected in the caller's atr_stop_mult when
    tuned for this mode (the trail agreement gate keeps entries aligned with
    the same stop that would protect a live fill).

    Returns (long_mask, short_mask, atr_vals).
    """
    n = len(close)
    if not (len(high) == n and len(low) == n):
        raise ValueError(
            f"detect_signals_sar_chop: OHLC length mismatch close={n} high={len(high)} low={len(low)}",
        )
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    cm = _sar_chop_common_mats(
        close, high, low,
        sar_start=sar_start,
        sar_increment=sar_increment,
        sar_max=sar_max,
        ma_fast_period=ma_fast_period,
        ma_long_period=ma_long_period,
        ma_short_period=ma_short_period,
        chop_period=chop_period,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
        use_lucid_sar=use_lucid_sar,
        use_utbot_trail=use_utbot_trail,
        utbot_atr_period=utbot_atr_period,
        utbot_atr_mult=utbot_atr_mult,
        atr_period=atr_period,
    )
    if cm is None:
        if n < 3:
            _scalp_vec_bt_diag_warn(
                f"sar_chop:short:{n}",
                f"detect_signals_sar_chop: len(close)={n} < 3; no signals generated.",
            )
        return long_mask, short_mask, atr(high, low, close, atr_period)
    (
        warmup,
        atr_v,
        psar_long_flip,
        psar_short_flip,
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    ) = cm
    if warmup >= n:
        _scalp_vec_bt_diag_warn(
            f"sar_chop:warm:{n}:{warmup}",
            f"detect_signals_sar_chop: len(close)={n} < warmup {warmup}; no signals generated.",
        )
        return long_mask, short_mask, atr_v
    long_mask, short_mask = _sar_chop_fill_masks(
        close,
        warmup,
        chop_threshold,
        use_lucid_sar,
        use_utbot_trail,
        atr_v,
        psar_long_flip,
        psar_short_flip,
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    )
    return long_mask, short_mask, atr_v


def sar_chop_diagnostic_frame(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    sar_start: float = 0.02,
    sar_increment: float = 0.02,
    sar_max: float = 0.2,
    ma_fast_period: int = 7,
    ma_long_period: int = 200,
    ma_short_period: int = 50,
    chop_period: int = 14,
    chop_threshold: float = 68.0,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    use_lucid_sar: bool = True,
    use_utbot_trail: bool = True,
    utbot_atr_period: int = 10,
    utbot_atr_mult: float = 2.0,
    atr_period: int = 14,
) -> dict[str, np.ndarray | int | float]:
    """Per-bar arrays for CSV / TradingView parity checks (same math as ``detect_signals_sar_chop``).

    Keys include ``warmup`` (int), ``chop_threshold`` (float), and numpy arrays:
    ``long_mask``, ``short_mask``, ``atr_v``, ``psar_long_flip``, ``psar_short_flip``,
    ``lucid_bull``, ``chop``, ``ma_fast``, ``ma_long``, ``ma_short``, ``macd_hist``, ``ut_dir``.
    """
    n = len(close)
    empty = {
        "warmup": 0,
        "chop_threshold": float(chop_threshold),
        "long_mask": np.zeros(n, dtype=bool),
        "short_mask": np.zeros(n, dtype=bool),
        "atr_v": atr(high, low, close, atr_period),
    }
    cm = _sar_chop_common_mats(
        close, high, low,
        sar_start=sar_start,
        sar_increment=sar_increment,
        sar_max=sar_max,
        ma_fast_period=ma_fast_period,
        ma_long_period=ma_long_period,
        ma_short_period=ma_short_period,
        chop_period=chop_period,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
        use_lucid_sar=use_lucid_sar,
        use_utbot_trail=use_utbot_trail,
        utbot_atr_period=utbot_atr_period,
        utbot_atr_mult=utbot_atr_mult,
        atr_period=atr_period,
    )
    if cm is None:
        return empty
    (
        warmup,
        atr_v,
        psar_long_flip,
        psar_short_flip,
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    ) = cm
    long_mask, short_mask = _sar_chop_fill_masks(
        close,
        warmup,
        chop_threshold,
        use_lucid_sar,
        use_utbot_trail,
        atr_v,
        psar_long_flip,
        psar_short_flip,
        lucid_bull,
        chop,
        ma_fast,
        ma_long,
        ma_short,
        macd_h,
        ut_dir,
    )
    return {
        "warmup": warmup,
        "chop_threshold": float(chop_threshold),
        "long_mask": long_mask,
        "short_mask": short_mask,
        "atr_v": atr_v,
        "psar_long_flip": psar_long_flip,
        "psar_short_flip": psar_short_flip,
        "lucid_bull": lucid_bull.astype(np.int16),
        "chop": chop,
        "ma_fast": ma_fast,
        "ma_long": ma_long,
        "ma_short": ma_short,
        "macd_hist": macd_h,
        "ut_dir": ut_dir.astype(np.int16),
    }


def sar_chop_live_bundle(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    sar_start: float = 0.02,
    sar_increment: float = 0.02,
    sar_max: float = 0.2,
    ma_fast_period: int = 7,
    ma_long_period: int = 200,
    ma_short_period: int = 50,
    chop_period: int = 14,
    chop_threshold: float = 68.0,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    use_lucid_sar: bool = True,
    use_utbot_trail: bool = True,
    utbot_atr_period: int = 10,
    utbot_atr_mult: float = 2.0,
    atr_period: int = 14,
) -> dict[str, float | bool]:
    """Last-bar SAR+CHOP state for the live indicator engine."""
    defaults: dict[str, float | bool] = {
        "sar_chop_long_setup": False,
        "sar_chop_short_setup": False,
        "sar_value": 0.0,
        "chop_value": 0.0,
        "sar_chop_trail_bull": False,
    }
    n = len(close)
    if n < 3:
        return defaults
    long_m, short_m, _ = detect_signals_sar_chop(
        close, high, low,
        sar_start=sar_start, sar_increment=sar_increment, sar_max=sar_max,
