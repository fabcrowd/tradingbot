# Research plan: scalp bot self-adjusting and learning models

## Core question
How does the Coinbase CDE scalp bot generate signals, execute trades, and self-adjust its parameters and risk posture through WFO, param tuning, and regime detection?

## Subtopics
1. **Signal pipeline**: indicators.py → signal_engine.py → scalp_trader.py — how raw candles become buy/sell decisions
2. **WFO + param tuner**: scalp_wfo.py + param_tuner.py — how the system trains, selects champion params, and perturbs them live
3. **Regime + risk management**: scalp_runtime.py + scalp_config.py — regime detection, risk halts, position sizing, warmup, snapshots

## Output format
Mermaid diagram + written report
