# Research plan: TP/SL best practices on leveraged crypto futures

## Core question
What are best practices for setting take-profit and stop-loss levels on leveraged crypto futures positions, and how does 2x leverage specifically factor into the right target percentages?

## Context
- Bot trades BTC, SOL, XRP perp futures on Coinbase CDE at 2x leverage
- Current config: atr_stop_mult=2.0, atr_tp_mult=4.0 (1:2 R:R ATR-based)
- Goal: each trade making 1.5-2% profit (leveraged return)
- Trailing stop: breakeven at 1×ATR profit, trail from 2×ATR profit
- Question: is the current approach optimal, and should we add a % floor?

## Subtopics
1. **leverage-rr**: How 2x leverage affects TP/SL sizing — optimal R:R at leverage, position-level vs account-level targets, liquidation buffer requirements
2. **winrate-rr**: Win rate vs. R:R tradeoffs for crypto scalping — what ratios are sustainable, ATR-based vs fixed-percentage targets, empirical data from crypto futures
3. **trailing-stop**: Trailing stop best practices on leveraged futures — when/how to trail, breakeven mechanics, optimal trail distance, impact on expectancy

## Output format
Concrete recommendation: what TP/SL config changes (if any) should be made to target 1.5-2% per trade at 2x leverage, with supporting data.
