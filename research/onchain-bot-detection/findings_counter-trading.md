# Counter-Trading / Farming Bot Strategies for Profit

Counter-trading MEV bots is a real but narrow field. The most documented approach — "salmonella" poisoned tokens — worked in 2021-2022 but modern bots simulate transactions before committing, making naive traps ineffective. Advanced techniques exist that exploit simulation/execution discrepancies, but they require deep EVM knowledge and the profit window closes fast as bots adapt.

---

## Salmonella Attacks: The Original Counter-Strategy

The [Salmonella contract](https://github.com/Defi-Cartel/salmonella) (Defi Cartel, 2021) was the first widely-known anti-bot weapon. The mechanism:

1. Deploy a poisoned ERC-20 token that behaves normally for the owner
2. For any other caller, `transfer()` only sends 10% of the requested amount — but emits event logs showing the full amount
3. Place a large buy order in the public mempool to bait sandwich bots
4. The bot front-runs with a buy, receives 90% fewer tokens than expected, then its back-run sell fails or is deeply unprofitable

**Why it stopped working:** Modern bots don't trust event logs alone. They run `debug_traceCall` simulations and verify final token balances in their contract before committing. If the simulation shows a loss, they skip the trade. The salmonella approach is now blocked by the standard bot simulation pipeline.

## Exploiting Simulation vs. Execution Discrepancies

The key insight from researchers at Zellic and others: if you can make a transaction behave differently in simulation vs. on-chain execution, you can still trap bots.

**Known techniques:**

- **Block context detection**: Check if the current block is being built by a specific builder (e.g., Flashbots). During simulation, the builder context differs from the live chain. A poisoned token can return full amounts in simulation but fractional amounts on-chain. (Documented by [@bertcmiller](https://twitter.com/bertcmiller/status/1381296074086830091))
- **Paying the builder directly**: Normally, an unprofitable sandwich wouldn't be included because the bot won't bribe the builder. The counter-strategy: the poisoned token contract itself pays the builder tip, ensuring the bot's losing transaction gets included regardless.
- **State-dependent transfers**: The [UniV2 Token Trapper](https://degatchi.com/articles/baiting-mev-bots-univ2-token-trapper) concept (DeGatchi, Dec 2024) uses a custom ERC-20 that tracks Uniswap pair reserves internally. The `transfer()` function checks whether WETH reserves increased or decreased to determine if it's a buy or sell, then conditionally blocks sells after a threshold — trapping bot funds in the pool.

**Limitation**: These require deploying custom smart contracts, seeding liquidity pools with real ETH, and hoping the targeted bot hasn't patched against this specific vector. The cat-and-mouse cycle is fast.

## Draining MEV Bot Contracts Directly

Zellic researchers discovered a vulnerability in one of Ethereum's largest MEV bots (0x2387...8CDB) caused by gas optimization:

- The bot used a jump-table optimization that let callers specify arbitrary code addresses in calldata — bypassing function selector checks
- This allowed anyone to call internal functions that skip authentication, including making arbitrary external calls from the bot contract
- In practice: an attacker could call `approve()` on any ERC-20 the bot holds, then drain those tokens

This is not a general strategy — it's a specific bug class (unsafe jump destinations in hand-optimized bytecode). But it illustrates that MEV bot contracts are attack surface themselves, and many are written by devs who prioritize gas savings over security.

## Uncle Bandit / Ommer Attacks

When Ethereum produces competing blocks, the losing block (ommer/uncle) leaks its transaction bundles publicly. An attacker can:

1. Watch for leaked MEV bundles in ommer blocks
2. Extract just the profitable front-run transaction from the bundle
3. Sandwich *that* transaction in the next block

Documented profit: ~0.4 ETH in one studied case. Small but repeatable. Modern bots mitigate this by including block ID checks — their transactions revert if included in any block other than the intended one.

## How Market Makers Adapt to Competing Bots

This area is thin in public literature. What's documented:

- **Searcher competition drives margins to near-zero**: Top MEV bots now pay 99%+ of extracted value as builder bribes. The actual profit margin per sandwich is often $1-3 on Ethereum.
- **Consolidation**: The MEV space on Ethereum is dominated by a small number of sophisticated operators. New entrants face a cold-start problem — you need speed, capital, and infrastructure to compete.
- **Private orderflow**: Increasingly, transactions bypass the public mempool entirely (Flashbots Protect, MEV Blocker, private RPCs). This shrinks the pool of sandwichable transactions.

## Is Counter-Trading Actually Profitable?

**Honest assessment: mostly theoretical, with narrow exceptions.**

Arguments for:
- Salmonella-style attacks provably worked in 2021-2022 before bots added simulation checks
- Simulation/execution discrepancy exploits still work against less sophisticated bots
- Bug bounty-style contract draining has yielded real (if small) returns
- On Solana, where MEV tooling is less mature, the attack surface may be wider

Arguments against:
- Modern Ethereum MEV bots simulate everything — the low-hanging fruit is gone
- Deploying poisoned tokens requires seeding real liquidity (capital at risk)
- The counter-strategy community is small; most documented examples are educational/theoretical
- Legal ambiguity — intentionally deploying contracts designed to trap and drain bots may have legal risk depending on jurisdiction
- Profit per successful trap is typically small ($10-$1000 range in documented cases)

**Bottom line**: You're not going to build a sustainable income stream from salmonella contracts. The realistic application is niche: targeting specific under-hardened bots with custom exploits, treating it more like security research than a trading strategy.

## Risks and Limitations

1. **Capital at risk**: Seeding liquidity pools with ETH/SOL to bait bots means that capital is exposed if the trap fails
2. **Bot operators retaliate**: Some MEV operators monitor for known trap patterns and blacklist deployer addresses
3. **Fast adaptation**: Any publicly disclosed technique gets patched within days to weeks
4. **Gas costs**: Failed traps still cost gas. On Ethereum mainnet, this adds up
5. **Legal gray area**: Deliberately deploying deceptive contracts crosses lines that regulators haven't clearly drawn yet
6. **Solana differences**: Solana's lack of a traditional mempool and its leader-based ordering create different dynamics — cross-slot sandwiches are now 93% of attacks, and counter-strategies need to account for validator collusion

---

## Sources

- **Zellic Research** — "Your Sandwich Is My Lunch: How to Drain MEV Contracts V2" — Deep technical analysis of MEV bot vulnerabilities and gas optimization bugs. https://www.zellic.io/blog/your-sandwich-is-my-lunch-how-to-drain-mev-contracts-v2
- **DeGatchi** — "Baiting MEV Bots: UniV2 Token Trapper" (Dec 2024) — Thought experiment on building conditional ERC-20 tokens that trap bot funds in Uniswap pools. https://degatchi.com/articles/baiting-mev-bots-univ2-token-trapper
- **MEV Wiki** — Salmonella attack documentation and overview. https://mev.wiki/attempts-to-trick-the-bots/salmonella/
- **dev.to** — "Solana MEV Defense in 2026" — Stats on $500M extraction, cross-slot sandwich prevalence, and protocol-level defenses. https://dev.to/ohmygod/solana-mev-defense-in-2026-how-sandwich-bots-extracted-500m-and-the-6-protocol-level-defenses-16d9
- **dev.to** — "The End of Sandwich Attacks? Encrypted Mempools in 2026" — Coverage of emerging defenses including Shutter Network and SUAVE. https://dev.to/ohmygod/the-end-of-sandwich-attacks-how-encrypted-mempools-are-reshaping-defi-security-in-2026-h56
