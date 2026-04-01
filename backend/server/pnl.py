"""P&L tracker — persists trades to JSONL, replays on startup.

Paper and live trades are stored in separate files so P&L never mixes.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .fee_schedule import current_tier_info, maker_fee_bps
from .state import TradeRecord

if TYPE_CHECKING:
    from .state import BotState

LOG = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
MAX_RECENT_FILLS = 500
THIRTY_DAYS_SEC = 30 * 86400


def _trades_file(mode: str) -> Path:
    return DATA_DIR / f"trades_{mode}.jsonl"


class PnLTracker:
    def __init__(self, state: BotState, mode: str = "paper") -> None:
        self._state = state
        self._mode = mode
        self._trades_file = _trades_file(mode)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._replay()

    def _replay(self) -> None:
        if not self._trades_file.exists():
            LOG.info("No %s trade history found, starting fresh", self._mode)
            return

        count = 0
        cutoff = time.time() - THIRTY_DAYS_SEC
        volume_30d = 0.0

        with open(self._trades_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    self._apply_record(rec, persist=False)
                    if rec.get("timestamp", 0) >= cutoff:
                        volume_30d += rec["price"] * rec["qty"]
                    count += 1
                except (json.JSONDecodeError, KeyError) as e:
                    LOG.warning("Skipping bad trade record: %s", e)

        self._state.volume_30d = volume_30d
        fee = maker_fee_bps(volume_30d)
        LOG.info(
            "[%s] Replayed %d fills | P&L: $%.4f | Sells: %d | Win: %.1f%% | "
            "30d vol: $%.0f | Fee: %d bps",
            self._mode.upper(), count, self._state.total_pnl,
            self._state.total_trades, self._state.win_rate, volume_30d, fee,
        )
        tier = current_tier_info(volume_30d)
        if tier.get("next_tier_threshold"):
            LOG.info(
                "Next fee tier at $%.0f (need $%.0f more, %.1f%% progress)",
                tier["next_tier_threshold"],
                tier["volume_to_next_tier"],
                tier["progress_pct"],
            )

    def switch_mode(self, new_mode: str) -> None:
        """Reset all P&L state and replay from the new mode's trade file."""
        self._mode = new_mode
        self._trades_file = _trades_file(new_mode)

        s = self._state
        s.recent_fills.clear()
        s.total_pnl = 0.0
        s.total_trades = 0
        s.total_wins = 0
        s.fill_event_count = 0
        s.spread_captured = 0.0
        s.pnl_curve.clear()
        s.volume_30d = 0.0

        self._replay()
        LOG.info("Switched P&L tracking to %s mode", new_mode)

    def record_fill(
        self,
        pair_key: str,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        fee: float,
        pnl_delta: float,
        *,
        gross_spread: float | None = None,
    ) -> TradeRecord:
        now = time.time()
        rec_dict: dict = {
            "timestamp": now,
            "pair_key": pair_key,
            "symbol": symbol,
            "side": side,
            "price": price,
            "qty": qty,
            "fee": fee,
            "pnl_delta": pnl_delta,
        }
        # Enrich with spread/book snapshot for learner (no sentiment).
        ps = self._state.pairs.get(pair_key)
        if ps is not None:
            rec_dict["spread_bps"] = getattr(
                self._state, "current_spread_bps", None,
            )
            rec_dict["book_imbalance"] = getattr(ps, "book_imbalance", 0.0)
            rec_dict["mid_velocity_bps"] = getattr(ps, "mid_velocity_bps", 0.0)
        tm = time.gmtime(now)
        rec_dict["hour_utc"] = tm.tm_hour
        if gross_spread is not None:
            rec_dict["gross_spread"] = gross_spread
        self._apply_record(rec_dict, persist=True)
        return self._state.recent_fills[-1]

    def _apply_record(self, rec: dict, persist: bool = True) -> None:
        trade = TradeRecord(
            timestamp=rec["timestamp"],
            pair_key=rec["pair_key"],
            symbol=rec["symbol"],
            side=rec["side"],
            price=rec["price"],
            qty=rec["qty"],
            fee=rec["fee"],
            pnl_delta=rec["pnl_delta"],
            spread_bps=rec.get("spread_bps"),
        )
        self._state.recent_fills.append(trade)
        if len(self._state.recent_fills) > MAX_RECENT_FILLS:
            self._state.recent_fills = self._state.recent_fills[-MAX_RECENT_FILLS:]

        self._state.fill_event_count += 1
        self._state.total_pnl += trade.pnl_delta
        if persist:
            self._state.volume_30d += trade.price * trade.qty

        side = trade.side
        if side == "sell":
            self._state.total_trades += 1
            if trade.pnl_delta > 0:
                self._state.total_wins += 1
            gs = rec.get("gross_spread")
            if gs is not None:
                self._state.spread_captured += float(gs)
            else:
                # Legacy rows: approximate economic edge before fees
                self._state.spread_captured += max(0.0, trade.pnl_delta + trade.fee)
        else:
            # Buy leg: optional legacy double-count guard
            pass

        self._state.pnl_curve.append((trade.timestamp, self._state.total_pnl))

        if persist:
            self._persist(rec)

    def _persist(self, rec: dict) -> None:
        try:
            with open(self._trades_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError as e:
            LOG.error("Failed to persist trade: %s", e)
