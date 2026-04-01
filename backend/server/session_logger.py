"""Session logger — writes structured JSONL events for overnight analysis.

One file per bot session: data/session_YYYYMMDD_HHMMSS.jsonl
Each line is a JSON event. Feed the file back for analysis.

Event types:
  session_start   — bot started, config summary
  fill            — order filled (buy or sell)
  learner         — learner made a spread adjustment
  pain_floor      — pain floor raised, lowered, or decayed
  snapshot        — periodic per-pair state summary (every 5 min)
  session_summary — written at shutdown; per-pair aggregate stats
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import AppConfig
    from .state import BotState

LOG = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class SessionLogger:
    """Writes one JSONL event per line to a per-session file."""

    def __init__(self, state: "BotState", config: "AppConfig") -> None:
        self._state = state
        self._config = config
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._path = DATA_DIR / f"session_{ts}.jsonl"
        self._file = open(self._path, "a", encoding="utf-8", buffering=1)  # line-buffered
        self._session_start = time.time()
        self._fill_counts: dict[str, int] = defaultdict(int)
        self._sell_counts: dict[str, int] = defaultdict(int)
        self._win_counts: dict[str, int] = defaultdict(int)
        self._pnl_totals: dict[str, float] = defaultdict(float)
        self._snapshot_task = None
        LOG.info("Session log: %s", self._path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_session_start(self) -> None:
        pairs = {}
        for key, pc in self._config.pairs.items():
            if key in self._config.pair_keys_for_trading():
                pairs[key] = {
                    "symbol": pc.symbol,
                    "spread_bps": pc.spread_bps,
                    "spread_floor_bps": pc.spread_floor_bps,
                    "fee_schedule": pc.fee_schedule,
                    "order_size": pc.order_size,
                    "max_inventory": pc.max_inventory,
                }
        self._write({
            "event": "session_start",
            "mode": self._config.mode,
            "pairs": pairs,
            "decay_start_sec": getattr(self._config.bot, "decay_start_sec", 90),
            "decay_interval_sec": getattr(self._config.bot, "decay_interval_sec", 60),
            "pain_floor_decay_hours": getattr(self._config.bot, "pain_floor_decay_hours", 4),
            "per_trade_profitability": getattr(self._config.bot, "per_trade_profitability", True),
            "daily_loss_limit": getattr(self._config.bot, "daily_loss_limit_usd", None),
            "daily_profit_target": getattr(self._config.bot, "daily_profit_target_usd", None),
        })

    def log_fill(
        self,
        pair: str,
        side: str,
        price: float,
        qty: float,
        fee: float,
        pnl: float,
        spread_bps: int,
        market_spread_bps: float = 0.0,
    ) -> None:
        self._fill_counts[pair] += 1
        if side == "sell":
            self._sell_counts[pair] += 1
            self._pnl_totals[pair] += pnl
            if pnl > 0:
                self._win_counts[pair] += 1
        self._write({
            "event": "fill",
            "pair": pair,
            "side": side,
            "price": round(price, 8),
            "qty": round(qty, 6),
            "fee": round(fee, 6),
            "pnl": round(pnl, 6),
            "spread_bps": spread_bps,
            "market_spread_bps": round(market_spread_bps, 1),
            "total_pnl": round(self._state.total_pnl, 6),
        })

    def log_learner(
        self,
        pair: str,
        action: str,
        spread_old: int,
        spread_new: int,
        reason: str = "",
        pain_floor: int = 0,
        idle_sec: float = 0.0,
        avg_sell_pnl: float = 0.0,
        ema_rate: float = 0.0,
        fills_interval: int = 0,
    ) -> None:
        self._write({
            "event": "learner",
            "pair": pair,
            "action": action,
            "spread_old": spread_old,
            "spread_new": spread_new,
            "reason": reason,
            "pain_floor": pain_floor,
            "idle_sec": round(idle_sec, 0),
            "avg_sell_pnl": round(avg_sell_pnl, 6),
            "ema_rate": round(ema_rate, 6),
            "fills_interval": fills_interval,
        })

    def log_pain_floor(
        self, pair: str, old: int, new: int, reason: str
    ) -> None:
        self._write({
            "event": "pain_floor",
            "pair": pair,
            "old": old,
            "new": new,
            "reason": reason,
        })

    def log_risk_halt(self, reason: str) -> None:
        self._write({
            "event": "risk_halt",
            "reason": reason,
            "total_pnl": round(self._state.total_pnl, 6),
            "peak_pnl": round(self._state.peak_pnl, 6),
        })

    def log_momentum(
        self, pair: str, active: bool, sells_in_window: int, window_sec: float
    ) -> None:
        self._write({
            "event": "momentum",
            "pair": pair,
            "active": active,
            "sells_in_window": sells_in_window,
            "window_sec": round(window_sec, 1),
        })

    def log_snapshot(self) -> None:
        now = time.time()
        pairs_data: dict[str, Any] = {}
        for key in self._config.pair_keys_for_trading():
            pc = self._config.pairs.get(key)
            ps = self._state.pairs.get(key)
            if pc is None or ps is None:
                continue
            sells = self._sell_counts[key]
            wins = self._win_counts[key]
            win_rate = (wins / sells * 100) if sells > 0 else 0.0
            last_fill = self._state.last_fill_ts.get(key, 0.0)
            pairs_data[key] = {
                "spread_bps": pc.spread_bps,
                "fills": self._fill_counts[key],
                "sells": sells,
                "win_rate": round(win_rate, 1),
                "pnl": round(self._pnl_totals[key], 6),
                "market_spread_bps": round(
                    (ps.best_ask - ps.best_bid) / ps.mid_price * 10_000
                    if ps.mid_price > 0 else 0, 1
                ),
                "last_fill_age_sec": round(now - last_fill, 0) if last_fill > 0 else None,
            }
        self._write({
            "event": "snapshot",
            "session_sec": round(now - self._session_start, 0),
            "total_pnl": round(self._state.total_pnl, 6),
            "pairs": pairs_data,
        })

    def write_summary(self) -> None:
        now = time.time()
        duration = now - self._session_start
        pairs_summary: dict[str, Any] = {}
        for key in self._config.pair_keys_for_trading():
            pc = self._config.pairs.get(key)
            if pc is None:
                continue
            fills = self._fill_counts[key]
            sells = self._sell_counts[key]
            wins = self._win_counts[key]
            pnl = self._pnl_totals[key]
            win_rate = (wins / sells * 100) if sells > 0 else 0.0
            pnl_per_hour = (pnl / duration * 3600) if duration > 0 else 0.0
            fills_per_hour = (fills / duration * 3600) if duration > 0 else 0.0
            pairs_summary[key] = {
                "symbol": pc.symbol,
                "final_spread_bps": pc.spread_bps,
                "fills": fills,
                "sells": sells,
                "wins": wins,
                "win_rate_pct": round(win_rate, 1),
                "total_pnl": round(pnl, 6),
                "pnl_per_hour": round(pnl_per_hour, 6),
                "fills_per_hour": round(fills_per_hour, 2),
            }
        self._write({
            "event": "session_summary",
            "duration_sec": round(duration, 0),
            "total_pnl": round(self._state.total_pnl, 6),
            "total_fills": sum(self._fill_counts.values()),
            "total_sells": sum(self._sell_counts.values()),
            "pairs": pairs_summary,
            "file": str(self._path),
        })
        LOG.info("Session summary written to %s", self._path)

    def close(self) -> None:
        try:
            self.write_summary()
        except Exception:
            LOG.debug("Error writing session summary", exc_info=True)
        try:
            self._file.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write(self, data: dict) -> None:
        data["ts"] = round(time.time(), 3)
        try:
            self._file.write(json.dumps(data) + "\n")
        except Exception:
            LOG.debug("Session log write failed", exc_info=True)
