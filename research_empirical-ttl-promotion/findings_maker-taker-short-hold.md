# Findings: Maker vs taker for high-turnover / short-hold; when taker is rational

**Summary:** **Makers** supply liquidity (typically rest at bid/offer) and are compensated via **narrower fees or rebates**; **takers** demand **immediacy** and pay the **spread edge** and usually **higher fees** ([CME Group — Market Makers vs. Market Takers](https://www.cmegroup.com/education/courses/trading-and-analysis/market-makers-vs-market-takers)). For **short-hold** strategies, **expected edge per unit time** must exceed **round-trip fees + adverse slippage**; paying **taker** can be rational when **opportunity cost of non-fill** (missing the move, gap risk, or strategy decay) exceeds the **incremental fee and spread** of crossing.

## Maker–taker economics (regulatory / structural reference)

- U.S. equity **maker–taker** is documented in SEC EMSAC materials: exchanges may **rebate liquidity providers** and **charge** those who remove liquidity ([SEC EMSAC — Maker-Taker Fees on Equities Exchanges (PDF)](https://www.sec.gov/spotlight/emsac/memo-maker-taker-fees-on-equities-exchanges.pdf)). Crypto perps use the same **conceptual** split; rates are venue-specific (Coinbase CDE in this repo uses configured bps in `[scalp]`).

## When paying taker is “rational” (conceptual checklist)

1. **Alpha half-life shorter than fill latency:** If the signal decays faster than typical limit queue time, **waiting** destroys more expectancy than **crossing**.
2. **Convex loss from missing the trade:** Risk of a **discrete** bad outcome (e.g., trend continuation without position) can dominate linear fee savings.
3. **Thin book / jump risk:** If the next tick may **gap through** your limit, the option value of resting can be negative.
4. **Net edge dominates round-trip costs:** Short-hold does **not** automatically justify taker; it **raises the bar** for per-trade edge because **fees compound** with turnover.

## CME educational framing (symbiosis, not optimization)

- CME’s lesson emphasizes **symbiosis**: takers **give up the edge** (spread) for **liquidity and immediacy**; makers earn the edge by standing ready ([CME Group — Market Makers vs. Market Takers](https://www.cmegroup.com/education/courses/trading-and-analysis/market-makers-vs-market-takers)). This is **pedagogical**, not a formal optimality proof.

## Limitations / gaps

- **Investopedia** maker–taker article is widely cited but was not fetched (prior 402 on sister page); SEC PDF + CME education are used as **primary / venue-authority** style references.
- No per-symbol **Coinbase CDE** fee table is reproduced here—use live `config.toml` / venue docs.

## Sources

- U.S. Securities and Exchange Commission (EMSAC). “Maker-Taker Fees on Equities Exchanges” (memo PDF). https://www.sec.gov/spotlight/emsac/memo-maker-taker-fees-on-equities-exchanges.pdf  
- CME Group Education. “Market Makers vs. Market Takers.” https://www.cmegroup.com/education/courses/trading-and-analysis/market-makers-vs-market-takers  
