# Research plan: Freqtrade ecosystem vs Fabcrowd Arceus scalp bot

## Core question
What structural patterns, signal implementations, backtesting approaches, and optimization techniques from the Freqtrade ecosystem (main bot, strategy library, technical indicators) validate, enhance, or conflict with our Coinbase CDE scalp bot's architecture — and what concrete improvements should we adopt?

## Subtopics
1. **Architecture & execution model**: Freqtrade's strategy lifecycle, order management, exchange abstraction, and event loop vs our asyncio scalp runtime + Coinbase order manager. Focus on: how they handle multi-exchange, position lifecycle, and risk management.
2. **Strategy & signal patterns**: Freqtrade-strategies library — which RSI/EMA/Bollinger/MACD implementations are most similar to our signal modes, what novel signal combinations exist, and how their strategy framework compares to our multi-mode WFO approach.
3. **Backtesting, optimization & walk-forward**: Freqtrade's backtesting engine, hyperopt (ML optimization), and the `technical` indicators library — how their optimization pipeline compares to our WFO + param tuner + vec backtest, and whether their approach reveals gaps or confirms our design.

## Output format
Comparative report with concrete recommendations: adopt, adapt, or reject — with rationale for each.
