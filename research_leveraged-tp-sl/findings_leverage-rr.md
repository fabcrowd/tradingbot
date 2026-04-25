# Findings: 2x Leverage TP/SL Sizing on Crypto Futures

**Summary:** At 2x leverage, a 1.5-2% leveraged profit target requires only a 0.75-1.0% underlying price move, making tight entries viable but also making the position sensitive to normal intraday noise. The key tension is that 2x leverage is conservative enough to provide wide liquidation buffers (theoretical liquidation at ~50% adverse move from margin), but short-term crypto volatility still demands stops of at least 0.5-1.0% below entry to avoid noise-driven exit. R:R of 1:2 or better is the practitioner consensus for leveraged futures.

---

## 1. Underlying Price Move Required for 1.5-2% Leveraged Profit at 2x

Formula: `Required underlying move = Target leveraged return / Leverage multiplier`

| Leveraged return target | Leverage | Required price move |
|------------------------|----------|---------------------|
| 1.5% | 2x | 0.75% |
| 2.0% | 2x | 1.00% |

**Practical implication:** A 1% BTC move at 2x returns 2% on the margin deployed. BTC routinely moves 1-3% in a single hour, so the TP target is reachable within a single candle on volatile sessions. However, the position sits inside normal noise if stops are set too close.

**Fees:** At Coinbase Advanced, taker fees ~0.05-0.08% each side. Round-trip at 2x erodes ~0.1-0.16% of leveraged return, so net on a 2% target is closer to 1.84-1.90%.

---

## 2. Minimum Stop Distance to Avoid Noise Stopouts at 2x

### Intraday noise benchmarks
- BTC ATR(1h): 0.5-1.5% under normal conditions; liquidation cascade spikes of 3-5% with instant recovery are documented.
- SOL ATR(1h): frequently 1-2%. XRP: 1.5-3%. Both noisier than BTC per unit move.
- Wick spikes of 0.5-1.0% below session support are common even in trending markets.

### Recommended minimum stop distance (underlying price)

| Asset | Min stop distance (underlying) | At 2x (leveraged loss) |
|-------|-------------------------------|------------------------|
| BTC | 0.5-0.8% | 1.0-1.6% |
| SOL | 0.8-1.2% | 1.6-2.4% |
| XRP | 1.0-1.5% | 2.0-3.0% |

**Key rule:** Stop should be placed 20-30% of the distance before the liquidation price. At 2x, liquidation is ~45-50% adverse move away, so practical stops are structurally far from liquidation. The binding constraint is noise, not liquidation risk.

**Practical floor:** A stop tighter than 0.5% underlying on BTC will be hit by normal wicks regardless of direction. For scalp timeframes (5-15m), a 0.5-1.0% underlying stop (1.0-2.0% leveraged) is the minimum viable width.

---

## 3. Recommended R:R Ratios for Leveraged Crypto Futures

### Consensus from practitioner sources:
- **General leveraged futures:** Minimum 1:2 R:R (risk 1 unit, target 2 units) — the widely cited floor.
- **Scalping specifically:** 1:1 to 1:1.5 R:R used by high-frequency scalpers relying on win rate > 60%. Without that documented edge, 1:1 R:R does not produce positive expectancy after fees.
- **Swing/positional at 2x:** 1:2.5 to 1:3 is the practitioner target, allowing win rates of 40-45% to remain profitable.

### Applying to the bot's 1.5-2% leveraged target:

| TP (leveraged) | R:R | Implied SL (leveraged) | Implied SL (underlying at 2x) |
|----------------|-----|------------------------|-------------------------------|
| 1.5% | 1:2 | 0.75% | 0.375% |
| 1.5% | 1:1.5 | 1.0% | 0.5% |
| 2.0% | 1:2 | 1.0% | 0.5% |
| 2.0% | 1:2.5 | 0.8% | 0.4% |

**Warning:** A 1:2 R:R with 2% TP implies a 0.5% underlying SL — at the lower edge of BTC's noise floor and well inside SOL/XRP noise. Consider widening to 1.5% leveraged SL (0.75% underlying) and accepting 1:1.3 R:R, compensated by maintaining win rate > 55%.

---

## 4. Liquidation Price and Stop Placement

### At 2x isolated margin:
- At 2x leverage, liquidation threshold is approximately a 45-50% adverse price move from entry.
- Example: BTC long at $84,000 with $1,000 margin at 2x ($2,000 notional) — liquidation near $42,000-$43,000.

### Practical stop vs. liquidation relationship:
- Liquidation at 2x is structurally far from entry — it is NOT the binding constraint for stop placement.
- Stops should be set on market structure (support/resistance, ATR, noise floor), not on liquidation distance.
- 20-30% buffer rule: stop >= 20% of entry-to-liquidation gap. At 2x with ~48% gap, 20% = 9.6% underlying — far wider than needed for scalp stops. The noise floor (0.5-1.5% underlying) always binds first.

### Cross-margin vs. isolated margin:
- Cross-margin uses full account balance as buffer, pushing liquidation even further away.
- Stop placement logic is unchanged — always driven by noise floor and structure, not liquidation math.

---

## 5. Position-Level vs. Account-Level Targets

### Account-level risk rule (standard):
- Risk no more than 1-2% of total account equity per trade.
- Position size formula: `Position margin = (Account × Risk%) / (SL% leveraged)`

### Position-level TP target:
- The 1.5-2% leveraged TP is a position-level return on margin deployed, not on total account.
- If position margin is $500 of a $10,000 account, a 2% leveraged TP = $10 profit = 0.1% account return per trade.

### Reconciling both constraints:
- Cap margin per trade at 10-20% of account. This produces 0.1-0.2% account-level risk per 1% leveraged SL.
- Scale up only after edge is documented over 100+ trades.

---

## Key Takeaways for the Bot (BTC/SOL/XRP at 2x)

1. **TP math is clean:** 1.5-2% leveraged target = 0.75-1.0% underlying move. Reachable within single candles on active sessions.
2. **Minimum SL: 0.5% underlying on BTC, 0.8-1.0% on SOL/XRP.** Tighter stops will be noise-whipped regardless of signal quality.
3. **Target R:R >= 1:2** when SL is tight. If widening SL to clear noise, accept 1:1.5 and require win rate >= 55%.
4. **Liquidation is irrelevant to stop placement at 2x.** It sits 45-50% from entry — the noise floor always binds first.
5. **Account risk cap:** Size positions so SL hit costs <= 1% of account equity per trade.

---

## Sources

- [Futures Leverage Trading Guide — MEXC Learn](https://www.mexc.com/learn/article/futures-leverage-trading-guide-master-2x-500x-futures-strategies-risk-control/1) — Leverage mechanics, liquidation, and risk controls with concrete examples.
- [How Much Leverage Is Too Much? — Mudrex Learn](https://mudrex.com/learn/how-much-leverage-is-too-much-for-crypto-futures/) — Risk-calibrated leverage selection; 2-3x recommended.
- [Crypto Futures Trading: Position Sizing, Leverage, and Risk — Crypticorn](https://www.crypticorn.com/crypto-futures-trading-position-leverage-risk/) — Position sizing formulas, profit calculation on price moves.
- [What Is the Risk-Reward Ratio in Crypto Trading — BingX Learn](https://bingx.com/en/learn/article/what-is-risk-reward-ratio-in-crypto-trading-and-how-to-manage-risk-and-profits) — 1:2 minimum for leveraged futures.
- [Liquidation: How To Avoid It in Crypto Futures — WazirX Blog](https://wazirx.com/blog/liquidation-in-crypto-futures-explained/) — Stop-loss buffer relative to liquidation; wick cascade examples.
- [Leverage Trading Crypto — Altrady Blog](https://www.altrady.com/blog/crypto-trading-strategies/leverage-trading) — BTC intraday noise floor, 1-2% capital risk per trade standard.
