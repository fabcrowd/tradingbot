"""Economic calendar feed — Forex Factory public JSON (same source as toodegrees TV indicator).

Fetches this-week + next-week event lists from nfs.faireconomy.media and caches them.
Provides ``upcoming_events()`` for regime risk-on checks.

Event JSON shape (each item):
  {
    "title":    "Non-Farm Payrolls",
    "country":  "USD",
    "date":     "2026-04-25T08:30:00-04:00",   # ISO-8601 with tz offset
    "impact":   "High" | "Medium" | "Low" | "Holiday",
    "forecast": "...",
    "previous": "..."
  }
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Sequence

LOG = logging.getLogger(__name__)

# Public Forex Factory mirror — same data source the toodegrees TV indicator pulls via request.seed
_FF_URLS: tuple[str, ...] = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
)

IMPACT_RANK = {"High": 3, "Medium": 2, "Low": 1, "Holiday": 0}

_cache: dict = {"events": [], "fetched_at": 0.0, "error_at": 0.0}
_CACHE_TTL = 3600.0       # refresh once per hour
_ERROR_BACKOFF = 300.0    # on fetch failure, retry after 5 min


def _fetch_sync() -> list[dict]:
    """Blocking fetch of both week feeds; safe to call from a thread."""
    events: list[dict] = []
    headers = {"User-Agent": "TradingBot/1.0 (+economic-calendar)"}
    for url in _FF_URLS:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                if isinstance(raw, list):
                    events.extend(raw)
                    LOG.debug("news_calendar: fetched %d events from %s", len(raw), url)
        except urllib.error.URLError as exc:
            LOG.warning("news_calendar: fetch failed for %s: %s", url, exc)
        except Exception as exc:
            LOG.warning("news_calendar: unexpected error for %s: %s", url, exc)
    return events


async def _refresh_if_stale() -> None:
    """Non-blocking refresh: fetches in a thread if cache is stale."""
    global _cache
    now = time.time()
    # Don't hammer on errors
    if now - _cache["fetched_at"] < _CACHE_TTL:
        return
    if _cache.get("error_at", 0.0) and now - _cache["error_at"] < _ERROR_BACKOFF:
        return
    try:
        events = await asyncio.to_thread(_fetch_sync)
        if events:
            _cache = {"events": events, "fetched_at": now, "error_at": 0.0}
            LOG.info("news_calendar: refreshed — %d events loaded", len(events))
        else:
            _cache["error_at"] = now
            LOG.warning("news_calendar: refresh returned 0 events — retaining stale cache")
    except Exception as exc:
        _cache["error_at"] = now
        LOG.warning("news_calendar: refresh error: %s", exc)


def _parse_event_ts(date_str: str) -> float | None:
    """Parse ISO-8601 date string → Unix timestamp. Returns None on failure."""
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def upcoming_events(
    now_ts: float,
    *,
    lookahead_sec: float = 900.0,
    lookbehind_sec: float = 0.0,
    min_impact: str = "High",
    currencies: Sequence[str] | None = None,
) -> list[dict]:
    """Return cached events within ``[now - lookbehind, now + lookahead]``.

    ``currencies`` is a list of country codes (e.g. ``["USD"]``).
    Empty / None = no currency filter (all events).
    """
    min_rank = IMPACT_RANK.get(min_impact, 3)
    cur_upper = [c.upper() for c in currencies] if currencies else []
    result: list[dict] = []
    for ev in _cache["events"]:
        rank = IMPACT_RANK.get(str(ev.get("impact", "")), -1)
        if rank < min_rank:
            continue
        if cur_upper and str(ev.get("country", "")).upper() not in cur_upper:
            continue
        ts = _parse_event_ts(str(ev.get("date", "")))
        if ts is None:
            continue
        if now_ts - lookbehind_sec <= ts <= now_ts + lookahead_sec:
            result.append({**ev, "_ts": ts})
    return result


def cache_summary() -> dict:
    """Diagnostic snapshot for dashboard / logs."""
    return {
        "cached_events": len(_cache["events"]),
        "fetched_at": _cache.get("fetched_at", 0.0),
        "error_at": _cache.get("error_at", 0.0),
        "cache_age_sec": round(time.time() - float(_cache.get("fetched_at", 0.0)), 1),
    }
