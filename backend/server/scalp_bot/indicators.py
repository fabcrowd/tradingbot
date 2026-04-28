"""Incremental indicator engine using hexital — O(1) per candle update.

Each pair gets its own IndicatorSet. Call update(candle) on every closed
candle, then read signal properties to check for entry conditions.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass

import numpy as np

from .candle_feed import Candle
from .indicator_warmup import effective_min_bars_ready, ohlc_hist_maxlen_for_pair
from .scalp_config import ScalpPairConfig
from .scalp_vec_backtest import (
    daviddtech_live_bundle,
    supertrend_live_bundle,
    squeeze_live_bundle,
    qqe_live_bundle,
    utbot_live_bundle,
    hull_live_bundle,
    sar_chop_live_bundle,
)

LOG = logging.getLogger(__name__)


@dataclass
class IndicatorValues:
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    rsi: float = 50.0
    prev_rsi: float = 50.0         # RSI from previous candle (for exit detection)
    atr: float = 0.0
    volume: float = 0.0
    volume_ma: float = 0.0
    vwap_session: float = 0.0
    close: float = 0.0
    # Derived signals
    ema_bullish: bool = False
    ema_crossed_up: bool = False
    ema_crossed_down: bool = False
    rsi_bullish: bool = False       # 50 < RSI < 70 (EMA momentum zone)
    rsi_oversold: bool = False      # RSI below buy threshold (RSI reversion entry)
    rsi_sell_trigger: bool = False  # RSI crossed above sell threshold (RSI reversion exit)
    vwap_bullish: bool = False  # display-only — surfaced in MONITOR/WS snapshots; never read by signal_engine
    volume_confirmed: bool = False
    # EMA scalp (Tony's strategy)
    ema_scalp: float = 0.0          # single EMA value (e.g. 20 EMA)
    ema_scalp_cross_bull: bool = False  # price crossed above EMA AND candle is bullish
    ema_scalp_cross_bear: bool = False  # price crossed below EMA AND candle is bearish
    high_8: float = 0.0             # highest close of last N bars (resistance)
    low_8: float = 0.0              # lowest close of last N bars (support)
    prev_close: float = 0.0         # previous candle close
    # MACD scalp (Scalp Pro — Ehlers super-smoother)
    macd_line: float = 0.0           # fast_smooth - slow_smooth (scaled)
    macd_signal: float = 0.0         # smoothed macd
    macd_cross_bull: bool = False    # macd crossed above signal
    macd_cross_bear: bool = False    # macd crossed below signal
    # Optimized Strategy (DaviddTech-style)
    t3: float = 0.0
    hlc_green: float = 0.0
    hlc_red: float = 0.0
    wae_up: float = 0.0
    wae_down: float = 0.0
    adx: float = 0.0
    optimized_ready: bool = False
    optimized_long_setup: bool = False
    optimized_short_setup: bool = False
    # Supertrend
    supertrend_long: bool = False      # last-bar flip to bullish
    supertrend_short: bool = False     # last-bar flip to bearish
    supertrend_bull: bool = False      # currently in bullish supertrend state
    # Squeeze Momentum
    squeeze_long: bool = False         # momentum crossed above zero
    squeeze_short: bool = False        # momentum crossed below zero
    # QQE Mod
    qqe_long: bool = False             # smooth_rsi crossed above trail + > 50
    qqe_short: bool = False            # smooth_rsi crossed below trail + < 50
    # UT Bot Alert
    utbot_long: bool = False           # trailing stop flipped to bull
    utbot_short: bool = False          # trailing stop flipped to bear
    utbot_bull: bool = False           # currently in bullish UT Bot state
    # Hull Suite (TV Hull Suite Strategy — Hma, MHULL vs SHULL)
    hull_long: bool = False            # HMA > HMA two bars ago
    hull_short: bool = False           # HMA < HMA two bars ago
    hull_bull: bool = False            # same as hull_long (TV trend color)
    # SAR + CHOP (TV "5 min bot scalper" decode)
    sar_chop_long_setup: bool = False  # last-bar qualified long entry
    sar_chop_short_setup: bool = False # last-bar qualified short entry
    sar_value: float = 0.0             # latest PSAR value (for debugging / UI)
    chop_value: float = 0.0            # latest Choppiness Index value
    sar_chop_trail_bull: bool = False  # UT Bot trail currently in bull state
    # Readiness
    ready: bool = False
    # Mode-specific bar count (aligns vec warmup); ``daviddtech`` still uses ``optimized_ready``.
    mode_ready: bool = True
    min_bars_ready_mode: int = 0


class _IncrementalEMA:
    """SMA-seeded EMA matching scalp_vec_backtest.ema() exactly.

    Collects the first `period` values to seed with their mean, then applies
    α = 2/(period+1) exponential smoothing — identical to the numpy batch helper
    used by the backtest.  This ensures live indicators produce the same values
    as the WFO when use_numpy_indicators=True.
    """

    def __init__(self, period: int) -> None:
        self._period = max(1, period)
        self._alpha = 2.0 / (self._period + 1)
        self._seed_buf: list[float] = []
        self._value: float = float("nan")
        self._seeded: bool = False

    def push(self, value: float) -> float:
        if not self._seeded:
            self._seed_buf.append(value)
            if len(self._seed_buf) >= self._period:
                self._value = sum(self._seed_buf) / len(self._seed_buf)
                self._seeded = True
            return self._value  # nan until seeded
        self._value = self._alpha * value + (1.0 - self._alpha) * self._value
        return self._value

    @property
    def value(self) -> float:
        return self._value


class _IncrementalRSI:
    """Wilder RSI matching scalp_vec_backtest.rsi() exactly.

    Seeds avg_gain / avg_loss from the first `period` deltas (SMA), then
    applies Wilder smoothing: avg = (prev * (period-1) + current) / period.
    """

    def __init__(self, period: int) -> None:
        self._period = max(1, period)
        self._prev: float = float("nan")
        self._gains: list[float] = []
        self._losses: list[float] = []
        self._avg_gain: float = 0.0
        self._avg_loss: float = 0.0
        self._seeded: bool = False
        self._value: float = 50.0

    def push(self, close: float) -> float:
        if self._prev != self._prev:  # nan check
            self._prev = close
            return 50.0

        delta = close - self._prev
        self._prev = close
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0

        if not self._seeded:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) >= self._period:
                self._avg_gain = sum(self._gains) / self._period
                self._avg_loss = sum(self._losses) / self._period
                self._seeded = True
                if self._avg_loss == 0:
                    self._value = 100.0
                else:
                    self._value = 100.0 - 100.0 / (1.0 + self._avg_gain / self._avg_loss)
            return self._value

        self._avg_gain = (self._avg_gain * (self._period - 1) + gain) / self._period
        self._avg_loss = (self._avg_loss * (self._period - 1) + loss) / self._period
        if self._avg_loss == 0:
            self._value = 100.0
        else:
            self._value = 100.0 - 100.0 / (1.0 + self._avg_gain / self._avg_loss)
        return self._value

    @property
    def value(self) -> float:
        return self._value


class _SuperSmoother:
    """Ehlers 2-pole super-smoother filter — recursive IIR with pi*sqrt(2) coefficients."""

    def __init__(self, period: int) -> None:
        import math
        f = (1.4142135623730951 * math.pi) / period
        a = math.exp(-f)
        self._c2 = 2.0 * a * math.cos(f)
        self._c3 = -(a * a)
        self._c1 = 1.0 - self._c2 - self._c3
        self._prev1 = 0.0  # ssmooth[i-1]
        self._prev2 = 0.0  # ssmooth[i-2]
        self._prev_input = 0.0
        self._count = 0

    def push(self, value: float) -> float:
        if self._count == 0:
            result = value
        elif self._count == 1:
            result = self._c1 * (value + self._prev_input) * 0.5 + self._c2 * self._prev1
        else:
            result = (
                self._c1 * (value + self._prev_input) * 0.5
                + self._c2 * self._prev1
                + self._c3 * self._prev2
            )
        self._prev2 = self._prev1
        self._prev1 = result
        self._prev_input = value
        self._count += 1
        return result


class IndicatorSet:
    """Maintains incremental indicators for one pair.

    By default uses the hexital library for EMA/RSI/ATR.
    When ``use_numpy=True``, substitutes numpy-matched incremental classes for
    EMA and RSI so that live values align with the backtest (SMA-seeded).
    This only affects ema_momentum, ema_scalp, and rsi_reversion modes;
    daviddtech_scalp already routes through the shared numpy bundle.
    """

    def __init__(self, cfg: ScalpPairConfig, use_numpy: bool = False) -> None:
        self._cfg = cfg
        self._use_numpy = use_numpy
        self._ready = False
        self._candle_count = 0
        self._ohlc_hist_maxlen = int(ohlc_hist_maxlen_for_pair(cfg))
        self._prev_ema_fast = 0.0
        self._prev_ema_slow = 0.0
        self._prev_rsi = 50.0

        # Session VWAP state (resets at midnight UTC)
        self._vwap_cum_pv = 0.0   # cumulative price*volume
        self._vwap_cum_v = 0.0    # cumulative volume
        self._vwap_session_day = -1

        # Volume rolling average (simple manual deque — hexital VWAP uses session VWAP)
        self._volume_window: deque[float] = deque(maxlen=cfg.volume_ma_period)

        # Close history for 8-bar high/low
        self._close_history: deque[float] = deque(maxlen=max(cfg.ema_scalp_sr_bars, 8))
        self._prev_close: float = 0.0
        self._prev_ema_scalp: float = 0.0
        self._ohlc_hist: deque[tuple[float, float, float]] = deque(maxlen=self._ohlc_hist_maxlen)

        # Super-smoother MACD state (Ehlers 2-pole filter)
        self._ss_fast = _SuperSmoother(cfg.macd_fast_len)
        self._ss_slow = _SuperSmoother(cfg.macd_slow_len)
        self._ss_signal = _SuperSmoother(cfg.macd_signal_len)
        self._prev_macd: float = 0.0
        self._prev_macd_signal: float = 0.0

        # Numpy-matched incremental indicators (use_numpy=True path)
        self._np_ema_fast = _IncrementalEMA(cfg.ema_fast)
        self._np_ema_slow = _IncrementalEMA(cfg.ema_slow)
        self._np_ema_scalp = _IncrementalEMA(cfg.ema_scalp_period)
        self._np_rsi = _IncrementalRSI(cfg.rsi_period)

        # Hexital indicators.
        # Always load ATR (used even in numpy mode for a more accurate ATR estimate).
        # EMA/RSI hexital objects are only created when use_numpy=False.
        self._hexital_ok = False
        try:
            from hexital import EMA, RSI, ATR
            self._atr = ATR(period=cfg.atr_period)
            if not use_numpy:
                self._ema_fast = EMA(period=cfg.ema_fast)
                self._ema_slow = EMA(period=cfg.ema_slow)
                self._ema_scalp_ind = EMA(period=cfg.ema_scalp_period)
                self._rsi = RSI(period=cfg.rsi_period)
            self._hexital_ok = True
        except ImportError:
            LOG.error(
                "hexital not installed — run: pip install hexital. "
                "Falling back to numpy indicators automatically."
            )
            self._use_numpy = True

    @property
    def ohlc_hist_maxlen(self) -> int:
        return int(self._ohlc_hist_maxlen)

    def update(
        self,
        candle: Candle,
        *,
        strategy_mode_for_ready: str | None = None,
    ) -> IndicatorValues:
        """Append one closed candle and return current indicator values.

        ``strategy_mode_for_ready`` should match the execution mode (e.g. resolved ``auto``);
        when omitted, ``cfg.strategy_mode`` is used (may be ``auto`` — then ``effective_min_bars_ready`` applies).
        """
        self._candle_count += 1
        _rmode = (
            strategy_mode_for_ready
            if strategy_mode_for_ready is not None
            else self._cfg.strategy_mode
        )
        _min_mode_bars = effective_min_bars_ready(str(_rmode or "").strip(), self._cfg)
        _mode_ready = self._candle_count >= _min_mode_bars

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

        self._ohlc_hist.append((candle.high, candle.low, candle.close))
        t3_v = hlc_g = hlc_r = w_up = w_dn = adx_v = 0.0
        opt_ready = opt_long = opt_short = False
        # New-strategy flags (computed from ohlc history deque)
        st_long = st_short = st_bull = False
        sq_long = sq_short = False
        qq_long = qq_short = False
        ut_long = ut_short = ut_bull = False
        hl_long = hl_short = hl_bull = False
        sc_long = sc_short = False
        sc_trail_bull = False
        sc_sar = sc_chop = 0.0

        if len(self._ohlc_hist) >= 5:
            h_arr = np.array([x[0] for x in self._ohlc_hist], dtype=np.float64)
            l_arr = np.array([x[1] for x in self._ohlc_hist], dtype=np.float64)
            c_arr = np.array([x[2] for x in self._ohlc_hist], dtype=np.float64)
            cfg = self._cfg
            ob = daviddtech_live_bundle(
                h_arr, l_arr, c_arr,
                atr_period=cfg.atr_period,
                adx_period=getattr(cfg, "adx_period", 14),
                t3_length=cfg.t3_length,
                t3_vfactor=cfg.t3_vfactor,
                hlc_close_period=cfg.hlc_close_period,
                hlc_low_period=cfg.hlc_low_period,
                hlc_high_period=cfg.hlc_high_period,
                adx_threshold=cfg.adx_threshold,
                wae_sensitivity=cfg.wae_sensitivity,
                wae_fast_len=cfg.wae_fast_len,
                wae_slow_len=cfg.wae_slow_len,
                wae_bb_len=cfg.wae_bb_len,
                wae_bb_mult=cfg.wae_bb_mult,
            )
            t3_v = float(ob["t3"])
            hlc_g = float(ob["hlc_green"])
            hlc_r = float(ob["hlc_red"])
            w_up = float(ob["wae_up"])
            w_dn = float(ob["wae_down"])
            adx_v = float(ob["adx"])
            opt_ready = bool(ob["optimized_ready"])
            opt_long = bool(ob["optimized_long_setup"])
            opt_short = bool(ob.get("optimized_short_setup", False))

            # Supertrend
            try:
                sb = supertrend_live_bundle(
                    h_arr, l_arr, c_arr,
                    period=cfg.supertrend_period,
                    factor=cfg.supertrend_factor,
                    atr_period=cfg.atr_period,
                )
                st_long, st_short, st_bull = sb["supertrend_long"], sb["supertrend_short"], sb["supertrend_bull"]
            except Exception:
                pass
            # Squeeze Momentum
            try:
                sqb = squeeze_live_bundle(
                    h_arr, l_arr, c_arr,
                    bb_period=cfg.squeeze_bb_period,
                    bb_mult=cfg.squeeze_bb_mult,
                    kc_mult=cfg.squeeze_kc_mult,
                    mom_period=cfg.squeeze_mom_period,
                    atr_period=cfg.atr_period,
                )
                sq_long, sq_short = sqb["squeeze_long"], sqb["squeeze_short"]
            except Exception:
                pass
            # QQE Mod
            try:
                qqb = qqe_live_bundle(
                    h_arr, l_arr, c_arr,
                    rsi_period=cfg.qqe_rsi_period,
                    qqe_factor=cfg.qqe_factor,
                    qqe_smoothing=cfg.qqe_smoothing,
                    atr_period=cfg.atr_period,
                )
                qq_long, qq_short = qqb["qqe_long"], qqb["qqe_short"]
            except Exception:
                pass
            # UT Bot Alert
            try:
                utb = utbot_live_bundle(
                    h_arr, l_arr, c_arr,
                    atr_period=cfg.utbot_atr_period,
                    atr_mult=cfg.utbot_atr_mult,
                )
                ut_long, ut_short, ut_bull = utb["utbot_long"], utb["utbot_short"], utb["utbot_bull"]
            except Exception:
                pass
            # Hull Suite
            try:
                hlb = hull_live_bundle(
                    h_arr, l_arr, c_arr,
                    hull_period=cfg.hull_period,
                    atr_period=cfg.atr_period,
                )
                hl_long, hl_short, hl_bull = hlb["hull_long"], hlb["hull_short"], hlb["hull_bull"]
            except Exception:
                pass
            # SAR + CHOP (TV "5 min bot scalper" decode)
            try:
                scb = sar_chop_live_bundle(
                    h_arr, l_arr, c_arr,
                    sar_start=cfg.sar_start,
                    sar_increment=cfg.sar_increment,
                    sar_max=cfg.sar_max,
                    ma_fast_period=getattr(cfg, "sar_chop_ma_fast_period", 7),
                    ma_long_period=cfg.sar_chop_ma_long_period,
                    ma_short_period=cfg.sar_chop_ma_short_period,
                    chop_period=cfg.sar_chop_chop_period,
                    chop_threshold=cfg.sar_chop_chop_threshold,
                    macd_fast=cfg.sar_chop_macd_fast,
                    macd_slow=cfg.sar_chop_macd_slow,
                    macd_signal=cfg.sar_chop_macd_signal,
                    use_lucid_sar=cfg.sar_chop_use_lucid,
                    use_utbot_trail=cfg.sar_chop_use_utbot_trail,
                    utbot_atr_period=cfg.sar_chop_utbot_atr_period,
                    utbot_atr_mult=cfg.sar_chop_utbot_mult,
                    atr_period=cfg.atr_period,
                )
                sc_long = bool(scb["sar_chop_long_setup"])
                sc_short = bool(scb["sar_chop_short_setup"])
                sc_sar = float(scb["sar_value"])
                sc_chop = float(scb["chop_value"])
                sc_trail_bull = bool(scb["sar_chop_trail_bull"])
            except Exception:
                pass

        iv = IndicatorValues(
            close=candle.close, vwap_session=vwap, volume=candle.volume,
            t3=t3_v, hlc_green=hlc_g, hlc_red=hlc_r, wae_up=w_up, wae_down=w_dn, adx=adx_v,
            optimized_ready=opt_ready, optimized_long_setup=opt_long,
            optimized_short_setup=opt_short,
            supertrend_long=st_long, supertrend_short=st_short, supertrend_bull=st_bull,
            squeeze_long=sq_long, squeeze_short=sq_short,
            qqe_long=qq_long, qqe_short=qq_short,
            utbot_long=ut_long, utbot_short=ut_short, utbot_bull=ut_bull,
            hull_long=hl_long, hull_short=hl_short, hull_bull=hl_bull,
            sar_chop_long_setup=sc_long, sar_chop_short_setup=sc_short,
            sar_value=sc_sar, chop_value=sc_chop, sar_chop_trail_bull=sc_trail_bull,
            mode_ready=_mode_ready,
            min_bars_ready_mode=_min_mode_bars,
        )

        if self._use_numpy:
            # ── Numpy-matched path (SMA-seeded EMA + Wilder RSI) ────────────
            _ef = self._np_ema_fast.push(candle.close)
            _es = self._np_ema_slow.push(candle.close)
            _esc = self._np_ema_scalp.push(candle.close)
            _rsi = self._np_rsi.push(candle.close)
            ema_fast = _ef if _ef == _ef else 0.0        # nan → 0.0 pre-seed
            ema_slow = _es if _es == _es else 0.0
            ema_scalp_val = _esc if _esc == _esc else 0.0
            rsi_val = _rsi if _rsi == _rsi else 50.0
            # ATR: use the daviddtech bundle value (already computed above) when available,
            # otherwise fall back to a simple running estimate from OHLC.
            atr_val = adx_v  # adx_v = 0.0 until warm; we use hexital ATR below if available
            # Override atr from hexital if it was already loaded (hybrid: numpy EMA/RSI + hexital ATR)
            if self._hexital_ok:
                from hexital.core.candle import Candle as HCandle
                hc = HCandle(open=candle.open, high=candle.high,
                             low=candle.low, close=candle.close, volume=candle.volume)
                try:
                    self._atr.append(hc)
                    atr_val = self._atr.reading() or 0.0
                except Exception:
                    pass
            else:
                # Fall back: use last ATR from the daviddtech bundle (adx_v != ATR, so use ohlc hist)
                if len(self._ohlc_hist) >= 2:
                    highs = [x[0] for x in self._ohlc_hist]
                    lows = [x[1] for x in self._ohlc_hist]
                    closes = [x[2] for x in self._ohlc_hist]
                    tr_vals = [max(highs[i] - lows[i],
                                   abs(highs[i] - closes[i - 1]),
                                   abs(lows[i] - closes[i - 1]))
                               for i in range(1, len(highs))]
                    atr_val = sum(tr_vals) / len(tr_vals) if tr_vals else 0.0
        elif not self._hexital_ok:
            return iv
        else:
            # ── Hexital path (default) ────────────────────────────────────────
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
            self._ema_scalp_ind.append(hc)
            try:
                self._rsi.append(hc)
            except ZeroDivisionError:
                pass
            try:
                self._atr.append(hc)
            except ZeroDivisionError:
                pass

            ema_fast = self._ema_fast.reading() or 0.0
            ema_slow = self._ema_slow.reading() or 0.0
            ema_scalp_val = self._ema_scalp_ind.reading() or 0.0
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
        ema_crossed_down = (not ema_bullish) and self._prev_ema_fast >= self._prev_ema_slow
        rsi_bullish = 50.0 < rsi_val < 70.0
        rsi_oversold = rsi_val <= self._cfg.rsi_buy_threshold
        rsi_sell_trigger = (
            self._prev_rsi < self._cfg.rsi_sell_threshold
            and rsi_val >= self._cfg.rsi_sell_threshold
        )
        vwap_bullish = candle.close > vwap  # DISPLAY ONLY — not used to gate any entry in signal_engine
        volume_confirmed = (
            vol_ma > 0 and candle.volume >= vol_ma * self._cfg.volume_mult
        )

        # EMA scalp: cross detection (price crosses EMA + trend direction)
        # cross() in PineScript = value crossed above or below the line this bar
        prev_above_ema = self._prev_close > self._prev_ema_scalp if self._prev_close > 0 else False
        cur_above_ema = candle.close > ema_scalp_val if ema_scalp_val > 0 else False
        crossed_ema = prev_above_ema != cur_above_ema and self._prev_close > 0 and ema_scalp_val > 0
        bullish_candle = candle.close > self._prev_close if self._prev_close > 0 else False
        bearish_candle = candle.close < self._prev_close if self._prev_close > 0 else False
        ema_scalp_cross_bull = crossed_ema and cur_above_ema and bullish_candle
        ema_scalp_cross_bear = crossed_ema and not cur_above_ema and bearish_candle

        # 8-bar high/low (support/resistance from close prices)
        self._close_history.append(candle.close)
        sr_bars = self._cfg.ema_scalp_sr_bars
        hist = list(self._close_history)
        high_8 = max(hist[-sr_bars:]) if len(hist) >= sr_bars else max(hist) if hist else candle.close
        low_8 = min(hist[-sr_bars:]) if len(hist) >= sr_bars else min(hist) if hist else candle.close

        # Super-smoother MACD
        ss_fast_val = self._ss_fast.push(candle.close)
        ss_slow_val = self._ss_slow.push(candle.close)
        macd_raw = (ss_fast_val - ss_slow_val) * 1e7
        macd_signal_val = self._ss_signal.push(macd_raw)
        macd_cross_bull = self._prev_macd <= self._prev_macd_signal and macd_raw > macd_signal_val
        macd_cross_bear = self._prev_macd >= self._prev_macd_signal and macd_raw < macd_signal_val

        prev_rsi = self._prev_rsi
        prev_close_val = self._prev_close
        self._prev_ema_fast = ema_fast
        self._prev_ema_slow = ema_slow
        self._prev_rsi = rsi_val
        self._prev_close = candle.close
        self._prev_ema_scalp = ema_scalp_val
        self._prev_macd = macd_raw
        self._prev_macd_signal = macd_signal_val

        return IndicatorValues(
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi=rsi_val,
            prev_rsi=prev_rsi,
            atr=atr_val,
            volume=candle.volume,
            volume_ma=vol_ma,
            vwap_session=vwap,
            close=candle.close,
            ema_bullish=ema_bullish,
            ema_crossed_up=ema_crossed_up,
            ema_crossed_down=ema_crossed_down,
            rsi_bullish=rsi_bullish,
            rsi_oversold=rsi_oversold,
            rsi_sell_trigger=rsi_sell_trigger,
            vwap_bullish=vwap_bullish,
            volume_confirmed=volume_confirmed,
            ema_scalp=ema_scalp_val,
            ema_scalp_cross_bull=ema_scalp_cross_bull,
            ema_scalp_cross_bear=ema_scalp_cross_bear,
            high_8=high_8,
            low_8=low_8,
            prev_close=prev_close_val,
            macd_line=macd_raw,
            macd_signal=macd_signal_val,
            macd_cross_bull=macd_cross_bull,
            macd_cross_bear=macd_cross_bear,
            t3=t3_v,
            hlc_green=hlc_g,
            hlc_red=hlc_r,
            wae_up=w_up,
            wae_down=w_dn,
            adx=adx_v,
            optimized_ready=opt_ready,
            optimized_long_setup=opt_long,
            optimized_short_setup=opt_short,
            supertrend_long=st_long,
            supertrend_short=st_short,
            supertrend_bull=st_bull,
            squeeze_long=sq_long,
            squeeze_short=sq_short,
            qqe_long=qq_long,
            qqe_short=qq_short,
            utbot_long=ut_long,
            utbot_short=ut_short,
            utbot_bull=ut_bull,
            hull_long=hl_long,
            hull_short=hl_short,
            hull_bull=hl_bull,
            sar_chop_long_setup=sc_long,
            sar_chop_short_setup=sc_short,
            sar_value=sc_sar,
            chop_value=sc_chop,
            sar_chop_trail_bull=sc_trail_bull,
            ready=ready,
            mode_ready=_mode_ready,
            min_bars_ready_mode=_min_mode_bars,
        )

    @property
    def candle_count(self) -> int:
        return self._candle_count
