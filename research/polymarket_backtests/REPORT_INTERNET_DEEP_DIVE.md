# Deep internet research — Polymarket bots, latency, and adjacent strategies (Phase 2)

**Scope:** Second-pass desk research after local proxy backtests. Sources include Medium, ecosystem blogs (PolyTrack, pm.wiki), Polymarket official docs, and secondary summaries where X posts could not be fetched directly.  
**Note:** “Iterate 10×” below is implemented as **ten explicit research/test cycles** you can run; this document is not a literal wall-clock loop.

---

## 1. How to read this landscape (source quality)

| Tier | Meaning | Examples in this dossier |
|------|---------|---------------------------|
| **A — Primary** | Platform or protocol documentation | [Polymarket Maker Rebates](https://docs.polymarket.com/market-makers/maker-rebates), [Polymarket Fees](https://docs.polymarket.com/trading/fees) |
| **B — Credible themes, unverified numbers** | Good mental models; treat statistics and PnL as **anecdotal** until you replicate | Illumination / Medium strategy survey (Feb 2026) |
| **C — Marketing / affiliate risk** | Narrative may be true in direction but **not** trustworthy on magnitudes; often ends in product links | Coinmonks “MIT quant / 0.3s loophole” (Apr 2026) → **MidasAI** CTA |
| **D — Aggregator / SEO** | Useful directories; cross-check fee claims against **A** | pm.wiki guides, PolyTrack blog, agent listicles |

**Important correction:** Some third-party guides still say Polymarket has **“0% fees on most markets.”** Official docs show **category-specific taker fees** on **fee-enabled** markets (crypto uses `feeRate = 0.072` in the fee formula). Always read `feesEnabled` / `fee-rate?token_id=` for the **actual** contract you trade.

---

## 2. Medium — what was read and what to trust

### 2.1 [Beyond Simple Arbitrage: 4 Polymarket Strategies Bots Actually Profit From in 2026](https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f) (ILLUMINATION, Feb 2026)

**Thesis (useful):** “Naive YES+NO &lt; $1” arb windows have **compressed**; capital migrates to **market making**, **information-speed** strategies, **cross-market consistency**, and **short-horizon momentum/latency**.

**Quantitative claims (treat as unverified):**

- Average arb opportunity duration **2.7s** (vs **12.3s** in 2024); **73%** of arb PnL to **sub-100ms** bots.
- **27%** of bot profits from **non-pure-arb** strategies.
- Market-making example: **~12%** in three weeks on one BTC market (single anecdote).
- AI “probability arb”: ensemble vs market gap, **15%+** divergence threshold in narrative example.
- **Logical / correlation arb:** nested events (e.g. team vs conference) and **probability-mass violations** across buckets.
- **Momentum / latency** on **5-minute** crypto markets: monitor oracle stream vs UI/book (**2–15s** window claimed).

**Embedded product narrative:** Repeated “PolyCue” allocation profiles and backtested portfolio tables — read as **vendor storytelling**, not independent research.

**Actionable design ideas (decoupled from their numbers):**

1. **Inventory-skewed quoting** on both YES/NO with vol-based widen and news calendar pulls.
2. **News-first pipeline:** wire Reuters/AP/Bloomberg → model probability → EV vs mid **after fees**.
3. **Dependency graph** over active markets for **Frechet-style** bounds (min/max feasible probabilities).
4. **Latency stack:** websocket oracle + CLOB + colocated RPC; stale-connection detection (matches patterns in [dev.to oracle bot writeup](https://dev.to/jonathanpetersonn/building-a-real-time-oracle-latency-bot-for-polymarket-with-python-and-asyncio-3gpg)).

### 2.2 [I Tested Every "Free" Polymarket Bot on GitHub…](https://samarameer.medium.com/i-tested-every-free-polymarket-bot-on-github-heres-what-actually-happened-05708bf6e654)

**Status:** Page **fetch timed out** from the research environment; only the title/lineage appears in search snippets. **Recommendation:** read locally when building a bot shortlist; theme (many GitHub bots **don’t survive** fees/speed) aligns with Illumination + official fee docs.

### 2.3 [An MIT Quant Found a 0.3-Second Loophole…](https://medium.com/coinmonks/an-mit-quant-found-a-0-3-second-loophole-in-prediction-markets-and-built-a-bot-to-exploit-it-dd95b0bfa457) (Coinmonks, Apr 2026)

**Content:** Long-form story: **$2k → $500k** in a month, **0.3–0.5s** oracle vs exchange lag, **5-minute** markets, comparison to HFT in equities.

**Red flags:**

- Ends with **commercial CTA** ([midasai.trade](https://midasai.trade/)) and “undisclosed execution frameworks.”
- Author **1 follower**; no verifiable on-chain or API audit in the piece.
- Conflicts with **documented** Polymarket response: **dynamic taker fees** on short crypto markets (press + [Fees docs](https://docs.polymarket.com/trading/fees)) explicitly to **tax** activity near **50%**.

**Use:** Treat as **cultural documentation** (“latency narrative sells”) and as a reminder to **recompute EV under current fees**, not as evidence of returns.

### 2.4 Other Medium threads (search-only / not fully fetched)

- **Oracle latency infrastructure** pieces (e.g. Stork, generic “oracle requirements”) — relevant if you design **multi-source** price consensus.
- **Solana prediction markets** — different stack; lessons on **finality vs displayed price**, not Polymarket-specific.

---

## 3. X (Twitter) — indirect research (direct X fetch not used)

X URLs often block automated fetch; research used **X-adjacent** primary-style writeups:

### 3.1 [PolyTrack: Polymarket Twitter Guide 2026](https://www.polytrackhq.app/blog/polymarket-twitter)

**Theories:**

- **Information velocity:** breaking headlines → Polymarket can move **10–20% in ~60s**; edge is **first seconds**.
- **Whale following:** large on-chain prints as **signals** (survivorship / front-running risk).
- **Sentiment divergence:** extreme Twitter mood vs **stale** odds.
- **Operational hygiene:** lists, notifications, fake BREAKING defense, **5-second** pause rule.

**Tests these suggest (mappable to your stack):**

- Timestamp delta: **news publish time → first CLOB print change** (per market ID).
- **Lead-lag** between whale trade print and **retail volume** surge.
- **Placebo:** shuffle news timestamps vs returns — edge should vanish.

### 3.2 Search-snippet / third-party summaries

Claims such as **“~290ms Chainlink vs Binance”** and **agent swarm $500→$5k in 24h** appear in SEO/playbook pages (e.g. OpenClaw playbook). **Treat as unverified** unless you measure **your** path latency with packet timestamps.

---

## 4. pm.wiki — cross-venue arb and AI agents (fetched)

### 4.1 [How to Arbitrage Between Polymarket and Kalshi (2026)](https://pm.wiki/learn/polymarket-kalshi-arbitrage)

**Core structure:** Cheapest YES on one venue + cheapest NO on the other **&lt; $1** → synthetic boxed position **if** both legs fill and **resolutions agree**.

**Primary risk (well stated):** **Resolution divergence** — same **headline**, different **rules** → worst case both legs lose.

**Execution realities:** **2–5%** gross edge band cited; **Kalshi taker fee** material; **capital lock** until resolution; **edges shrink** with automation.

**New theory vs your fork:** Cross-venue is **orthogonal** to BTC/ETH **pair lag** on Polymarket; it’s **rule-reading + simultaneous execution**.

### 4.2 [AI Agents for Prediction Market Trading (2026)](https://pm.wiki/learn/ai-agents-prediction-markets)

**Solid framework:**

- **Analytical** vs **execution** agents.
- AI wins on **speed/breadth**; humans on **resolution text** and **novel** events.
- Failure modes: **hallucinated rules**, **liquidity impact**, **single-source** news, **overfitting** to history.

**Implication for “10× tests”:** Any LLM signal must include a **machine-readable resolution checklist** step and **fee-aware EV**.

---

## 5. Official Polymarket — maker rebates (fetched)

[Maker Rebates Program](https://docs.polymarket.com/market-makers/maker-rebates):

- Rebates **daily USDC**, **performance-based** on **filled maker** liquidity.
- **Crypto:** **20%** of taker fees returned to makers (per table); other categories **25%** where fees apply; **Geopolitics** fee-free.
- Rebate weighting uses the **same** `p(1-p)` structure as taker fees — rewards providing liquidity where **fee generation** is high.

**Strategic pivot:** Post-fee regime, **maker** economics + **rebate share competition per market** may dominate naive **taker** latency sniping near 50¢.

---

## 6. Tension table — narratives vs docs

| Narrative (blogs / X) | Official / structural counterweight |
|----------------------|-------------------------------------|
| “Sub-second oracle free money forever” | **Dynamic taker fees** peak near **50%** on fee-enabled crypto markets ([Fees](https://docs.polymarket.com/trading/fees)); edge must clear **fee + slippage**. |
| “Polymarket is 0% fee” on many guides | **Category fees** and `feesEnabled` on new deployments — verify per token. |
| “Risk-free cross-venue arb” | **Resolution** and **settlement** rules can differ — not risk-free. |
| “AI always beats humans” | Docs + pm.wiki agree **resolution ambiguity** and **thin books** break naive agents. |

---

## 7. Ten research / test iterations (the “10×” backlog)

Each item is a **standalone** cycle: hypothesis → data → metric → kill/continue.

1. **Per-token fee audit:** For each traded `token_id`, log `fee_rate_bps` and post-trade realized fee; reject trades with **EV &lt; fee + slippage buffer**.
2. **Oracle–CLOB timestamp study:** Record `T_oracle`, `T_best_ask_move`, `T_own_fill`; estimate **empirical** lag distribution (not blog 290ms).
3. **Maker rebate attribution:** Paper PnL = **spread capture + rebate_estimate** vs taker-only; optimize for **posted** liquidity if rebates &gt; taker edge.
4. **Cross-venue dry run:** Paper-trade Kalshi↔Polymarket with **full resolution text diff** scoring; **no capital** until divergence score &lt; threshold.
5. **Logical-consistency scanner:** Build a **small** graph of **implication** edges (A⇒B) on **liquid** markets; flag violations **&gt; fees**; backtest with **leg risk** model.
6. **News latency curve:** Same as PolyTrack suggests — measure **AP/Reuters** timestamp vs CLOB mid; **placebo** with unrelated headlines.
7. **Whale signal falsification:** If following whales, test **random** whale vs **win-rate-filtered** whale vs **lagged** entry (1m/5m delay) — if lag matches, you’re paying **informed flow**.
8. **BTC→ETH on Polymarket only:** Repeat your **cross-asset** test on **actual** ETH and BTC **15m token mids** vs **single** oracle feed timing — GBM correlation was **not** enough in our local proxy.
9. **Regime split:** Run oracle-lag proxy separately for **high-vol** vs **low-vol** days (real data); many latency edges are **vol-conditioned**.
10. **Adversarial execution:** Simulate **one more** competing taker in queue — if edge disappears with **2 bots**, it’s **capacity-constrained**.

---

## 8. Integration with local backtests (`results.json`)

The repo’s proxy backtests show:

- **Oracle-lag-style** rules can **outperform random** and **hold** on a **70/30** window split **on synthetic paths** — **not** Polymarket replay.
- **Simple BTC→ETH lead** classifiers were **~50%** at tested horizons on synthetic data.

Internet research **agrees directionally** that **speed + fees + resolution** define the game, but **disagrees** with **get-rich** Medium math unless you **verify** on **your** data.

---

## 9. Suggested reading order for you (when back)

1. [Polymarket Fees](https://docs.polymarket.com/trading/fees) + [Maker Rebates](https://docs.polymarket.com/market-makers/maker-rebates)  
2. [JonathanPetersonn RESEARCH.md](https://github.com/JonathanPetersonn/oracle-lag-sniper/blob/main/RESEARCH.md) (open, falsifiable structure)  
3. [Beyond Simple Arbitrage…](https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f) — **strategies only**, ignore PolyCue tables  
4. [pm.wiki Kalshi–Polymarket arb](https://pm.wiki/learn/polymarket-kalshi-arbitrage) — **resolution risk** section  
5. [PolyTrack Twitter guide](https://www.polytrackhq.app/blog/polymarket-twitter) — **ops** for signal latency  

---

## 10. Files in this research folder

| File | Role |
|------|------|
| `REPORT.md` | Phase 1: proxy backtest methodology + numeric results |
| `REPORT_INTERNET_DEEP_DIVE.md` | This file — Medium / ecosystem / docs synthesis |
| `results.json` | Raw outputs from `run_all.py` |

**Disclaimer:** Not legal, tax, or investment advice. Prediction markets and automation carry **total loss** risk; verify **terms and jurisdiction** yourself.
