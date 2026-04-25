# Findings: Limit TTL, unfilled limits, queue, adverse selection, time-in-force

**Summary:** Resting limit orders often fail to execute because price–time priority creates **queue position risk** and because **informed flow** concentrates at the front of the queue—so back-of-queue liquidity is both less likely to fill and more **adversely selected** when it does. **Time-in-force** (GTC, IOC, FOK, etc.) is the explicit contract for how long an order stays exposed; bot-level “TTL cancel” is an application-specific analogue of shortening that exposure.

## Why limits stay unfilled

- **Price never trades through the limit:** If the market does not reach the posted price before the order is canceled or expires, no match occurs.
- **Queue and latency:** At a given price, execution follows **time priority** among orders at that price. Submitters face **queuing uncertainty**—they do not know their exact position when competing with others in the same tight time window ([Yueshen 2025](https://doi.org/10.1287/mnsc.2023.03371)).
- **Strategic cancel/repost:** Liquidity suppliers may cancel when queue position is poor or when the book overshoots equilibrium depth; models predict **clustered adds followed by quick cancellations** (“fleeting” depth) as an equilibrium pattern under queuing uncertainty ([Yueshen 2025](https://doi.org/10.1287/mnsc.2023.03371)).

## Adverse selection (limit-side)

- In limit order book models, **marginal profit for liquidity supply typically declines with depth**: orders at the **front** of the queue can earn positive expected profit; **end-of-queue** orders are less likely to fill and, conditional on filling, more likely to face **larger informed market orders**—a “top-of-queue advantage” ([Yueshen 2025](https://doi.org/10.1287/mnsc.2023.03371)).
- Related microstructure literature links **pick-off risk** to the race between stale quotes and reactive market orders; the queuing paper explicitly connects its mechanism to that family of frictions ([Yueshen 2025](https://doi.org/10.1287/mnsc.2023.03371)).

## Time-in-force (TIF)

- **TIF** specifies how long an order remains active before automatic cancel or session rules apply ([Ledger Academy — Time in Force (TIF)](https://www.ledger.com/academy/glossary/time-in-force-tif)).
- Common types (crypto-oriented summary; venue specifics vary):
  - **GTC:** remains until filled or manually canceled (subject to venue/broker policies).
  - **IOC:** execute immediately what is possible at limit or better; **remainder canceled**.
  - **FOK:** fill **entire** size immediately at limit or better, else **cancel all** ([Ledger Academy — Time in Force (TIF)](https://www.ledger.com/academy/glossary/time-in-force-tif)).

**Note:** The Fabcrowd Arceus scalp bot’s entry TTL is **not** exchange TIF per se; it is a **strategy timer** that cancels an unfilled entry and then feeds `EmpiricalMarketPromotion` (see repo `empirical_market_promotion.py`).

## Link to this repo (behavioral, not exchange spec)

- On TTL cancel, the bot logs `entry_ttl_cancel` and starts a **missed-move watch** for `empirical_market_miss_eval_window_sec` requiring favorable drift ≥ `empirical_market_missed_move_bps` before a pattern hit counts (`empirical_market_promotion.py`).

## Limitations / gaps

- **Investopedia** and some broker pages are standard references for TIF but were not fetched successfully here (HTTP 402 on one attempt); Ledger’s glossary was used as an accessible alternative—**verify IOC partial-fill wording** against your venue’s rulebook (Ledger’s IOC text conflates “entire vs partial” in one sentence; industry definitions usually allow **partial** fill + cancel rest).

## Sources

- Yueshen, Bart Zhou. “Queuing Uncertainty of Limit Orders.” *Management Science* (Articles in Advance, 2025). Open-access listing with abstract: https://doi.org/10.1287/mnsc.2023.03371  
- Ledger Academy. “Time In Force (TIF).” https://www.ledger.com/academy/glossary/time-in-force-tif  
