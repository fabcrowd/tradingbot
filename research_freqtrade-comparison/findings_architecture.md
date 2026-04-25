# Freqtrade Architecture, Execution Model, and Risk Management

Freqtrade is a Python 3.11+ open-source crypto trading bot that uses CCXT for exchange abstraction, SQLite for trade persistence, and a synchronous throttled main loop (default ~5s cycle). It supports spot and futures/perps on 10+ exchanges, with a strategy interface based on vectorized pandas DataFrames, built-in backtesting, hyperparameter optimization, and optional ML via FreqAI.

---

## Strategy Lifecycle

- Strategies subclass `IStrategy` and implement three core methods: `populate_indicators()`, `populate_entry_trend()`, `populate_exit_trend()`. All operate on pandas DataFrames (vectorized, column-based signals).
- Additional callbacks: `bot_loop_start()` (called each tick), `confirm_trade_entry()`, `confirm_trade_exit()`, `custom_stoploss()`, `custom_stake_amount()`, `leverage()`, `adjust_trade_position()`.
- In live mode, the full callback chain runs every ~5 seconds per iteration. In backtesting, `populate_*` methods run once per pair over the full history.
- Strategies are loaded as Python modules from a configurable directory. One strategy is active per bot instance.

## Exchange Abstraction (CCXT)

- Freqtrade wraps CCXT in its own `Exchange` class (`freqtrade/exchange/exchange.py`), which normalizes order types, handles rate limiting, and manages exchange-specific quirks.
- CCXT provides unified REST API access to 100+ exchanges. Freqtrade also supports CCXT Pro (websockets) for real-time data, with automatic fallback to REST if the WS connection drops.
- Exchange-specific stoploss-on-exchange logic is implemented per-exchange since CCXT does not unify this.
- Orderbook data comes from `fetch_order_book()` (L2 aggregated); ticker data from `fetch_ticker()`/`fetch_tickers()`.

## Order Management

- Orders and trades are persisted in SQLite via SQLAlchemy ORM (`Trade` and `Order` models).
- The bot tracks one open position per pair at a time. The `adjust_trade_position()` callback allows DCA by adding to an existing position.
- Order lifecycle: signal detected -> `confirm_trade_entry()` callback -> order placed via exchange -> order tracked in DB -> fill confirmation -> position open -> exit signal or stoploss triggers close.
- The `FreqtradeBot` class orchestrates the main loop: fetch open trades, refresh pairlist, download OHLCV, call strategy, evaluate entries/exits, manage orders.

## Position Tracking

- All trade state lives in SQLite. Each `Trade` record holds entry price, stake amount, leverage, fees, current profit, stoploss price, and associated `Order` records.
- Profit/loss is calculated in real-time using current ticker prices.
- No in-memory-only state for positions; everything is persisted, making the bot resilient to restarts.

## Risk Controls

**Stoploss**: Fixed percentage-based (`stoploss = -0.10`), stoploss-on-exchange (actual stop order placed on exchange), trailing stoploss (configurable positive offset, trailing distance), and custom stoploss via `custom_stoploss()` callback (e.g., ATR-based, time-based).

**ROI Table**: Time-based profit targets mapping minutes-since-entry to minimum profit for auto-exit. E.g., `{"0": 0.04, "30": 0.02, "60": 0.01}`.

**Protections Plugin**: `StoplossGuard`, `MaxDrawdown`, `LowProfitPairs`, `CooldownPeriod`. These pairlock mechanisms temporarily disable pairs after consecutive losses or drawdown thresholds. They are per-pair controls, not true portfolio-level halts.

**Leverage & Liquidation**: Leverage set per-trade via `leverage()` callback. Supports `isolated` and `cross` margin modes.

## Multi-Pair Handling

- Pairlist plugins dynamically select which pairs to trade (e.g., `VolumePairList` picks top-volume pairs).
- The bot iterates over all whitelisted pairs each loop cycle, evaluating signals for each.
- One position per pair maximum (no grid or multi-position per pair natively).
- All pairs share the same strategy instance and configuration. No per-pair config overrides natively (workaround: conditional logic inside strategy based on pair name).

## Futures/Perps Specifics

- Enabled via `trading_mode = "futures"` in config, with `margin_mode = "isolated"` or `"cross"`.
- Supports long and short positions. Short entry via `enter_short` signal column in DataFrame.
- Funding rate costs are not natively tracked in P&L (known gap).
- Liquidation price awareness exists but is delegated to the exchange.

---

## Comparison Points (vs. Our Coinbase CDE Scalp Bot)

| Dimension | Freqtrade | Our Bot |
|---|---|---|
| **Runtime** | Synchronous throttled loop (~5s), single-threaded. CCXT Pro WS optional. | asyncio event loop, native async. Lower latency. |
| **Exchange layer** | Generic CCXT abstraction (100+ exchanges). | Coinbase-specific order manager (REST+WS), tightly coupled to CDE. |
| **Strategy model** | Single strategy class, vectorized DataFrame signals. | Multi-mode signal engine with WFO-driven mode selection. |
| **Optimization** | Hyperopt (Bayesian) on historical data. No walk-forward. | Walk-forward optimization with rolling windows. |
| **Per-pair config** | Not native. Must be hacked via conditional logic. | First-class per-pair config with dedicated param sets. |
| **Position sizing** | `custom_stake_amount()` callback. ATR-based not built-in. | ATR-based sizing built into core. Correlation group sizing. |
| **Portfolio risk** | Per-pair protections only. No true portfolio-level halt. | Portfolio-level risk halts, correlation group limits. |
| **Fee handling** | Fee-aware P&L. No maker/taker optimization in order placement. | Explicit maker/taker awareness influencing order type and spread. |
| **Telemetry** | SQLite + optional Telegram/API. | Session JSONL with structured events. |
| **Dashboard** | FreqUI (web) + Telegram bot. Mature. | Custom React dashboard via WebSocket. More customizable. |

---

## Sources

- [Freqtrade GitHub README](https://github.com/freqtrade/freqtrade)
- [Freqtrade Bot Basics](https://www.freqtrade.io/en/stable/bot-basics/)
- [Freqtrade Strategy Callbacks](https://www.freqtrade.io/en/stable/strategy-callbacks/)
- [Freqtrade Stoploss Documentation](https://www.freqtrade.io/en/stable/stoploss/)
- [Freqtrade Short/Leverage Docs](https://www.freqtrade.io/en/stable/leverage/)
- [Freqtrade Plugins (Protections)](https://www.freqtrade.io/en/stable/plugins/)
- [DeepWiki: freqtrade/freqtrade](https://deepwiki.com/freqtrade/freqtrade)
