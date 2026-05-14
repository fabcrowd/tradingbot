"""News Trade Manager — state machine for economic-event front-running.

Watches the Forex Factory event calendar.  For each High-impact event that
enters the ``news_watch_window_min`` look-ahead window:

  1. Fetches directional advice from ``news_ai_advisor`` (DDG search + keyword
     scoring, refreshed every ``news_advisor_refresh_min`` minutes).
  2. When confidence ≥ ``news_ai_confidence_threshold`` and the event is
     within ``news_front_run_entry_min`` minutes (but not closer than
     ``news_front_run_cutoff_min``), emits a ``NewsAction`` per pair.
  3. Tracks which events have already fired to prevent duplicate entries.
  4. Expires event watches once the post-event look-behind window closes.

State machine per event (not per pair):

    IDLE ──(within watch window)──► WATCHING
    WATCHING ──(advice ≥ threshold)──► PRIMED
    PRIMED ──(within entry window)──► ARMED
    ARMED ──(entry emitted)──► FIRED
    Any ──(event expired)──► IDLE (entry removed from watch dict)

``NewsTradeManager.tick()`` is called from ``ScalpRuntime``'s 60-second
heartbeat.  It returns a list of ``NewsAction`` objects; the runtime converts
each action into a ``ScalpSignal`` and calls ``_open_position``.

Reversals (close existing position → open opposite) are deferred to a future
iteration; v1 only enters when no position is already open for the pair.
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .news_ai_advisor import NewsAdvice
    from .scalp_config import ScalpBotConfig

LOG = logging.getLogger(__name__)


class _WatchState(enum.Enum):
    WATCHING = "watching"   # event in window, advice pending or weak
    PRIMED = "primed"       # advice ≥ threshold, waiting for entry window
    ARMED = "armed"         # within entry window, ready to fire
    FIRED = "fired"         # entry action emitted — do not fire again


@dataclass
class _EventWatch:
    event_id: str
    event: dict                        # raw calendar dict with _ts field
    state: _WatchState = _WatchState.WATCHING
    advice: "NewsAdvice | None" = None
    last_log_at: float = 0.0


@dataclass
class NewsAction:
    """Entry instruction emitted by ``NewsTradeManager.tick()``."""
    pair_key: str
    direction: str      # "long" | "short"
    event_id: str
    event_title: str
    confidence: int
    sl_atr_mult: float
    tp_atr_mult: float
    phase: str          # "pre_event"


class NewsTradeManager:
    """Stateful manager; one instance per ``ScalpRuntime``."""

    def __init__(self, cfg: "ScalpBotConfig") -> None:
        self._cfg = cfg
        self._watches: dict[str, _EventWatch] = {}  # event_id → watch

    # ── Public API ────────────────────────────────────────────────────────────

    async def tick(
        self,
        now: float,
        events: list[dict],
        open_pair_keys: set[str],
        all_pair_keys: list[str],
    ) -> list[NewsAction]:
        """Advance state machine and return any entry actions to execute.

        Parameters
        ----------
        now:            current unix timestamp
        events:         upcoming events from ``news_calendar.upcoming_events()``
                        (already filtered for impact / currency; include ``_ts``)
        open_pair_keys: pair keys with a currently open position (skip entry)
        all_pair_keys:  all configured pair keys
        """
        if not bool(getattr(self._cfg, "news_front_run_enabled", True)):
            return []

        cfg = self._cfg
        watch_min = float(getattr(cfg, "news_watch_window_min", 60.0))
        entry_min = float(getattr(cfg, "news_front_run_entry_min", 10.0))
        cutoff_min = float(getattr(cfg, "news_front_run_cutoff_min", 2.0))
        threshold = int(getattr(cfg, "news_ai_confidence_threshold", 65))
        sl_pre = float(getattr(cfg, "news_front_run_sl_atr_mult", 0.4))
        tp_pre = float(getattr(cfg, "news_front_run_tp_atr_mult", 1.5))
        refresh_min = float(getattr(cfg, "news_advisor_refresh_min", 20.0))
        post_min = float(getattr(cfg, "news_post_event_minutes", 30.0))

        from . import news_ai_advisor

        live_ids: set[str] = set()
        for ev in events:
            eid = news_ai_advisor.event_id(ev)
            ev_ts = float(ev.get("_ts", 0) or 0)
            minutes_away = (ev_ts - now) / 60.0
            if minutes_away < -post_min or minutes_away > watch_min:
                continue
            live_ids.add(eid)
            if eid not in self._watches:
                self._watches[eid] = _EventWatch(event_id=eid, event=ev)
                LOG.info(
                    "news_trader: watching '%s' in %.1f min",
                    ev.get("title", "?"), minutes_away,
                )
            else:
                self._watches[eid].event = ev

        for eid in list(self._watches.keys()):
            if eid not in live_ids:
                w = self._watches.pop(eid)
                LOG.debug("news_trader: expired watch for '%s'", w.event.get("title", eid))

        if not self._watches:
            return []

        actions: list[NewsAction] = []

        for eid, w in list(self._watches.items()):
            if w.state == _WatchState.FIRED:
                continue

            ev_ts = float(w.event.get("_ts", 0) or 0)
            minutes_away = (ev_ts - now) / 60.0

            # Skip post-event entries for now (v1: pre-event only)
            if minutes_away < 0:
                continue

            # Fetch / refresh advice
            try:
                advice = await news_ai_advisor.get_event_direction(
                    w.event, refresh_min=refresh_min
                )
                w.advice = advice
            except Exception as exc:
                LOG.warning("news_trader: advice fetch failed for '%s': %s", eid, exc)
                continue

            direction = advice.direction
            confidence = advice.confidence

            if direction == "neutral" or confidence < threshold:
                if w.state in (_WatchState.PRIMED, _WatchState.ARMED):
                    w.state = _WatchState.WATCHING
                    LOG.debug(
                        "news_trader: '%s' reverted to WATCHING (direction=%s conf=%d)",
                        w.event.get("title", eid), direction, confidence,
                    )
                continue  # nothing to act on

            # direction is bullish/bearish and confidence ≥ threshold
            if w.state == _WatchState.WATCHING:
                w.state = _WatchState.PRIMED
                LOG.info(
                    "news_trader: '%s' → PRIMED (%s conf=%d%% in %.1f min)",
                    w.event.get("title", eid), direction, confidence, minutes_away,
                )

            if minutes_away <= entry_min and w.state == _WatchState.PRIMED:
                w.state = _WatchState.ARMED
                LOG.info(
                    "news_trader: '%s' → ARMED (%s conf=%d%% in %.1f min)",
                    w.event.get("title", eid), direction, confidence, minutes_away,
                )

            if w.state != _WatchState.ARMED:
                continue

            if minutes_away < cutoff_min:
                if now - w.last_log_at > 60.0:
                    w.last_log_at = now
                    LOG.info(
                        "news_trader: '%s' inside cutoff (%.1f min < %.1f min cutoff) — skipping entry",
                        w.event.get("title", eid), minutes_away, cutoff_min,
                    )
                continue

            trade_direction = "long" if direction == "bullish" else "short"
            fired_any = False
            for pk in all_pair_keys:
                if pk in open_pair_keys:
                    LOG.debug(
                        "news_trader: skipping %s — position already open",
                        pk,
                    )
                    continue
                actions.append(NewsAction(
                    pair_key=pk,
                    direction=trade_direction,
                    event_id=eid,
                    event_title=str(w.event.get("title", eid)),
                    confidence=confidence,
                    sl_atr_mult=sl_pre,
                    tp_atr_mult=tp_pre,
                    phase="pre_event",
                ))
                fired_any = True

            if fired_any:
                w.state = _WatchState.FIRED
                LOG.info(
                    "news_trader: FIRED '%s' — %s conf=%d%% pairs=%d",
                    w.event.get("title", eid), trade_direction, confidence, len(all_pair_keys),
                )

        return actions

    def summary(self) -> dict:
        """Diagnostic snapshot for logging / dashboard."""
        now = time.time()
        return {
            "watches": [
                {
                    "event": w.event.get("title", w.event_id),
                    "state": w.state.value,
                    "direction": w.advice.direction if w.advice else "?",
                    "confidence": w.advice.confidence if w.advice else 0,
                    "minutes_away": round(
                        (float(w.event.get("_ts", 0) or 0) - now) / 60.0, 1
                    ),
                }
                for w in self._watches.values()
            ]
        }
