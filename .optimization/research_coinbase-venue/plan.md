# Research plan: Coinbase derivatives (CDE/INTX) vs spot (Advanced Trade) for scalp bot

## Core question
For a directional scalp bot running 5-15m bar signals (EMA momentum, DaviddTech, ATR stops/TP) on BTC/SOL/XRP, which Coinbase venue produces better outcomes: CDE nano futures, INTX perpetuals, or Advanced Trade spot?

## Subtopics
1. Fee structures: exact maker/taker tiers, per-leg cost, breakeven spread at each venue
2. Execution quality: liquidity depth, spreads, slippage profile on BTC/SOL/XRP at each venue
3. Strategy fit: bidirectional trading (short capability), leverage, funding rates, contract mechanics, and how each maps to this bot's signal architecture
