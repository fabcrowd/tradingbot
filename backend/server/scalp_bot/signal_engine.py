"""Signal engine — evaluates indicators and produces ScalpSignals.

Supports strategy modes selected by the WFO / config:
  - daviddtech_scalp: Optimized Strategy — T3 + HLC trend + WAE + ADX
  - ema_momentum: fast/slow EMA cross only (+ ATR for stops/TP)
  - rsi_reversion: Buy at RSI oversold, sell when RSI recovers
  - ema_scalp: Tony's EMA Scalper — 20 EMA cross + trend direction, S/R from 8-bar high/low
  - macd_scalp: Scalp Pro — Ehlers super-smoother MACD crossover
  - supertrend / squeeze_momentum / qqe_mod / utbot_alert / hull_suite: TV-style trend modes
    (hull_suite = Hull Suite Strategy Hma: long if HMA > HMA[2], short if HMA < HMA[2])
  - sar_chop: TV "5 min bot scalper" decode — PSAR (+ Lucid SAR) flip,
    CHOP regime filter, MA(200)/MA(50) trend, MACD(12,26,9) hist, UT Bot ATR trail gate

Candles are driven per pair by ``ScalpPairConfig.interval`` (default **5** minutes).

Long+short: shorts require ``shorts_enabled`` on the scalp bot config (Coinbase CDE perps).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from .indicators import IndicatorValues
from .scalp_config import ScalpPairConfig

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Entry pipeline (closed bar) — order matters
# ---------------------------------------------------------------------------
# 1) SignalEngine.evaluate: signal / loss cooldowns → ATR>0
# 2) Then strategy mode handler (``_eval_*``) — each mode uses its own masks from
#    ``scalp_vec_backtest.detect_signals_*`` / live bundles; there is **no** global RSI/EMA/MACD
#    chart filter before the mode runs (avoid duplicating logic WFO did not score).
# 3) ScalpRuntime stacks risk/portfolio gates, warmup, champion source, etc. **before** calling
#    evaluate — see ``scalp_runtime._on_closed_candle``.
# ``regime_adx_filter`` was removed: ``daviddtech_scalp`` already requires ``adx > adx_threshold``
# inside ``detect_signals_daviddtech`` / ``daviddtech_live_bundle``; an extra ADX floor here caused
# live/WFO skew when ``regime_adx_filter`` was enabled.


@dataclass
class ScalpSignal:
    pair_key: str
    symbol: str
    direction: str          # "long" | "short"
    entry_price: float
    stop_price: float
    tp_price: float
    atr: float
    signals_hit: list[str]
    confidence: float       # 0.0–1.0
    mode: str = "ema_momentum"
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class SignalEngine:
    def __init__(self) -> None:
        self._last_signal_ts: dict[str, float] = {}
        self._last_loss_ts: dict[str, float] = {}
        self._last_tick_signal_ts: dict[str, float] = {}
        self._shorts_enabled: bool = False

    @staticmethod
    def _dbg_skip(pair_key: str, reason: str, **details: object) -> None:
        """Set env ``SCALP_LOG_SIGNAL_SKIPS=1`` to log skips at INFO (diagnose zero-entry days)."""
        verbose = os.environ.get("SCALP_LOG_SIGNAL_SKIPS", "").strip().lower() in ("1", "true", "yes")
        log_fn = LOG.info if verbose else LOG.debug
        if details:
            tail = " ".join(f"{k}={v!r}" for k, v in details.items())
            log_fn("SIGNAL_SKIP %s: %s | %s", pair_key, reason, tail)
        else:
            log_fn("SIGNAL_SKIP %s: %s", pair_key, reason)

    def record_loss(self, pair_key: str) -> None:
        self._last_loss_ts[pair_key] = time.time()

    def record_win(self, pair_key: str) -> None:
        pass

    # ------------------------------------------------------------------
    # Public dispatcher — called by the runtime on each closed candle
    # ------------------------------------------------------------------

    def evaluate(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        *,
        mode_override: str | None = None,
        shorts_enabled: bool = False,
        signal_cooldown_sec: float | None = None,
    ) -> ScalpSignal | None:
        now = time.time()
        self._shorts_enabled = bool(shorts_enabled)
        sig_cd = float(cfg.signal_cooldown_sec if signal_cooldown_sec is None else signal_cooldown_sec)
        if now - self._last_signal_ts.get(pair_key, 0.0) < sig_cd:
            rem = sig_cd - (now - self._last_signal_ts.get(pair_key, 0.0))
            self._dbg_skip(pair_key, "signal_cooldown", remaining_sec=round(rem, 1))
            return None
        if now - self._last_loss_ts.get(pair_key, 0.0) < cfg.loss_cooldown_sec:
            rem = cfg.loss_cooldown_sec - (now - self._last_loss_ts.get(pair_key, 0.0))
            self._dbg_skip(pair_key, "loss_cooldown", remaining_sec=round(rem, 1))
            return None
        if iv.atr <= 0:
            self._dbg_skip(pair_key, "invalid_atr", atr=iv.atr)
            return None

        mode = mode_override or cfg.strategy_mode
        if mode == "auto":
            mode = str(getattr(cfg, "auto_mode_fallback", "sar_chop") or "sar_chop").strip()
            if mode == "auto":
                mode = "sar_chop"

        if mode == "daviddtech_scalp":
            if not iv.optimized_ready:
                self._dbg_skip(pair_key, "optimized_not_ready")
                return None
            if not iv.mode_ready:
                self._dbg_skip(pair_key, "mode_not_ready", min_bars=getattr(iv, "min_bars_ready_mode", 0))
                return None
            return self._eval_daviddtech_scalp(pair_key, symbol, cfg, iv, now)
        if not iv.ready:
            self._dbg_skip(pair_key, "indicators_not_ready")
            return None
        if not iv.mode_ready:
            self._dbg_skip(pair_key, "mode_not_ready", min_bars=getattr(iv, "min_bars_ready_mode", 0))
            return None
        if mode == "rsi_reversion":
            sig = self._eval_rsi_reversion(pair_key, symbol, cfg, iv, now)
            if sig is None and self._shorts_enabled:
                sig = self._eval_rsi_reversion_short(pair_key, symbol, cfg, iv, now)
            return sig
        if mode == "ema_scalp":
            sig = self._eval_ema_scalp(pair_key, symbol, cfg, iv, now)
            if sig is None and self._shorts_enabled:
                sig = self._eval_ema_scalp_short(pair_key, symbol, cfg, iv, now)
            return sig
        if mode == "macd_scalp":
            sig = self._eval_macd_scalp(pair_key, symbol, cfg, iv, now)
            if sig is None and self._shorts_enabled:
                sig = self._eval_macd_scalp_short(pair_key, symbol, cfg, iv, now)
            return sig
        if mode == "supertrend":
            sig = self._eval_supertrend(pair_key, symbol, cfg, iv, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_supertrend(pair_key, symbol, cfg, iv, now, "short")
            return sig
        if mode == "squeeze_momentum":
            sig = self._eval_squeeze(pair_key, symbol, cfg, iv, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_squeeze(pair_key, symbol, cfg, iv, now, "short")
            return sig
        if mode == "qqe_mod":
            sig = self._eval_qqe(pair_key, symbol, cfg, iv, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_qqe(pair_key, symbol, cfg, iv, now, "short")
            return sig
        if mode == "utbot_alert":
            sig = self._eval_utbot(pair_key, symbol, cfg, iv, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_utbot(pair_key, symbol, cfg, iv, now, "short")
            return sig
        if mode == "hull_suite":
            sig = self._eval_hull(pair_key, symbol, cfg, iv, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_hull(pair_key, symbol, cfg, iv, now, "short")
            return sig
        if mode == "sar_chop":
            sig = self._eval_sar_chop(pair_key, symbol, cfg, iv, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_sar_chop(pair_key, symbol, cfg, iv, now, "short")
            return sig
        if mode == "ema_momentum":
            sig = self._eval_ema_momentum(pair_key, symbol, cfg, iv, now)
            if sig is None and self._shorts_enabled:
                sig = self._eval_ema_momentum_short(pair_key, symbol, cfg, iv, now)
            return sig
        LOG.error("SignalEngine.evaluate: unknown mode %r for %s — no signal", mode, pair_key)
        return None

    def evaluate_counter(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        current_direction: str,
        *,
        mode_override: str | None = None,
        shorts_enabled: bool = False,
    ) -> ScalpSignal | None:
        """Evaluate for a counter-signal while a position is open.

        Bypasses signal_cooldown and loss_cooldown (the position itself is the
        gate — we're already in a trade).  Only returns a signal whose direction
        is opposite to ``current_direction``; same-direction signals are ignored
        so this can never accidentally pyramid an existing position.
        """
        if iv.atr <= 0:
            return None
        self._shorts_enabled = bool(shorts_enabled)
        now = time.time()

        mode = mode_override or cfg.strategy_mode
        if mode == "auto":
            mode = str(getattr(cfg, "auto_mode_fallback", "sar_chop") or "sar_chop").strip()
            if mode == "auto":
                mode = "sar_chop"

        if mode == "daviddtech_scalp":
            if not iv.optimized_ready or not iv.mode_ready:
                return None
            signal = self._eval_daviddtech_scalp(pair_key, symbol, cfg, iv, now)
        elif not iv.ready or not iv.mode_ready:
            return None
        elif mode == "rsi_reversion":
            signal = self._eval_rsi_reversion(pair_key, symbol, cfg, iv, now)
            if signal is None and shorts_enabled:
                signal = self._eval_rsi_reversion_short(pair_key, symbol, cfg, iv, now)
        elif mode == "ema_scalp":
            signal = self._eval_ema_scalp(pair_key, symbol, cfg, iv, now)
            if signal is None and shorts_enabled:
                signal = self._eval_ema_scalp_short(pair_key, symbol, cfg, iv, now)
        elif mode == "macd_scalp":
            signal = self._eval_macd_scalp(pair_key, symbol, cfg, iv, now)
            if signal is None and shorts_enabled:
                signal = self._eval_macd_scalp_short(pair_key, symbol, cfg, iv, now)
        elif mode == "supertrend":
            signal = self._eval_supertrend(pair_key, symbol, cfg, iv, now, "long")
            if signal is None and shorts_enabled:
                signal = self._eval_supertrend(pair_key, symbol, cfg, iv, now, "short")
        elif mode == "squeeze_momentum":
            signal = self._eval_squeeze(pair_key, symbol, cfg, iv, now, "long")
            if signal is None and shorts_enabled:
                signal = self._eval_squeeze(pair_key, symbol, cfg, iv, now, "short")
        elif mode == "qqe_mod":
            signal = self._eval_qqe(pair_key, symbol, cfg, iv, now, "long")
            if signal is None and shorts_enabled:
                signal = self._eval_qqe(pair_key, symbol, cfg, iv, now, "short")
        elif mode == "utbot_alert":
            signal = self._eval_utbot(pair_key, symbol, cfg, iv, now, "long")
            if signal is None and shorts_enabled:
                signal = self._eval_utbot(pair_key, symbol, cfg, iv, now, "short")
        elif mode == "hull_suite":
            signal = self._eval_hull(pair_key, symbol, cfg, iv, now, "long")
            if signal is None and shorts_enabled:
                signal = self._eval_hull(pair_key, symbol, cfg, iv, now, "short")
        elif mode == "sar_chop":
            signal = self._eval_sar_chop(pair_key, symbol, cfg, iv, now, "long")
            if signal is None and shorts_enabled:
                signal = self._eval_sar_chop(pair_key, symbol, cfg, iv, now, "short")
        elif mode == "ema_momentum":
            signal = self._eval_ema_momentum(pair_key, symbol, cfg, iv, now)
            if signal is None and shorts_enabled:
                signal = self._eval_ema_momentum_short(pair_key, symbol, cfg, iv, now)
        else:
            LOG.error("SignalEngine.evaluate_counter: unknown mode %r for %s — no signal", mode, pair_key)
            return None

        if signal is None:
            return None
        if signal.direction == current_direction:
            # Same direction — not a counter-signal; don't pyramid
            return None
        return signal

    # ------------------------------------------------------------------
    # Optimized Strategy (DaviddTech-style) — spot: long only
    # ------------------------------------------------------------------

    def _eval_daviddtech_scalp(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float,
    ) -> ScalpSignal | None:
        if iv.optimized_long_setup:
            entry = iv.close
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "daviddtech_long_invalid_stop", entry=entry, stop=stop)
                return None

            self._last_signal_ts[pair_key] = now
            signals_hit = ["t3", "hlc", "wae", "adx"]
            confidence = min(
                1.0,
                0.45
                + (0.15 if iv.adx > cfg.adx_threshold else 0.0)
                + (0.15 if iv.wae_up > iv.wae_down else 0.0)
                + (0.15 if iv.hlc_green > iv.hlc_red else 0.0)
                + (0.1 if entry > iv.t3 else 0.0),
            )

            signal = ScalpSignal(
                pair_key=pair_key, symbol=symbol, direction="long",
                entry_price=entry, stop_price=stop, tp_price=tp,
                atr=iv.atr, signals_hit=signals_hit,
                confidence=confidence, mode="daviddtech_scalp",
            )
            LOG.info(
                "SIGNAL [optimized] %s: long @ %.5f | stop=%.5f | tp=%.5f | t3=%.5f adx=%.1f | atr=%.5f",
                pair_key, entry, stop, tp, iv.t3, iv.adx, iv.atr,
            )
            return signal

        if self._shorts_enabled and iv.optimized_short_setup:
            entry = iv.close
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "daviddtech_short_invalid_stop", entry=entry, stop=stop)
                return None

            self._last_signal_ts[pair_key] = now
            signals_hit = ["t3", "hlc", "wae", "adx", "short"]
            confidence = min(
                1.0,
                0.45
                + (0.15 if iv.adx > cfg.adx_threshold else 0.0)
                + (0.15 if iv.wae_down > iv.wae_up else 0.0)
                + (0.15 if iv.hlc_red > iv.hlc_green else 0.0)
                + (0.1 if entry < iv.t3 else 0.0),
            )
            signal = ScalpSignal(
                pair_key=pair_key, symbol=symbol, direction="short",
                entry_price=entry, stop_price=stop, tp_price=tp,
                atr=iv.atr, signals_hit=signals_hit,
                confidence=confidence, mode="daviddtech_scalp",
            )
            LOG.info(
                "SIGNAL [optimized] %s: short @ %.5f | stop=%.5f | tp=%.5f | t3=%.5f adx=%.1f | atr=%.5f",
                pair_key, entry, stop, tp, iv.t3, iv.adx, iv.atr,
            )
            return signal

        self._dbg_skip(pair_key, "daviddtech_no_setup")
        return None

    # ------------------------------------------------------------------
    # Tick-level entry (price action on frozen last-bar indicators)
    # ------------------------------------------------------------------

    def evaluate_tick(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        *,
        mode_override: str | None = None,
        shorts_enabled: bool = False,
        tick_signal_cooldown_sec: float = 300.0,
        signal_cooldown_sec: float | None = None,
    ) -> ScalpSignal | None:
        """Optional mid-bar entry using last closed-bar indicator snapshot + live price.

        Does not call IndicatorSet.update — ``iv`` must be the frozen values from the
        last candle close. Respects ``signal_cooldown_sec`` (or ``signal_cooldown_sec``
        override) and ``loss_cooldown_sec``, plus ``tick_signal_cooldown_sec``.
        """
        now = time.time()
        self._shorts_enabled = bool(shorts_enabled)

        if now - self._last_tick_signal_ts.get(pair_key, 0.0) < tick_signal_cooldown_sec:
            rem = tick_signal_cooldown_sec - (now - self._last_tick_signal_ts.get(pair_key, 0.0))
            self._dbg_skip(pair_key, "tick_signal_cooldown", remaining_sec=round(rem, 1))
            return None
        sig_cd = float(cfg.signal_cooldown_sec if signal_cooldown_sec is None else signal_cooldown_sec)
        if now - self._last_signal_ts.get(pair_key, 0.0) < sig_cd:
            rem = sig_cd - (now - self._last_signal_ts.get(pair_key, 0.0))
            self._dbg_skip(pair_key, "signal_cooldown_tick", remaining_sec=round(rem, 1))
            return None
        if now - self._last_loss_ts.get(pair_key, 0.0) < cfg.loss_cooldown_sec:
            rem = cfg.loss_cooldown_sec - (now - self._last_loss_ts.get(pair_key, 0.0))
            self._dbg_skip(pair_key, "loss_cooldown_tick", remaining_sec=round(rem, 1))
            return None
        if iv.atr <= 0:
            self._dbg_skip(pair_key, "invalid_atr_tick", atr=iv.atr)
            return None

        mode = mode_override or cfg.strategy_mode
        if mode == "auto":
            mode = str(getattr(cfg, "auto_mode_fallback", "sar_chop") or "sar_chop").strip()
            if mode == "auto":
                mode = "sar_chop"

        if mode == "daviddtech_scalp":
            if not iv.optimized_ready:
                self._dbg_skip(pair_key, "tick_optimized_not_ready")
                return None
            return self._eval_tick_daviddtech(pair_key, symbol, cfg, iv, live_price, now)
        if not iv.ready:
            self._dbg_skip(pair_key, "tick_indicators_not_ready")
            return None
        if mode == "rsi_reversion":
            sig = self._eval_tick_rsi_reversion(pair_key, symbol, cfg, iv, live_price, now)
            if sig is None and self._shorts_enabled:
                sig = self._eval_tick_rsi_reversion_short(pair_key, symbol, cfg, iv, live_price, now)
            return sig
        if mode == "ema_scalp":
            sig = self._eval_tick_ema_scalp_long(pair_key, symbol, cfg, iv, live_price, now)
            if sig is None and self._shorts_enabled:
                sig = self._eval_tick_ema_scalp_short(pair_key, symbol, cfg, iv, live_price, now)
            return sig
        if mode == "macd_scalp":
            sig = self._eval_tick_macd_scalp_long(pair_key, symbol, cfg, iv, live_price, now)
            if sig is None and self._shorts_enabled:
                sig = self._eval_tick_macd_scalp_short(pair_key, symbol, cfg, iv, live_price, now)
            return sig
        if mode == "qqe_mod":
            return self._eval_tick_qqe_mod(pair_key, symbol, cfg, iv, live_price, now)
        if mode == "utbot_alert":
            sig = self._eval_tick_utbot(pair_key, symbol, cfg, iv, live_price, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_tick_utbot(pair_key, symbol, cfg, iv, live_price, now, "short")
            return sig
        if mode == "supertrend":
            sig = self._eval_tick_supertrend(pair_key, symbol, cfg, iv, live_price, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_tick_supertrend(pair_key, symbol, cfg, iv, live_price, now, "short")
            return sig
        if mode == "squeeze_momentum":
            sig = self._eval_tick_squeeze(pair_key, symbol, cfg, iv, live_price, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_tick_squeeze(pair_key, symbol, cfg, iv, live_price, now, "short")
            return sig
        if mode == "hull_suite":
            sig = self._eval_tick_hull(pair_key, symbol, cfg, iv, live_price, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_tick_hull(pair_key, symbol, cfg, iv, live_price, now, "short")
            return sig
        if mode == "sar_chop":
            sig = self._eval_tick_sar_chop(pair_key, symbol, cfg, iv, live_price, now, "long")
            if sig is None and self._shorts_enabled:
                sig = self._eval_tick_sar_chop(pair_key, symbol, cfg, iv, live_price, now, "short")
            return sig
        if mode == "ema_momentum":
            sig = self._eval_tick_ema_momentum(pair_key, symbol, cfg, iv, live_price, now)
            if sig is None and self._shorts_enabled:
                sig = self._eval_tick_ema_momentum_short(pair_key, symbol, cfg, iv, live_price, now)
            return sig
        LOG.error("SignalEngine.evaluate_tick: unknown mode %r for %s — no signal", mode, pair_key)
        return None

    def _mark_tick_signal(self, pair_key: str, now: float) -> None:
        self._last_signal_ts[pair_key] = now
        self._last_tick_signal_ts[pair_key] = now

    def _eval_tick_qqe_mod(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
    ) -> ScalpSignal | None:
        """QQE state from last closed bar; entry at live tick price (Coinbase feed)."""
        ep = float(live_price)
        if iv.qqe_long:
            entry = ep
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "tick_qqe_long_invalid_stop", entry=entry, stop=stop)
                return None
            self._mark_tick_signal(pair_key, now)
            signal = ScalpSignal(
                pair_key=pair_key,
                symbol=symbol,
                direction="long",
                entry_price=entry,
                stop_price=stop,
                tp_price=tp,
                atr=iv.atr,
                signals_hit=["qqe_trail_cross", "tick_entry"],
                confidence=0.68,
                mode="qqe_mod",
            )
            LOG.info(
                "SIGNAL [qqe_mod tick] %s: long @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
                pair_key, entry, stop, tp, iv.atr,
            )
            return signal
        if self._shorts_enabled and iv.qqe_short:
            entry = ep
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "tick_qqe_short_invalid_stop", entry=entry, stop=stop)
                return None
            self._mark_tick_signal(pair_key, now)
            signal = ScalpSignal(
                pair_key=pair_key,
                symbol=symbol,
                direction="short",
                entry_price=entry,
                stop_price=stop,
                tp_price=tp,
                atr=iv.atr,
                signals_hit=["qqe_trail_cross", "tick_entry"],
                confidence=0.68,
                mode="qqe_mod",
            )
            LOG.info(
                "SIGNAL [qqe_mod tick] %s: short @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
                pair_key, entry, stop, tp, iv.atr,
            )
            return signal
        self._dbg_skip(pair_key, "tick_qqe_no_setup")
        return None

    def _eval_tick_utbot(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
        direction: str,
    ) -> ScalpSignal | None:
        ep = float(live_price)
        if direction == "long":
            if not iv.utbot_long:
                self._dbg_skip(pair_key, "tick_utbot_no_long_flip")
                return None
            entry = ep
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "tick_utbot_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.utbot_short:
                self._dbg_skip(pair_key, "tick_utbot_no_short_flip")
                return None
            entry = ep
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "tick_utbot_short_invalid_stop", entry=entry, stop=stop)
                return None
        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["utbot_trail_flip", "tick_entry"],
            confidence=0.72,
            mode="utbot_alert",
        )
        LOG.info(
            "SIGNAL [utbot_alert tick] %s: %s @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.atr,
        )
        return signal

    def _eval_tick_supertrend(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
        direction: str,
    ) -> ScalpSignal | None:
        ep = float(live_price)
        if direction == "long":
            if not iv.supertrend_long:
                self._dbg_skip(pair_key, "tick_supertrend_no_long")
                return None
            entry = ep
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "tick_supertrend_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.supertrend_short:
                self._dbg_skip(pair_key, "tick_supertrend_no_short")
                return None
            entry = ep
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "tick_supertrend_short_invalid_stop", entry=entry, stop=stop)
                return None
        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["supertrend_flip", "tick_entry"],
            confidence=0.68,
            mode="supertrend",
        )
        LOG.info(
            "SIGNAL [supertrend tick] %s: %s @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.atr,
        )
        return signal

    def _eval_tick_squeeze(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
        direction: str,
    ) -> ScalpSignal | None:
        ep = float(live_price)
        if direction == "long":
            if not iv.squeeze_long:
                self._dbg_skip(pair_key, "tick_squeeze_no_long")
                return None
            entry = ep
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "tick_squeeze_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.squeeze_short:
                self._dbg_skip(pair_key, "tick_squeeze_no_short")
                return None
            entry = ep
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "tick_squeeze_short_invalid_stop", entry=entry, stop=stop)
                return None
        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["squeeze_momentum_cross", "tick_entry"],
            confidence=0.68,
            mode="squeeze_momentum",
        )
        LOG.info(
            "SIGNAL [squeeze tick] %s: %s @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.atr,
        )
        return signal

    def _eval_tick_hull(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
        direction: str,
    ) -> ScalpSignal | None:
        ep = float(live_price)
        if direction == "long":
            if not iv.hull_long:
                self._dbg_skip(pair_key, "tick_hull_no_long")
                return None
            entry = ep
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "tick_hull_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.hull_short:
                self._dbg_skip(pair_key, "tick_hull_no_short")
                return None
            entry = ep
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "tick_hull_short_invalid_stop", entry=entry, stop=stop)
                return None
        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["hull_hma_vs_lag2", "tick_entry"],
            confidence=0.68,
            mode="hull_suite",
        )
        LOG.info(
            "SIGNAL [hull_suite tick] %s: %s @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.atr,
        )
        return signal

    def _eval_tick_sar_chop(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
        direction: str,
    ) -> ScalpSignal | None:
        ep = float(live_price)
        if direction == "long":
            if not iv.sar_chop_long_setup:
                self._dbg_skip(pair_key, "tick_sar_chop_no_long_setup")
                return None
            entry = ep
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "tick_sar_chop_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.sar_chop_short_setup:
                self._dbg_skip(pair_key, "tick_sar_chop_no_short_setup")
                return None
            entry = ep
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "tick_sar_chop_short_invalid_stop", entry=entry, stop=stop)
                return None
        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["psar_flip", "chop_trending", "ma_trend", "macd_hist", "tick_entry"],
            confidence=0.7,
            mode="sar_chop",
        )
        LOG.info(
            "SIGNAL [sar_chop tick] %s: %s @ %.5f | stop=%.5f | tp=%.5f | chop=%.1f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.chop_value, iv.atr,
        )
        return signal

    def _eval_tick_daviddtech(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
    ) -> ScalpSignal | None:
        """Breakout continuation: trend filters from last bar + price vs close/T3."""
        adx_ok = iv.adx >= cfg.adx_threshold * 0.85
        if iv.hlc_green > iv.hlc_red and adx_ok and live_price > max(iv.close, iv.t3):
            entry = live_price
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "tick_daviddtech_long_invalid_stop", entry=entry, stop=stop)
                return None
            self._mark_tick_signal(pair_key, now)
            signal = ScalpSignal(
                pair_key=pair_key,
                symbol=symbol,
                direction="long",
                entry_price=entry,
                stop_price=stop,
                tp_price=tp,
                atr=iv.atr,
                signals_hit=["tick_breakout", "hlc", "adx", "t3"],
                confidence=0.55,
                mode="daviddtech_scalp",
            )
            LOG.info(
                "SIGNAL_TICK [optimized] %s: long @ %.5f | stop=%.5f | tp=%.5f | adx=%.1f",
                pair_key,
                entry,
                stop,
                tp,
                iv.adx,
            )
            return signal

        if (
            self._shorts_enabled
            and iv.hlc_red > iv.hlc_green
            and adx_ok
            and live_price < min(iv.close, iv.t3)
        ):
            entry = live_price
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "tick_daviddtech_short_invalid_stop", entry=entry, stop=stop)
                return None
            self._mark_tick_signal(pair_key, now)
            signal = ScalpSignal(
                pair_key=pair_key,
                symbol=symbol,
                direction="short",
                entry_price=entry,
                stop_price=stop,
                tp_price=tp,
                atr=iv.atr,
                signals_hit=["tick_breakdown", "hlc", "adx", "t3", "short"],
                confidence=0.55,
                mode="daviddtech_scalp",
            )
            LOG.info(
                "SIGNAL_TICK [optimized] %s: short @ %.5f | stop=%.5f | tp=%.5f | adx=%.1f",
                pair_key,
                entry,
                stop,
                tp,
                iv.adx,
            )
            return signal

        self._dbg_skip(
            pair_key,
            "tick_daviddtech_no_trigger",
            live_price=live_price,
            close=iv.close,
            t3=iv.t3,
            adx=iv.adx,
        )
        return None

    def _eval_tick_ema_momentum(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
    ) -> ScalpSignal | None:
        if not iv.ema_crossed_up:
            self._dbg_skip(pair_key, "tick_ema_momentum_no_ema_cross_up")
            return None

        entry = live_price
        stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
        if stop >= entry:
            self._dbg_skip(pair_key, "tick_ema_momentum_invalid_stop", entry=entry, stop=stop)
            return None

        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction="long",
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["ema_cross"],
            confidence=1.0,
            mode="ema_momentum",
        )
        LOG.info(
            "SIGNAL_TICK [ema] %s: long @ %.5f | stop=%.5f | tp=%.5f | ema_cross",
            pair_key,
            entry,
            stop,
            tp,
        )
        return signal

    def _eval_tick_ema_momentum_short(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
    ) -> ScalpSignal | None:
        if not iv.ema_crossed_down:
            self._dbg_skip(pair_key, "tick_ema_momentum_no_ema_cross_down")
            return None

        entry = live_price
        stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
        if stop <= entry:
            self._dbg_skip(pair_key, "tick_ema_momentum_short_invalid_stop", entry=entry, stop=stop)
            return None

        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction="short",
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["ema_cross_down"],
            confidence=1.0,
            mode="ema_momentum",
        )
        LOG.info(
            "SIGNAL_TICK [ema] %s: short @ %.5f | stop=%.5f | tp=%.5f | ema_cross_down",
            pair_key,
            entry,
            stop,
            tp,
        )
        return signal

    def _eval_tick_rsi_reversion(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
    ) -> ScalpSignal | None:
        if iv.rsi is None or iv.rsi > cfg.rsi_buy_threshold:
            self._dbg_skip(pair_key, "tick_rsi_not_oversold", rsi=iv.rsi, threshold=cfg.rsi_buy_threshold)
            return None
        if live_price >= iv.close:
            self._dbg_skip(pair_key, "tick_rsi_no_dip", live_price=live_price, close=iv.close)
            return None

        entry = live_price
        stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
        if stop >= entry:
            self._dbg_skip(pair_key, "tick_rsi_invalid_stop", entry=entry, stop=stop)
            return None

        self._mark_tick_signal(pair_key, now)
        confidence = max(0.0, 1.0 - iv.rsi / cfg.rsi_buy_threshold)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction="long",
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["rsi_oversold", "tick_dip"],
            confidence=confidence,
            mode="rsi_reversion",
        )
        LOG.info(
            "SIGNAL_TICK [rsi] %s: long @ %.5f | rsi=%.1f | close=%.5f",
            pair_key,
            entry,
            iv.rsi,
            iv.close,
        )
        return signal

    def _eval_tick_rsi_reversion_short(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
    ) -> ScalpSignal | None:
        if iv.rsi is None or iv.rsi < cfg.rsi_sell_threshold:
            self._dbg_skip(pair_key, "tick_rsi_not_overbought", rsi=iv.rsi, threshold=cfg.rsi_sell_threshold)
            return None
        if live_price <= iv.close:
            self._dbg_skip(pair_key, "tick_rsi_no_rally", live_price=live_price, close=iv.close)
            return None

        entry = live_price
        stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
        if stop <= entry:
            self._dbg_skip(pair_key, "tick_rsi_short_invalid_stop", entry=entry, stop=stop)
            return None

        self._mark_tick_signal(pair_key, now)
        rsi_range = max(1.0, 100.0 - cfg.rsi_sell_threshold)
        confidence = max(0.0, (iv.rsi - cfg.rsi_sell_threshold) / rsi_range)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction="short",
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["rsi_overbought", "tick_rally"],
            confidence=confidence,
            mode="rsi_reversion",
        )
        LOG.info(
            "SIGNAL_TICK [rsi] %s: short @ %.5f | rsi=%.1f | close=%.5f",
            pair_key,
            entry,
            iv.rsi,
            iv.close,
        )
        return signal

    def _eval_tick_ema_scalp_long(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
    ) -> ScalpSignal | None:
        # Last closed bar ended at/below EMA; live price now above (intra-bar cross up).
        if not (live_price > iv.ema_scalp and iv.close <= iv.ema_scalp):
            self._dbg_skip(
                pair_key,
                "tick_ema_scalp_no_long_cross",
                live_price=live_price,
                ema_scalp=iv.ema_scalp,
                last_close=iv.close,
            )
            return None

        entry = live_price
        stop_from_sr = iv.low_8 if iv.low_8 > 0 and iv.low_8 < entry else entry - iv.atr * cfg.atr_stop_mult
        stop_from_atr = entry - iv.atr * cfg.atr_stop_mult
        stop = round(max(stop_from_sr, stop_from_atr), 6)
        tp_from_sr = iv.high_8 if iv.high_8 > entry else entry + iv.atr * cfg.atr_tp_mult
        tp = round(tp_from_sr, 6)
        if stop >= entry:
            self._dbg_skip(pair_key, "tick_ema_scalp_long_invalid_stop", entry=entry, stop=stop)
            return None

        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction="long",
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["tick_ema_cross_up", "trend_up"],
            confidence=0.75,
            mode="ema_scalp",
        )
        LOG.info(
            "SIGNAL_TICK [ema_scalp] %s: long @ %.5f | ema20=%.5f",
            pair_key,
            entry,
            iv.ema_scalp,
        )
        return signal

    def _eval_tick_ema_scalp_short(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
    ) -> ScalpSignal | None:
        if not (live_price < iv.ema_scalp and iv.close >= iv.ema_scalp):
            self._dbg_skip(
                pair_key,
                "tick_ema_scalp_no_short_cross",
                live_price=live_price,
                ema_scalp=iv.ema_scalp,
                last_close=iv.close,
            )
            return None

        entry = live_price
        stop_from_sr = iv.high_8 if iv.high_8 > entry else entry + iv.atr * cfg.atr_stop_mult
        stop_from_atr = entry + iv.atr * cfg.atr_stop_mult
        stop = round(max(stop_from_sr, stop_from_atr), 6)
        tp_from_sr = iv.low_8 if iv.low_8 < entry else entry - iv.atr * cfg.atr_tp_mult
        tp_from_atr = entry - iv.atr * cfg.atr_tp_mult
        tp = round(min(tp_from_sr, tp_from_atr), 6)
        if stop <= entry:
            self._dbg_skip(pair_key, "tick_ema_scalp_short_invalid_stop", entry=entry, stop=stop)
            return None

        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction="short",
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["tick_ema_cross_down", "trend_down"],
            confidence=0.75,
            mode="ema_scalp",
        )
        LOG.info(
            "SIGNAL_TICK [ema_scalp] %s: short @ %.5f | ema20=%.5f",
            pair_key,
            entry,
            iv.ema_scalp,
        )
        return signal

    def _eval_tick_macd_scalp_long(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
    ) -> ScalpSignal | None:
        if not (iv.macd_line > iv.macd_signal and live_price > iv.close):
            self._dbg_skip(
                pair_key,
                "tick_macd_no_long",
                macd=iv.macd_line,
                signal=iv.macd_signal,
                live_price=live_price,
                close=iv.close,
            )
            return None

        entry = live_price
        stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
        if stop >= entry:
            self._dbg_skip(pair_key, "tick_macd_long_invalid_stop", entry=entry, stop=stop)
            return None

        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction="long",
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["macd_bull_align", "tick_breakout"],
            confidence=0.7,
            mode="macd_scalp",
        )
        LOG.info(
            "SIGNAL_TICK [macd_scalp] %s: long @ %.5f | macd=%.2f sig=%.2f",
            pair_key,
            entry,
            iv.macd_line,
            iv.macd_signal,
        )
        return signal

    def _eval_tick_macd_scalp_short(
        self,
        pair_key: str,
        symbol: str,
        cfg: ScalpPairConfig,
        iv: IndicatorValues,
        live_price: float,
        now: float,
    ) -> ScalpSignal | None:
        if not (iv.macd_line < iv.macd_signal and live_price < iv.close):
            self._dbg_skip(
                pair_key,
                "tick_macd_no_short",
                macd=iv.macd_line,
                signal=iv.macd_signal,
                live_price=live_price,
                close=iv.close,
            )
            return None

        entry = live_price
        stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
        if stop <= entry:
            self._dbg_skip(pair_key, "tick_macd_short_invalid_stop", entry=entry, stop=stop)
            return None

        self._mark_tick_signal(pair_key, now)
        signal = ScalpSignal(
            pair_key=pair_key,
            symbol=symbol,
            direction="short",
            entry_price=entry,
            stop_price=stop,
            tp_price=tp,
            atr=iv.atr,
            signals_hit=["macd_bear_align", "tick_breakdown"],
            confidence=0.7,
            mode="macd_scalp",
        )
        LOG.info(
            "SIGNAL_TICK [macd_scalp] %s: short @ %.5f | macd=%.2f sig=%.2f",
            pair_key,
            entry,
            iv.macd_line,
            iv.macd_signal,
        )
        return signal

    # ------------------------------------------------------------------
    # EMA momentum mode
    # ------------------------------------------------------------------

    def _eval_ema_momentum(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float,
    ) -> ScalpSignal | None:
        if not iv.ema_crossed_up:
            self._dbg_skip(pair_key, "ema_momentum_no_ema_cross_up")
            return None

        entry = iv.close
        stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
        if stop >= entry:
            self._dbg_skip(pair_key, "ema_momentum_invalid_stop", entry=entry, stop=stop)
            return None

        self._last_signal_ts[pair_key] = now
        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction="long",
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["ema_cross"],
            confidence=1.0, mode="ema_momentum",
        )
        LOG.info(
            "SIGNAL [ema] %s: long @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f | ema_cross",
            pair_key, entry, stop, tp, iv.atr,
        )
        return signal

    def _eval_ema_momentum_short(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float,
    ) -> ScalpSignal | None:
        if not iv.ema_crossed_down:
            self._dbg_skip(pair_key, "ema_momentum_no_ema_cross_down")
            return None

        entry = iv.close
        stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
        if stop <= entry:
            self._dbg_skip(pair_key, "ema_momentum_short_invalid_stop", entry=entry, stop=stop)
            return None

        self._last_signal_ts[pair_key] = now
        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction="short",
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["ema_cross_down"],
            confidence=1.0, mode="ema_momentum",
        )
        LOG.info(
            "SIGNAL [ema] %s: short @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f | ema_cross_down",
            pair_key, entry, stop, tp, iv.atr,
        )
        return signal

    # ------------------------------------------------------------------
    # RSI reversion mode
    # ------------------------------------------------------------------

    def _eval_rsi_reversion(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float,
    ) -> ScalpSignal | None:
        if iv.rsi is None or iv.rsi > cfg.rsi_buy_threshold:
            self._dbg_skip(
                pair_key,
                "rsi_reversion_not_oversold",
                rsi=iv.rsi,
                threshold=cfg.rsi_buy_threshold,
            )
            return None

        entry = iv.close
        stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
        if stop >= entry:
            self._dbg_skip(pair_key, "rsi_reversion_invalid_stop", entry=entry, stop=stop)
            return None

        self._last_signal_ts[pair_key] = now
        confidence = max(0.0, 1.0 - iv.rsi / cfg.rsi_buy_threshold)

        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction="long",
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["rsi_oversold"],
            confidence=confidence, mode="rsi_reversion",
        )
        LOG.info(
            "SIGNAL [rsi] %s: long @ %.5f | stop=%.5f | rsi=%.1f <= %.1f | atr=%.5f | conf=%.0f%%",
            pair_key, entry, stop, iv.rsi, cfg.rsi_buy_threshold, iv.atr, confidence * 100,
        )
        return signal

    def _eval_rsi_reversion_short(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float,
    ) -> ScalpSignal | None:
        if iv.rsi is None or iv.rsi < cfg.rsi_sell_threshold:
            self._dbg_skip(
                pair_key,
                "rsi_reversion_not_overbought",
                rsi=iv.rsi,
                threshold=cfg.rsi_sell_threshold,
            )
            return None

        entry = iv.close
        stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
        if stop <= entry:
            self._dbg_skip(pair_key, "rsi_reversion_short_invalid_stop", entry=entry, stop=stop)
            return None

        self._last_signal_ts[pair_key] = now
        rsi_range = max(1.0, 100.0 - cfg.rsi_sell_threshold)
        confidence = max(0.0, (iv.rsi - cfg.rsi_sell_threshold) / rsi_range)

        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction="short",
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["rsi_overbought"],
            confidence=confidence, mode="rsi_reversion",
        )
        LOG.info(
            "SIGNAL [rsi] %s: short @ %.5f | stop=%.5f | rsi=%.1f >= %.1f | atr=%.5f | conf=%.0f%%",
            pair_key, entry, stop, iv.rsi, cfg.rsi_sell_threshold, iv.atr, confidence * 100,
        )
        return signal

    # ------------------------------------------------------------------
    # EMA scalp mode (Tony's EMA Scalper)
    # ------------------------------------------------------------------

    def _eval_ema_scalp(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float,
    ) -> ScalpSignal | None:
        if not iv.ema_scalp_cross_bull:
            self._dbg_skip(pair_key, "ema_scalp_no_bull_cross")
            return None

        entry = iv.close
        # Stop at 8-bar support, floored by ATR
        stop_from_sr = iv.low_8 if iv.low_8 > 0 and iv.low_8 < entry else entry - iv.atr * cfg.atr_stop_mult
        stop_from_atr = entry - iv.atr * cfg.atr_stop_mult
        stop = round(max(stop_from_sr, stop_from_atr), 6)

        # TP at 8-bar resistance
        tp_from_sr = iv.high_8 if iv.high_8 > entry else entry + iv.atr * cfg.atr_tp_mult
        tp = round(tp_from_sr, 6)

        if stop >= entry:
            self._dbg_skip(pair_key, "ema_scalp_long_invalid_stop", entry=entry, stop=stop)
            return None

        self._last_signal_ts[pair_key] = now

        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction="long",
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["ema_cross_bull", "trend_up"],
            confidence=0.8, mode="ema_scalp",
        )
        LOG.info(
            "SIGNAL [ema_scalp] %s: long @ %.5f | stop=%.5f (low8=%.5f) | "
            "tp=%.5f (high8=%.5f) | ema20=%.5f | atr=%.5f",
            pair_key, entry, stop, iv.low_8, tp, iv.high_8, iv.ema_scalp, iv.atr,
        )
        return signal

    def _eval_ema_scalp_short(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float,
    ) -> ScalpSignal | None:
        if not iv.ema_scalp_cross_bear:
            self._dbg_skip(pair_key, "ema_scalp_no_bear_cross")
            return None

        entry = iv.close
        stop_from_sr = iv.high_8 if iv.high_8 > entry else entry + iv.atr * cfg.atr_stop_mult
        stop_from_atr = entry + iv.atr * cfg.atr_stop_mult
        stop = round(max(stop_from_sr, stop_from_atr), 6)

        tp_from_sr = iv.low_8 if iv.low_8 < entry else entry - iv.atr * cfg.atr_tp_mult
        tp_from_atr = entry - iv.atr * cfg.atr_tp_mult
        tp = round(min(tp_from_sr, tp_from_atr), 6)

        if stop <= entry:
            self._dbg_skip(pair_key, "ema_scalp_short_invalid_stop", entry=entry, stop=stop)
            return None

        self._last_signal_ts[pair_key] = now

        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction="short",
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["ema_cross_bear", "trend_down"],
            confidence=0.8, mode="ema_scalp",
        )
        LOG.info(
            "SIGNAL [ema_scalp] %s: short @ %.5f | stop=%.5f | tp=%.5f | ema20=%.5f | atr=%.5f",
            pair_key, entry, stop, tp, iv.ema_scalp, iv.atr,
        )
        return signal

    # ------------------------------------------------------------------
    # MACD scalp mode (Scalp Pro — Ehlers super-smoother)
    # ------------------------------------------------------------------

    def _eval_macd_scalp(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float,
    ) -> ScalpSignal | None:
        if not iv.macd_cross_bull:
            self._dbg_skip(pair_key, "macd_scalp_no_bull_cross")
            return None

        entry = iv.close
        stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
        if stop >= entry:
            self._dbg_skip(pair_key, "macd_scalp_long_invalid_stop", entry=entry, stop=stop)
            return None

        self._last_signal_ts[pair_key] = now

        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction="long",
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["macd_cross_bull"],
            confidence=0.75, mode="macd_scalp",
        )
        LOG.info(
            "SIGNAL [macd_scalp] %s: long @ %.5f | stop=%.5f | tp=%.5f | "
            "macd=%.2f | signal=%.2f | atr=%.5f",
            pair_key, entry, stop, tp, iv.macd_line, iv.macd_signal, iv.atr,
        )
        return signal

    def _eval_macd_scalp_short(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float,
    ) -> ScalpSignal | None:
        if not iv.macd_cross_bear:
            self._dbg_skip(pair_key, "macd_scalp_no_bear_cross")
            return None

        entry = iv.close
        stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
        tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
        if stop <= entry:
            self._dbg_skip(pair_key, "macd_scalp_short_invalid_stop", entry=entry, stop=stop)
            return None

        self._last_signal_ts[pair_key] = now

        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction="short",
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["macd_cross_bear"],
            confidence=0.75, mode="macd_scalp",
        )
        LOG.info(
            "SIGNAL [macd_scalp] %s: short @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
            pair_key, entry, stop, tp, iv.atr,
        )
        return signal

    # ------------------------------------------------------------------
    # Supertrend
    # ------------------------------------------------------------------

    def _eval_supertrend(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float, direction: str,
    ) -> ScalpSignal | None:
        if direction == "long":
            if not iv.supertrend_long:
                self._dbg_skip(pair_key, "supertrend_no_long_flip")
                return None
            entry = iv.close
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "supertrend_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.supertrend_short:
                self._dbg_skip(pair_key, "supertrend_no_short_flip")
                return None
            entry = iv.close
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "supertrend_short_invalid_stop", entry=entry, stop=stop)
                return None

        self._last_signal_ts[pair_key] = now
        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction=direction,
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["supertrend_flip"],
            confidence=0.75, mode="supertrend",
        )
        LOG.info(
            "SIGNAL [supertrend] %s: %s @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.atr,
        )
        return signal

    # ------------------------------------------------------------------
    # Squeeze Momentum
    # ------------------------------------------------------------------

    def _eval_squeeze(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float, direction: str,
    ) -> ScalpSignal | None:
        if direction == "long":
            if not iv.squeeze_long:
                self._dbg_skip(pair_key, "squeeze_no_long")
                return None
            entry = iv.close
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "squeeze_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.squeeze_short:
                self._dbg_skip(pair_key, "squeeze_no_short")
                return None
            entry = iv.close
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "squeeze_short_invalid_stop", entry=entry, stop=stop)
                return None

        self._last_signal_ts[pair_key] = now
        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction=direction,
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["squeeze_momentum_cross"],
            confidence=0.7, mode="squeeze_momentum",
        )
        LOG.info(
            "SIGNAL [squeeze] %s: %s @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.atr,
        )
        return signal

    # ------------------------------------------------------------------
    # QQE Mod
    # ------------------------------------------------------------------

    def _eval_qqe(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float, direction: str,
    ) -> ScalpSignal | None:
        if direction == "long":
            if not iv.qqe_long:
                self._dbg_skip(pair_key, "qqe_no_long")
                return None
            entry = iv.close
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "qqe_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.qqe_short:
                self._dbg_skip(pair_key, "qqe_no_short")
                return None
            entry = iv.close
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "qqe_short_invalid_stop", entry=entry, stop=stop)
                return None

        self._last_signal_ts[pair_key] = now
        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction=direction,
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["qqe_trail_cross"],
            confidence=0.7, mode="qqe_mod",
        )
        LOG.info(
            "SIGNAL [qqe_mod] %s: %s @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.atr,
        )
        return signal

    # ------------------------------------------------------------------
    # UT Bot Alert (Chandelier Exit)
    # ------------------------------------------------------------------

    def _eval_utbot(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float, direction: str,
    ) -> ScalpSignal | None:
        if direction == "long":
            if not iv.utbot_long:
                self._dbg_skip(pair_key, "utbot_no_long_flip")
                return None
            entry = iv.close
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "utbot_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.utbot_short:
                self._dbg_skip(pair_key, "utbot_no_short_flip")
                return None
            entry = iv.close
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "utbot_short_invalid_stop", entry=entry, stop=stop)
                return None

        self._last_signal_ts[pair_key] = now
        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction=direction,
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["utbot_trail_flip"],
            confidence=0.75, mode="utbot_alert",
        )
        LOG.info(
            "SIGNAL [utbot_alert] %s: %s @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.atr,
        )
        return signal

    # ------------------------------------------------------------------
    # Hull Suite
    # ------------------------------------------------------------------

    def _eval_hull(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float, direction: str,
    ) -> ScalpSignal | None:
        if direction == "long":
            if not iv.hull_long:
                self._dbg_skip(pair_key, "hull_trend_down_no_long")
                return None
            entry = iv.close
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "hull_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.hull_short:
                self._dbg_skip(pair_key, "hull_no_short_flip")
                return None
            entry = iv.close
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "hull_short_invalid_stop", entry=entry, stop=stop)
                return None

        self._last_signal_ts[pair_key] = now
        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction=direction,
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["hull_hma_vs_lag2"],
            confidence=0.7, mode="hull_suite",
        )
        LOG.info(
            "SIGNAL [hull_suite] %s: %s @ %.5f | stop=%.5f | tp=%.5f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.atr,
        )
        return signal

    # ------------------------------------------------------------------
    # SAR + CHOP (TV "5 min bot scalper" decode)
    # ------------------------------------------------------------------

    def _eval_sar_chop(
        self, pair_key: str, symbol: str, cfg: ScalpPairConfig,
        iv: IndicatorValues, now: float, direction: str,
    ) -> ScalpSignal | None:
        if direction == "long":
            if not iv.sar_chop_long_setup:
                self._dbg_skip(pair_key, "sar_chop_no_long_setup")
                return None
            entry = iv.close
            stop = round(entry - iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry + iv.atr * cfg.atr_tp_mult, 6)
            if stop >= entry:
                self._dbg_skip(pair_key, "sar_chop_long_invalid_stop", entry=entry, stop=stop)
                return None
        else:
            if not iv.sar_chop_short_setup:
                self._dbg_skip(pair_key, "sar_chop_no_short_setup")
                return None
            entry = iv.close
            stop = round(entry + iv.atr * cfg.atr_stop_mult, 6)
            tp = round(entry - iv.atr * cfg.atr_tp_mult, 6)
            if stop <= entry:
                self._dbg_skip(pair_key, "sar_chop_short_invalid_stop", entry=entry, stop=stop)
                return None
        self._last_signal_ts[pair_key] = now
        signal = ScalpSignal(
            pair_key=pair_key, symbol=symbol, direction=direction,
            entry_price=entry, stop_price=stop, tp_price=tp,
            atr=iv.atr, signals_hit=["psar_flip", "chop_trending", "ma_trend", "macd_hist"],
            confidence=0.74, mode="sar_chop",
        )
        LOG.info(
            "SIGNAL [sar_chop] %s: %s @ %.5f | stop=%.5f | tp=%.5f | sar=%.5f chop=%.1f | atr=%.5f",
            pair_key, direction, entry, stop, tp, iv.sar_value, iv.chop_value, iv.atr,
        )
        return signal
