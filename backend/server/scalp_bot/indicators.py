"""Incremental indicator engine using hexital — O(1) per candle update.

Each pair gets its own IndicatorSet. Call update(candle) on every closed
candle, then read signal properties to check for entry conditions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .candle_feed import Candle
from .scalp_config import ScalpPairConfig

LOG = logging.getLogger(__name__)


@dataclass
class IndicatorValues:
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    rsi: float = 50.0
    atr: float = 0.0
    volume: float = 0.0
    volume_ma: float = 0.0
    vwap_session: float = 0.0   # cumulative session VWAP
    close: float = 0.0
    # Derived signals
    ema_bullish: bool = False       # fast > slow
    ema_crossed_up: bool = False    # fast just crossed above slow this candle
    rsi_bullish: bool = False       # 50 < RSI < 70 (in gear, not overbought)
    vwap_bullish: bool = False      # price above session VWAP
    volume_confirmed: bool = False  # volume spike present
    # Readiness
    ready: bool = False             # enough candles loaded to trust indicators


class IndicatorSet:
    """Maintains incremental indicators for one pair using hexital."""

    def __init__(self, cfg: ScalpPairConfig) -> None:
        self._cfg = cfg
        self._ready = False
        self._candle_count = 0
        self._prev_ema_fast = 0.0
        self._prev_ema_slow = 0.0

        # Session VWAP state (resets at midnight UTC)
        self._vwap_cum_pv = 0.0   # cumulative price*volume
        self._vwap_cum_v = 0.0    # cumulative volume
        self._vwap_session_day = -1

        # Volume rolling average (simple manual deque — hexital VWAP uses session VWAP)
        from collections import deque
        self._volume_window: deque[float] = deque(maxlen=cfg.volume_ma_period)

        # Hexital indicators
        try:
            from hexital import EMA, RSI, ATR
            self._ema_fast = EMA(period=cfg.ema_fast)
            self._ema_slow = EMA(period=cfg.ema_slow)
            self._rsi = RSI(period=cfg.rsi_period)
            self._atr = ATR(period=cfg.atr_period)
            self._hexital_ok = True
        except ImportError:
            LOG.error(
                "hexital not installed — run: pip install hexital. "
                "Indicator set will return neutral values until installed."
            )
            self._hexital_ok = False

    def update(self, candle: Candle) -> IndicatorValues:
        """Append one closed candle and return current indicator values."""
        self._candle_count += 1
        self._volume_window.append(candle.volume)

        # Session VWAP — resets at midnight UTC
        import datetime
        day = datetime.datetime.utcfromtimestamp(candle.timestamp).day
        if day != self._vwap_session_day:
            self._vwap_cum_pv = 0.0
            self._vwap_cum_v = 0.0
            self._vwap_session_day = day
        typical = (candle.high + candle.low + candle.close) / 3.0
        self._vwap_cum_pv += typical * candle.volume
        self._vwap_cum_v += candle.volume
        vwap = self._vwap_cum_pv / self._vwap_cum_v if self._vwap_cum_v > 0 else candle.close

        iv = IndicatorValues(close=candle.close, vwap_session=vwap, volume=candle.volume)

        if not self._hexital_ok:
            return iv

        from hexital.core.candle import Candle as HCandle
        hc = HCandle(
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
        )
        self._ema_fast.append(hc)
        self._ema_slow.append(hc)
        self._rsi.append(hc)
        self._atr.append(hc)

        ema_fast = self._ema_fast.reading() or 0.0
        ema_slow = self._ema_slow.reading() or 0.0
        rsi_val = self._rsi.reading() or 50.0
        atr_val = self._atr.reading() or 0.0

        vol_ma = (
            sum(self._volume_window) / len(self._volume_window)
            if self._volume_window else 0.0
        )

        # Readiness: need enough candles for the slowest indicator
        min_candles = max(self._cfg.ema_slow, self._cfg.rsi_period,
                          self._cfg.atr_period, self._cfg.min_candles_required)
        ready = self._candle_count >= min_candles

        # Derived signals
        ema_bullish = ema_fast > ema_slow
        ema_crossed_up = ema_bullish and self._prev_ema_fast <= self._prev_ema_slow
        rsi_bullish = 50.0 < rsi_val < 70.0
        vwap_bullish = candle.close > vwap
        volume_confirmed = (
            vol_ma > 0 and candle.volume >= vol_ma * self._cfg.volume_mult
        )

        self._prev_ema_fast = ema_fast
        self._prev_ema_slow = ema_slow

        return IndicatorValues(
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi=rsi_val,
            atr=atr_val,
            volume=candle.volume,
            volume_ma=vol_ma,
            vwap_session=vwap,
            close=candle.close,
            ema_bullish=ema_bullish,
            ema_crossed_up=ema_crossed_up,
            rsi_bullish=rsi_bullish,
            vwap_bullish=vwap_bullish,
            volume_confirmed=volume_confirmed,
            ready=ready,
        )

    @property
    def candle_count(self) -> int:
        return self._candle_count
