from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import BotConfig
from .models import Metrics, RiskConfig


@dataclass
class BotState:
    cfg: BotConfig
    running: bool = False
    paused: bool = True
    started_at: float = field(default_factory=time.time)
    last_update_ts: float = field(default_factory=time.time)
    metrics: Metrics = field(default_factory=Metrics)
    risk: RiskConfig = field(default_factory=lambda: RiskConfig(20.0, 0.5, 200.0))
    trades: list[dict[str, Any]] = field(default_factory=list)
    last_taker_signal: dict[str, Any] = field(default_factory=dict)
    feed_staleness: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: BotConfig) -> BotState:
        state = cls(cfg=cfg)
        state.metrics.mode = cfg.mode
        state.risk = RiskConfig(
            daily_loss_limit_usd=cfg.daily_loss_limit_usd,
            max_position_pct=cfg.max_position_pct,
            max_portfolio_exposure_usd=cfg.max_portfolio_exposure_usd,
        )
        return state

    def status(self) -> dict[str, Any]:
        now = time.time()
        return {
            "bot_id": self.cfg.bot_id,
            "display_name": self.cfg.display_name,
            "version": self.cfg.version,
            "env": self.cfg.env,
            "mode": self.metrics.mode,
            "running": self.running,
            "paused": self.paused,
            "uptime_sec": max(0.0, now - self.started_at),
            "last_update_ts": self.last_update_ts,
            "health": "ok",
            "last_taker_signal": self.last_taker_signal,
            "trade_count": len(self.trades),
            "open_positions": self.metrics.open_positions,
            "resolved_positions": self.metrics.resolved_positions,
            "pnl_total_usd": self.metrics.pnl_total_usd,
            "win_rate": self.metrics.win_rate,
            "feed_staleness": self.feed_staleness,
        }
