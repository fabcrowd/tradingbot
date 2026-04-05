# Mitch Trading Bot

Spread-based market-making bot for Kraken. Places limit orders on both sides of the bid-ask spread, captures the gap when both fill, repeats every few seconds. Portfolio-level risk controls, adaptive spread learning, and a real-time dashboard.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          config.toml + .env                         │
│  (pairs, spreads, fees, risk limits, API keys)                      │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         main.py  (entry point)                      │
│  1. load_config()                                                   │
│  2. connect Kraken WS (auth + public)                               │
│  3. initialize LiveOrderManager → cancel orphans, reconcile         │
│  4. sync Kraken balances → InventoryManager                         │
│  5. load StrategyLearner state (pain_floor only)                    │
│  6. start BookClient, ThreatDetector, SpreadEngine, Dashboard       │
└──────┬──────────┬──────────┬──────────┬──────────┬─────────────────┘
       │          │          │          │          │
       ▼          ▼          ▼          ▼          ▼
┌──────────┐ ┌─────────┐ ┌────────┐ ┌─────────┐ ┌──────────────────┐
│BookClient│ │Threat   │ │Spread  │ │Strategy │ │DashboardServer   │
│(WS v2   │ │Detector │ │Engine  │ │Learner  │ │(HTTP + WS push)  │
│ public)  │ │         │ │        │ │         │ │                  │
│          │ │velocity │ │_tick() │ │hill-    │ │snapshots @ 0.5s  │
│ L2 book  │→│imbalance│→│per pair│←│climb on │ │alerts broadcast  │
│ updates  │ │blowout  │ │every   │ │$/min    │ │action dispatch   │
│          │ │vol      │ │cycle   │ │         │ │                  │
└──────────┘ └─────────┘ └───┬────┘ └─────────┘ └──────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     LiveOrderManager                                │
│  Kraken WS v2 authenticated: add_order / cancel_order / executions  │
│  Rate limiter (token bucket) · Order state tracking · Fill routing  │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         BotState                                    │
│  PairState (per pair: book, inventory, threat, orders)              │
│  active_orders · recent_fills · total_pnl · risk_halted             │
│  push_alert() → broadcast to all dashboard WS clients               │
└─────────────────────────────────────────────────────────────────────┘
```

### Data flow — one tick

```
BookClient receives L2 update
  → PairState.best_bid/ask/levels updated, last_book_update_ts set
  → ThreatDetector.update() computes velocity, imbalance, vol

SpreadEngine._tick(pair_key) fires every cycle_ms:
  1. Risk gates: risk_halted? pair_halted? book stale?
  2. Reference price: _clean_microprice() — volume-weighted mid excluding own orders
  3. Fee floor: effective_fee_bps + profitability margin
  4. Effective spread: max(config spread, learner spread, fee floor)
     + volatility widen + velocity widen + threat multiplier
  5. Inventory skew: Avellaneda-Stoikov reservation price shift
  6. buy_price = reservation - half_spread
     sell_price = reservation + half_spread
  7. Smart cancel: only cancel if price drift > 1.5× or stale > 600s
  8. Place missing sides via LiveOrderManager (if can_buy/can_sell)

LiveOrderManager sends add_order via Kraken WS v2
  → Kraken confirms → order tracked in active_orders
  → On fill (executions channel) → PnLTracker records, inventory updates
  → DashboardServer pushes snapshot to all connected clients
```

### Key files

| File | Purpose |
|------|---------|
| `config.toml` | All tunable parameters: pairs, spreads, fees, risk limits |
| `backend/server/main.py` | Entry point, startup orchestration, shutdown |
| `backend/server/spread_engine.py` | Core tick loop, spread calculation, order logic |
| `backend/server/live_order_manager.py` | Kraken WS v2 order placement, cancellation, reconciliation |
| `backend/server/state.py` | `BotState`, `PairState`, `ActiveOrder` — central mutable state |
| `backend/server/config.py` | Config model, `load_config()`, fee resolution |
| `backend/server/strategy_learner.py` | Hill-climb optimizer: adjusts spread_bps per pair on $/min |
| `backend/server/book_client.py` | Kraken WS v2 public L2 order book subscription |
| `backend/server/inventory.py` | Balance sync from Kraken, `can_buy()`/`can_sell()` checks |
| `backend/server/threat_detector.py` | Velocity, imbalance, spread blowout, realized vol |
| `backend/server/cex_bot_detector.py` | Order book microstructure: quote flickering, phantom liquidity, symmetric quoting |
| `backend/server/bot_classifier.py` | Combines CEX signals into regime classification (clean/elevated/competitive/toxic) |
| `backend/server/fee_schedule.py` | Kraken fee tier tables (spot, stablecoin, maker_rebate, USDG) |
| `backend/server/pnl.py` | P&L tracking, JSONL persistence, 30-day volume |
| `backend/server/session_logger.py` | JSONL session telemetry (fills, learner, halts, momentum) |
| `backend/server/ws_server.py` | HTTP server + WebSocket push to dashboard |
| `backend/server/rate_limiter.py` | Token-bucket rate limiter for Kraken API calls |
| `lessons.md` | Postmortems, design decisions, operational lessons |
| `AGENTS.md` | Quick-start guide for new agents inheriting the repo |

## Quick Start

```bash
pip install -r backend/requirements.txt
python -m backend.server.main
```

Open `http://localhost:8080` for the dashboard. The engine starts paused — press **START** to begin trading.

## Configuration

Edit `config.toml` for pair settings (spread, order size, max inventory, fees).
Copy `.env.example` to `.env` and add your Kraken API keys for live trading.

### Spread vs fees

`spread_bps` is the **half-width** from mid to each quote. Total quoted width ≈ `2 × spread_bps`.
Fees are charged per fill leg. You need `2 × spread_bps > 2 × fee_bps` for a profitable round-trip.

Key insight: **your spread must also be competitive with the market spread**. If the natural market
spread is 1 bps and you quote at 20 bps, you'll never get filled regardless of fee math. Always
check the live ticker: `https://api.kraken.com/0/public/Ticker?pair=USDGUSDT`.

Fee schedules vary by pair type:
- **USDG pairs**: 0% maker (use `fee_schedule = "usdg"`)
- **Maker rebate pairs** (TEL): 23 bps at lowest tier, negative at high volume
- **Spot crypto** (XRP, BTC, ETH): 25 bps at lowest tier, drops with volume
- **USDe promo**: 0% maker/taker

### Learner behavior

The `StrategyLearner` hill-climbs on EMA-smoothed profit rate ($/min) per pair.
On each session start, `spread_bps` resets to the value in `config.toml`. Only
the `pain_floor` (memory of losing spreads) persists across sessions. Within a session,
the learner tightens on idle (no-fill decay) and widens when recent sells average negative.

**Pain floor mechanics:**
- When the learner widens due to losses, it records the old spread as a `pain_floor` —
  it won't go below this again until time decay lowers it.
- Decay rate: 1 bps per `pain_floor_decay_hours` (default 1 hour). A pain floor of 80
  decays to the config floor of 50 in ~30 hours.
- **Ceiling trap protection:** If the pain floor reaches the ceiling (`adaptive_spread_ceiling_bps`),
  the learner would be permanently stuck. A safety valve resets pain to the midpoint between
  config floor and ceiling. Pain raises are also capped at `ceiling - 10` to prevent this.
- On load, saved pain floors are clamped to `ceiling - 10` to prevent stale high values
  from re-trapping the learner after config changes.

**Volume sync:**
The bot syncs 30-day trading volume from Kraken's `TradeVolume` API every 5 minutes
in a background thread. This ensures fee tier calculations use the real account-wide
volume (including manual trades), not just the bot's own fill replay. The sync never
blocks the order path — `state.volume_30d` is a plain float read in the tick loop.

### Inventory skew

Avellaneda-Stoikov style. When holding excess base inventory, the reservation price shifts
down — bids become less aggressive, asks more aggressive — encouraging sells to rebalance.
`inventory_skew_scale` (default 0.4) controls strength. At max inventory (q=1.0), the full
skew applies.

### Microprice

The engine uses a volume-weighted mid (`_clean_microprice`) as its reference price. This
strips out our own resting orders from the top-of-book calculation to prevent self-pollution
on thin pairs where our order dominates the book.

## Modes

- **Paper** (default): Real Kraken order book data, simulated fills. No API keys needed.
- **Live**: Real orders on Kraken. Requires API keys in `.env`.

Switch via `config.toml` (`mode = "live"`) or from the dashboard.

## Risk Management

Four independent portfolio-level stops, each cancels all orders and halts the engine:

| Stop | Config Key | Description |
|------|-----------|-------------|
| P&L floor | `min_total_pnl_usd` | Halt if cumulative P&L drops below threshold |
| Daily profit | `daily_profit_target_usd` | Halt after reaching daily target |
| Daily loss | `daily_loss_limit_usd` | Halt if daily loss exceeds limit |
| Max drawdown | `max_drawdown_pct` | Halt on % drawdown from peak |

Additional protections:
- **Depeg circuit breaker**: Emergency liquidation if a pegged pair deviates beyond threshold
- **Threat-level spread widening**: Widens spread on HIGH threat; pauses quoting on CRITICAL
- **Momentum hold**: Suppresses buys after sell bursts to prevent buying back tops
- **Stale book guard**: Skips quoting when order book data is older than 600 seconds
- **Fill cascade cooldown**: After any fill, same-side resting orders are cancelled and new
  orders on that side are paused for `fill_cooldown_sec` (default 5s). Prevents stacking fills
  at stale prices on thin books.
- **Sell quantity cap**: Sell exposure is capped to the quantity in profitable `pending_barriers`
  only. Legacy underwater inventory is never sold — only recently-bought inventory at known cost.
- **Anti-deadlock guarantee**: Six independent suppression systems (ping-pong, profitability,
  consecutive loss, momentum, risk halt, cooldown) are coordinated through a priority hierarchy.
  Both sides of the book can never be suppressed simultaneously.

## Alert System

All backend errors and warnings are pushed to the dashboard as toast notifications:
- Order rejections, rate limits, reconciliation issues
- Risk halts, engine errors, stale books
- Balance sync failures

Toasts are color-coded by severity and auto-dismiss. Nothing fails silently.

## MEV / Bot Detection

The bot classifies order book activity into regimes (clean, competitive, toxic) using
microstructure signals:

- **Quote flickering**: Rapid top-of-book changes (> 5/sec suggests bot activity)
- **Symmetric quoting**: Equal-sized bid/ask levels equidistant from mid
- **Phantom liquidity**: Large volume vanishing without trades (with $5 notional floor
  to filter noise on thin books)
- **Level stuffing**: Sudden bursts of deep levels
- **Microprice oscillation**: Tight-band high-frequency wiggle

Signals produce a composite score. Above `mev_bot_score_threshold` (default 0.65), the
spread widens modestly based on the detected pattern. Below threshold, the spread tightens
by up to `mev_clean_tighten_scale` (10%). The system is tuned to stay competitive — it
widens modestly on strong signals only, never panics.

**Thin-book calibration:** On pairs with $20–$50 at the top of book (e.g. DRIFT/USD), normal
smart-cancel behavior looks like phantom liquidity to the detector. Raise
`mev_bot_score_threshold` (0.65+) and `mev_detector_window_sec` (120+) to prevent the
composite score from oscillating on every tick and jittering the spread unnecessarily.

Configure via `[bot]` in `config.toml`: `mev_detection_enabled`, `mev_bot_widen_scale`,
`mev_arb_widen_scale`, `mev_clean_tighten_scale`, `mev_bot_score_threshold`,
`mev_detector_window_sec`.

## Smart Order Management

Orders are not blindly cancelled each cycle. The engine only cancels when:
- **Price drift** > 1.5× half-spread from current target
- **Stale** > 600 seconds without filling
- **Near-fill protection**: Orders within 3 bps of filling are kept alive (up to 300s)
- **Exchange minimum enforcement**: Multi-level orders respect `min_order_qty` per pair —
  levels are reduced rather than placing sub-minimum orders that get rejected

## Advanced Controls

Optional `[bot]` settings in `config.toml`:

- `rate_limit_order_per_sec`, `rate_limit_burst`: Token-bucket pacing for Kraken API
- `threat_quoting_pause`: Pause quoting on CRITICAL threat level
- `trailing_stop_enabled`, `trailing_stop_pct`, `take_profit_usd`: Per-pair trailing/TP exits
- `oco_enabled`, `oco_stop_bps`, `oco_tp_bps`: OCO pairs after buys
- `twap_enabled`, `twap_slice_count`, `twap_duration_sec`: Time-weighted buy splitting
- `btd_enabled`, `btd_sma_short/long`, `btd_levels`, `btd_step_bps`: Buy-the-Dip on SMA downtrend
- `abort_on_withdraw_permission`: Abort if API key has withdrawal access
- `fill_cooldown_sec`: Seconds to pause same-side quoting after a fill (default 5.0)
- `barrier_auto_reseed_pct`: Auto-reseed cost basis at START if sell floor exceeds market by this % (default 5.0, set 0 to disable)
- `mev_detection_enabled`, `mev_bot_widen_scale`, etc.: Bot detection and counter-strategy
- `mev_bot_score_threshold`, `mev_detector_window_sec`: Tune detection sensitivity per book depth
- `min_order_qty` (per pair): Exchange minimum order volume — levels auto-reduce to fit

## Cost Basis & Fill Barriers

Each buy fill creates a `FillBarrier` entry in `data/fill_barriers.json` tracking the exact
entry price, quantity, and (if `triple_barrier_enabled`) stop/tp/time-limit exit levels.
`min_profitable_sell_price()` uses the cheapest barrier to floor sell prices above breakeven.

**When the position goes underwater** (market drops below all barriers), sells are suppressed.
The bot auto-detects this at START and reseeds to current market mid automatically.

**Auto-reseed:** At every START, after loading barriers from disk, the engine checks whether
`min_profitable_sell_price > mid × (1 + barrier_auto_reseed_pct / 100)`. If so, it calls
`reseed_barriers_at_mid()` automatically — no operator action required. Tune the threshold
in `config.toml` (`barrier_auto_reseed_pct`, default `5.0`). Set to `0` to disable.

**Manual override** (use mid-session without restart):
```
Send via browser console:
new WebSocket(`ws://${location.host}/ws`).onopen = function() {
  this.send(JSON.stringify({ action: "reseed_barriers", pair_key: "DRIFT_USD" }));
}
```

**Full reset path** (cleanest, clears all state):
```
Dashboard → Full Reset (shift-click) → START
Barriers wiped → load_barriers() seeds from current mid → auto-reseed check is a no-op.
```

On `load_barriers()` at startup, if the barrier file is empty or missing, the bootstrap
path seeds from current `mid_price` (not historical blended cost). Inventory gaps between
tracked barriers and wallet balance are automatically reconciled with a blended-cost entry.

## Persistence

- Fills: `data/trades_{paper|live}.jsonl` — cumulative P&L replayed on restart
- Sessions: `data/session_YYYYMMDD_HHMMSS.jsonl` — full event telemetry
- Learner: `data/learner_state_{paper|live}.json` — pain_floor across sessions
- Cost basis: `data/cost_basis.json` — per-pair blended cost basis
- Fill barriers: `data/fill_barriers.json` — per-fill lot tracking for profitable sell pricing
- Volume: synced from Kraken `TradeVolume` API every 5 min (background, non-blocking)

## Backtesting

Replay recorded session fills with different spread assumptions:

```bash
python -m backend.server.backtest --session data/session_YYYYMMDD_HHMMSS.jsonl --spread-bps 4
python -m backend.server.backtest --session data/session_YYYYMMDD_HHMMSS.jsonl --spread-bps 4 --compare 8
```

## Scalp Bot

A second, independent trading engine running alongside the MM bot on separate pairs.
Uses directional momentum signals rather than spread capture.

### Architecture

```
config.toml [scalp] section
  enabled, allocated_capital_usd, pairs (XBT/USD, ETH/USD)
         │
         ▼
ScalpRuntime (asyncio Task in main.py)
         │
         ├── CandleFeed (public WS ohlc channel)
         │     • Seeds 100 candles from REST on startup
         │     • Streams confirmed closed candles from WS
         │     • Never fires on open/live candles (no repainting)
         │
         ├── IndicatorSet (per pair, via hexital — O(1) per candle)
         │     • EMA fast (9) / slow (21)
         │     • RSI (9)
         │     • ATR (14) — for stop/tp sizing
         │     • Session VWAP (resets midnight UTC)
         │     • Volume rolling average (20-bar)
         │
         ├── SignalEngine
         │     • Evaluates 4 signals per closed candle:
         │         1. EMA crossover (fast crossed above slow)
         │         2. RSI 50–70 (in gear, not overbought)
         │         3. Price above session VWAP
         │         4. Volume spike (>1.5× 20-bar average)
         │     • Requires min_signals (default 3) to align
         │     • Per-pair signal cooldown + loss cooldown
         │     • Long-only (Kraken spot, no shorting)
         │
         └── ScalpTrader
               • Sizes position: risk_pct% of allocated_capital / stop_distance
               • Entry: limit or market order via LiveOrderManager
               • On fill: places stop-loss-limit + take-profit-limit
               • On exit fill: cancels sibling (application-layer OCO)
               • Tracks daily P&L, halts on daily_loss_limit_pct
```

### Signal logic — one closed candle

```
CandleFeed receives confirm=true candle
  → IndicatorSet.update(candle) → IndicatorValues

SignalEngine.evaluate():
  signal_count = 0
  if EMA(9) crossed above EMA(21):  signal_count++  ← strongest signal
  elif EMA(9) > EMA(21):            signal_count++  ← continuing trend
  if 50 < RSI(9) < 70:             signal_count++
  if close > session_VWAP:         signal_count++
  if volume > 1.5× volume_MA(20):  signal_count++

  if signal_count >= min_signals (3):
    stop  = entry - ATR(14) × atr_stop_mult (1.0)
    tp    = entry + ATR(14) × atr_tp_mult   (2.0)  ← 2:1 R:R
    → ScalpSignal(entry, stop, tp, confidence)

ScalpTrader.try_open():
  qty = (allocated_capital × risk_pct) / stop_distance
  → place limit buy at entry price
  → on fill: place stop-loss-limit + take-profit-limit
  → on either exit fill: cancel sibling order
```

### Key files

| File | Purpose |
|------|---------|
| `backend/server/scalp_bot/candle_feed.py` | WS ohlc subscription + REST seed |
| `backend/server/scalp_bot/indicators.py` | Incremental EMA/RSI/ATR/VWAP/volume (hexital) |
| `backend/server/scalp_bot/signal_engine.py` | 3/4 confluence evaluation, cooldowns |
| `backend/server/scalp_bot/scalp_trader.py` | Position lifecycle, OCO, capital management |
| `backend/server/scalp_bot/scalp_runtime.py` | asyncio Task, wires all components |
| `backend/server/scalp_bot/scalp_config.py` | Config dataclass, parsed from `[scalp]` |

### Configuration

```toml
[scalp]
enabled = true                   # set false to disable entirely
allocated_capital_usd = 150.0    # USD budget — separate from MM bot capital
max_concurrent_positions = 2
daily_loss_limit_pct = 5.0       # halt after losing 5% of allocated capital in one day
order_type = "limit"             # "limit" = maker (preferred), "market" = immediate

[scalp.pairs.BTC_USD]
symbol = "XBT/USD"
interval = 5                     # 5-minute candles
ema_fast = 9
ema_slow = 21
rsi_period = 9
atr_period = 14
atr_stop_mult = 1.0              # stop = entry - 1×ATR
atr_tp_mult = 2.0                # tp   = entry + 2×ATR
risk_pct = 0.01                  # 1% of allocated_capital at risk per trade
min_signals = 3                  # require 3 of 4 signals
signal_cooldown_sec = 60.0
loss_cooldown_sec = 120.0
min_candles_required = 30        # warm-up period before first trade
```

### Separation from MM bot

The scalp bot and MM bot share the same process and `BotState` but are fully independent:

- **Separate pairs**: scalp uses XBT/USD + ETH/USD; MM uses USDG/USDC etc. Kraken
  rate limits are per-pair — overlapping pairs would compete for the same counter.
- **Separate capital**: `allocated_capital_usd` is a hard cap. The scalp bot never
  touches MM bot quote balances.
- **Shared halt**: when `state.risk_halted = True` (MM bot portfolio stop), the scalp
  bot also stops entering new positions.
- **Shared `LiveOrderManager`**: both bots use the same authenticated WS connection
  for order placement. Fill events are routed by `cl_ord_id` prefix (`scalp_entry_*`,
  `scalp_stop_*`, `scalp_tp_*`).

### Activation

```bash
pip install hexital   # incremental indicators library (one-time)
```

In `config.toml`, set `scalp.enabled = true`, then Dashboard → **RESTART PROCESS**.
The bot seeds indicators from 100 historical candles before placing any trades.

### Walk-forward optimization (WFO) and LOOKBACK progress

When `wfo_enabled` is true, the scalp runtime runs `ScalpWalkForwardOptimizer`: periodic rolling train/holdout grid search over stored bars, writing a **champion** parameter set for live trading.

**Config (`[scalp]` in `config.toml`):**

| Key | Role |
|-----|------|
| `wfo_enabled` | Master switch for the WFO task and UI block |
| `wfo_interval_sec` | Seconds between scheduled passes (default 3600); loop sleeps first, then runs |
| `wfo_train_days` / `wfo_holdout_days` | Rolling window sizes (defaults 14 / 7) |
| `wfo_min_trades` | Minimum trades for candidate strategies |
| `wfo_objective` | Scoring objective (e.g. `expectancy_sqrt_n`) |

**Note:** Rolling **step** size for windows defaults to **7 days** in `WFOConfig` (`scalp_wfo.py`) and is not read from TOML unless extended.

**Data readiness:** Before WFO can evaluate multiple rolling folds, each pair needs enough **persisted** OHLC history. The server computes `overall_progress_pct` as the **worst** pair’s readiness: calendar span must cover train+holdout, and there must be at least **two** rolling windows. Bars are loaded with a window of `train + holdout + 3×step + 1` days (same footprint as the optimizer). When a champion is already active, the dashboard **LOOKBACK** bar shows full progress (`ui_progress_pct` = 100%) even if you are still accumulating history for the next run.

**Dashboard (`frontend-new`):** A **LOOKBACK** strip below the header shows progress, short status text, and countdown to the next scheduled WFO pass when `scalp.wfo` is present in snapshots. Restart the backend process after enabling WFO so the snapshot includes the new field.

## Docker

```bash
docker compose up --build -d
```

Mounts `./data`, `./config.toml` (read-only), and `.env` for API keys.
Dashboard at `http://localhost:8080`.
