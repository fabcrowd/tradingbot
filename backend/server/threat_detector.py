"""Threat detection from live order book updates (fast, local, zero latency)."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Deque

from .state import PairState, ThreatLevel

if TYPE_CHECKING:
    from .config import AppConfig


@dataclass
class _PairWindow:
    last_mid: float = 0.0
    last_ts: float = 0.0
    spreads: Deque[float] = None  # type: ignore[assignment]
    mids: Deque[tuple[float, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.spreads is None:
            self.spreads = deque(maxlen=120)
        if self.mids is None:
            self.mids = deque(maxlen=120)


class ThreatDetector:
    """Compute per-pair ThreatLevel from order book snapshots."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._windows: dict[str, _PairWindow] = {}

    def _window(self, pair_key: str) -> _PairWindow:
        win = self._windows.get(pair_key)
        if win is None:
            win = _PairWindow()
            self._windows[pair_key] = win
        return win

    def update(self, pair_key: str, ps: PairState) -> None:
        """Update metrics and threat level from latest book for this pair."""
        now = time.time()
        mid = ps.mid_price
        if mid <= 0.0:
            ps.threat_level = ThreatLevel.ELEVATED
            return

        # Compute raw metrics from book.
        bid_vol = sum(l.volume for l in ps.bid_levels[:5])
        ask_vol = sum(l.volume for l in ps.ask_levels[:5])
        total_vol = bid_vol + ask_vol
        if total_vol > 0:
            imbalance = (bid_vol - ask_vol) / total_vol
        else:
            imbalance = 0.0

        spread = ps.spread
        win = self._window(pair_key)

        # Rolling windows for spread and mid-price.
        win.spreads.append(spread)
        win.mids.append((now, mid))

        avg_spread = (
            sum(win.spreads) / len(win.spreads) if win.spreads else spread or 0.0
        )
        spread_ratio = (spread / avg_spread) if avg_spread > 0 else 1.0

        # Mid-price velocity (bps over last ~10 seconds).
        velocity_bps = 0.0
        lookback_s = 10.0
        # Find a point at least lookback_s in the past, if present.
        for ts, m in reversed(win.mids):
            if now - ts >= lookback_s:
                if m > 0:
                    velocity_bps = abs(mid - m) / m * 10_000
                break

        # Tick volatility: stddev of mid returns (used for dashboard + A-S spread).
        vol_bps = 0.0
        realized_vol = 0.0
        if len(win.mids) >= 3:
            returns = []
            prev = None
            for _, m in win.mids:
                if prev is not None and prev > 0:
                    returns.append((m - prev) / prev)
                prev = m
            if returns:
                mean_r = sum(returns) / len(returns)
                var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
                std_r = var_r ** 0.5
                vol_bps = std_r * 10_000
                realized_vol = std_r

        # Persist metrics on PairState for dashboard / learner.
        ps.book_imbalance = imbalance
        ps.spread_blow_out_ratio = spread_ratio
        ps.mid_velocity_bps = velocity_bps
        ps.tick_volatility = vol_bps
        ps.realized_vol = realized_vol

        # Thresholds from config (with sensible defaults).
        bot = self._config.bot
        imb_thr = getattr(bot, "threat_imbalance_threshold", 0.5)
        spread_thr = getattr(bot, "threat_spread_blowout_ratio", 2.0)
        vel_thr = getattr(bot, "threat_velocity_bps", 15.0)
        crit_vel = getattr(bot, "threat_critical_velocity_bps", 50.0)

        level = ThreatLevel.CALM
        triggers = 0

        if abs(imbalance) >= imb_thr:
            triggers += 1
        if spread_ratio >= spread_thr:
            triggers += 1
        if velocity_bps >= vel_thr:
            triggers += 1

        if velocity_bps >= crit_vel and spread_ratio >= spread_thr:
            level = ThreatLevel.CRITICAL
        elif triggers >= 2 or spread_ratio >= spread_thr * 1.5:
            level = ThreatLevel.HIGH
        elif triggers >= 1:
            level = ThreatLevel.ELEVATED
        else:
            level = ThreatLevel.CALM

        ps.threat_level = level

