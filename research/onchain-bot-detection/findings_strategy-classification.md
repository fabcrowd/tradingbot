# Strategy Classification — Reverse-Engineering Bot Strategies from On-Chain Data

Classifying what strategy a detected trading bot is running is achievable through a combination of transaction-pattern heuristics, behavioral fingerprinting, and machine learning on aggregate wallet features. Academic research has produced both rule-based detection systems (covering 44+ swap event patterns) and ML classifiers reaching 77–83% accuracy at distinguishing MEV subtypes. The richest signals come from gas usage patterns, transaction timing entropy, swap-graph topology, and position-sizing regularity.

---

## 1. Bot Taxonomy — Distinguishing Strategy Types

### Comprehensive Classification (Niedermayer et al., 2024)

A peer-reviewed taxonomy for Ethereum financial bots identifies **7 categories and 24 subcategories**:

| Category | Subcategories |
|---|---|
| **MEV Bots** | Front-running (insertion, displacement, suppression), generalized front-running, sandwich, atomic arbitrage, statistical arbitrage, liquidation, generalized searching, sniping |
| **CEX Bots** | Hot wallets, deposit wallets, funding/aggregating wallets |
| **DEX Bots** | Custom trading strategies, liquidity compounding |
| **NFT Bots** | Custom trading, parallel minting |
| **Play-to-Earn Bots** | Game-progressing actions |
| **General Purpose** | Protocol updates, rollups, routine payments, airdrop collecting |
| **Non-Attributable** | Automated behavior with no clear purpose |

### Distinguishing the Major MEV Subtypes

**Sandwich Bots:**
- On-chain signature: Three+ transactions in the same block — bot buys token A→B, victim swaps A→B (getting worse price), bot sells B→A.
- Advanced variants: "multi-layered burger" attacks sandwich multiple victims (median 2 victims), "conjoined sandwich" attacks use multiple attack transactions (median 4 victims).
- Conjoined sandwiches generate ~5x the profit of normal sandwiches.
- On Solana, "wide sandwiches" span multiple validator slots (93% of Solana sandwich activity in 2025-2026).

**Arbitrage Bots:**
- On-chain signature: Cyclic swap pattern within a single transaction — token A→B→C→A across different DEXes, where the initiator ends with more of the starting token.
- Can be front-running (profitable when replayed at block start) or back-running (only profitable at its actual position).
- Back-running arbitrage is becoming dominant as competition intensifies.
- 81.6% of arbitrages missed by older methods were due to limited swap pattern coverage (only tracking a few DEXes).

**Liquidation Bots:**
- On-chain signature: Interactions with lending protocol liquidation functions (Aave, Compound).
- Easiest to classify (93% accuracy) because they show **unusually high gas limit values** — the single most distinguishing feature.
- Gas-limit-based features dominate SHAP importance for liquidation classification.

**Sniper Bots:**
- On-chain signature: One of the first transactions interacting with a newly deployed token contract or newly created liquidity pool.
- Operational tactic: Buy tokens within the first blocks of listing, sell as retail drives price up.
- Identified through heuristic examination of transaction timing relative to pool creation events.

**Market Making Bots:**
- Behavioral signature: Maintain ongoing positions on both sides of a market, consistent liquidity provision.
- Distinguished from arbitrage by position *duration* — market makers hold inventory, arbitrageurs don't.
- Focus on earning bid-ask spread rather than rapid entry-exit cycles.

---

## 2. Transaction Signature Analysis

### Function-Call Patterns

The paper by Chi et al. (2024) catalogued **44 distinct swap event patterns** across DEXes (a 5-6x increase over prior work). Key decoded function signatures used for classification:

| Function | Signature Hash | Typical User |
|---|---|---|
| `swapExactTokensForTokens` | `0x38ed1739` | Standard DEX traders and bots |
| `swapTokensForExactTokens` | `0x8803dbee` | Precision-targeting bots |
| `swapExactTokensForETH` | `0x18cbafe5` | Token-to-ETH exits |
| Transfer event | `0xddf252ad` | Universal (parsed for balance changes) |
| Uniswap V2 Swap event | `0xd78ad95f` | AMM swap tracking |
| Uniswap V3 Swap event | `0xc42079f9` | Concentrated liquidity swaps |

### Multicall and Bundle Patterns

- MEV bots frequently use **Flashbots bundles** — multiple transactions submitted atomically to a builder.
- Sandwich attacks require bundle submission: front-run tx + victim tx + back-run tx in exact order.
- The APOLLO tool (NDSS 2026) identified **20 distinct code-level strategies** by analyzing MEV bot smart contract bytecode, including de-obfuscation of deliberately hidden strategies.

### Profitability-Based Identification

Chi et al. proposed a **profitability identification algorithm** that constructs a directed graph of token swaps and calculates exchange rates to determine if a transaction was genuinely profitable — outperforming heuristic approaches with ≤2.4% false positive/negative rates. This replaces brittle rules like "output amount > input amount" which fail on multi-hop swaps with flash loans.

---

## 3. Behavioral Fingerprinting

### Timing Patterns (Most Discriminative Features)

From Niedermayer et al.'s SHAP analysis, the **top 5 features** for bot/human classification:

1. **Out-TX-Entropy** — Entropy of the hour-of-day distribution of outgoing transactions. Bots transact uniformly across all hours; humans cluster around waking hours. Higher entropy → more likely bot.
2. **Out-TX-Per-Block** — Average outgoing transactions per block. High values signal automated activity.
3. **Out-TX-Frequency** — Transaction rate during active periods.
4. **TX-GasPrice-Max** — Maximum gas price paid. Higher max gas → more likely bot (willing to pay premium for time-critical execution).
5. **Out-TX-Sleepiness** — "GapBasedSleepiness" metric measuring max inactivity gaps in 2-day windows. Bots have low sleepiness (no sleep cycles); humans show clear sleep patterns.

### Position Sizing Patterns

- **Benford's Law analysis**: Bot trade values deviate from Benford's distribution (the expected first-digit distribution in natural datasets). Bots calculate exact optimal amounts.
- **Trade Value Clustering**: Human traders use round numbers as cognitive anchors. The ratio of round-to-non-round values distinguishes bots from humans. Bots show values calculated to exact decimals optimizing for fee tiers.
- **Swap path length**: Bots use multi-hop swap paths (3+ hops) more frequently than humans.

### Inventory Behavior

- Arbitrage bots: Net zero inventory — start and end with the same token in the same transaction.
- Sandwich bots: Temporary inventory within a block — buy before victim, sell after.
- Market makers: Sustained bilateral inventory across many blocks.
- Sniper bots: Accumulate rapidly, then exit over hours/days as price rises.

---

## 4. Machine Learning Approaches to Automated Classification

### Binary Bot Detection (Niedermayer et al., ACM WWW 2024)

- **Dataset**: 133 human + 137 bot addresses, 83 features per wallet
- **Best supervised model**: Random Forest — 83% accuracy, 87% precision, 0.80 F1
- **Best unsupervised model**: Gaussian Mixture Model — 82.6% cluster purity (30 clusters)
- CEX bots and Deposit Wallet bots form the most distinct clusters (>92% purity)
- Code open-sourced: github.com/Tommel71/Ethereum-Bot-Detection

### Multiclass MEV Classification

- 4-class problem: Arbitrage / Sandwich / Liquidation / non-MEV (111 addresses each)
- Random Forest: **77% macro accuracy**
- Liquidation bots easiest to classify (93% accuracy) — dominated by gas-limit features
- Sandwich bots hardest (68% accuracy) — 16% misclassified as arbitrage, 13% as non-MEV
- Gas limit statistics are the top 5 features for multiclass distinction

### AI-Based Pattern Detection

- Analyzes transaction timing, gas fees, and sequence irregularities
- Feature extraction: timestamps, gas prices, slippage tolerance settings, transaction ordering
- Searches for correlated transaction pairs from the same address with irregular price action
- Uses anomaly detection for suspicious behavior patterns

---

## 5. Flashbots / MEV-Boost Data for Strategy Identification

### Data Sources

- **Flashbots Transparency Dashboard**: Public data on Realized Extractable Value (REV) since September 2022.
- **Flashbots API** (blocks.flashbots.net): Records private transaction bundles passed through Flashbots relays.
- **Relay APIs**: Each MEV-Boost relay publishes proposed blocks, enabling analysis of builder behavior.
- **Blocknative**: Historical archive of Ethereum mempool transaction events — any transaction NOT in this archive but included in a block was likely a private transaction.

### What MEV-Boost Data Reveals

- **Bundle structure reveals intent**: Flashbots bundles containing front-run + target + back-run are sandwich attacks. Single-tx bundles with cyclic swaps are arbitrages.
- **Builder-searcher relationships**: Analysis of 6 months (Oct 2023–Mar 2024) showed builder market share correlates with access to exclusive order flow from integrated searchers. Exclusive signals, non-atomic arbitrages, and Telegram bot flow strongly correlate with both market share and profitability.
- **Private vs. mempool strategy shift**: In Stage II (Flashbots era), 37.1% of MEV came from mempool. In Stage III (PBS era), only 4.7% — nearly all MEV now flows through private channels.
- **Success rate signal**: Mempool MEV activities have <40% success rate, explaining the migration to private transaction pools. 67.7% of private arbitrages would have been unprofitable if broadcast to the mempool.

### APOLLO Tool (NDSS 2026)

The most comprehensive strategy classification tool to date:
- Analyzed **2,052 MEV bots** on Ethereum
- Identified **20 code-level strategies** used in the wild
- First systematic attempt at **smart contract de-obfuscation** to uncover hidden bot strategies
- Discovered **5 specific transaction types** that create profit opportunities for MEV bots
- Covers the full bot lifecycle, not just individual transactions

---

## 6. On-Chain Signatures of Common Strategies — Quick Reference

| Strategy | Block-Level Signature | Key Distinguishing Feature |
|---|---|---|
| **Sandwich** | Buy→Victim Swap→Sell in same block, same token pair | Attacker's buy and sell bracket the victim; same `from` or `to` address on attack txs |
| **Atomic Arbitrage** | Single tx with cyclic swap graph (A→B→...→A) | Net positive token balance for initiator; often uses flash loans |
| **Statistical Arbitrage** | Cross-block trades exploiting price lag | Non-atomic; positions held across blocks; harder to detect |
| **Liquidation** | Calls to lending protocol liquidation functions | Extremely high gas limits; interaction with Aave/Compound contracts |
| **Sniping** | First transactions after pool creation or token deploy | Timing relative to contract deployment; often followed by rapid sell |
| **Back-running Arbitrage** | Arbitrage tx placed immediately after a target swap | 79.7% have no intermediate transactions between target and backrun |
| **Market Making** | Continuous two-sided order placement | Long-lived positions; low time-between-trades variance; bid-ask spread capture |
| **Toxic Arbitrage** | Sandwich attack where attack txs also qualify as profitable arbitrages | Overlap of sandwich and arbitrage signatures (148,133 cases identified) |

---

## 7. Scale of the Problem

- **9.4 million MEV activities** identified on Ethereum through August 2023 (6.3M arbitrages + 3.0M sandwich attacks)
- **100,709 DEX addresses** and **129,909 tokens** involved in MEV transactions
- Uniswap V2 accounts for 53% of all MEV-related swap events (81,818 pools targeted)
- Over 50% of labeled Ethereum addresses in a 100K-block sample were bots
- On Solana, one dominant sandwich bot executed 51,600 transactions daily with 88.9% success rate, earning ~$450K/day
- Solana sandwich bots extracted ~$500M cumulatively by early 2026

---

## Sources

1. **Niedermayer, Saggese & Haslhofer (2024)** — "Detecting Financial Bots on the Ethereum Blockchain" (ACM WWW '24). ML-based bot detection with 7-category taxonomy, 83% classification accuracy. https://arxiv.org/html/2403.19530v2

2. **Chi, He, Hu & Wang (2024)** — "Remeasuring the Arbitrage and Sandwich Attacks of Maximal Extractable Value in Ethereum." Profitability-based identification algorithms, 9.4M MEV activities detected, largest dataset to date. https://arxiv.org/html/2405.17944v2

3. **Luo et al. (NDSS 2026)** — "Light into Darkness: Demystifying Profit Strategies Throughout the MEV Bot Lifecycle." APOLLO tool analyzing 2,052 bots, 20 code-level strategies, smart contract de-obfuscation. https://www.ndss-symposium.org/ndss-paper/light-into-darkness-demystifying-profit-strategies-throughout-the-mev-bot-lifecycle/

4. **Outlook India / AI & MEV Detection** — Overview of AI-based detection of sandwich attacks and front-running using ML and anomaly detection. https://outlookindia.com/xhub/blockchain-insights/how-does-ai-identify-mev-patterns-like-sandwich-attacks-front-running

5. **dev.to / Solana MEV Defense 2026** — Analysis of Solana sandwich bot extraction ($500M), wide sandwich patterns (93% of activity), and 6 protocol-level defenses. https://dev.to/ohmygod/solana-mev-defense-in-2026-how-sandwich-bots-extracted-500m-and-the-6-protocol-level-defenses-16d9

6. **AgentBets.ai / PBot1 Analysis** — Case study reverse-engineering a live Polymarket trading bot's strategy (temporal arbitrage, complete-set arbitrage, market making, momentum sniping). https://agentbets.ai/news/pbot1-polymarket-bot-analysis/

7. **Flashbots Transparency Dashboard** — Public MEV-Boost relay data since September 2022. https://docs.flashbots.net/flashbots-data/dashboard
