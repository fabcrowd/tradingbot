"""Dual-provider sportsbook odds client.

Primary:   odds-api.io  v3 — 5,000 req/hr, 2 bookmakers (free tier)
Secondary: the-odds-api.com v4 — 500 req/month, ALL bookmakers per request

The CompositeOddsClient merges results from both providers, preferring
the-odds-api.com data (deeper book coverage) and falling back to
odds-api.io for event discovery and high-frequency polling.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientSession, ClientTimeout

LOG = logging.getLogger("polymarket_bot.odds_client")

# ── Provider base URLs ──
ODDS_API_IO_BASE = "https://api.odds-api.io/v3"
THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# ── Sport key mappings ──
# the-odds-api.com uses compound keys like "basketball_nba"
THE_ODDS_API_SPORT_KEYS: dict[str, list[str]] = {
    "basketball": ["basketball_nba", "basketball_ncaab"],
    "baseball": ["baseball_mlb"],
    "ice-hockey": ["icehockey_nhl"],
    "american-football": ["americanfootball_nfl", "americanfootball_ufl"],
    "football": ["soccer_usa_mls", "soccer_epl"],
    "mixed-martial-arts": ["mma_mixed_martial_arts"],
}

LEAGUE_TO_SPORT: dict[str, str] = {
    "nba": "basketball",
    "cbb": "basketball",
    "nfl": "american-football",
    "cfb": "american-football",
    "mlb": "baseball",
    "nhl": "ice-hockey",
    "epl": "football",
    "mls": "football",
    "ufc": "mixed-martial-arts",
}

SHARP_BOOKMAKERS = [
    "pinnacle", "bet365", "1xbet", "draftkings",
    "fanduel", "betmgm", "betfair",
]

TEAM_ALIASES: dict[str, list[str]] = {
    "LAL": ["Los Angeles Lakers", "LA Lakers", "Lakers", "LAL"],
    "BOS": ["Boston Celtics", "Celtics", "BOS"],
    "NYK": ["New York Knicks", "Knicks", "NYK"],
    "GSW": ["Golden State Warriors", "Warriors", "GSW", "GS"],
    "MIL": ["Milwaukee Bucks", "Bucks", "MIL"],
    "PHI": ["Philadelphia 76ers", "76ers", "Sixers", "PHI"],
    "DEN": ["Denver Nuggets", "Nuggets", "DEN"],
    "DAL": ["Dallas Mavericks", "Mavericks", "Mavs", "DAL"],
    "MIA": ["Miami Heat", "Heat", "MIA"],
    "CLE": ["Cleveland Cavaliers", "Cavaliers", "Cavs", "CLE"],
    "OKC": ["Oklahoma City Thunder", "Thunder", "OKC"],
    "MIN": ["Minnesota Timberwolves", "Timberwolves", "Wolves", "MIN"],
    "IND": ["Indiana Pacers", "Pacers", "IND"],
    "ORL": ["Orlando Magic", "Magic", "ORL"],
    "SAC": ["Sacramento Kings", "Kings", "SAC"],
    "PHX": ["Phoenix Suns", "Suns", "PHX"],
    "TOR": ["Toronto Raptors", "Raptors", "TOR"],
    "CHI": ["Chicago Bulls", "Bulls", "CHI"],
    "BKN": ["Brooklyn Nets", "Nets", "BKN"],
    "WAS": ["Washington Wizards", "Wizards", "WAS"],
    "CHA": ["Charlotte Hornets", "Hornets", "CHA"],
    "SAS": ["San Antonio Spurs", "Spurs", "SAS"],
    "POR": ["Portland Trail Blazers", "Trail Blazers", "Blazers", "POR"],
    "NOP": ["New Orleans Pelicans", "Pelicans", "NOP"],
    "MEM": ["Memphis Grizzlies", "Grizzlies", "MEM"],
    "HOU": ["Houston Rockets", "Rockets", "HOU", "Houston Astros", "Astros"],
    "LAC": ["Los Angeles Clippers", "LA Clippers", "Clippers", "LAC"],
    "NYY": ["New York Yankees", "Yankees", "NYY"],
    "LAD": ["Los Angeles Dodgers", "Dodgers", "LAD"],
    "ATL": ["Atlanta Braves", "Braves", "ATL", "Atlanta Hawks", "Hawks"],
    "BUF": ["Buffalo Bills", "Bills", "BUF"],
    "KC": ["Kansas City Chiefs", "Chiefs", "KC"],
    "SF": ["San Francisco 49ers", "49ers", "SF"],
    "DET": ["Detroit Lions", "Lions", "DET", "Detroit Pistons", "Pistons"],
}


def _normalize_team(name: str) -> str:
    name_u = name.upper().strip()
    for abbr, aliases in TEAM_ALIASES.items():
        for a in aliases:
            if a.upper() == name_u:
                return abbr
    return name_u[:3] if len(name_u) > 3 else name_u


def decimal_to_probability(odds: float) -> float:
    if odds <= 1.0:
        return 0.0
    return 1.0 / odds


# ── Shared data models ──

@dataclass
class BookmakerLine:
    bookmaker: str
    home_odds: float
    away_odds: float
    home_prob: float
    away_prob: float
    market_name: str = ""
    updated_at: str = ""


@dataclass
class OddsEvent:
    event_id: int | str
    sport_slug: str
    league_slug: str
    league_name: str
    home_team: str
    away_team: str
    commence_time: str
    status: str
    home_score: int = 0
    away_score: int = 0
    bookmakers: list[BookmakerLine] = field(default_factory=list)

    @property
    def home_abbr(self) -> str:
        return _normalize_team(self.home_team)

    @property
    def away_abbr(self) -> str:
        return _normalize_team(self.away_team)

    @property
    def is_live(self) -> bool:
        return self.status == "live"

    def sharp_probability(self) -> tuple[float, float]:
        """Return (home_prob, away_prob) from the sharpest available book."""
        for pref in SHARP_BOOKMAKERS:
            for bm in self.bookmakers:
                if pref in bm.bookmaker.lower():
                    total = bm.home_prob + bm.away_prob
                    if total > 0:
                        return bm.home_prob / total, bm.away_prob / total
                    return bm.home_prob, bm.away_prob
        if self.bookmakers:
            bm = self.bookmakers[0]
            total = bm.home_prob + bm.away_prob
            if total > 0:
                return bm.home_prob / total, bm.away_prob / total
        return 0.5, 0.5

    def consensus_probability(self) -> tuple[float, float]:
        if not self.bookmakers:
            return 0.5, 0.5
        hp = sum(b.home_prob for b in self.bookmakers) / len(self.bookmakers)
        ap = sum(b.away_prob for b in self.bookmakers) / len(self.bookmakers)
        total = hp + ap
        if total > 0:
            return hp / total, ap / total
        return 0.5, 0.5


# ═══════════════════════════════════════════════════════════════════
# Provider 1: odds-api.io (high-frequency event discovery + 2 books)
# ═══════════════════════════════════════════════════════════════════

class OddsApiIoClient:
    """Odds-API.io v3 — 5,000 req/hr, 2 bookmaker slots on free tier."""

    def __init__(self, api_key: str, poll_sec: float = 30.0, bookmakers: str = "Bet365,1xbet") -> None:
        self._api_key = api_key
        self._poll_sec = poll_sec
        self._bookmakers = bookmakers
        self._session: ClientSession | None = None
        self._cache: dict[str, tuple[float, list[OddsEvent]]] = {}
        self._requests = 0

    @property
    def requests_used(self) -> int:
        return self._requests

    @property
    def has_key(self) -> bool:
        return bool(self._api_key)

    async def _sess(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=15))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_live_events(self, sport_slug: str) -> list[OddsEvent]:
        now = time.time()
        cached = self._cache.get(sport_slug)
        if cached and now - cached[0] < self._poll_sec:
            return cached[1]

        if not self._api_key:
            return []

        sess = await self._sess()
        params = {"apiKey": self._api_key, "sport": sport_slug, "status": "live", "limit": "20"}

        try:
            async with sess.get(f"{ODDS_API_IO_BASE}/events", params=params) as resp:
                if resp.status == 401:
                    LOG.error("odds-api.io: invalid API key")
                    return []
                if resp.status != 200:
                    return self._cache.get(sport_slug, (0, []))[1]
                data = await resp.json()
        except Exception as exc:
            LOG.warning("odds-api.io events error: %s", exc)
            return self._cache.get(sport_slug, (0, []))[1]

        self._requests += 1
        raw_list = data.get("value", data) if isinstance(data, dict) else data
        events = [e for e in (self._parse(r) for r in (raw_list if isinstance(raw_list, list) else [])) if e]
        self._cache[sport_slug] = (now, events)
        return events

    async def fetch_odds(self, event_id: int) -> OddsEvent | None:
        if not self._api_key:
            return None
        sess = await self._sess()
        params = {"apiKey": self._api_key, "eventId": str(event_id), "bookmakers": self._bookmakers}
        try:
            async with sess.get(f"{ODDS_API_IO_BASE}/odds", params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except Exception as exc:
            LOG.warning("odds-api.io odds error: %s", exc)
            return None
        self._requests += 1
        return self._parse_with_odds(data)

    def _parse(self, raw: dict) -> OddsEvent | None:
        h, a = raw.get("home", ""), raw.get("away", "")
        if not h or not a:
            return None
        scores = raw.get("scores") or {}
        sport = raw.get("sport") or {}
        league = raw.get("league") or {}
        return OddsEvent(
            event_id=int(raw.get("id", 0)),
            sport_slug=str(sport.get("slug", "")),
            league_slug=str(league.get("slug", "")),
            league_name=str(league.get("name", "")),
            home_team=h, away_team=a,
            commence_time=str(raw.get("date", "")),
            status=str(raw.get("status", "")),
            home_score=int(scores.get("home", 0)),
            away_score=int(scores.get("away", 0)),
        )

    def _parse_with_odds(self, data: dict) -> OddsEvent | None:
        evt = self._parse(data)
        if not evt:
            return None
        bm_data = data.get("bookmakers", {})
        if isinstance(bm_data, dict):
            for bm_name, markets in bm_data.items():
                if not isinstance(markets, list):
                    continue
                for mkt in markets:
                    name = mkt.get("name", "")
                    if not any(k in name.lower() for k in ("winner", "moneyline", "match")):
                        continue
                    for row in (mkt.get("odds") or []):
                        ho = float(row.get("home", 0) or 0)
                        ao = float(row.get("away", 0) or 0)
                        if ho > 1.0 and ao > 1.0:
                            evt.bookmakers.append(BookmakerLine(
                                bm_name, ho, ao,
                                decimal_to_probability(ho), decimal_to_probability(ao),
                                name, str(mkt.get("updatedAt", "")),
                            ))
        return evt


# ═══════════════════════════════════════════════════════════════════
# Provider 2: the-odds-api.com (deep book — ALL bookmakers per call)
# ═══════════════════════════════════════════════════════════════════

class TheOddsApiClient:
    """the-odds-api.com v4 — 500 req/month per key, but every request
    returns odds from ALL available bookmakers (Pinnacle, DraftKings,
    FanDuel, Bet365, etc.) in one shot.

    Supports multiple API keys with automatic rotation. When one key's
    quota runs low (< 20 remaining), the next key is used.
    """

    def __init__(self, api_keys: list[str], poll_sec: float = 120.0) -> None:
        self._keys = [k for k in api_keys if k]
        self._key_idx = 0
        self._key_remaining: dict[int, int] = {}
        self._poll_sec = poll_sec
        self._session: ClientSession | None = None
        self._cache: dict[str, tuple[float, list[OddsEvent]]] = {}
        self._requests = 0

    @property
    def requests_used(self) -> int:
        return self._requests

    @property
    def has_key(self) -> bool:
        return len(self._keys) > 0

    @property
    def active_key_index(self) -> int:
        return self._key_idx

    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def remaining_quota(self) -> dict[int, int]:
        return dict(self._key_remaining)

    def _current_key(self) -> str:
        if not self._keys:
            return ""
        return self._keys[self._key_idx % len(self._keys)]

    def _rotate_key(self) -> bool:
        if len(self._keys) <= 1:
            return False
        self._key_idx = (self._key_idx + 1) % len(self._keys)
        LOG.info("Rotated to the-odds-api.com key #%d/%d", self._key_idx + 1, len(self._keys))
        return True

    async def _sess(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=20))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_sport_odds(self, sport_key: str) -> list[OddsEvent]:
        """Fetch all events + odds for a sport key (e.g. 'basketball_nba').
        One request returns ALL bookmakers for ALL events in that sport.
        Auto-rotates keys when quota runs low.
        """
        now = time.time()
        cached = self._cache.get(sport_key)
        if cached and now - cached[0] < self._poll_sec:
            return cached[1]

        api_key = self._current_key()
        if not api_key:
            return []

        sess = await self._sess()
        url = f"{THE_ODDS_API_BASE}/sports/{sport_key}/odds/"
        params = {
            "apiKey": api_key,
            "regions": "us,eu,uk",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }

        try:
            async with sess.get(url, params=params) as resp:
                if resp.status == 401:
                    LOG.error("the-odds-api.com: invalid key #%d", self._key_idx + 1)
                    if self._rotate_key():
                        return await self.fetch_sport_odds(sport_key)
                    return []
                if resp.status == 429:
                    LOG.warning("the-odds-api.com: key #%d quota exhausted", self._key_idx + 1)
                    if self._rotate_key():
                        return await self.fetch_sport_odds(sport_key)
                    return self._cache.get(sport_key, (0, []))[1]
                if resp.status != 200:
                    LOG.warning("the-odds-api.com HTTP %s for %s", resp.status, sport_key)
                    return self._cache.get(sport_key, (0, []))[1]

                remaining_str = resp.headers.get("x-requests-remaining", "")
                remaining = int(remaining_str) if remaining_str.isdigit() else -1
                self._key_remaining[self._key_idx] = remaining
                LOG.info(
                    "the-odds-api.com key #%d/%d: %s remaining",
                    self._key_idx + 1, len(self._keys), remaining_str or "?",
                )

                if 0 < remaining < 20 and len(self._keys) > 1:
                    LOG.info("Key #%d low on quota, rotating", self._key_idx + 1)
                    self._rotate_key()

                data = await resp.json()
        except Exception as exc:
            LOG.warning("the-odds-api.com error: %s", exc)
            return self._cache.get(sport_key, (0, []))[1]

        self._requests += 1

        if not isinstance(data, list):
            return []

        events: list[OddsEvent] = []
        for raw in data:
            evt = self._parse(raw)
            if evt:
                events.append(evt)

        self._cache[sport_key] = (now, events)
        return events

    async def fetch_for_sport_group(self, sport_slug: str) -> list[OddsEvent]:
        """Fetch odds for all league keys in a sport group (e.g. 'basketball')."""
        keys = THE_ODDS_API_SPORT_KEYS.get(sport_slug, [])
        all_events: list[OddsEvent] = []
        for k in keys:
            evts = await self.fetch_sport_odds(k)
            all_events.extend(evts)
        return all_events

    def _parse(self, raw: dict[str, Any]) -> OddsEvent | None:
        ht = raw.get("home_team", "")
        at = raw.get("away_team", "")
        if not ht or not at:
            return None

        evt = OddsEvent(
            event_id=str(raw.get("id", "")),
            sport_slug=str(raw.get("sport_key", "")),
            league_slug=str(raw.get("sport_key", "")),
            league_name=str(raw.get("sport_title", "")),
            home_team=ht,
            away_team=at,
            commence_time=str(raw.get("commence_time", "")),
            status="upcoming",
        )

        for bm in raw.get("bookmakers", []):
            bm_key = bm.get("key", "")
            bm_title = bm.get("title", bm_key)
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                outcomes = mkt.get("outcomes", [])
                home_odds = 0.0
                away_odds = 0.0
                for o in outcomes:
                    if o.get("name") == ht:
                        home_odds = float(o.get("price", 0))
                    elif o.get("name") == at:
                        away_odds = float(o.get("price", 0))

                if home_odds > 1.0 and away_odds > 1.0:
                    evt.bookmakers.append(BookmakerLine(
                        bookmaker=bm_title,
                        home_odds=home_odds,
                        away_odds=away_odds,
                        home_prob=decimal_to_probability(home_odds),
                        away_prob=decimal_to_probability(away_odds),
                        market_name="h2h",
                        updated_at=str(mkt.get("last_update", "")),
                    ))

        return evt


# ═══════════════════════════════════════════════════════════════════
# Composite client: merges both providers
# ═══════════════════════════════════════════════════════════════════

class OddsClient:
    """Unified odds client that merges data from odds-api.io + the-odds-api.com.

    Strategy:
    - odds-api.io: used for high-frequency live event discovery (cheap requests)
    - the-odds-api.com: used for deep odds data with many bookmakers (expensive)
    - Deep odds are fetched conservatively (every 2 min) to preserve quota

    The bot sees a single stream of OddsEvent objects with as many
    BookmakerLine entries as both providers can supply.
    """

    def __init__(
        self,
        api_key: str = "",
        the_odds_api_keys: list[str] | None = None,
        poll_interval_sec: float = 30.0,
    ) -> None:
        self._io = OddsApiIoClient(api_key=api_key, poll_sec=poll_interval_sec)
        self._toa = TheOddsApiClient(api_keys=the_odds_api_keys or [], poll_sec=120.0)
        self._merged_cache: dict[str, tuple[float, list[OddsEvent]]] = {}

    @property
    def requests_used(self) -> int:
        return self._io.requests_used + self._toa.requests_used

    @property
    def io_requests(self) -> int:
        return self._io.requests_used

    @property
    def toa_requests(self) -> int:
        return self._toa.requests_used

    @property
    def has_io_key(self) -> bool:
        return self._io.has_key

    @property
    def has_toa_key(self) -> bool:
        return self._toa.has_key

    @property
    def toa_key_count(self) -> int:
        return self._toa.key_count

    @property
    def toa_active_key(self) -> int:
        return self._toa.active_key_index + 1

    @property
    def toa_remaining_quota(self) -> dict[int, int]:
        return self._toa.remaining_quota

    async def close(self) -> None:
        await self._io.close()
        await self._toa.close()

    async def fetch_live_events(self, sport_slug: str) -> list[OddsEvent]:
        """Fetch live events from odds-api.io (fast, frequent)."""
        return await self._io.fetch_live_events(sport_slug)

    async def fetch_deep_odds(self, sport_slug: str) -> list[OddsEvent]:
        """Fetch deep multi-bookmaker odds from the-odds-api.com (slow, conserve quota)."""
        return await self._toa.fetch_for_sport_group(sport_slug)

    async def fetch_all_live_sports(self, sport_slugs: list[str] | None = None) -> list[OddsEvent]:
        """Merge events from both providers for the given sports."""
        if sport_slugs is None:
            sport_slugs = ["basketball", "baseball", "ice-hockey"]

        all_events: list[OddsEvent] = []

        for slug in sport_slugs:
            io_events = await self._io.fetch_live_events(slug)

            toa_events: list[OddsEvent] = []
            if self._toa.has_key:
                toa_events = await self._toa.fetch_for_sport_group(slug)

            merged = self._merge_events(io_events, toa_events)
            all_events.extend(merged)

        return all_events

    async def fetch_odds(self, event_id: int) -> OddsEvent | None:
        """Fetch odds for a single event from odds-api.io."""
        return await self._io.fetch_odds(event_id)

    def _merge_events(
        self,
        io_events: list[OddsEvent],
        toa_events: list[OddsEvent],
    ) -> list[OddsEvent]:
        """Merge events from both providers by team name matching.

        For matching events, combine bookmaker lines (dedup by name).
        Non-matching events are included as-is.
        """
        if not toa_events:
            return io_events
        if not io_events:
            return toa_events

        used_toa: set[int] = set()
        merged: list[OddsEvent] = []

        for io_evt in io_events:
            best_match: OddsEvent | None = None
            best_idx = -1
            ih = _normalize_team(io_evt.home_team)
            ia = _normalize_team(io_evt.away_team)

            for idx, toa_evt in enumerate(toa_events):
                if idx in used_toa:
                    continue
                th = _normalize_team(toa_evt.home_team)
                ta = _normalize_team(toa_evt.away_team)
                if (ih == th and ia == ta) or (ih == ta and ia == th):
                    best_match = toa_evt
                    best_idx = idx
                    break

            if best_match and best_idx >= 0:
                used_toa.add(best_idx)
                seen_books = {bm.bookmaker.lower() for bm in io_evt.bookmakers}
                for bm in best_match.bookmakers:
                    if bm.bookmaker.lower() not in seen_books:
                        io_evt.bookmakers.append(bm)
                        seen_books.add(bm.bookmaker.lower())
            merged.append(io_evt)

        for idx, toa_evt in enumerate(toa_events):
            if idx not in used_toa:
                merged.append(toa_evt)

        return merged

    def find_event_for_teams(
        self, events: list[OddsEvent], home_team: str, away_team: str,
    ) -> OddsEvent | None:
        h = _normalize_team(home_team)
        a = _normalize_team(away_team)
        for evt in events:
            if (evt.home_abbr == h and evt.away_abbr == a) or \
               (evt.home_abbr == a and evt.away_abbr == h):
                return evt
        for evt in events:
            hu = evt.home_team.upper()
            au = evt.away_team.upper()
            if (h in hu or h in au) and (a in hu or a in au):
                return evt
        return None
