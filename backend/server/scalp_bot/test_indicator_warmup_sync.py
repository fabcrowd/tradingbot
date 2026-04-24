"""Vec warmup prefix vs ``indicator_warmup``."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from scalp_bot.indicator_warmup import vec_warmup_prefix_len, effective_min_bars_ready
from scalp_bot.scalp_vec_backtest import detect_signals_ema, detect_signals_sar_chop


def test_ema_momentum_first_signal_index_matches_prefix() -> None:
    n = 80
    close = np.linspace(100.0, 120.0, n, dtype=np.float64)
    high = close + 0.5
    low = close - 0.5
    ema_slow = 13
    atr_p = 14
    long_m, short_m, _atr = detect_signals_ema(
        close, high, low,
        volume=np.ones(n),
        timestamp=np.arange(n, dtype=np.float64),
        ema_fast_period=5,
        ema_slow_period=ema_slow,
        rsi_period=9,
        atr_period=atr_p,
        vol_ma_period=20,
        vol_mult=1.5,
        min_signals=2,
    )
    prefix = vec_warmup_prefix_len(
        "ema_momentum",
        SimpleNamespace(ema_slow=ema_slow, atr_period=atr_p),
    )
    assert not long_m[:prefix].any()
    assert not short_m[:prefix].any()
    need = effective_min_bars_ready("ema_momentum", SimpleNamespace(ema_slow=ema_slow, atr_period=atr_p))
    assert long_m.shape[0] >= need


def test_sar_chop_loop_start_matches_prefix() -> None:
    n = 260
    rng = np.random.default_rng(0)
    close = 100.0 + np.cumsum(rng.standard_normal(n).astype(np.float64) * 0.1)
    high = close + 0.2
    low = close - 0.2
    long_m, short_m, _ = detect_signals_sar_chop(
        close, high, low,
        ma_long_period=200,
        ma_fast_period=7,
        ma_short_period=50,
        chop_period=14,
        chop_threshold=38.2,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        use_lucid_sar=True,
        use_utbot_trail=True,
        utbot_atr_period=10,
        utbot_atr_mult=2.0,
        atr_period=14,
    )
    ns = SimpleNamespace(
        sar_chop_ma_long_period=200,
        sar_chop_macd_slow=26,
        sar_chop_macd_signal=9,
        sar_chop_chop_period=14,
        atr_period=14,
        sar_chop_utbot_atr_period=10,
    )
    w = vec_warmup_prefix_len("sar_chop", ns)
    assert not long_m[:w].any() and not short_m[:w].any()
