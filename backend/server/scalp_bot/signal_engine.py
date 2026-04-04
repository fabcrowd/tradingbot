"""Signal confluence engine — evaluates indicators and produces ScalpSignals.

A signal is only generated when min_signals out of 4 align:
  1. EMA crossover (fast crossed above slow this candle)
  2. RSI in bullish zone (50-70)
  3. Price above session VWAP
  4. Volume spike confirmed

Only long signals are produced (Kraken spot = no shorting).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .indicators import IndicatorValues
from .scalp_config import ScalpPairConfig

LOG = logging.getLogger(__name__)


@dataclass
class ScalpSignal:
    pair_key: str
    symbol: str
    direction: str          # "long" (short not supported on spot)
    entry_price: float      # suggested limit entry (current close)
    stop_price: float       # ATR-based stop loss
    tp_price: float         # ATR-based take profit
    atr: float
    signals_hit: list[str]  # which signals fired
    confidence: float       # 0.0–1.0 (signals_hit / 4)
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class SignalEngine:
    def __init__(self) -> None:
        self._last_signal_ts: dict[str, float] = {}
        self._last_loss_ts: dict[str, float] = {}

    def record_loss(self, pair_key: str) -> None:
        """Call after a stopped-out trade to trigger the loss cooldown."""
        self._last_loss_ts[pair_key] = time.time()

    def evaluate(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
    ) -> ScalpSignal | None:
        """Evaluate indicators against confluence rules. Returns signal or None."""
        if not iv.ready:
            return None

        now = time.time()

        # Signal cooldown
        last_sig = self._last_signal_ts.get(pair_key, 0.0)
        if now - last_sig < cfg.signal_cooldown_sec:
            return None

        # Loss cooldown
        last_loss = self._last_loss_ts.get(pair_key, 0.0)
        if now - last_loss < cfg.loss_cooldown_sec:
            return None

        if iv.atr <= 0:
            return None

        # Evaluate each signal
        signals_hit: list[str] = []

        # 1. EMA crossover (strongest signal — fresh cross only)
        if iv.ema_crossed_up:
            signals_hit.append("ema_cross")
        elif iv.ema_bullish:
            # Continuing bullish trend also counts, just less fresh
            signals_hit.append("ema_trend")

        # 2. RSI in bullish zone
        if iv.rsi_bullish:
            signals_hit.append("rsi")

        # 3. Price above session VWAP
        if iv.vwap_bullish:
            signals_hit.append("vwap")

        # 4. Volume spike
        if iv.volume_confirmed:
            signals_hit.append("volume")

        if len(signals_hit) < cfg.min_signals:
            return None

        # Require at least ema_cross or ema_trend — don't trade without trend direction
        if "ema_cross" not in signals_hit and "ema_trend" not in signals_hit:
            return None

        entry = iv.close
        stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)

        if stop >= entry:
            LOG.debug("Signal %s: stop >= entry (atr=%.6f), skipping", pair_key, iv.atr)
            return None

        self._last_signal_ts[pair_key] = now
        confidence = len(signals_hit) / 4.0

        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction="long",
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=signals_hit,
            confidence=confidence,
        )
        LOG.info(
            "SIGNAL %s: long @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f | "
            "signals=%s | confidence=%.0f%%",
            pair_key, entry, stop, tp, iv.atr,
            "+".join(signals_hit), confidence * 100,
        )
        return signal
