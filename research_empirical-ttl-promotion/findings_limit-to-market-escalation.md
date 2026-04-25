# Findings: Limit → market escalation after non-fill (hybrid execution)

**Summary:** Execution research frames the mix of **limit vs market** orders as a **dynamic control** problem: passive orders seek price improvement and lower fees, while **non-fill / horizon risk** pushes toward **immediacy**. Modern academic work uses **reinforcement learning** to learn when to shift allocation toward market orders in simulated limit order books.

## Practitioner / academic framing

- **Optimal execution** (broad literature) trades off **implementation shortfall / market impact** of aggressive trading against **execution risk** from failing to complete before a deadline. Non-fill is a first-class state variable: as the horizon shrinks or urgency rises, policies allocate more to **marketable** flow.
- **RL with explicit limit + market choice:** Cheridito & Weiss formulate execution as **dynamic allocation** between market and limit orders to maximize expected revenue, with learned policies outperforming benchmarks in LOB simulations featuring noise traders, tactical traders, and a strategic acquirer/liquidator ([Reinforcement Learning for Trade Execution with Market and Limit Orders](https://arxiv.org/abs/2507.06345)).

## Hybrid policies (conceptual)

- **PEG / chase / discretionary limits:** Many institutional algorithms start passive and **cross the spread** or **escalate** when progress vs a benchmark (VWAP, TWAP, arrival price) falls behind—conceptually similar to “if limit didn’t work, pay for certainty.”
- **Risk of naive escalation:** Escalating on *every* cancel without a signal that the opportunity remains good can **pay taker fees and slippage** on sequences where the limit was correctly *avoiding* bad fills (adverse move away rather than favorable missed move).

## Relation to Fabcrowd Arceus `EmpiricalMarketPromotion`

- **Pattern arm:** Requires repeated **TTL cancel → favorable drift vs limit** (missed-move confirmations) before arming a **burst** of market entries, with **cooldown** between arms—closer to “evidence-based escalation.”
- **TTL-direct arm:** Optional `empirical_market_ttl_cancel_arms_promotion` arms market slots on **each** TTL cancel with **no** drift proof—closer to **unconditional escalation** after non-fill (see `empirical_market_promotion.py`).

## Limitations / gaps

- Industry **vendor-specific** algo documentation (e.g., full broker white papers) was not deeply mined; arXiv RL paper is a **simulation** result, not proof of profitability on Coinbase CDE.
- Classic **Almgren–Chriss**-style models are the traditional reference for horizon pressure; this note prioritizes a **primary arXiv** source that names limit vs market explicitly.

## Sources

- Cheridito, Patrick, and Moritz Weiss. “Reinforcement Learning for Trade Execution with Market and Limit Orders.” arXiv:2507.06345 (v2 revised Jan 2026). https://arxiv.org/abs/2507.06345  
