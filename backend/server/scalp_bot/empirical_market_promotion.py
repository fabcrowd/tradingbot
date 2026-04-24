"""Empirical promotion from limit entries to market.

- **Missed-move path:** repeated ``TTL cancel → favorable drift vs limit`` within a window
  arms a burst of market entries (pattern count + ``empirical_market_promotion_entries``).
- **TTL-direct path (optional):** ``empirical_market_ttl_cancel_arms_promotion`` adds
  ``empirical_market_ttl_cancel_promotion_entries`` market slots on **each** TTL cancel so
  the next signal(s) can fill with market without waiting for the pattern.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .scalp_config import ScalpBotConfig

LOG = logging.getLogger(__name__)


@dataclass
class _MissedMoveWatch:
    pair_key: str
    symbol: str
    direction: str
    limit_px: float
    deadline: float
    confirmed: bool = False


class EmpiricalMarketPromotion:
    """Tracks TTL cancels, detects missed favorable moves, arms temporary market entries."""

    def __init__(self, cfg: ScalpBotConfig) -> None:
        self._cfg = cfg
        self._watches: list[_MissedMoveWatch] = []
        # (monotonic_ts, pair_key) — pattern hits for rolling window count
        self._pattern_events: deque[tuple[float, str]] = deque(maxlen=256)
        self._promotion_remaining: dict[str, int] = {}
        self._last_arm_ts: dict[str, float] = {}

    def _enabled(self) -> bool:
        return bool(getattr(self._cfg, "empirical_market_promotion_enabled", False))

    def _miss_bps(self) -> float:
        return float(getattr(self._cfg, "empirical_market_missed_move_bps", 12.0) or 0.0)

    def _eval_window(self) -> float:
        return float(getattr(self._cfg, "empirical_market_miss_eval_window_sec", 600.0) or 1.0)

    def _min_pattern(self) -> int:
        return max(1, int(getattr(self._cfg, "empirical_market_min_pattern_in_window", 3)))

    def _pattern_window(self) -> float:
        return float(getattr(self._cfg, "empirical_market_pattern_window_sec", 86400.0) or 60.0)

    def _promotion_entries(self) -> int:
        return max(1, int(getattr(self._cfg, "empirical_market_promotion_entries", 2)))

    def _arm_cooldown(self) -> float:
        return float(getattr(self._cfg, "empirical_market_promotion_cooldown_sec", 3600.0) or 0.0)

    def update_cfg(self, cfg: ScalpBotConfig) -> None:
        self._cfg = cfg

    def resolve_order_type(self, pair_key: str) -> tuple[str, bool]:
        """Return (order_type, used_promotion) for the next entry."""
        base = str(getattr(self._cfg, "order_type", "limit") or "limit").lower().strip()
        if base == "hybrid":
            # Prefer maker first; empirical promotion upgrades to market after TTL + missed-move pattern.
            base = "limit"
        if base == "market":
            return "market", False
        if not self._enabled():
            return base, False
        if self._promotion_remaining.get(pair_key, 0) > 0:
            return "market", True
        return "limit", False

    def after_promoted_market_entry(self, pair_key: str) -> None:
        """Call after a venue entry was placed as market due to promotion."""
        n = self._promotion_remaining.get(pair_key, 0)
        if n <= 0:
            return
        self._promotion_remaining[pair_key] = n - 1
        if self._promotion_remaining[pair_key] <= 0:
            self._promotion_remaining.pop(pair_key, None)

    def note_entry_ttl_cancel(
        self,
        pair_key: str,
        symbol: str,
        direction: str,
        limit_px: float,
        mark_at_cancel: float,
        *,
        session_log: Any | None = None,
    ) -> None:
        """Start watching for favorable drift after a limit entry TTL cancel."""
        if not self._enabled():
            return
        lp = float(limit_px)
        if lp <= 0:
            return
        now = time.monotonic()
        deadline = now + self._eval_window()
        self._watches.append(
            _MissedMoveWatch(
                pair_key=pair_key,
                symbol=str(symbol or "").strip(),
                direction=str(direction or "long").lower(),
                limit_px=lp,
                deadline=deadline,
            )
        )
        LOG.info(
            "EmpiricalMarketPromotion %s: TTL cancel — watch missed-move until +%.0fs "
            "(limit=%.5f mark=%.5f dir=%s, need ≥%.1f bps favorable)",
            pair_key,
            self._eval_window(),
            lp,
            float(mark_at_cancel or 0.0),
            direction,
            self._miss_bps(),
        )
        if session_log is not None and hasattr(session_log, "log_scalp"):
            session_log.log_scalp(
                "entry_ttl_cancel",
                pair_key=pair_key,
                symbol=str(symbol or "").strip(),
                direction=direction,
                limit_price=round(lp, 8),
                mark_at_cancel=round(float(mark_at_cancel or 0.0), 8),
                eval_window_sec=round(self._eval_window(), 2),
                missed_move_bps=round(self._miss_bps(), 4),
            )
        self._maybe_arm_promotion_on_ttl_cancel(pair_key, session_log=session_log)

    def _maybe_arm_promotion_on_ttl_cancel(
        self, pair_key: str, *, session_log: Any | None = None
    ) -> None:
        """Arm market-entry burst immediately after TTL cancel (optional; see config)."""
        if not self._enabled():
            return
        if not bool(getattr(self._cfg, "empirical_market_ttl_cancel_arms_promotion", False)):
            return
        add = max(1, int(getattr(self._cfg, "empirical_market_ttl_cancel_promotion_entries", 1)))
        self._promotion_remaining[pair_key] = self._promotion_remaining.get(pair_key, 0) + add
        LOG.warning(
            "EmpiricalMarketPromotion %s: TTL cancel — armed %d market entry(ies) for next signal(s)",
            pair_key,
            add,
        )
        if session_log is not None and hasattr(session_log, "log_scalp"):
            session_log.log_scalp(
                "empirical_market_promotion_armed",
                pair_key=pair_key,
                promotion_entries=add,
                arm_reason="ttl_cancel",
                pattern_hits=0,
                pattern_window_sec=0.0,
            )

    def on_pair_mark(self, pair_key: str, mark: float, *, session_log: Any | None = None) -> None:
        """Feed mid/mark for a pair; confirm missed moves and maybe arm promotion."""
        now = time.monotonic()
        if not self._enabled():
            self._prune_pattern_events(now)
            return
        if mark <= 0:
            self._prune_expired_watches(now)
            self._prune_pattern_events(now)
            return

        mb = self._miss_bps()
        if mb <= 0:
            self._prune_expired_watches(now)
            self._prune_pattern_events(now)
            return

        new_watches: list[_MissedMoveWatch] = []
        for w in self._watches:
            if w.pair_key != pair_key:
                new_watches.append(w)
                continue
            if w.confirmed:
                continue
            if now > w.deadline:
                continue
            lp = w.limit_px
            if w.direction == "long":
                move_bps = (mark - lp) / lp * 10_000.0
            else:
                move_bps = (lp - mark) / lp * 10_000.0
            if move_bps >= mb:
                self._pattern_events.append((now, pair_key))
                LOG.warning(
                    "EmpiricalMarketPromotion %s: missed-move confirmed "
                    "(favorable %.1f bps vs limit %.5f, mark=%.5f, dir=%s)",
                    pair_key,
                    move_bps,
                    lp,
                    mark,
                    w.direction,
                )
                if session_log is not None and hasattr(session_log, "log_scalp"):
                    session_log.log_scalp(
                        "empirical_missed_move",
                        pair_key=pair_key,
                        direction=w.direction,
                        limit_price=round(lp, 8),
                        mark=round(mark, 8),
                        favorable_bps=round(move_bps, 4),
                        threshold_bps=round(mb, 4),
                    )
                self._try_arm_promotion(pair_key, session_log=session_log)
            else:
                new_watches.append(w)
        self._watches = new_watches
        self._prune_expired_watches(now)
        self._prune_pattern_events(now)

    def _prune_expired_watches(self, now: float) -> None:
        self._watches = [w for w in self._watches if not w.confirmed and now <= w.deadline]

    def _prune_pattern_events(self, now: float) -> None:
        pw = self._pattern_window()
        while self._pattern_events and now - self._pattern_events[0][0] > pw:
            self._pattern_events.popleft()

    def _try_arm_promotion(self, pair_key: str, *, session_log: Any | None = None) -> None:
        now = time.monotonic()
        self._prune_pattern_events(now)
        pw = self._pattern_window()
        cutoff = now - pw
        n = sum(1 for t, pk in self._pattern_events if pk == pair_key and t >= cutoff)
        if n < self._min_pattern():
            return
        cd = self._arm_cooldown()
        last = self._last_arm_ts.get(pair_key, 0.0)
        if cd > 0 and now - last < cd:
            LOG.info(
                "EmpiricalMarketPromotion %s: pattern met (%d/%d) but arm cooldown (%.0fs left)",
                pair_key,
                n,
                self._min_pattern(),
                cd - (now - last),
            )
            return
        self._last_arm_ts[pair_key] = now
        add = self._promotion_entries()
        self._promotion_remaining[pair_key] = self._promotion_remaining.get(pair_key, 0) + add
        LOG.warning(
            "EmpiricalMarketPromotion %s: armed %d market entry(ies) "
            "(%d missed-move hits in %.0fs window)",
            pair_key,
            add,
            n,
            pw,
        )
        if session_log is not None and hasattr(session_log, "log_scalp"):
            session_log.log_scalp(
                "empirical_market_promotion_armed",
                pair_key=pair_key,
                promotion_entries=add,
                pattern_hits=n,
                pattern_window_sec=round(pw, 2),
            )

    def dashboard_snapshot(self) -> dict[str, object]:
        """Operator / dashboard: promotion burst state and watch count."""
        now = time.monotonic()
        self._prune_expired_watches(now)
        self._prune_pattern_events(now)
        watches = sum(1 for w in self._watches if not w.confirmed and now <= w.deadline)
        return {
            "enabled": self._enabled(),
            "promotion_remaining": dict(self._promotion_remaining),
            "active_watch_count": watches,
            "pattern_buffer_len": len(self._pattern_events),
        }

    def active_watch_symbols(self) -> list[tuple[str, str]]:
        """(pair_key, product_id) for open watches — refresh marks even without a position."""
        out: list[tuple[str, str]] = []
        now = time.monotonic()
        for w in self._watches:
            if w.confirmed or now > w.deadline:
                continue
            if w.symbol:
                out.append((w.pair_key, w.symbol))
        return out
