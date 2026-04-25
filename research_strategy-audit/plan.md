# Research plan: Strategy audit — canonical rules vs implementation

## Core question
For each of the 5 strategy modes in the scalp bot, what are the canonical entry/exit/SL/TP/trailing-stop rules per the original published strategy, and how does the bot's implementation (signal_engine.py + scalp_vec_backtest.py) deviate?

## Subtopics
1. **ema-momentum-rsi-vwap**: Canonical EMA cross + RSI zone + VWAP + volume confluence rules (trend-following, long bias)
2. **rsi-reversion**: Canonical RSI mean-reversion rules (oversold/overbought entries, RSI recovery exit) — and whether fixed-% TP or ATR TP is canonical
3. **tony-ema-scalper-macd-scalp**: Tony's EMA Scalper (single EMA + S/R levels) and Ehlers super-smoother MACD scalp — canonical entry, exit, and trail rules
4. **daviddtech-wae-adx-t3**: DaviddTech Optimized Strategy — Tillson T3, HLC trend bands, Waddah Attar Explosion, ADX — canonical entry/exit rules and WAE interpretation

## Output format
Findings report: for each strategy, canonical rules → what the bot does → gaps/bugs → recommended fixes
