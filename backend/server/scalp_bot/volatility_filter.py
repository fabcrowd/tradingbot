"""Two-step volume spike filter for execution risk-on (separate from regime / WFO risk-on).

1) **Prime** — a closed bar must pass normal ``volume_confirmed`` *and* clear a **high**
   volume multiple vs rolling volume MA (``volatility_spike_volume_mult``).

2) **Climax guard** — optional rejection of bars that look like one-way washout prints
   (bearish close pinned to the lows, or optional bullish exhaustion at the highs).

3) **Confirm** on the **next** closed bar — sustained participation (volume vs MA) *or*
   meaningful follow-through vs the spike close (ATR-scaled), so a single huge sell
   without continuation does not arm aggressive sizing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .candle_feed import Candle

if TYPE_CHECKING:
    from .indicators import IndicatorValues


def climax_reject_spike_bar(
    candle: Candle,
    *,
    bearish_frac: float,
    bullish_exhaust_frac: float,
    reject_bullish_exhaust: bool,
) -> bool:
    """True if this spike bar should **not** prime (likely one-print washout / exhaustion)."""
    h = float(candle.high)
    low = float(candle.low)
    if h <= low:
        return False
    c = float(candle.close)
    o = float(candle.open)
    rng = h - low
    pos = (c - low) / rng
    # Bearish climax: red bar, close stuck in bottom ``bearish_frac`` of the range.
    if c < o and pos <= bearish_frac:
        return True
    if reject_bullish_exhaust and c > o and pos >= bullish_exhaust_frac:
        return True
    return False


def eligible_spike_prime(
    iv: "IndicatorValues",
    candle: Candle,
    spike_volume_mult: float,
) -> bool:
    """High-threshold spike candidate: ready tape + normal volume_confirmed + spike mult."""
    if not iv.ready or iv.volume_ma <= 0.0:
        return False
    if not iv.volume_confirmed:
        return False
    return float(candle.volume) >= iv.volume_ma * float(spike_volume_mult)


def confirm_spike(
    spike: Candle,
    confirm: Candle,
    iv_confirm: "IndicatorValues",
    *,
    confirm_vol_mult: float,
    follow_atr_mult: float,
) -> bool:
    """Second closed bar validates the spike (volume stays elevated and/or real follow-through)."""
    vma = iv_confirm.volume_ma
    if vma > 0.0 and float(confirm.volume) >= vma * float(confirm_vol_mult):
        return True
    fatr = float(follow_atr_mult)
    if fatr > 0.0 and iv_confirm.atr > 0.0:
        move = abs(float(confirm.close) - float(spike.close))
        if move >= iv_confirm.atr * fatr:
            return True
    return False
