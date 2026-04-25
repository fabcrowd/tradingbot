# Scalping horizon: dated (calendar) futures vs perpetual swaps — research notes

## Summary

For **very short holds (minutes)**, the dominant structural difference is **how the contract stays tied to spot**: perpetual-style products use **recurring funding (or equivalent cashflows)** between longs and shorts, while **expiring futures** rely on **calendar convergence** toward settlement and may require **rolling** if exposure is continued across contract months. **Roll risk** is usually a **second-order** concern for minute-scale scalps **as long as** you are trading the **liquid front contract** and are **not** operating through **last trading days** or **illiquid back months**. **Basis** still matters intraday (mark/index vs spot, contract premium/discount), but for minutes the bigger practical levers are typically **spread/depth**, **fee + funding cadence vs your holding period**, and **margin/liquidation mechanics**—all **venue-specific**. This note mixes **fetched educational pages** with **high-level synthesis**; **Coinbase help/learn URLs** are listed because they are relevant to your venue investigation, but their HTML **could not be retrieved here (HTTP 403)**—**verify formulas, funding intervals, and clearing cycles on the live site**.

---

## Roll risk (dated / quarterly crypto futures)

**Standard futures lifecycle (educational, not crypto-exclusive):** CME Group’s introduction states that futures have a **limited lifespan**, and that **rollover** means moving from the **front month** to a **deferred month**, often guided by watching **volume** in the expiring contract vs the next contract. Rolling is described as **simultaneously offsetting** the current position and **opening** the new month (e.g., sell Sept / buy Dec in their example). If a trader does **not** offset or roll before expiration, the contract **expires** and may proceed to **settlement** (cash or physical depending on the market).

**Implication for minute scalps:** Roll mechanics matter most when (a) you **hold through** a roll window, (b) you trade **near-expiry** contracts where **liquidity migrates**, or (c) you must **close/reopen** two legs and pay **bid–ask** twice. For **intra-hour** trades in the **dominant tenor**, roll is often **avoidable operationally** by trading only where the book is thick and monitoring **last trading day** calendars—this paragraph is **logical inference from standard roll education**, not a cited empirical study of crypto scalp PnL.

---

## Basis (futures price vs spot / index)

**Perpetuals:** Investopedia explains that perpetual futures use a **funding rate** to keep the contract **near spot**; it also maps **positive funding** to **contango** (futures above spot; longs pay shorts in their description) and **negative funding** to **backwardation** (futures below spot; shorts pay longs). That framing is **about the long–short payment direction and price relationship**, not a guarantee about your broker’s exact index definition.

**Dated futures:** Traditional contracts **converge** toward settlement as expiration approaches (general futures property; CME discusses expiration/roll choices). **Intraday**, basis can still move with **rates, sentiment, inventory of leverage**, and **local order flow**—again **venue- and product-specific**.

**Scalping lens:** Over **minutes**, you care about **whether your mark/index model** creates **discrete jumps**, **auction effects** near funding timestamps, or **widening** during stress—those are **implementation details** to read from **exchange specs**, not universal constants.

---

## Funding rates (perpetual / perpetual-style futures)

Investopedia’s overview (crypto-focused) states that perpetual futures **have no expiry** and are **adjusted** by a **funding rate** mechanism; funding is a **periodic payment** between longs and shorts driven by the **difference between contract and spot**, with formulas **varying by exchange**, and funding commonly discussed on an **8-hour** cadence on **many** platforms while noting **some exchanges differ**.

**Holding-period math (generic):** If funding is assessed on a fixed schedule, a **minutes-long** position **may or may not** cross a funding boundary; when it does, the payment hits **account equity** and can interact with **margin** over longer stacks of leverage. **Magnitude** scales with **rate × notional × time exposed across intervals**—for **short holds**, funding is often **smaller than** a single **half-spread** round-turn **unless** rates are **extreme** or size is **large**; this inequality is **illustrative reasoning**, not a dataset claim.

**Venue note (Coinbase):** Official Coinbase **Learn** and **Help** articles on perpetual futures funding are listed under **Sources**. They were **not machine-readable in this research pass (403)**; treat any **third-party restatement** of Coinbase’s **hourly smoothing** or **clearing timestamps** as **unverified** until confirmed on Coinbase’s pages.

---

## Liquidity and microstructure

Investopedia’s piece notes perpetual futures are **popular and liquid** in crypto, cites **large notional turnover** in its narrative, and lists **leverage** and **liquidation** as core risks—useful as **general market context**, not a live order-book measurement.

**Quarterly vs perpetual (exchange blog, not independent research):** BTSE’s blog contrasts **quarterly** fixed expiry with **perpetual** continuous trading, claims **perpetuals** are commonly used for **short-term trading/scalping** in their framing, and states **quarterly** contracts **do not** have the same **recurring funding fee** structure as typical **perpetuals** (their article contrasts **~8-hour** style funding for perps). This is **helpful for vocabulary and retail positioning**, but it is **BTSE marketing/education**—see disclaimer on that page.

**Practical liquidity checklist (synthesis):** Compare **top-of-book depth**, **effective spread**, **open interest and volume by contract**, and **whether your bot’s product is the “main” contract**—prefer **measurable** endpoints over **assumptions**.

---

## Practitioner-style recommendations (labeled)

**Grounded in cited educational material**

1. **If using dated futures:** Plan **expiration/roll** the way introductory futures curricula describe—**monitor volume migration**, avoid being forced to **roll or exit** at bad prints (**CME education**). For **minute strategies**, **avoid the expiry day microstructure** unless you explicitly model it.

2. **If using perpetuals / perpetual-style:** Treat **funding** as a **first-class cost and risk factor**—Investopedia emphasizes funding can **help or hurt** depending on **side** and **sign/magnitude** of the rate.

3. **Always read the venue rulebook:** **Funding cadence**, **mark vs index**, **insurance fund / ADL** (if applicable), and **margin currency** are **exchange-specific**; do not assume **8-hour** funding if your venue uses **hourly** or **batch-processed** credits/debits.

**Anecdotal / desk folklore (not evidenced in the fetched pages above)**

- Many short-horizon crypto desks **default to perpetuals** for **operational simplicity** and **continuous liquidity**; that pattern is **widely claimed in industry commentary** but should be validated with **your symbol’s live metrics**, not this memo.

---

## Sources

| Source | URL | Notes |
|--------|-----|--------|
| CME Group — Understanding Futures Expiration & Contract Roll | https://www.cmegroup.com/education/courses/introduction-to-futures/understanding-futures-expiration-contract-roll | **Fetched**; traditional futures lifecycle/roll framing. |
| Investopedia — What Are Perpetual Futures? | https://www.investopedia.com/what-are-perpetual-futures-7494870 | **Fetched**; funding vs spot, contango/backwardation mapping in narrative, interval variability. |
| BTSE Blog — Perpetual Futures vs. Quarterly Contracts | https://www.btse.com/blog/perpetual-futures-vs-quarterly-contracts/ | **Fetched**; **exchange blog** (disclaimer: not investment advice; promotional context). |
| Coinbase Learn — Understanding Funding Rates in Perpetual Futures | https://www.coinbase.com/learn/perpetual-futures/understanding-funding-rates-in-perpetual-futures-and-their-impact | **Listed for venue alignment**; **403 on fetch** in this environment—open manually. |
| Coinbase Help — US Perpetual-Style Futures Funding Rate | https://help.coinbase.com/derivatives/perpetual-style-futures/funding-rate | **Listed for venue alignment**; **403 on fetch** in this environment—open manually. |

---

## Retrieval log (transparency)

- **Successfully fetched:** CME Group (roll/expiration), Investopedia (perpetuals/funding), BTSE blog (quarterly vs perpetual comparison).
- **Failed fetch (HTTP 403):** Coinbase Learn and Coinbase Help funding pages—**do not treat** search-snippet paraphrases as **verbatim** exchange rules without opening the official pages.
