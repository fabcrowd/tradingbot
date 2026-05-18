"""Single source of truth for per-mode indicator warmup bar counts.

Aligns live ``IndicatorValues.mode_ready`` with vector backtest ``detect_signals_*``
first-bar semantics. Duck-typing accepts ``ScalpPairConfig`` or ``ParamSet`` (same attrs).
"""

from __future__ import annotations

import math
from typing import Any


def _daviddtech_warmup_bars(cfg: Any) -> int:
    atr_period = int(getattr(cfg, "atr_period", 14))
    adx_period = int(getattr(cfg, "adx_period", 14))
    t3_length = int(getattr(cfg, "t3_length", 7))
    hlc_close_period = int(getattr(cfg, "hlc_close_period", 5))
    hlc_low_period = int(getattr(cfg, "hlc_low_period", 13))
    hlc_high_period = int(getattr(cfg, "hlc_high_period", 34))
    wae_slow_len = int(getattr(cfg, "wae_slow_len", 40))
    wae_bb_len = int(getattr(cfg, "wae_bb_len", 20))
    return max(
        atr_period * 3 + 5,
        adx_period * 2 + 5,
        t3_length * 6,
        hlc_close_period,
        hlc_low_period,
        hlc_high_period,
        wae_slow_len * 2 + wae_bb_len + 5,
    )


def min_bars_ready_for_mode(mode: str, cfg: Any) -> int:
    """Minimum closed candles before **mode-specific** entries should be allowed.

    Must match ``detect_signals_*`` warmup in ``scalp_vec_backtest.py``.
    """
    m = (mode or "").strip()
    if m == "daviddtech_scalp":
        w = _daviddtech_warmup_bars(cfg)
        return w + 1  # vec masks ``[:warm]`` when ``warm < n``
    if m == "ema_momentum":
        # WFO/live mode string — detector is ``scalp_vec_backtest.detect_signals_ema``.
        w = max(int(getattr(cfg, "ema_slow", 13)), int(getattr(cfg, "atr_period", 14)))
        return w + 1  # vec masks ``[:warmup]``; first signal at index ``warmup``
    if m == "rsi_reversion":
        w = max(int(getattr(cfg, "rsi_period", 9)), int(getattr(cfg, "atr_period", 14)))
        return w + 1
    if m == "ema_scalp":
        # Detector: ``detect_signals_ema_scalp``; S/R window is validity-only at entry.
        w = max(
            int(getattr(cfg, "ema_scalp_period", 20)),
            int(getattr(cfg, "atr_period", 14)),
            int(getattr(cfg, "ema_scalp_sr_bars", 8)),
        )
        return w + 1
    if m == "macd_scalp":
        # Heuristic: max period + 1; chained super_smooth converges well inside this prefix.
        w = max(
            int(getattr(cfg, "macd_fast_len", 8)),
            int(getattr(cfg, "macd_slow_len", 10)),
            int(getattr(cfg, "macd_signal_len", 8)),
            int(getattr(cfg, "atr_period", 14)),
        )
        return w + 1
    if m == "supertrend":
        w = max(int(getattr(cfg, "atr_period", 14)), int(getattr(cfg, "supertrend_period", 10)))
        # vec masks ``[:vec_warmup_prefix_len]`` (= w+1); recurrence seeds at bar w (= mask_prefix-1).
        return w + 2
    if m == "squeeze_momentum":
        bb = int(getattr(cfg, "squeeze_bb_period", 20))
        mom = int(getattr(cfg, "squeeze_mom_period", 12))
        atr = int(getattr(cfg, "atr_period", 14))
        # val needs bb-window stats; mom is mom_period slope on val.
        # First finite mom[i] at i = bb + mom - 2; first cross needs mom[i-1] too -> bb + mom - 1.
        w = max(bb + mom - 1, atr)
        return w + 2  # vec masks ``[:vec_warmup_prefix_len]`` (= w + 1)
    if m == "qqe_mod":
        rsi_period = int(getattr(cfg, "qqe_rsi_period", 14))
        smoothing = int(getattr(cfg, "qqe_smoothing", 5))
        wilders_period = rsi_period * 2 - 1
        atr = int(getattr(cfg, "atr_period", 14))
        # Chain: RSI(period) → EMA(smooth) → |diff| → Wilder atr_rsi(wilders) → trail from bar wilders.
        # Conservative margin after trail seed for Wilder/band convergence (see HANDOFF_AUDIT_QQE_MOD.md).
        w = max(rsi_period + smoothing + wilders_period + 5, atr)
        return w + 2  # vec masks ``[:vec_warmup_prefix_len]`` (= w + 1)
    if m == "utbot_alert":
        w = int(getattr(cfg, "utbot_atr_period", 10))
        # vec masks ``[:vec_warmup_prefix_len]`` (= w+1); recurrence seeds at bar w.
        return w + 2
    if m == "hull_suite":
        hull_period = int(getattr(cfg, "hull_period", 38))
        sqrtn = max(1, int(round(math.sqrt(hull_period))))
        # Conservative sum (not max): HMA lag-2 + ATR; vec masks ``[:warmup]`` via vec_warmup_prefix_len.
        w = hull_period + sqrtn + int(getattr(cfg, "atr_period", 14))
        return w + 1
    if m == "sar_chop":
        ma_long = int(getattr(cfg, "sar_chop_ma_long_period", 200))
        macd_slow = int(getattr(cfg, "sar_chop_macd_slow", 26))
        macd_signal = int(getattr(cfg, "sar_chop_macd_signal", 9))
        chop_period = int(getattr(cfg, "sar_chop_chop_period", 14))
        atr_period = int(getattr(cfg, "atr_period", 14))
        utbot_atr = int(getattr(cfg, "sar_chop_utbot_atr_period", 10))
        w = max(ma_long, macd_slow + macd_signal, chop_period, atr_period, utbot_atr) + 2
        return w + 1  # vec loop starts at ``i == warmup``; need ``n >= warmup+1``
    # Unknown / auto at caller — conservative default
    return int(getattr(cfg, "min_candles_required", 20))


def min_bars_ready_for_auto(cfg: Any) -> int:
    """When ``strategy_mode`` is ``auto``, require enough bars for fallback + sar_chop."""
    fb = str(getattr(cfg, "auto_mode_fallback", "sar_chop") or "sar_chop").strip()
    return max(min_bars_ready_for_mode(fb, cfg), min_bars_ready_for_mode("sar_chop", cfg))


def effective_min_bars_ready(strategy_mode: str, cfg: Any) -> int:
    sm = (strategy_mode or "").strip()
    if sm == "auto":
        return min_bars_ready_for_auto(cfg)
    return min_bars_ready_for_mode(sm, cfg)


def vec_warmup_prefix_len(strategy_mode: str, cfg: Any) -> int:
    """Exclusive-end index of bars vec forces False before first possible signal (``min_bars - 1``)."""
    n = effective_min_bars_ready(strategy_mode, cfg)
    return max(0, int(n) - 1)


def ohlc_hist_maxlen_for_pair(cfg: Any) -> int:
    """Rolling OHLC deque maxlen for live bundles (§1b track A).

    ``ohlc_hist_max_bars`` on pair: >0 uses that value; else ``max(320, sar_chop_ma_long+50)``.
    """
    explicit = int(getattr(cfg, "ohlc_hist_max_bars", 0) or 0)
    if explicit > 0:
        return explicit
    ma_long = int(getattr(cfg, "sar_chop_ma_long_period", 200))
    return max(320, ma_long + 50)
