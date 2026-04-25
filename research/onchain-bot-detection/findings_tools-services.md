# Existing Tools and Services for On-Chain Bot Analysis

The tooling landscape for on-chain analysis is mature on Ethereum and rapidly catching up on Solana. Four major platforms dominate (Dune, Nansen, Arkham, EigenPhi), each solving a different slice of the problem. For MEV-specific analysis, EigenPhi and ZeroMEV are the go-to tools. Mempool monitoring is handled by Blocknative (Ethereum) and bloXroute/Jito (Solana). Most useful tools have free tiers, but serious wallet labeling and real-time alerts require paid subscriptions.

---

## MEV-Specific Analysis Platforms

### EigenPhi
- **What it does**: Real-time MEV tracking on Ethereum. Daily reports on sandwich attacks, arbitrage, and liquidations with profit/loss breakdowns per bot.
- **Key features**: MEV live-stream, transaction analysis, MEV bot leaderboards, hot liquidity pool identification
- **Integrations**: Data used by Etherscan (5.3M labeled txs), DefiLlama, CoW Swap. Cited in academic research (Yale, Max Planck Institute)
- **Chrome extension**: EigenTx for detailed transaction visualization
- **URL**: https://eigenphi.io

### ZeroMEV
- **What it does**: Ethereum frontrunning explorer. Detects sandwich attacks, toxic MEV, and transaction censorship.
- **Key features**: Block analysis comparing actual vs. fair time-based ordering, Flashbots bundle tracking, network delay metrics
- **Data source**: Uses Flashbots' `mev-inspect-py` for classification
- **Known limitation**: Misses ~64% of arbitrage instances compared to EigenPhi. Sandwich detection is comparable.
- **URL**: https://info.zeromev.org

### Flashbots (Open Source)
- **What it does**: The dominant MEV infrastructure on Ethereum. Flashbots Protect routes transactions through private channels to avoid sandwich attacks.
- **Key tools**:
  - `mev-inspect-py` — open-source MEV classification engine (Python)
  - MEV-Boost — connects validators to block builders for MEV extraction
  - Flashbots Protect RPC — free private transaction submission
  - SUAVE — upcoming intent-based architecture for MEV management
- **Coverage**: 90%+ Ethereum validator coverage through MEV-Boost
- **URL**: https://docs.flashbots.net

## General On-Chain Analytics Platforms

### Dune Analytics
- **What it does**: Community-driven SQL query platform for raw blockchain data
- **Key features**: 100+ chains indexed, 60K+ decoded smart contracts, materialized views, scheduled refreshes, AI query generation, custom data uploads
- **MEV relevance**: Thousands of community-built MEV dashboards. Can query raw sandwich/arbitrage data with SQL. No pre-built wallet labels.
- **Pricing**: Free tier is functional. Plus $349/mo, Pro $390/mo for private queries and faster execution.
- **URL**: https://dune.com

### Nansen
- **What it does**: Smart money intelligence with 500M+ labeled wallet addresses
- **Key features**: Real-time alerts (email/Telegram/Slack), Token God Mode for deep token analysis, Smart Segments for custom wallet cohorts, in-app trading
- **MEV relevance**: Can track known MEV bot wallets, monitor smart money flows, detect unusual accumulation patterns. Labels include entity types and historical performance.
- **Pricing**: Paid subscription, low hundreds/month for meaningful access. Free tier is heavily limited.
- **Notable use case**: Valkyrie used Nansen alerts to detect UST Curve pool draining hours before the depeg, saving "tens of millions."
- **URL**: https://nansen.ai

### Arkham Intelligence
- **What it does**: Blockchain investigation platform with AI-powered entity identification ("Ultra" engine)
- **Key features**: 350M+ labels across 200K+ entity pages, fund flow visualizer, cross-chain tracing, Intel Exchange bounty marketplace
- **MEV relevance**: Can identify MEV bot operators, trace bot profits to exchanges/wallets, investigate bot contract deployers. Best for "who is behind this bot?" questions.
- **Pricing**: Free tier is surprisingly functional. Pro subscription for full toolkit. Intel Exchange uses ARKM token.
- **Notable use cases**: FTX hack tracking (used by SDNY prosecutors), German government BTC sale monitoring, Mt. Gox repayment tracking
- **URL**: https://intel.arkm.com

### DeBank
- **What it does**: Multi-chain portfolio tracker and wallet analytics
- **MEV relevance**: Useful for inspecting specific bot wallet portfolios, DeFi positions, and transaction histories. Less sophisticated than Nansen/Arkham for labeling.
- **URL**: https://debank.com

### Token Terminal
- **What it does**: Standardized financial metrics for protocols (revenue, fees, P/E ratios)
- **MEV relevance**: Limited — useful for understanding protocol economics but not for wallet-level bot analysis
- **URL**: https://tokenterminal.com

## Mempool Monitoring Services

### Blocknative (Ethereum)
- **What it does**: Global mempool monitoring through a distributed node network with custom telemetry
- **Key features**: Mempool Explorer for filtering pending transactions, Transaction Boost for private submission, near-100% transaction visibility
- **MEV relevance**: Critical for seeing what bots see — pending transactions in the mempool before they're mined
- **URL**: https://docs.blocknative.com

### bloXroute
- **What it does**: Low-latency transaction propagation and MEV protection across Ethereum and Solana
- **Key features**: Leader-aware MEV protection on Solana (scores validators in real-time), multi-path routing through Jito/Paladin/bloXroute, tiered protection levels
- **October 2025 update**: Added protections against validator-led cross-slot sandwiches (93% of Solana attacks)
- **URL**: https://docs.bloxroute.com

### Jito Labs (Solana)
- **What it does**: Solana's dominant MEV infrastructure, analogous to Flashbots on Ethereum
- **Key features**: Block Engine for atomic bundle execution, searcher-to-validator tip marketplace
- **MEV relevance**: The only reliable way to guarantee bundle atomicity on Solana. Understanding Jito is essential for analyzing Solana MEV bots.
- **URL**: https://www.jito.wtf

## Wallet Labeling Services and Databases

| Service | Labels | Approach | Free Tier |
|---------|--------|----------|-----------|
| Nansen | 500M+ addresses | Proprietary ML + manual curation | Limited |
| Arkham | 350M+ labels, 200K entities | AI "Ultra" engine + community Intel Exchange | Yes, basic |
| Etherscan | Tags on known addresses | Community submissions + partnerships (uses EigenPhi data) | Yes |
| Dune | Community-contributed | User-uploaded label sets, no built-in labeling | Yes |

## Ethereum vs. Solana Tooling Differences

| Aspect | Ethereum | Solana |
|--------|----------|--------|
| Mempool | Public mempool exists; Flashbots Protect bypasses it | No traditional mempool; leader-based ordering |
| MEV infrastructure | Flashbots MEV-Boost (mature, 90%+ coverage) | Jito Block Engine (dominant but less mature) |
| Analysis tools | EigenPhi, ZeroMEV, extensive Dune dashboards | Fewer dedicated tools; Jito explorer, some Dune dashboards |
| Attack patterns | Classic mempool sandwiches | Cross-slot sandwiches via validator collusion (93% of attacks) |
| Private txs | Flashbots Protect, MEV Blocker, private RPCs | bloXroute leader-aware protection, Jito bundles |
| Bot sophistication | Highly optimized, simulation-hardened | Growing fast; $450K/day top earners documented |

## Open Source Tools Worth Noting

- **mev-inspect-py** (Flashbots) — MEV classification engine, Python. Core of ZeroMEV's detection. https://github.com/flashbots/mev-inspect-py
- **Foundry** (Paradigm) — Ethereum development toolkit used for simulating and testing MEV strategies locally. https://github.com/foundry-rs/foundry
- **Dune** community dashboards — Free SQL-based MEV analysis. Search "MEV" or "sandwich" on Dune for hundreds of pre-built dashboards.
- **Etherscan** — Free block explorer with MEV bot address labels (sourced from EigenPhi). https://etherscan.io

## What's Missing / Gaps

- **No unified cross-chain MEV tracker**: EigenPhi is Ethereum-only. Solana MEV tooling is fragmented.
- **Real-time mempool APIs are expensive or proprietary**: Blocknative and bloXroute are paid services. There's no good free mempool monitoring API.
- **Bot wallet labeling is incomplete**: Even Nansen's 500M labels don't specifically tag "MEV bot" vs. "arbitrage bot" vs. "sandwich bot" in a way that's easy to query.
- **Solana tooling lags Ethereum by ~2 years**: The analysis and protection infrastructure is catching up but still less mature.

---

## Sources

- **Sablier Blog** — "Onchain Analytics Platforms for Crypto Teams (2026)" — Comprehensive comparison of Dune, Nansen, Arkham, and Token Terminal with pricing and use cases. https://blog.sablier.com/onchain-analytics-platforms-for-crypto-teams-2026/
- **EigenPhi** — MEV tracking platform with daily reports and live-stream. https://eigenphi.io
- **ZeroMEV** — Frontrunning explorer with data source methodology documentation. https://info.zeromev.org/sources.html
- **bloXroute** — "A New Era of MEV on Solana" — October 2025 update on Solana MEV protection changes. https://bloxroute.com/pulse/a-new-era-of-mev-on-solana/
- **Blocknative** — Mempool monitoring and MEV protection documentation. https://docs.blocknative.com
- **FRB Agent** — "Best MEV Relays by Chain" — Overview of Flashbots, Jito, and bloXroute relay infrastructure. https://ai-frb.com/blog/best-mev-relays-by-chain
