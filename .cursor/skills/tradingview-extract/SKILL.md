---
name: tradingview-extract
description: >
  Extracts parameters, plot/alert structure, and defaults from TradingView indicators (including
  closed-source) using public/metadata endpoints: page fetch, pubscripts-suggest-json, and
  pine-facade translate. Activates when the user says "extract tradingview", "tradingview script",
  "pine script extract", "pine-facade", "tv indicator", "decode tradingview", "reverse engineer
  indicator from tradingview.com/v/", or wants to rebuild a TV study in Python/Pine from a slug or
  URL. Also activates when aligning bot code (e.g. scalp modes) with a TV script. Do NOT use for
  circumventing TradingView IP (no deobfuscation of IL, no bypassing auth on /get/ for protected
  source); do NOT promise human-readable Pine for closed scripts; do NOT use for PnL champion
  tournaments or multi-mode lab picks (use pnl-feedback-lab); do NOT skip resolving scriptIdPart
  from the slug. When metadata is enough, follow translate + metaInfo workflow below.
---

## Overview

This skill teaches a **repeatable, legitimate** workflow: resolve the real `scriptIdPart` and
version, call **`pine-facade/translate`** for `metaInfo` (defaults, styles, stats), map inputs to
indicators, infer alert/plot roles, then document or implement. Closed-source scripts **do not**
expose Pine via `/get/` without permission; **`/translate/`** still yields structure needed to
reconstruct logic in this repo (see `lessons.md` §35 / `sar_chop`).

## Workflow

1. **Identify slug and human name** — From `https://www.tradingview.com/v/{slug}/`, note script
   title and author. The slug alone is **not** `scriptIdPart`.

2. **Resolve `scriptIdPart` + `version`** — `GET https://www.tradingview.com/pubscripts-suggest-json/?search={URL-encoded name}`.
   From `results[]`, take `scriptIdPart` (form `PUB;…` or `STD;…`) and `version`. If search fails,
   try partial name, author profile, or alternate spelling.

3. **Fetch metadata (always use translate for closed scripts)** —  
   `GET https://pine-facade.tradingview.com/pine-facade/translate/{scriptIdPart}/{version}?no_4xx=true`  
   Use `last` instead of numeric version if needed: `/translate/{scriptIdPart}/last`  
   Do **not** rely on `/pine-facade/get/...` for protected Pine source; expect denial for closed
   scripts. Document `success` and errors honestly.

4. **Mine `result.metaInfo`** — In order:
   - `defaults.inputs` / `metaInfo.defaults.inputs` — every `in_*` default; map to indicators using
     fingerprints below and the public page description.
   - `stats` — `alertcondition`, `plot`, `plotshape` counts (structure).
   - `defaults.styles` — plot types, titles, colors, `BelowBar` / `AboveBar` (signal semantics).
   - `filledAreas` / `defaults.palettes` — zones, clouds, CHOP/trend backgrounds.

5. **Reconstruction rules** — List every indicator with parameters; map N alerts to entry/exit
   patterns; **account for every input** — if metaInfo has N inputs, the spec must use N. **Multiple
   MA periods:** treat **all** as potential gates until disproven (shortest MA is often momentum /
   recency, not “decorative”). Cross-check with user screenshots: every visible line must map to a
   plot style or be called out as unknown.

6. **Repo touchpoints (this codebase)** — After extraction, wire findings into `lessons.md`
   (lesson entry), `scalp_vec_backtest.py` / `signal_engine.py` / `indicators.py` as appropriate,
   and WFO grids if adding a mode. Cite `AGENTS.md` / `[scalp]` for fees and sim vs live limits.

## Output format

- **Discovery section:** slug, resolved `scriptIdPart`, version, endpoints called (no secrets).
- **Tables:** inputs → inferred meaning; stats; key plots with colors/locations.
- **Signal hypothesis:** long/short/exit mapping from alerts + styles (explicit uncertainties).
- **Implementation notes:** file-level suggestions for this repo; gaps if metaInfo is ambiguous.
- **Forbidden in write-ups:** claiming full Pine source for closed scripts; IL “decode” steps; any
   instruction to bypass TV ToS or authentication.

## Examples

### Happy path

**Input:** “Decode `https://www.tradingview.com/v/vNSZwQsS/` for our bot.”

**Expected:** pubscripts search → `PUB;1CeTr8xhlMOD1KIBlPpqEoblpE0cTavq` + version → translate JSON
summarized; MA(7)/MA(50)/MA(200)/MACD/SAR/Lucid/UT/CHOP-style stack documented; link to
`lessons.md` §35 / `sar_chop` implementation.

### Edge case

**Input:** “Search returns nothing.”

**Expected:** Try alternate query strings, author page, or `translate/.../last`; list what was tried.

### Negative (must refuse)

**Input:** “Decompile the IL / give me the hidden Pine source.”

**Expected:** Refuse circumventing protection; stick to `metaInfo` + public description + screenshot
parity only.

---

## Quick reference: endpoints

| Purpose | URL pattern |
|--------|-------------|
| Page + description | `https://www.tradingview.com/v/{slug}/` |
| Name → `scriptIdPart` | `https://www.tradingview.com/pubscripts-suggest-json/?search={encoded}` |
| Pine source (open / permitted only) | `https://pine-facade.tradingview.com/pine-facade/get/{scriptIdPart}/{version}?no_4xx=true` |
| **IL + metaInfo (metadata path)** | `https://pine-facade.tradingview.com/pine-facade/translate/{scriptIdPart}/{version}?no_4xx=true` |

## Parameter fingerprints (heuristics)

| Values / pattern | Likely meaning |
|------------------|----------------|
| 12, 26, 9 | MACD |
| 0.02, 0.02, 0.2 (twice) | PSAR + Lucid SAR (or similar dual-SAR) |
| 200, 50, 7 (etc.) | MA stack — **count all** as filters until proven otherwise |
| 14 | RSI / ATR / stochastic period (context from styles) |
| 2–3 near ATR/UT naming | UT Bot–style sensitivity |
| 10–14 standalone | CHOP-like period |

## Alert / plot count heuristics

- **4× alertcondition** — often long entry, short entry, long exit, short exit (verify names in meta).
- **3×** — long, short, flat or combined exit.
- **2×** — directional entries; exits may be opposite signal.

## Built-ins and versions

- Built-ins often use `STD;{encoded_name}` rather than `PUB;`.
- Prefer published `version` from suggest JSON; fall back to `last` on translate if mismatched.
- `usesPrivateLib: true` — note extra library deps; reconstruction may be incomplete without those libs.

## In-repo example (applied extraction)

**“5 min bot scalper”** (`vNSZwQsS`): `PUB;1CeTr8xhlMOD1KIBlPpqEoblpE0cTavq`, translate `metaInfo`
→ 14 inputs including **MA(7)** as fast filter, 4 alerts, 12 plotshapes, 3 plots — implemented as
`sar_chop`; full narrative in **`lessons.md` §35**.
