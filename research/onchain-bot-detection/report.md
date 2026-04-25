# On-chain bot detection, strategy reverse-engineering, and counter-trading

## Summary

Yes, you can identify bot wallets from on-chain data and classify what strategy they run. Machine learning classifiers hit 83% accuracy distinguishing bots from humans, and 77% accuracy classifying MEV subtypes (arbitrage, sandwich, liquidation). The signals are well-documented: transaction timing entropy, gas price extremes, sleep/wake patterns, and swap-graph topology. Tools like EigenPhi, Nansen, Arkham, and Flashbots' open-source `mev-inspect-py` make this practical today.

The harder question is whether you can profitably *trade against* identified bots. The honest answer: mostly no, at least not through direct counter-strategies like poisoned tokens. Modern bots simulate everything before committing. Where the intelligence is actually useful is in *avoiding* bot activity (routing around sandwich bots, timing trades to avoid front-running) and in *adapting your own market-making spread* based on the detected competitive landscape. You won't farm sandwich bots. You might avoid getting farmed by them, and you can study profitable bots to inform your own strategy parameters.

## Findings

### How to detect bot wallets

Transaction timing is the strongest signal. Bots transact uniformly across all hours. Humans cluster around waking hours and show clear overnight gaps. A metric called "GapBasedSleepiness" — measuring the longest inactivity window in rolling 2-day periods — separates bots from humans reliably. Bots don't sleep. ([Niedermayer et al., 2024](https://arxiv.org/html/2403.19530v2))

Gas behavior is the second-best signal. Bots consistently pay higher maximum gas prices because they need ordering guarantees. Liquidation bots are identifiable at 93% accuracy from gas limit statistics alone. The sandwich bot `jaredfromsubway.eth` spent $90M in gas during 2023 — a spending level no human wallet approaches. ([Nansen](https://research.nansen.ai/articles/mev-masters-value-extraction-in-the-dark-forest))

Other reliable indicators:
- Bots interact with a narrow set of DEX router functions (`swapExactTokensForTokens`, etc.) through custom contracts that execute multi-step strategies atomically
- Bot trade values follow Benford's Law (calculated to exact decimals), while human values cluster around round numbers
- Bot wallets are often short-lived: created, funded, used intensively, drained
- Vanity addresses with extra leading zeros are more common among bot wallets (gas optimization)
- A Random Forest classifier using 83 features across 4 categories (address, transaction, function-call, event-based) achieves 83% binary bot/human accuracy on a [labeled dataset of 270 addresses](https://github.com/Tommel71/Ethereum-Bot-Detection)

The MEV market is concentrated. The top 1% of profitable MEV wallets capture 49% of all profits. Only 43% of MEV-labeled wallets are profitable — 398 wallets lost a combined $126M. This concentration means the high-value bots worth studying are a small, identifiable group.

### How to classify what strategy a bot runs

Six major on-chain signatures distinguish strategy types:

| Strategy | Fingerprint | Classification accuracy |
|---|---|---|
| Sandwich | Buy→Victim Swap→Sell in same block, same pair | 68% (often confused with arb) |
| Atomic arbitrage | Cyclic swap graph (A→B→C→A) in single tx | ~80% |
| Liquidation | Calls to Aave/Compound liquidation functions, extreme gas limits | 93% |
| Sniping | Among first txs after pool creation or token deploy | Heuristic detection |
| Market making | Continuous two-sided positions, long-lived inventory | Behavioral |
| Back-running | Arbitrage tx immediately after large swap, 79.7% have no intermediate txs | ~80% |

The [APOLLO tool](https://www.ndss-symposium.org/ndss-paper/light-into-darkness-demystifying-profit-strategies-throughout-the-mev-bot-lifecycle/) (NDSS 2026) is the most comprehensive work — it analyzed 2,052 MEV bots on Ethereum, identified 20 code-level strategies, and even de-obfuscated hidden smart contract logic to reveal what bots were doing.

Flashbots bundle data reveals strategy types through structure: sandwich bundles contain 3+ ordered transactions, arbitrage bundles contain single cyclic-swap transactions. Private order flow now dominates — only 4.7% of MEV comes from the public mempool (down from 37.1% before MEV-Boost), meaning most bot activity flows through private channels that are harder to observe.

A [profitability-based identification algorithm](https://arxiv.org/html/2405.17944v2) (Chi et al., 2024) constructs directed graphs of token swaps and calculates exchange rates, achieving ≤2.4% false positive/negative rates — far better than simple "output > input" heuristics.

### Can you profitably trade against bots?

**Direct counter-trading is mostly dead on Ethereum.** The original weapon — Salmonella poisoned tokens that return 10% of requested amounts to non-owner callers — worked in 2021-2022. Modern bots now run `debug_traceCall` simulations and verify final balances before committing, blocking naive traps. ([Zellic Research](https://www.zellic.io/blog/your-sandwich-is-my-lunch-how-to-drain-mev-contracts-v2))

Advanced techniques that exploit simulation vs. execution discrepancies still exist:
- Block context detection: tokens that behave differently based on the builder environment
- Builder-paid traps: the poisoned contract itself pays the builder tip, ensuring the bot's losing transaction gets included
- State-dependent transfers: custom ERC-20s that track Uniswap pair reserves and conditionally block sells

But these require deploying custom smart contracts, seeding real liquidity as bait (capital at risk), deep EVM expertise, and they get patched within days of public disclosure. Documented profit per successful trap: $10-$1000. Not a business.

**Where bot intelligence is actually useful for your trading bot:**

1. Avoiding sandwich attacks: If you detect heavy sandwich bot activity on a pair, route your orders through Flashbots Protect or private RPCs, or adjust slippage tolerance downward.

2. Spread adjustment: If you identify competing market-making bots on your pair, their quoting behavior tells you about competitive dynamics. Widening spread when aggressive arb bots are active, tightening when they're dormant.

3. Timing optimization: Knowing when MEV bots are most active on your pair lets you time your own order placement to minimize adverse selection.

4. Strategy mimicry: Studying profitable bots' parameters (position sizing, rebalance frequency, pair selection) can inform your own strategy. The PBot1 analysis on Polymarket reverse-engineered a bot running temporal arbitrage, complete-set arbitrage, market making, and momentum sniping simultaneously. ([AgentBets.ai](https://agentbets.ai/news/pbot1-polymarket-bot-analysis/))

5. Competitive intelligence: If a dominant market maker on your pair suddenly changes behavior, that's a signal — maybe they detected something you haven't.

### Tools and services

**MEV-specific:**
- [EigenPhi](https://eigenphi.io): Real-time Ethereum MEV tracking, bot leaderboards, daily reports. Best for monitoring sandwich/arb activity on specific pairs.
- [ZeroMEV](https://info.zeromev.org): Frontrunning explorer. Misses ~64% of arbitrages vs. EigenPhi but good for sandwich detection.
- [Flashbots mev-inspect-py](https://github.com/flashbots/mev-inspect-py): Open-source MEV classification engine in Python. The foundation most tools build on.

**General analytics:**
- [Dune Analytics](https://dune.com): Free SQL queries across 100+ chains. Thousands of community-built MEV dashboards. Best for custom analysis.
- [Nansen](https://nansen.ai): 500M+ labeled wallets, real-time alerts, smart money tracking. Paid (low hundreds/month for useful access).
- [Arkham Intelligence](https://intel.arkm.com): AI entity identification, 350M+ labels. Functional free tier. Best for "who is behind this bot?" questions.

**Mempool monitoring:**
- [Blocknative](https://docs.blocknative.com): Ethereum mempool monitoring (paid)
- [bloXroute](https://docs.bloxroute.com): Multi-chain, includes Solana MEV protection
- [Jito Labs](https://www.jito.wtf): Solana's MEV infrastructure, analogous to Flashbots

**Open-source / academic:**
- [Ethereum-Bot-Detection](https://github.com/Tommel71/Ethereum-Bot-Detection): Labeled dataset (270 addresses) and classifier code from the WWW '24 paper
- APOLLO tool (NDSS 2026): Most comprehensive bot strategy analysis — 2,052 bots, 20 strategies

## Source evaluation

| Source | Type | Reliability | Notes |
|---|---|---|---|
| [Niedermayer et al. (ACM WWW 2024)](https://arxiv.org/html/2403.19530v2) | Peer-reviewed | High | Gold standard for feature engineering. Open code/data. |
| [Chi et al. (2024)](https://arxiv.org/html/2405.17944v2) | Peer-reviewed | High | Largest MEV dataset (9.4M activities). Profitability algorithm is novel. |
| [APOLLO / Luo et al. (NDSS 2026)](https://www.ndss-symposium.org/ndss-paper/light-into-darkness-demystifying-profit-strategies-throughout-the-mev-bot-lifecycle/) | Peer-reviewed | High | Most comprehensive strategy classification. Top-tier venue. |
| [Nansen MEV report](https://research.nansen.ai/articles/mev-masters-value-extraction-in-the-dark-forest) | Industry research | Medium-High | Proprietary data, possible selection bias. Numbers are directionally reliable. |
| [Zellic Research](https://www.zellic.io/blog/your-sandwich-is-my-lunch-how-to-drain-mev-contracts-v2) | Security research | High | First-hand vulnerability analysis with code-level detail. |
| [DeGatchi](https://degatchi.com/articles/baiting-mev-bots-univ2-token-trapper) | Blog/thought experiment | Medium | Concept not battle-tested. Useful for understanding the design space. |
| [Cred Protocol](https://credprotocol.com/blog/sybil-detection-api) | Industry | Medium | API-focused, limited methodology transparency. |

## Gaps and limitations

- **Solana tooling is 2 years behind Ethereum.** No unified MEV tracker. Cross-slot sandwich detection is fragmented.
- **Private order flow is invisible.** With 95%+ of Ethereum MEV flowing through private channels, mempool monitoring misses most bot activity. You can only analyze what's already on-chain (post-execution).
- **Real-time classification is hard.** The ML classifiers work on aggregate wallet history. Classifying a bot in real-time from its first few transactions is much less reliable.
- **Counter-trading profitability data is thin.** Most documentation is educational or from security researchers. No one publishes their P&L from farming bots (for obvious reasons).
- **CEX-DEX arbitrage bots are nearly invisible on-chain** because one leg happens off-chain on the CEX. You only see the DEX side.

## Next steps

1. **Wire up EigenPhi data for your active pairs.** Their API gives you real-time sandwich and arb activity. Use this to adjust your spread dynamically — widen when MEV activity spikes on your pair.

2. **Build a simple bot classifier using the open-source dataset.** The [Ethereum-Bot-Detection repo](https://github.com/Tommel71/Ethereum-Bot-Detection) has labeled data and code. Train a Random Forest on wallets that trade your pairs. This tells you who your competitors are.

3. **Monitor competing market makers via Dune.** Write SQL queries that track the top 10 wallets by volume on your pair. Their quoting behavior changes are your competitive intelligence.

4. **Route your own orders through Flashbots Protect** (Ethereum) or **bloXroute** (Solana) to avoid being sandwiched. This is the lowest-effort, highest-impact action.

5. **Skip counter-trading for now.** The risk-reward isn't there unless you're willing to invest serious EVM engineering time for small, inconsistent returns. Focus on using bot intelligence defensively (avoiding adverse selection) rather than offensively (trapping bots).

6. **Watch the APOLLO tool.** If the code gets released, it would be the best available system for understanding what strategies are running on your pairs.
