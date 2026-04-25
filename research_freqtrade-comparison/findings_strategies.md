# Freqtrade Strategy Library — Signal Patterns and Strategy Framework

## Summary

Freqtrade strategies follow a rigid three-method interface (populate_indicators / populate_entry_trend / populate_exit_trend) that forces clean separation of indicator computation from signal logic. The community strategy library is small (5 numbered strategies in the official repo) but the ecosystem is broad, with FreqST aggregating hundreds of community submissions. Most high-performing strategies use 4-5 indicator confluence (RSI + MACD + Bollinger + ADX + volume filter) rather than the mode-switching approach our bot uses — they bake one fixed combination into a single strategy class and rely on Freqtrade's hyperopt to tune thresholds.

---

## Key Strategy Patterns Found

### Indicator Combinations in Practice

| Strategy | Indicators | Entry Logic | Notes |
|----------|-----------|-------------|-------|
| **Strategy005** (official) | Stoch Fast, RSI, Fisher RSI, SMA(40), SAR, MACD, MINUS_DI | Oversold reversal: price < SMA, stoch crossover, RSI > 26, volume > 4x avg | Dual exit modes (RSI-MACD-DI vs SAR-FisherRSI) |
| **InformativeSample** (official) | EMA(20/50/100), SMA(20) on BTC/USDT 15m | EMA20 crosses above EMA50 AND BTC 15m close > BTC SMA20 | Multi-timeframe + informative pair demo |
| **8787% ROI** (community) | RSI(14), MACD, Bollinger(20), EMA, ADX, ATR, volume | Long: RSI > 30 + close > BB lower + MACD > signal + ADX in range + volume > mean | 5-indicator confluence with ATR-based trailing exit |

### Common Signal Building Blocks

- **Volume filter**: Nearly universal. Most strategies require volume > Nx mean volume (N = 1-4x) as a gate.
- **RSI as regime gate, not standalone signal**: RSI gates entries (RSI > 30 for longs, < 70 for shorts) rather than generating crossover signals.
- **Stochastic crossover**: Used in Strategy005 as a timing trigger (fastd > fastk). Not in our signal engine.
- **ADX range filter**: ADX within a configurable range (not just "above threshold") to filter for trending-but-not-exhausted conditions.
- **Fisher RSI transform**: Normalized RSI variant mapped to [-1, 1]; used for extreme-reading detection. Not in our indicator set.
- **ATR-multiplied trailing exits**: Exit when close < EMA - (multiplier x ATR). Similar to our ATR stoploss but applied as a signal.

### Entry/Exit Logic Patterns

1. **Confluence gating**: All conditions must be true simultaneously (AND logic). No OR-based mode switching.
2. **Tagged entries**: `enter_tag` / `exit_tag` columns for labeling which signal fired — post-hoc analysis, not runtime mode selection.
3. **Dual exit modes**: Strategy005 has two exit rule-sets selectable via parameter — closest parallel to our multi-mode approach, but static config, not WFO-driven.

---

## Framework Comparison: Freqtrade IStrategy vs Our Multi-Mode Signal Engine

| Aspect | Freqtrade IStrategy | Our Signal Engine |
|--------|---------------------|-------------------|
| **Structure** | Single class, 3 methods | Multi-mode engine: each mode is self-contained |
| **Indicator compute** | All indicators in one DataFrame; vectorized pandas | Incremental O(1) via hexital; per-candle on arrival |
| **Signal selection** | One fixed set of entry/exit rules; hyperopt tunes thresholds | WFO selects best mode per symbol; param tuner fine-tunes |
| **Multi-timeframe** | First-class: `@informative` decorator, multiple TFs merged into main DF | Not implemented; single-timeframe per symbol |
| **Informative pairs** | Can pull any pair (e.g., BTC/USDT) as context | Not implemented; each symbol trades independently |
| **Regime detection** | Not built-in; manual via pair-locking, Trade history, informative TF | Implicit via WFO mode selection; no explicit classifier |
| **Execution** | Batch: runs on closed candles, checks all pairs each tick | Streaming: WebSocket-driven, processes each candle as it closes |

---

## Notable Signal Combinations Worth Testing

1. **Stochastic Fast crossover (fastd > fastk)** as entry timing trigger after trend/momentum filters satisfied.
2. **ADX range filter (e.g., 20 < ADX < 45)** — caps upper end to filter blow-off/exhaustion moves.
3. **Fisher RSI transform** — maps RSI to normal distribution, making extreme readings more statistically meaningful.
4. **BTC/USDT as informative pair gate** — before any altcoin long, require BTC 15m or 1h close > SMA(20). Most common "regime" filter in Freqtrade community.
5. **ATR-multiplied dynamic exit (close < EMA - K*ATR)** — more responsive than fixed stops.

---

## Multi-Timeframe & Informative Pairs Gaps

**Multi-timeframe**: Freqtrade's `@informative('1h')` lets 5m strategies trivially access 1h indicators. Common patterns: 5m entries gated by 1h EMA trend, 15m RSI used to filter 5m entries, daily ATR for dynamic stoploss.

**Informative pairs**: BTC/USDT trend as long-only gate for altcoins, ETH/BTC ratio as risk-on/risk-off, total market cap proxy via BTC dominance.

**Our gap**: Each symbol trades in isolation. A shared "market context" feed would be architecturally simple since we already have WebSocket feeds.

---

## Sources

- [freqtrade/freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies) — Official strategy repo
- [Freqtrade Strategy Customization Docs](https://www.freqtrade.io/en/stable/strategy-customization/)
- [Freqtrade Advanced Strategy Docs](https://www.freqtrade.io/en/stable/strategy-advanced/)
- [FreqST](https://freqst.com/) — Community strategy aggregator
- [8787% ROI Algo Strategy (Medium)](https://imbuedeskpicasso.medium.com/the-8787-roi-algo-strategy-unveiled-for-crypto-futures-22a5dd88c4a5)
- [Strategy005 source](https://github.com/freqtrade/freqtrade-strategies/blob/master/user_data/strategies/Strategy005.py)
- [InformativeSample source](https://raw.githubusercontent.com/freqtrade/freqtrade-strategies/master/user_data/strategies/InformativeSample.py)
- [freqtrade/technical](https://github.com/freqtrade/technical) — Community indicator library
