# Resolution-rule diff tooling for cross-market / cross-venue arbitrage

**Goal:** Understand whether you can build a **machine-readable pipeline** that (a) matches "same event" across Polymarket and Kalshi, (b) **diffs** their resolution rules, (c) scores **divergence risk**, and (d) gates arb capital only where the score is low enough.

---

## 1. Why resolution divergence is the dominant risk

Cross-venue arb math is simple: cheapest YES on venue A + cheapest NO on venue B < $1 → profit.

The math **breaks** when the two venues **resolve differently for the same real-world event**. This is not theoretical:

| Incident | Polymarket | Kalshi | Net arb outcome |
|----------|-----------|-------|-----------------|
| **Cardi B Super Bowl halftime** (Feb 2026) | **YES** at $1.00 | **Rule 6.3(c)**: settled at last traded price ($0.26 YES / $0.74 NO) | Arb player holding PM-YES + Kalshi-NO **wins** on PM, **loses most of Kalshi-NO** → **net loss or tiny gain** depending on entry |
| **Khamenei "out as leader"** (Jan–Feb 2026) | N/A (did not list identical) | **Death carveout** → last traded price, not $1.00 | Single-venue, but shows **buried exceptions** in rules |
| **Venezuela invasion** (Jan 2026) | **NO** (didn't meet "establish control" criteria) | N/A (different framing) | Shows how PM's own text can **surprise** traders |

**Source:** [DeFi Rate — How Kalshi and Polymarket Settle Markets (and Disputes)](https://defirate.com/prediction-markets/how-contracts-settle/); [Prediction Circle — How Prediction Market Contracts Resolve](https://predictioncircle.com/learn/how-prediction-market-contracts-resolve).

### The six failure modes (from Prediction Circle)

1. **Ambiguity** — contract wording doesn't map cleanly to the real event.
2. **Buried exceptions** — carveouts not in the summary (e.g. "death carveout").
3. **Source risk** — named evidence source is delayed, revised, or pressured.
4. **Timing risk** — close time ≠ determination time.
5. **Governance risk** — oracle whale votes or committee conflicts of interest.
6. **Null outcome** — market voided entirely (Manifold "N/A"; PM "Unknown/50-50").

**For cross-venue arb, (1) and (2) are the ones you can partially automate.**

---

## 2. What the APIs actually give you

### Polymarket (Gamma API)

**Endpoint:** `GET https://gamma-api.polymarket.com/markets/{id}`

**Resolution-relevant fields** (from the OpenAPI spec at `docs.polymarket.com`):

| Field | Type | What it contains |
|-------|------|-----------------|
| `question` | string | The market question ("Will X happen by Y?") |
| `description` | string | Free-text, often contains the **resolution rules** in human prose |
| `resolutionSource` | string (nullable) | Named source URL or name (e.g. "Associated Press") |
| `endDate` / `endDateIso` | datetime | When resolution becomes eligible |
| `umaEndDate` / `umaEndDateIso` | datetime | UMA oracle resolution window |
| `umaResolutionStatus` | string | Current UMA status |
| `outcomes` | string (JSON array) | e.g. `["Yes","No"]` |
| `category` | string | e.g. "crypto", "politics", "sports" |
| `closed` / `resolved` | boolean | Terminal state flags |
| `resolvedBy` | string | Who/what resolved |
| `questionID` | string | Hash used for UMA resolution |
| `conditionId` | string | CTF condition on Polygon |

**The full resolution rules live in `description`** — there is no separate structured "rules" field. You must **parse the prose**.

### Kalshi API

**Endpoint:** `GET https://api.elections.kalshi.com/trade-api/v2/events/{event_ticker}/metadata`

**Resolution-relevant fields** (from the OpenAPI spec at `docs.kalshi.com`):

| Field | Type | What it contains |
|-------|------|-----------------|
| `settlement_sources` | array of `{name, url}` | Named sources (e.g. `{name: "BLS", url: "https://bls.gov/cpi/"}`) |
| `market_details[].market_ticker` | string | Per-market ticker within the event |

**Missing from the metadata endpoint:** The **actual contract text / resolution criteria** (the "Rules" tab content on the Kalshi website). This lives in **CFTC-filed PDFs** and on the **market page** but is **not** exposed as a structured API field in the public docs. You'd need to **scrape** the market page or **fetch the CFTC filing PDF** to get the text.

### Kalshi market-level data

**Endpoint:** `GET https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}`  

Returns: `title`, `subtitle`, `result`, `status`, `expiration_time`, `settlement_timer_seconds`, `rules_primary`, `rules_secondary` — but the search results don't confirm the exact field names for rules text. **Practical step:** call the endpoint and inspect the response shape.

---

## 3. Third-party: Verilex Data "Resolution Intelligence"

[Verilex Data](https://verilexdata.com/products/pm-resolution) offers a commercial API ($0.02/query) that:

- **Classifies** Polymarket markets by resolution type (economic data, elections, legal, monetary policy).
- **Maps** to authoritative sources with expected release dates and URLs.
- Claims **3,421 markets mapped, 89 resolution sources** (as of their sample data).

**Use case for you:** If you don't want to build the Polymarket side of the classifier yourself, you could **query Verilex** for `resolution_source` + `resolution_type` + `expected_date`, then **match** to Kalshi's `settlement_sources` by name.

**Caveat:** Verilex covers **Polymarket only**; you still need to build the **Kalshi side** and the **cross-matching** yourself.

---

## 4. Tooling design: resolution-diff pipeline

### Architecture

```
┌──────────────────┐     ┌──────────────────┐
│ Polymarket Gamma │     │   Kalshi API     │
│  /markets?...    │     │  /events/...     │
│  → question      │     │  → title         │
│  → description   │     │  → rules (scrape)│
│  → resolutionSrc │     │  → settlement_   │
│  → endDate       │     │    sources       │
│  → category      │     │  → expiration    │
└────────┬─────────┘     └────────┬─────────┘
         │                        │
         ▼                        ▼
┌─────────────────────────────────────────────┐
│             Event Matcher                   │
│                                             │
│  1. Normalize titles (lowercase, strip      │
│     dates, canonicalize entity names)       │
│  2. TF-IDF or embedding cosine similarity  │
│  3. Category alignment (sports↔sports, etc)│
│  4. Date window overlap check              │
│  5. Output: candidate pairs + sim score    │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│          Resolution Rule Differ             │
│                                             │
│  For each matched pair:                     │
│  1. Extract resolution_source names         │
│     PM: from resolutionSource + description │
│     KA: from settlement_sources[].name      │
│  2. Source match score (exact / fuzzy)       │
│  3. Extract key clauses from prose:          │
│     - "except" / "unless" / "provided that" │
│     - Temporal qualifiers ("by", "before")  │
│     - Measurement spec ("25bps", ">3%")     │
│  4. Flag divergences:                        │
│     - Different named sources               │
│     - Different date/time cutoffs           │
│     - One has carveouts the other lacks     │
│     - Ambiguous / vague source on either    │
│  5. Output: divergence_score (0–1)          │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│            Arb Gate                         │
│                                             │
│  Only pass to execution if:                 │
│  - match_confidence > threshold             │
│  - divergence_score < threshold             │
│  - price_gap > fee_sum + buffer             │
│  - both markets liquid enough               │
│  - capital available on both venues         │
└─────────────────────────────────────────────┘
```

### Event Matcher — implementation notes

**No existing open-source tool does this.** The NLP search for "prediction market cross platform matching same event" returned zero results. You build it.

**Approach (simplest first, escalate if needed):**

1. **Exact-slug / keyword match:** Normalize both titles, split on entities + dates, check overlap. Catches "Will Fed cut rates in June 2026?" ↔ "Fed rate cut June 2026". Fast, high precision, low recall.

2. **TF-IDF cosine on `(title + description)`:** Vectorize both corpora, compute pairwise sim. Filter > 0.7. Standard sklearn or even pure-Python. Good for ~90% of matches.

3. **Sentence embeddings** (if you want higher recall on paraphrased events): `sentence-transformers` or OpenAI embeddings. Overkill unless the corpus is large and titles are very differently worded.

4. **Category + date gate:** Only compare PM "politics" to Kalshi "politics"; require `endDate` windows to overlap within 7 days.

### Resolution Rule Differ — implementation notes

This is **the hard part** and **the high-ROI part**.

**Step 1: Source extraction**

- PM: `resolutionSource` field (often a URL). Fallback: regex the `description` for URLs and source-agency names.
- Kalshi: `settlement_sources[].name` and `.url`. Supplementary: scrape the market page for the "Rules" tab HTML.

**Step 2: Source comparison**

```python
def sources_match(pm_src: str, ka_sources: list[dict]) -> float:
    """Return 0–1 match score for resolution sources."""
    pm_norm = normalize(pm_src)  # lowercase, strip protocol, etc.
    for s in ka_sources:
        ka_norm = normalize(s["name"])
        if pm_norm == ka_norm or fuzz_ratio(pm_norm, ka_norm) > 85:
            return 1.0
    # partial: same domain
    for s in ka_sources:
        if domain(pm_src) == domain(s.get("url", "")):
            return 0.8
    return 0.0
```

**Step 3: Clause extraction (regex / keyword)**

Key phrases that indicate divergence risk:

| Pattern | Risk |
|---------|------|
| `except`, `unless`, `provided that`, `solely due to` | **Carveout / exception** |
| `consensus of credible reporting` | **Subjective source** |
| `at the sole discretion` | **Platform override** |
| Different numeric thresholds (`>3%` vs `≥3%`, `25bps` vs `50bps`) | **Measurement divergence** |
| Different time zones or cutoff times | **Timing divergence** |
| `Rule 6.3(c)` (Kalshi) | **Last-traded-price settlement** fallback |

**Step 4: Score**

```
divergence_score = w1 * (1 - source_match) 
                 + w2 * clause_divergence_count / max_clauses
                 + w3 * (1 if subjective_source else 0)
                 + w4 * timing_gap_days / 30
```

Weights tuned by hand initially; could calibrate against known divergences (Cardi B, Khamenei, etc.) if you label ~20 historical pairs.

---

## 5. Known divergence case library (seed your training set)

| Event | PM resolution | Kalshi resolution | Divergence type |
|-------|--------------|-------------------|-----------------|
| Cardi B Super Bowl halftime | YES ($1) | Rule 6.3(c) last traded ($0.26 YES) | **Ambiguity** + **different interpretation standard** |
| NFL win totals (Jan 2026) | N/A | Initially wrong, corrected after press | **Operational error** (Kalshi-only) |
| Oscars viewership (2025) | N/A | Wrong side paid; not reversed | **Operational error** |
| Soccer market with no tie (2025) | N/A | Both sides lost | **Missing outcome** |
| Ukraine mineral deal (Mar 2025) | YES (despite no confirmed deal) | N/A | **Governance / whale vote** |
| Venezuela invasion (Jan 2026) | NO (didn't meet "establish control") | N/A | **Strict interpretation** |
| Zelenskyy suit (Jul 2025) | Reversed YES→NO by UMA | N/A | **UMA override** |
| "Trump says China" at summit (2025) | NO (retroactive clarification) | N/A | **Post-hoc clarification** |

Use these to **test** your diff pipeline: can it flag the **source** or **clause** that caused the divergence?

---

## 6. Edge cases your tooling must handle

1. **Kalshi rules not in API:** You'll need a **scraper** for the market page's "Rules" tab, or a periodic bulk download of CFTC self-certification PDFs. Budget for this being fragile.

2. **PM `description` is unstructured prose:** LLM-assisted extraction (or careful regex) required. Don't trust an LLM to never hallucinate a rule — always surface the **raw text** alongside the extracted fields for human review on any position above a size threshold.

3. **Multi-market events:** PM groups markets under events (e.g. "Who will win?" with sub-markets per candidate). Kalshi has a similar event/market nesting. Your matcher must handle **1:N** and **N:M** mappings, not just **1:1**.

4. **Resolution timing:** PM uses UMA (~2h undisputed, days if disputed). Kalshi uses internal team (~hours, but can be days). Your arb capital is **locked** until **both** resolve. Model the **max lock time** for capital efficiency.

5. **Polymarket US vs International:** Different resolution processes (Markets Team vs UMA oracle). Your pipeline should detect which version the market is on.

6. **Null / voided outcomes:** PM can resolve "Unknown/50-50" ($0.50 each). Kalshi can invoke Rule 6.3(c) (last traded price). Neither is $0 or $1. Your PnL model must handle these.

---

## 7. ROI assessment

| Dimension | Assessment |
|-----------|-----------|
| **Gross edge** | 2–5% per position ([pm.wiki](https://pm.wiki/learn/polymarket-kalshi-arbitrage)); realistic **net** after fees: 1–3% |
| **Fee drag** | PM crypto taker: `0.072 × p(1-p)`; other PM categories: 0.03–0.05; Kalshi: ~1.75% max taker. **Maker** on PM: 0% + rebates |
| **Capital lock** | Days to weeks per position; **annualized** ROI depends on resolution speed |
| **Biggest risk** | Resolution divergence → **total loss on both legs** |
| **Tooling cost** | Medium — API integration is standard; **rule diffing** is the bespoke work |
| **Defensibility** | **High** — most arb tooling (ArbPoly etc.) does **price scanning only**, not **rule analysis**. This is the **last defensible edge** per [pm.wiki](https://pm.wiki/learn/polymarket-kalshi-arbitrage): "The consistent edge is in resolution analysis" |

**Key quote from pm.wiki (2026):**
> "Pure mechanical arb opportunities are becoming rarer and smaller. The consistent edge is in resolution analysis — understanding when identical-sounding events have different resolution criteria that create asymmetric risk."

---

## 8. Implementation plan (if you build this)

| Phase | Work | Output |
|-------|------|--------|
| **1 — Data collection** | Poll PM Gamma `/markets` and Kalshi `/events` on a cron; store `question`, `description`, `resolutionSource`, `settlement_sources`, `endDate`, prices | SQLite or JSONL corpus |
| **2 — Event matcher** | TF-IDF + category + date overlap; manual labeling of ~50 pairs for precision/recall | `matched_pairs.json` with `sim_score` |
| **3 — Rule extractor** | Regex + optional LLM for PM `description`; scraper for Kalshi rules tab | Structured `{source, clauses, thresholds, timing}` per market |
| **4 — Diff scorer** | Weighted formula (§4 above); calibrate on known divergences (§5) | `divergence_score` per pair |
| **5 — Arb gate** | Price gap > fees + buffer **AND** divergence_score < threshold → signal | Trade candidates with full audit trail |
| **6 — Paper trade** | Execute both legs on paper; track resolution outcomes | Realized vs expected PnL; false positive rate on divergence score |
| **7 — Small live** | Real capital, small size, both venues | Live validation |

---

## 9. Files in this research folder

| File | Role |
|------|------|
| `REPORT.md` | Phase 1: proxy backtests |
| `REPORT_INTERNET_DEEP_DIVE.md` | Phase 2: Medium / ecosystem synthesis |
| **`REPORT_RESOLUTION_DIFF.md`** | **This file** — resolution-rule diff tooling design |
| `results.json` | Backtest numeric outputs |

**Disclaimer:** Not legal, tax, or investment advice. Cross-venue prediction market trading carries resolution divergence risk, regulatory risk, and total loss risk. Verify terms, jurisdiction, and compliance yourself.
