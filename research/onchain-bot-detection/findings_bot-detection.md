# On-Chain Bot Detection Techniques

Bot wallets can be reliably distinguished from human wallets using a combination of transaction timing patterns, gas bidding behavior, contract interaction signatures, and wallet lifecycle analysis. Machine learning classifiers (Random Forest, GMM clustering) achieve ~83% accuracy on Ethereum data, with the most predictive features being transaction time entropy, transactions-per-block frequency, gas price maximums, and sleep/wake patterns. The MEV market is extremely concentrated — the top 1% of profitable wallets capture 49% of all profits — making high-value bots relatively easy to identify once you know what to look for.

---

## Transaction Frequency & Timing Patterns

The strongest single signal separating bots from humans is **transaction time entropy**. Human wallets show high entropy (irregular timing spread across waking hours), while bots show low entropy (regular intervals, often 24/7 activity). This was the #1 most influential feature in a Random Forest classifier achieving 83% accuracy on a labeled Ethereum dataset.

Key timing indicators:

- **Transactions per block**: Bots show elevated, consistent transactions-per-block ratios. The feature "Out-TX-Per-Block" was the #2 most influential predictor — high values signal high-frequency automation.
- **GapBasedSleepiness**: A novel metric that measures the maximum gap between transactions in rolling two-day windows. Humans exhibit clear sleep/wake cycles with long gaps overnight. Bots don't sleep — low sleepiness values strongly predict bot classification. This was the #5 most influential feature.
- **Transaction frequency**: Average outgoing transactions per second during active periods. Bots cluster at both extremes — very high frequency (arbitrage/sandwich bots making hundreds of transactions per day) and very regular low frequency (deposit wallets, protocol update bots).
- **Time-of-day distribution**: Bots transact uniformly across hours; humans cluster around business hours and evenings in their timezone.

Concrete numbers from Nansen's MEV analysis: the top MEV bot wallet made 216,100 trades with constant activity and no dormant periods. Flash Boy wallets "switch from being very active to dormant during certain periods," suggesting active human management. The average profitable MEV wallet made ~5,400 trades; the median was ~350 — a few wallets transact massively more than the rest.

On Solana, the dominant sandwich bot program executes **51,600 transactions daily** at an 88.9% success rate.

## Gas Bidding Behavior

Gas-related features are among the most informative for bot detection:

- **Maximum gas price paid** ("TX-GasPrice-Max"): The #4 most predictive feature. Bots consistently pay higher maximum gas prices than humans because they need transaction ordering guarantees. The higher the highest gas price an address has ever paid, the more likely it's a bot.
- **Gas limit statistics**: For distinguishing between bot *types* (arbitrage vs. sandwich vs. liquidation), gas limit features were the top 5 most influential. Liquidation bots are especially distinctive — they show unusually high gas limit values because failed liquidation transactions are expensive and time-critical. Liquidation bots were classified with 93% accuracy based primarily on gas features.
- **Priority gas auctions (PGAs)**: Before EIP-1559, front-runners engaged in visible gas price bidding wars — sequences of rapidly escalating gas prices all targeting the same liquidity pool. Post-EIP-1559, the signal shifted to priority fees (tips). Front-running transactions pay significantly elevated priority fees relative to block medians.
- **Gas spending patterns**: The bot `jaredfromsubway.eth` spent over $90 million in gas fees during 2023 alone — only viable because extracted value exceeded costs. This level of gas spending is a clear bot fingerprint.

## Interaction Patterns (Contract Calls & DEX Router Usage)

Bot wallets interact with a narrow, specific set of contracts in distinctive ways:

- **DEX router dominance**: Bots overwhelmingly call Uniswap V2/V3 router swap functions (`swapExactTokensForTokens`, `swapETHForExactTokens`, etc.). The study decoded 8 specific Uniswap swap function signatures that capture the majority of bot trading activity. Other protocols bots frequently target: Curve, SushiSwap, DODO, and various lending protocols for liquidations.
- **Atomic multi-step transactions**: MEV bots operate through dedicated smart contracts that execute multi-step strategies atomically — flash loans, multi-pool swaps, and WETH wrapping/unwrapping all in a single transaction that reverts if any step fails. This is forensically distinctive from human trading which uses standard DEX frontends.
- **Swap path length**: Bots use longer swap paths (routing through multiple pools) more frequently than humans. The "StatisticsPathLength" feature captures whether an address routes through 2+ intermediary pools, which is common for arbitrage bots finding multi-hop price discrepancies.
- **Contract verification status**: Many MEV bot contracts are not verified on Etherscan (no published source code), but their bytecode can be decompiled to reveal swap patterns.
- **Concentrated protocol usage**: Uniswap V3 dominates both arbitrage and sandwich trading. Arbitrage pools are concentrated in Uniswap V3 and Curve. Sandwich attacks are spread more broadly across Uniswap V3, DODO, Curve, and SushiSwap.

## MEV-Specific Indicators

### Sandwich Attacks
The clearest forensic signature: three transactions in the same block — **bot buy → victim swap → bot sell** — all targeting the same liquidity pool, with the bot's profit matching the victim's excess slippage. Over 1.25 million sandwich attacks have been catalogued on Ethereum. In 2025, sandwich attacks constituted $289 million (51.5% of total MEV volume). By March 2025, just 101 identified sandwich entities were victimizing 33,000+ users monthly.

93% of current sandwich attacks on Solana now span **multiple validator slots** (wide/multi-slot sandwiches), splitting front-run and back-run transactions across different blocks to evade single-block detection.

### Front-Running
Near-identical transaction data submitted with higher gas price, executing in an earlier block position than the target transaction. Pure front-running is common in arbitrage and NFT minting.

### Back-Running
Arbitrage transaction placed immediately after a large swap to capture the price impact. The most common MEV type by volume. Restores pool price to market equilibrium.

### JIT (Just-In-Time) Liquidity
Liquidity add → large swap → liquidity remove sequence within the same block. The liquidity provider earns disproportionate fees. Growing on Uniswap V3/V4.

### Liquidation Front-Running
Liquidation transaction submitted with high priority fee immediately after a price oracle update crosses a lending protocol's threshold. Detectable by gas limit anomalies — liquidation bots show the highest gas limits of any bot type.

## Wallet Age, Funding Patterns & Token Diversity

- **Vanity addresses (leading zeros)**: Bot wallets more frequently use "mined" addresses with extra leading zeros. These addresses are cheaper to interact with (gas savings) and signal deliberate creation by automated tooling. The "NLeadingZeros" feature captures this.
- **Funding through privacy tools**: The Peraire-Bueno brothers' MEV exploit was traced partly because their validator was funded through the Aztec zk-rollup 18 days before the attack. Bots frequently use Tornado Cash, Aztec, or freshly created wallets funded from exchanges to obscure origin.
- **Digit entropy of addresses**: "DigitEntropy" captures whether an address's hex characters show unusual patterns — bot-generated addresses may differ from randomly generated human wallet addresses.
- **Token diversity**: MEV bots predominantly deal in WETH, USDC, USDT, WBTC, and high-liquidity tokens. Top profit tokens for MEV bots: WBTC, SHIB, SPELL, WETH, USDC, FTT. Humans tend to have more diverse, longer-held token portfolios.
- **Benford's Law compliance**: Human-entered trade values violate Benford's Law (preference for round numbers). Bot-generated transaction values follow Benford's distribution more closely. The "TradeValueClustering" feature measures the ratio of round to non-round numbers — humans cluster around cognitively salient round numbers while bots don't.
- **Wallet lifespan patterns**: Many bot wallets are short-lived — created, funded, used intensively, drained. The gap between first and last transaction, combined with transaction density in that period, separates bot behavior from human "hold and occasionally trade" patterns.

## Machine Learning Approaches to Bot Classification

### Supervised Classification
- **Random Forest**: 83% accuracy for binary Bot/Human classification on 270 labeled Ethereum addresses (137 bots, 133 humans). Also 77% accuracy on 4-class MEV subtype classification (Arbitrage/Sandwich/Liquidation/non-MEV).
- **Gradient Boosting (XGBoost)**: 82% accuracy binary, 76% multiclass — comparable to Random Forest.
- **AdaBoost**: 83% binary but only 50% multiclass — poor at distinguishing between MEV subtypes.
- **Feature set**: 83 features per wallet across 4 categories: address-based (vanity address indicators), transaction-based (timing, gas, value), function-call-based (swap patterns), and event-based (swap events, transfer events).

### Unsupervised Clustering
- **Gaussian Mixture Model**: 82.6% average cluster purity at 30 clusters. CEX wallets and deposit wallets cluster with >92% purity — these bot types are trivially separable. Human wallets also cluster with 87% purity.
- **k-means**: Lower purity than GMM across all configurations. Best result: 80% purity at 30 clusters.
- **UMAP dimensionality reduction**: Helps with small cluster counts but hurts with large counts — simplifies data too much for fine-grained separation.

### Explainability (SHAP Values)
The top 5 features by SHAP importance for binary bot detection:
1. Out-TX-Entropy (transaction time entropy)
2. Out-TX-Per-Block (activity rate)
3. Out-TX-Frequency (transactions per second when active)
4. TX-GasPrice-Max (highest gas price paid)
5. Out-TX-Sleepiness (sleep/wake cycle detection)

For MEV subtype classification, gas limit statistics dominate — they're especially decisive for liquidation bots. Sandwich bots are hardest to classify (68% accuracy, often confused with arbitrage and non-MEV).

### Other ML/AI Approaches
- **Graph Neural Networks**: Used for anti-money laundering on stablecoins but outperformed by tree ensemble models for wallet classification tasks.
- **AI-powered address clustering**: Converts fragmented wallets into entity-level views (e.g., TRM Labs, Nansen). Reduces investigation timelines by 60-80%.
- **Deep neural networks**: Applied for attack instance classification with combined supervised/unsupervised approaches.

### Tools & Datasets
- **Flashbots MEV-Inspect**: Rule-based Python tool that detects arbitrage, sandwich, and liquidation transactions. Used to generate labeled datasets for ML training.
- **EigenPhi**: Analytics platform cataloguing MEV transactions and protocol-level extraction.
- **zeromev**: Public dashboard cataloguing 1.25M+ sandwich attacks across Ethereum history.
- **Nansen**: Wallet labeling platform (MEV Bot, Flash Boy, Sandwich Attacker labels) covering 1,194 MEV-active wallets.
- **Cred Protocol Sybil Detection API**: Behavioral analysis computing sybil scores per address using transaction time entropy, counterparty diversity, contract interaction breadth, and gas spending.
- **GitHub: Tommel71/Ethereum-Bot-Detection**: Open-source code and labeled dataset (270 addresses) from the academic paper.

## Scale of the MEV/Bot Market

- Cumulative MEV extraction on Ethereum: **>$680 million** (as of Nansen report).
- Bots have extracted **>$1 billion** in total profits from Ethereum (academic estimate).
- Sandwich bots extracted **$370-500M from Solana users** over 16 months.
- 2025 sandwich attacks on Ethereum: **$289 million** (51.5% of MEV volume).
- Top 1% of profitable MEV wallets capture **49% of all profits**.
- Only **43% of MEV-labeled wallets are profitable** — 398 wallets lost a combined $126M.
- The top individual MEV bot extracted **$61M in profit** from $22.5B in volume (0.27% ROI).
- Actively managed (Flash Boy) wallets achieve far higher ROI (up to 107%) vs. MEV bots (0.03-14%).

---

## Sources

1. **Niedermayer, Saggese & Haslhofer (2024) — "Detecting Financial Bots on the Ethereum Blockchain"** — https://arxiv.org/html/2403.19530v2  
   Academic paper (ACM WWW '24) presenting ML-based bot detection with 83% accuracy. Proposes taxonomy of 7 bot categories/24 subcategories. Open-source code and labeled dataset. Primary source for feature engineering and SHAP analysis.

2. **Nansen Research — "MEV Masters: Value Extraction in the Dark Forest"** — https://research.nansen.ai/articles/mev-masters-value-extraction-in-the-dark-forest  
   Data-driven analysis of 1,194 MEV wallets using Nansen labels. Top 20 wallets by profit and ROI. Market concentration analysis showing top 1% captures 49% of profits. Risk quantification: 398 losing wallets lost $126M combined.

3. **Crypto Trace Labs — "How Do Investigators Detect Front-Running Patterns in DeFi Transactions?"** — https://cryptotracelabs.com/blog/how-do-investigators-detect-front-running-patterns-in-defi-transactions-2/  
   Forensic investigation methodology covering block position analysis, gas price anomaly detection, smart contract forensics, and profit calculation. Includes legal case studies (Eisenberg/Mango Markets, Peraire-Bueno brothers). Updated February 2026.

4. **dev.to — "Solana MEV Defense in 2026"** — https://dev.to/ohmygod/solana-mev-defense-in-2026-how-sandwich-bots-extracted-500m-and-the-6-protocol-level-defenses-16d9  
   Solana-specific analysis. Dominant sandwich program doing 51,600 txns/day at 88.9% success. 93% of attacks now span multiple slots. B91 bot case study: 82,000 attacks in 30 days.

5. **Cred Protocol Blog — "Sybil Detection API"** — https://credprotocol.com/blog/sybil-detection-api  
   On-chain behavioral analysis API. Uses transaction time entropy, unique counterparties, contract interaction diversity, gas spending, and wallet age to compute sybil/bot risk scores per Ethereum address.
