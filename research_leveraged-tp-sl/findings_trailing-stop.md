# Findings: Trailing Stop Best Practices — Leveraged Crypto Futures

**Summary:** Trailing stops outperform fixed TPs in trending conditions but underperform in choppy markets; the hybrid approach (partial fixed TP + trailing runner) consistently produces the best risk-adjusted expectancy for crypto scalping. ATR-based trailing distances are superior to fixed-percentage distances in crypto because they adapt to volatility regimes, and the consensus breakeven trigger threshold is 1×R — not 0.5×R — because sub-1R moves haven't yet proved the thesis and early breakeven triggers generate excess false exits on normal retracements.

---

## 1. Trailing Stop vs. Fixed TP — Which Maximizes Expectancy?

**The core tension:**
- A fixed TP guarantees a known reward but caps the trade, leaving money on the table in momentum regimes.
- A trailing stop lets winners run but gives back unrealized PnL before triggering; it also fails in range/choppy conditions where normal oscillations repeatedly stop out healthy positions.

**What the evidence says:**
- Neither dominates universally — the choice is regime-dependent.
- Trailing stops improve expectancy in **trending, low-noise** conditions (clear directional momentum after entry).
- Fixed TPs improve expectancy in **mean-reverting, choppy** conditions (price reaches a level then fades).
- For crypto scalping (5m–15m timeframes), a **pure trailing stop** frequently gives back too much — the trail itself becomes the primary source of slippage and drag, because intra-candle wicks routinely exceed 1×ATR without invalidating the trade thesis.

**Practical conclusion:** Fixed TPs at a defined R-multiple produce more consistent expectancy at the scalping horizon. Pure trailing stops for scalps targeting 1.5–2% leveraged return are likely to reduce realized winners relative to a hybrid structure.

---

## 2. Breakeven Trigger Threshold

**Consensus: 1×R before moving stop to entry, not 0.5×R.**

- A trade at 0.5R profit has not yet proven the market agrees with the thesis; a normal crypto retracement at that stage will generate a false breakeven stop-out.
- A trade at 1×R has returned the full risk unit — the stop can now be moved to entry without statistical cost to expectancy.
- Moving to breakeven at 0.5R increases false stops significantly in volatile crypto markets, converting potentially profitable trades into scratch trades.

**Assessment of current bot config (breakeven at 1×ATR):** Correct, *if* 1×ATR profit ≥ 1×R. This holds when the initial SL is also ≤ 1×ATR. Calibration check: `ATR_profit_for_breakeven / initial_stop_distance >= 1.0`. If the SL is wider than 1×ATR, the breakeven is triggering too early.

---

## 3. ATR-Based vs. Percentage-Based Trailing Stops in Crypto

**ATR-based is clearly superior for crypto:**
- Crypto volatility is regime-dependent; a fixed 0.5% trail appropriate in a quiet session becomes a near-certain stop-out during a liquidation cascade or news spike.
- ATR adapts: when the market breathes more, the trail widens; when it quiets, it tightens.
- Standard ATR period for intraday crypto: **14 bars** on the signal timeframe.

**Multiplier ranges from the literature:**
| Multiplier | Regime | Notes |
|---|---|---|
| 1.0–1.5×ATR | Scalping (tight) | High whipsaw risk on sub-15m |
| 2.0–2.5×ATR | Scalping (moderate) | Recommended for 2–4% target moves |
| 3.0×ATR+ | Swing/position | Not relevant here |

**Warning noted explicitly by practitioners:** ATR trailing stops are *less suitable for scalping* than for trend-following, because scalp timeframes have noisy ATR readings. If the 1×ATR trail fires frequently after entry, widen to 1.5×ATR or convert to a fixed TP.

**Assessment of current config (trail at 1×ATR):** On the tight end for a scalp timeframe. This will routinely fire on normal candle-body oscillations. Recommend widening trail to **1.5×ATR** or using the trail only as a runner backstop (see Section 4).

---

## 4. Partial TP + Trailing Runner — Does It Outperform Single Fixed TP?

**Yes, with conditions.** This is the most commonly recommended structure for crypto futures scalping:

**Recommended structure:**
- Close 60% of position at the primary fixed TP (where the 1.5–2% leveraged return is achieved).
- Trail the remaining 40% with **1.5×ATR** trail distance.
- Runner either hits a secondary target or exits on the trail.

**Why it outperforms a single fixed TP:**
1. Locks in guaranteed profit on the majority of the position.
2. Runner participates in extended moves (low probability but high payoff), improving the return distribution tail.
3. Psychological/operational benefit: secured partial exit reduces incentive to manually override the runner.

**Why it outperforms a pure trailing stop:**
1. Eliminates the scenario where all gains are given back before the trail fires on a sudden reversal.
2. The fixed partial exit anchors a minimum P&L floor.

**Illustrative expectancy improvement (45% win rate):**
- Single fixed TP at 2×R: expectancy = (0.45 × 2R) – (0.55 × 1R) = **+0.35R per trade**
- Hybrid (60% at 2×R + 40% runner averaging 2.6×R on winners): expectancy ≈ **+0.42R per trade** — roughly 20% improvement, assuming the runner trail doesn't over-fire.

**Critical caveat:** If the runner trail is 1×ATR on a 5m chart, the runner will stop out on the first normal candle after partial exit, capturing near-zero extra value. The runner trail needs ≥1.5×ATR to have room.

---

## 5. Direct Assessment of Current Bot Config

| Parameter | Current | Literature Recommendation | Assessment |
|---|---|---|---|
| Breakeven trigger | 1×ATR profit | 1×R profit | Correct if SL = 1×ATR |
| Trail start | 2×ATR profit | 1.5–2×R before trailing | Correct |
| Trail distance | 1×ATR behind price | 1.5–2×ATR for scalping | Too tight; whipsaw risk |
| Exit structure | Pure trailing (implied) | Partial fixed TP + trailing runner | Consider hybrid |

**Recommended adjustment for 1.5–2% leveraged profit target:**
1. Take 60% of position at the fixed TP (where 1.5–2% leveraged return is locked).
2. Trail the remaining 40% at **1.5×ATR** (widened from 1×ATR).
3. Keep breakeven trigger at 1×ATR (already correct assuming SL ≤ 1×ATR).

---

## Sources

- [Trailing Stop Loss vs Take Profit — Altrady](https://www.altrady.com/blog/crypto-trading-strategies/trailing-stop-loss-vs-take-profit) — Comparison of trailing stop vs. fixed TP mechanics, hybrid approach, win rate / R-ratio expectancy framework.
- [Stop-Loss, Take-Profit, Triple-Barrier & Time-Exit — Medium/Jakub Polec](https://medium.com/@jpolec_72972/stop-loss-take-profit-triple-barrier-time-exit-advanced-strategies-for-backtesting-8b51836ec5a2) — Conceptual framework for exit mechanisms, partial exits, Python backtesting implementation.
- [ATR Trailing Stops: A Guide to Better Risk Management — TrendSpider](https://trendspider.com/learning-center/atr-trailing-stops-a-guide-to-better-risk-management/) — ATR multiplier ranges, ATR vs. fixed-percentage comparison, regime suitability.
- [ATR Stop Loss Strategy for Crypto — Flipster](https://flipster.io/blog/atr-stop-loss-strategy) — Crypto-specific ATR settings, scalping caveats, ATR superiority in volatile regimes.
- [The Best Time to Move Your Stop Loss to Breakeven — Daily Price Action](https://dailypriceaction.com/blog/the-best-time-move-stop-loss-breakeven/) — 1×R vs 0.5×R breakeven trigger analysis, false stop costs of early breakeven.
- [Stop Loss, Take Profit & Trailing Stop Guide — Hyperdash](https://hyperdash.com/learn/stop-losses-take-profits-trailing-stops-order-types-every-trader-should-know) — Hybrid partial TP + trailing stop structure, expectancy discussion, scaling out benefits.
