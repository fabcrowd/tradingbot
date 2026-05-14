"""News AI Advisor — zero-cost, zero-API-key directional scorer for economic events.

Uses DuckDuckGo News search (``duckduckgo-search`` package, free, no API key) to fetch
recent analyst commentary and news headlines about an upcoming economic event, then
applies a weighted keyword inventory to produce:

    direction:   "bullish" | "bearish" | "neutral"   (from *crypto's* perspective)
    confidence:  0–100 integer
    reasoning:   brief summary of dominant signal phrases

Directional interpretation for USD macro events:
    USD hawkish / strong data  →  USD up  →  crypto BEARISH
    USD dovish / weak data     →  USD down →  crypto BULLISH

Cache: results are cached per event and refreshed every ``refresh_min`` minutes
(default 20).  All network I/O runs in a thread via ``asyncio.to_thread`` so it
never blocks the event loop.

Install dependency once:
    pip install duckduckgo-search
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

LOG = logging.getLogger(__name__)

# Resolve DDGS class once at import time; package was renamed duckduckgo-search → ddgs.
try:
    from ddgs import DDGS as _DDGS_CLASS  # type: ignore[import]
except ImportError:
    try:
        from duckduckgo_search import DDGS as _DDGS_CLASS  # type: ignore[import]
    except ImportError:
        _DDGS_CLASS = None  # type: ignore[assignment]

# ── Keyword inventory ─────────────────────────────────────────────────────────
# (regex_pattern, weight, direction)
# direction  +1 = crypto BEARISH (USD bullish signal)
#            -1 = crypto BULLISH (USD bearish signal)
#             0 = neutral anchor (subtracts from both sides)
# weight     2.0 = strong phrase   1.5 = moderate   1.0 = contextual   0.5 = weak

_RAW_KEYWORDS: list[tuple[str, float, int]] = [
    # ══ USD / macro BULLISH → crypto BEARISH ════════════════════════════════
    # Hard beats
    (r"beats?\s+(?:the\s+)?(?:fore)?cast", 2.0, +1),
    (r"beat(?:s|ing)?\s+expectations?", 2.0, +1),
    (r"stronger\s+than\s+expected", 2.0, +1),
    (r"above\s+(?:fore)?cast", 1.5, +1),
    (r"above\s+expectations?", 1.5, +1),
    (r"better\s+than\s+expected", 1.5, +1),
    (r"exceeds?\s+(?:fore)?cast", 1.5, +1),
    (r"exceeds?\s+expectations?", 1.5, +1),
    (r"tops?\s+(?:fore)?cast", 1.5, +1),
    (r"tops?\s+expectations?", 1.5, +1),
    (r"hotter\s+than\s+expected", 2.0, +1),
    (r"hotter.{0,15}(?:expected|fore)", 1.5, +1),
    # Inflation hot
    (r"hot\s+(?:cpi|inflation|pce|core|jobs|nfp|payroll)", 2.0, +1),
    (r"inflation\s+(?:surges?|jumps?|rises?|climbs?|accelerates?|heats?|remains?\s+elevated)", 2.0, +1),
    (r"inflation\s+(?:above|higher\s+than)\s+(?:fore)?cast", 2.0, +1),
    (r"core\s+(?:cpi|pce)\s+(?:rises?|jumps?|beats?|surges?|higher)", 2.0, +1),
    # Jobs strong
    (r"(?:nfp|payrolls?|jobs?)\s+(?:surges?|soars?|beats?|jumps?|smashes?|blowout|strong)", 2.0, +1),
    (r"(?:nfp|payrolls?|job\s+gains?)\s+(?:above|beat|top|exceed)", 2.0, +1),
    (r"strong\s+(?:labor|labour|jobs?|employment|payroll|nfp)", 1.5, +1),
    (r"solid\s+(?:labor|labour|jobs?|employment|payroll)", 1.5, +1),
    (r"robust\s+(?:labor|labour|jobs?|employment|payroll)", 1.5, +1),
    (r"tight\s+labor\s+market", 1.5, +1),
    (r"unemployment\s+(?:falls?|drops?|decreases?|declines?\s+to)", 1.5, +1),
    (r"jobless\s+claims?\s+(?:fall|drop|decline|decrease)", 1.0, +1),
    (r"job(?:s)?\s+(?:added|created|gained|adding|growth\s+strong)", 1.0, +1),
    (r"wages?\s+(?:rise|grow|surge|beat|jump|higher)", 1.5, +1),
    (r"average\s+hourly\s+earnings\s+(?:beat|rise|jump|surge)", 1.5, +1),
    # GDP / growth strong
    (r"(?:gdp|growth)\s+(?:surges?|beats?|jumps?|expands?|accelerates?)", 1.5, +1),
    (r"gdp\s+(?:above|beat|top|exceed)", 1.5, +1),
    (r"economy\s+(?:grows?|expands?)\s+(?:faster|stronger|more\s+than)", 1.0, +1),
    # USD strength
    (r"dollar\s+(?:rises?|climbs?|strengthens?|rallies?|gains?|surges?|soars?)", 1.5, +1),
    (r"dxy\s+(?:rises?|climbs?|gains?|rallies?|surges?)", 1.5, +1),
    (r"usd\s+(?:rises?|climbs?|strengthens?|gains?|rallies?)", 1.5, +1),
    (r"greenback\s+(?:rises?|gains?|strengthens?|rallies?)", 1.0, +1),
    (r"dollar\s+(?:strength|rally|surge|climb)", 1.0, +1),
    # Hawkish / tightening
    (r"hawkish", 2.0, +1),
    (r"rate\s+hike", 2.0, +1),
    (r"interest\s+rate\s+(?:hike|increase|rise)", 2.0, +1),
    (r"tightening\s+(?:cycle|policy|monetary)", 1.5, +1),
    (r"higher.{0,15}(?:rates?|yields?|for\s+longer)", 1.5, +1),
    (r"rates?\s+(?:higher|elevated)\s+for\s+longer", 2.0, +1),
    (r"fed\s+(?:hikes?|raises?|increases?\s+rates?|tightens?)", 2.0, +1),
    (r"fomc\s+(?:hikes?|raises?\s+rates?|hawkish)", 2.0, +1),
    (r"no\s+(?:rate\s+)?cuts?\s+(?:in|this|next|anytime)", 1.5, +1),
    (r"delays?\s+(?:rate\s+)?cut", 1.5, +1),
    (r"pushes?\s+(?:back|out)\s+(?:rate\s+)?cut", 1.5, +1),
    (r"(?:rate\s+)?cut\s+(?:unlikely|off\s+the\s+table|postponed|delayed)", 1.5, +1),
    (r"fewer\s+(?:rate\s+)?cuts?", 1.5, +1),
    (r"yield(?:s)?\s+(?:rise?|climb|surge|jump)", 1.0, +1),
    (r"treasury\s+yield\s+(?:rises?|climbs?|surges?)", 1.0, +1),
    # Retail / consumer strong
    (r"retail\s+sales?\s+(?:beat|surge|jump|rise|top|strong|solid)", 1.0, +1),
    (r"consumer\s+(?:confidence|spending|sentiment)\s+(?:rises?|beats?|surges?|improves?)", 1.0, +1),
    (r"ism\s+(?:manufacturing|services?|pmi)\s+(?:beats?|rises?|expands?|above)", 1.0, +1),
    (r"pmi\s+(?:beats?|rises?|expands?|above\s+50)", 1.0, +1),

    # ══ USD / macro BEARISH → crypto BULLISH ════════════════════════════════
    # Hard misses
    (r"misses?\s+(?:the\s+)?(?:fore)?cast", 2.0, -1),
    (r"miss(?:es|ing)?\s+expectations?", 2.0, -1),
    (r"weaker\s+than\s+expected", 2.0, -1),
    (r"below\s+(?:fore)?cast", 1.5, -1),
    (r"below\s+expectations?", 1.5, -1),
    (r"worse\s+than\s+expected", 1.5, -1),
    (r"falls?\s+short\s+of", 1.5, -1),
    (r"disappoints?(?:ing|ment)?", 1.5, -1),
    (r"underwhelms?(?:ing)?", 1.5, -1),
    (r"cooler\s+than\s+expected", 2.0, -1),
    (r"cooler.{0,15}(?:expected|fore)", 1.5, -1),
    # Inflation cooling
    (r"cool(?:ing|ed|s)?\s+(?:cpi|inflation|pce|core)", 2.0, -1),
    (r"inflation\s+(?:cools?|slows?|eases?|falls?|drops?|decelerates?|moderates?)", 2.0, -1),
    (r"inflation\s+(?:below|lower\s+than)\s+(?:fore)?cast", 2.0, -1),
    (r"core\s+(?:cpi|pce)\s+(?:falls?|drops?|misses?|cools?|lower)", 2.0, -1),
    (r"disinflation", 1.5, -1),
    (r"deflationary", 1.5, -1),
    # Jobs weak
    (r"(?:nfp|payrolls?|jobs?)\s+(?:misses?|falls?|drops?|disappoint|shrinks?|weaker)", 2.0, -1),
    (r"(?:nfp|payrolls?|job\s+losses?)\s+(?:below|miss|fall|drop)", 2.0, -1),
    (r"weak\s+(?:labor|labour|jobs?|employment|payroll|nfp)", 1.5, -1),
    (r"soft\s+(?:labor|labour|jobs?|employment|payroll)", 1.5, -1),
    (r"unemployment\s+(?:rises?|jumps?|increases?|climbs?\s+to)", 1.5, -1),
    (r"jobless\s+claims?\s+(?:rise|jump|increase|surge|climb)", 1.0, -1),
    (r"job(?:s)?\s+(?:lost|losses|cut|shed|decline)", 1.0, -1),
    (r"layoffs?(?:\s+surge|\s+rise|\s+increase)?", 1.0, -1),
    (r"wages?\s+(?:fall|drop|miss|disappoint|weaken|lower)", 1.5, -1),
    (r"average\s+hourly\s+earnings\s+(?:miss|fall|drop|weaken)", 1.5, -1),
    # GDP / growth weak
    (r"(?:gdp|growth)\s+(?:misses?|falls?|contracts?|slows?|declines?|shrinks?)", 1.5, -1),
    (r"gdp\s+(?:below|miss|fall|contract|shrink)", 1.5, -1),
    (r"recession\s+(?:fears?|risk|concerns?|looming|possible)", 1.5, -1),
    (r"economy\s+(?:slows?|contracts?|shrinks?|stagnates?)", 1.0, -1),
    # USD weakness
    (r"dollar\s+(?:falls?|drops?|weakens?|slides?|tumbles?|loses?|plunges?)", 1.5, -1),
    (r"dxy\s+(?:falls?|drops?|weakens?|slides?|plunges?)", 1.5, -1),
    (r"usd\s+(?:falls?|drops?|weakens?|slides?|plunges?)", 1.5, -1),
    (r"greenback\s+(?:falls?|drops?|weakens?|slides?)", 1.0, -1),
    (r"dollar\s+(?:weakness|decline|drop|fall|plunge|slide)", 1.0, -1),
    # Dovish / easing
    (r"dovish", 2.0, -1),
    (r"rate\s+cut", 2.0, -1),
    (r"interest\s+rate\s+(?:cut|decrease|reduction)", 2.0, -1),
    (r"cutting\s+rates?", 2.0, -1),
    (r"easing\s+(?:cycle|policy|monetary)", 1.5, -1),
    (r"lower.{0,15}(?:rates?|yields?)", 1.5, -1),
    (r"fed\s+(?:cuts?|lowers?\s+rates?|eases?|pauses?\s+hikes?)", 2.0, -1),
    (r"fomc\s+(?:cuts?\s+rates?|dovish|pauses?\s+hikes?)", 2.0, -1),
    (r"rate\s+pause", 1.5, -1),
    (r"pauses?\s+(?:rate|hike)", 1.5, -1),
    (r"holds?\s+rates?\s+steady", 1.0, -1),
    (r"pivot\s+(?:to\s+)?(?:dovish|easing|cuts?)", 1.5, -1),
    (r"quantitative\s+easing", 1.5, -1),
    (r"yield(?:s)?\s+(?:fall|drop|decline|tumble)", 1.0, -1),
    # Retail / consumer weak
    (r"retail\s+sales?\s+(?:miss|drop|fall|decline|weak|disappoint)", 1.0, -1),
    (r"consumer\s+(?:confidence|spending|sentiment)\s+(?:falls?|drops?|misses?|weakens?|declines?)", 1.0, -1),
    (r"ism\s+(?:manufacturing|services?|pmi)\s+(?:misses?|falls?|below|contracts?)", 1.0, -1),
    (r"pmi\s+(?:misses?|falls?|below\s+50|contracts?)", 1.0, -1),

    # ══ Neutral anchors (reduce net score) ══════════════════════════════════
    (r"in\s+line\s+with\s+expectations?", -0.5, 0),
    (r"as\s+expected", -0.5, 0),
    (r"meets?\s+(?:fore)?cast", -0.5, 0),
    (r"matches?\s+expectations?", -0.5, 0),
    (r"mixed\s+(?:signals?|data|reading|results?)", -0.5, 0),
    (r"unchanged\s+(?:at|from|vs)", -0.3, 0),
    (r"no\s+major\s+surprise", -0.3, 0),
    (r"(?:largely|broadly)\s+(?:in\s+line|as\s+expected)", -0.5, 0),
]

# Pre-compile
_COMPILED = [
    (re.compile(pat, re.IGNORECASE), weight, direction)
    for pat, weight, direction in _RAW_KEYWORDS
]

_MIN_NET_FOR_DIRECTION = 1.0   # minimum |net_score| to declare direction
_CONFIDENCE_SCALE = 10.0       # net_score units per 10 confidence points


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class NewsAdvice:
    event_id: str
    direction: str     # "bullish" | "bearish" | "neutral"  (crypto perspective)
    confidence: int    # 0–100
    reasoning: str
    fetched_at: float  # unix timestamp
    bull_score: float = 0.0
    bear_score: float = 0.0
    hit_count: int = 0


# ── Cache ─────────────────────────────────────────────────────────────────────

_cache: dict[str, NewsAdvice] = {}   # event_id → NewsAdvice
_DEFAULT_REFRESH_MIN = 20.0


def event_id(event: dict) -> str:
    return f"{event.get('country','')}/{event.get('title','')}/{event.get('date','')}"


# ── Search ────────────────────────────────────────────────────────────────────

def _build_query(event: dict) -> str:
    title = str(event.get("title", ""))
    country = str(event.get("country", ""))
    ts = float(event.get("_ts", 0) or 0)
    month_year = ""
    if ts:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        month_year = dt.strftime("%B %Y")
    parts = [p for p in (title, country, month_year, "forecast analyst consensus") if p]
    return " ".join(parts)


def _search_sync(query: str, max_results: int) -> str:
    """Blocking DuckDuckGo news search. Run via asyncio.to_thread — never call directly."""
    if _DDGS_CLASS is None:
        LOG.warning("news_ai_advisor: search package not installed — run: pip install ddgs")
        return ""
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with _DDGS_CLASS() as client:
                results = list(client.news(query, max_results=max_results))
        return " ".join(f"{r.get('title', '')} {r.get('body', '')}" for r in results)
    except Exception as exc:
        # 403 rate-limits happen on rapid successive calls; at 20-min intervals this is rare
        LOG.warning("news_ai_advisor: DDG search error: %s", exc)
        return ""


async def _fetch_text(query: str, max_results: int = 10) -> str:
    return await asyncio.to_thread(_search_sync, query, max_results)


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(text: str) -> tuple[float, float, int]:
    """Returns (bull_score, bear_score, hit_count) from keyword scan."""
    bull = 0.0
    bear = 0.0
    hits = 0
    for pattern, weight, direction in _COMPILED:
        if direction == 0:
            # Neutral anchor — apply negative weight to both sides
            if pattern.search(text):
                reduction = abs(weight)
                bull = max(0.0, bull - reduction)
                bear = max(0.0, bear - reduction)
            continue
        for _ in pattern.finditer(text):
            if direction == +1:
                bull += weight
            else:
                bear += weight
            hits += 1
    return bull, bear, hits


def _build_advice(event_id: str, bull: float, bear: float, hits: int, ts: float) -> NewsAdvice:
    net = bull - bear
    total = bull + bear
    if total < 0.5 or abs(net) < _MIN_NET_FOR_DIRECTION:
        return NewsAdvice(
            event_id=event_id, direction="neutral", confidence=0,
            reasoning=f"no clear signal (bull={bull:.1f} bear={bear:.1f} hits={hits})",
            fetched_at=ts, bull_score=bull, bear_score=bear, hit_count=hits,
        )
    raw_conf = int(min(100, abs(net) / _CONFIDENCE_SCALE * 100))
    raw_conf = max(0, raw_conf)
    if net > 0:
        # USD strong → BTC bearish
        direction = "bearish"
        reasoning = f"USD-bullish signal (bull={bull:.1f} bear={bear:.1f} net={net:+.1f} hits={hits})"
    else:
        # USD weak → BTC bullish
        direction = "bullish"
        reasoning = f"USD-bearish signal (bull={bull:.1f} bear={bear:.1f} net={net:+.1f} hits={hits})"
    return NewsAdvice(
        event_id=event_id, direction=direction, confidence=raw_conf,
        reasoning=reasoning, fetched_at=ts, bull_score=bull, bear_score=bear, hit_count=hits,
    )


# ── Public API ────────────────────────────────────────────────────────────────

async def get_event_direction(
    event: dict,
    *,
    refresh_min: float = _DEFAULT_REFRESH_MIN,
) -> NewsAdvice:
    """Return directional advice for an economic event.

    Fetches fresh DuckDuckGo news if the cache entry is older than ``refresh_min``
    minutes.  Returns cached result otherwise.

    ``direction`` is from crypto's perspective:
        "bullish"  → go long  (USD weak, risk-on)
        "bearish"  → go short (USD strong, risk-off)
        "neutral"  → no clear edge; skip trade
    """
    eid = event_id(event)
    now = time.time()
    cached = _cache.get(eid)
    if cached and (now - cached.fetched_at) < refresh_min * 60.0:
        return cached

    query = _build_query(event)
    LOG.info("news_ai_advisor: fetching news for '%s'", query)
    text = await _fetch_text(query)
    bull, bear, hits = _score(text)
    advice = _build_advice(eid, bull, bear, hits, now)
    _cache[eid] = advice
    LOG.info(
        "news_ai_advisor: %s → %s conf=%d%% (bull=%.1f bear=%.1f hits=%d)",
        event.get("title", "?"), advice.direction, advice.confidence,
        bull, bear, hits,
    )
    return advice


def cache_summary() -> dict:
    """Diagnostic snapshot for logging / dashboard."""
    now = time.time()
    return {
        "cached_events": len(_cache),
        "entries": [
            {
                "event_id": k,
                "direction": v.direction,
                "confidence": v.confidence,
                "age_min": round((now - v.fetched_at) / 60.0, 1),
            }
            for k, v in _cache.items()
        ],
    }
