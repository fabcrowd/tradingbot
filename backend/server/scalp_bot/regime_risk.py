"""Regime detection for risk-on scheduling (WFO cadence, bootstrap window, Nemesis gates).

Closed-bar triggers use ``IndicatorValues`` only. Live triggers use the last closed ``iv``
(ATR, volume MA) plus the **forming** candle from the feed and optional short-window
price velocity (bps range / mid).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .candle_feed import Candle
from .indicators import IndicatorValues

if TYPE_CHECKING:
    from .scalp_config import ScalpBotConfig


def regime_risk_on_triggers(iv: IndicatorValues, cfg: "ScalpBotConfig") -> list[str]:
    """Return non-empty reason tags when this bar qualifies as elevated activity."""
    if not bool(getattr(cfg, "regime_risk_on_enabled", True)):
        return []
    reasons: list[str] = []
    vm = float(getattr(cfg, "regime_volume_spike_mult", 2.5))
    if iv.volume_ma > 0.0 and iv.volume >= iv.volume_ma * vm:
        reasons.append("volume_spike")
    atr_m = float(getattr(cfg, "regime_price_move_atr_mult", 1.75))
    if iv.atr > 0.0 and iv.prev_close > 0.0:
        move = abs(iv.close - iv.prev_close)
        if move >= iv.atr * atr_m:
            reasons.append("large_atr_move")
    pct_thr = float(getattr(cfg, "regime_price_move_min_pct", 0.0))
    if pct_thr > 0.0 and iv.prev_close > 0.0:
        pct = abs(iv.close - iv.prev_close) / iv.prev_close * 100.0
        if pct >= pct_thr:
            reasons.append("large_pct_move")
    return reasons


def regime_risk_on_triggers_live(
    iv: IndicatorValues,
    live: Candle,
    cfg: "ScalpBotConfig",
    *,
    live_velocity_bps: float = 0.0,
) -> list[str]:
    """Intrabar / tick-path regime tags — extends the same global risk-on window as closed bars."""
    if not bool(getattr(cfg, "regime_risk_on_enabled", True)):
        return []
    if not bool(getattr(cfg, "regime_live_vol_enabled", True)):
        return []
    reasons: list[str] = []
    vm = float(getattr(cfg, "regime_volume_spike_mult", 2.5))
    # Forming-bar cumulative volume vs MA from last *closed* bar (spike shows up immediately).
    if bool(getattr(cfg, "regime_live_use_volume", True)):
        if iv.volume_ma > 0.0 and live.volume >= iv.volume_ma * vm:
            reasons.append("live_volume_spike")
    range_m = float(getattr(cfg, "regime_live_range_atr_mult", 1.75))
    if iv.atr > 0.0 and live.high > 0.0 and live.low > 0.0:
        bar_range = float(live.high) - float(live.low)
        if bar_range >= iv.atr * range_m:
            reasons.append("live_range_atr")
    vel_min = float(getattr(cfg, "regime_live_velocity_min_bps", 20.0))
    win_sec = float(getattr(cfg, "regime_live_velocity_window_sec", 45.0))
    if win_sec > 0.0 and vel_min > 0.0 and live_velocity_bps >= vel_min:
        reasons.append("live_velocity_bps")
    return reasons
