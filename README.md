# Mitch Trading Bot

**Active product:** **Scalp bot on Coinbase Derivatives Exchange (CDE)** — directional signals, walk-forward tuning, and execution via Coinbase (see `[scalp]` in `config.toml` and `coinbase_order_manager.py`).

**Shuttered (not in active use):** **Kraken spot market maker** — spread capture, inventory skew, adaptive spread learning. Trading capital has moved off Kraken; the MM stack remains in the repo for a future revival, but treat it as **inactive** unless you explicitly re-enable and fund Kraken.

Real-time dashboard: `frontend-new/` (Vite + React) or built assets; backend HTTP/WebSocket from `[server]` in `config.toml` (default **8080**).

---

## Kraken spot market maker — architecture (shuttered)

The following describes the **legacy / dormant** Kraken MM pipeline. It still runs in `main.py` when configured, but **operations and docs assume Coinbase CDE scalp is the primary system** until Kraken MM work resumes.

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
│  2. connect coinbase (auth + public)                               │
│  3. initialize LiveOrderManager → cancel orphans, reconcile         │
│  4. sync coinbase balances → InventoryManager                         │
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

**Primary (Coinbase CDE scalp):**

| File | Purpose |
|------|---------|
| `config.toml` | `[scalp]`, venue, pairs; also Kraken MM keys if MM revived |
| `backend/server/scalp_bot/scalp_runtime.py` | Scalp coordinator (feed, WFO, snapshots) |
| `backend/server/scalp_bot/scalp_config.py` | Parsed scalp configuration |
| `backend/server/coinbase_order_manager.py` | Coinbase / CDE order execution |
| `backend/server/scalp_bot/signal_engine.py` | Signal evaluation and modes |
| `backend/server/scalp_bot/bar_store.py` | Historical bars / backfill |
| `backend/server/ws_server.py` | Dashboard HTTP + WebSocket (scalp actions, snapshots) |

**Kraken spot MM (shuttered — code retained):**

| File | Purpose |
|------|---------|
| `config.toml` | `[bot]` / `[pairs.*]` when MM is used |
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
| `backend/server/rate_limiter.py` | Token-bucket rate limiter for Kraken API calls |
| `lessons.md` | Postmortems, design decisions, operational lessons |
| `AGENTS.md` | Quick-start guide for new agents inheriting the repo |

## Quick Start

```bash
pip install -r backend/requirements.txt
python -m backend.server.main
```

Open `http://localhost:8080` for the dashboard. The MM engine starts **paused** — press **START** if Kraken MM is enabled; **scalp** behavior depends on `[scalp]` and sim/live toggles in the UI.

## Configuration

**Primary (Coinbase CDE scalp):** set `[scalp] venue = "coinbase_perps"` and CDE-style `product_id` symbols (e.g. `BIP-20DEC30-CDE`, `SLP-20DEC30-CDE`, `XPP-20DEC30-CDE` — match your account; **INTX** IDs such as `BTC-PERP-INTX` can **403** on CDE-only accounts). Add `COINBASE_API_KEY` and `COINBASE_API_SECRET` to `.env` (see `.env.example` for PEM formatting). Some docs still mention **INTX** for Coinbase International; prefer **CDE** product IDs when your account is CDE.

**Optional — INTX portfolio UUID:** if open perps never appear in the app but Coinbase shows them, auto-discovery may be using the wrong Advanced Trade sub-portfolio. Set **`COINBASE_INTX_PORTFOLIO_UUID`** in `.env` (loaded into **`AppConfig.coinbase_intx_portfolio_uuid`**). See **`lessons.md` §30** for reconcile cadence, **`exchange_open_orders`**, and **buying power vs total equity**.

**Kraken MM (only if reviving the shuttered bot):** edit `[bot]` / `[pairs.*]` for spreads, sizes, fees; copy `.env.example` to `.env` and add Kraken API keys for live Kraken trading.

**Operational validation (recommended):** run the scalp bot in `sim_mode` on live Coinbase candles for 24h+ before enabling live orders; go live with minimal size and **1× leverage** on a single product first.

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

- **Kraken MM — Paper** (default for MM path): Real Kraken order book data, simulated fills. No Kraken keys needed.
- **Kraken MM — Live**: Real orders on Kraken. Requires Kraken keys in `.env`. **Shuttered in current operations** (no Kraken funds); only relevant if you revive MM.
- **Scalp (Coinbase CDE):** sim vs live is controlled via dashboard / scalp settings and Coinbase credentials — this is the **active** trading path.

Switch MM mode via `config.toml` (`mode = "live"`) or from the dashboard.

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
- Scalp WFO: `data/scalp_champion.json` — **per-symbol** champion rows (see Scalp / WFO section above)
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

## Scalp bot (Coinbase CDE) — primary trading system

The **main** automated strategy in current use: runs in the same process as the Kraken MM code but targets **Coinbase CDE** (or configured venue) with directional / multi-mode signals, WFO, and bar store — not Kraken spread capture. Keep **scalp pairs disjoint** from any enabled Kraken MM pairs if both were ever on, to avoid rate-limit contention (with MM shuttered, overlap is mainly a future footgun).

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
         │     • Side / shorting rules depend on venue (spot vs CDE perps)
         │
         └── ScalpTrader
               • Sizes position: risk_pct% of allocated_capital / stop_distance
               • Entry: limit or market via venue order path (Coinbase or Kraken)
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
| `backend/server/scalp_bot/candle_feed.py` | WS ohlc subscription + REST seed; `register_tick_callback` for live regime / intra-bar hooks |
| `backend/server/scalp_bot/indicators.py` | Incremental EMA/RSI/ATR/VWAP/volume (hexital) |
| `backend/server/scalp_bot/signal_engine.py` | 3/4 confluence evaluation, cooldowns |
| `backend/server/scalp_bot/scalp_trader.py` | Position lifecycle, OCO, capital management |
| `backend/server/scalp_bot/scalp_runtime.py` | asyncio Task, wires all components |
| `backend/server/scalp_bot/scalp_config.py` | Config dataclass, parsed from `[scalp]` |
| `backend/server/scalp_bot/regime_risk.py` | Triggers for “WFO risk on” (volume / ATR-scaled move) |
| `backend/server/scalp_bot/scalp_wfo.py` | WFO loop; optional dynamic sleep via `interval_sec_resolver` |

### INTX sync, balances, and dashboard

- **Reconciliation** (`coinbase_order_manager.reconcile_scalp_intx_positions`): pulls **`list_perps_positions`**, then **`get_perps_position`** for each configured `[scalp.pairs.*].symbol` missing from that list, merges into `ScalpTrader` (including manual opens). Runs on the **~30s** balance poll and about every **~12s** from the fill-poll loop.
- **Open orders:** each reconcile pass also queries **`list_orders`** with **`OPEN`** for those product IDs. The scalp snapshot includes **`exchange_open_orders`** (Scalp tab). Unfilled entries are **orders**, not **positions** — they will not appear in perp position APIs until filled.
- **Analytics tab (Coinbase perps):** **COINBASE_CAPITAL** shows futures **total equity**, **committed** (margin in positions + collateral in open orders), **available margin** / **buying power**, and **spot USDC+USD available**.
- **Product IDs** in config must **exactly** match Coinbase. Otherwise legs show under **`intx_unmapped_positions`** on the Scalp tab and are not mapped to a pair key.

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

### Relationship to the Kraken MM bot (shuttered)

The scalp bot and Kraken MM share the same process and `BotState` but are independent strategies:

- **Separate pairs / venues**: scalp uses `[scalp]` symbols (e.g. CDE product IDs); MM used Kraken spot pairs. Do not overlap symbols if both are enabled later.
- **Separate capital**: `allocated_capital_usd` caps scalp; MM used Kraken balances when it was active.
- **Shared halt**: when `state.risk_halted = True` (portfolio-level MM risk stop), scalp typically stops entering new positions — confirm current `scalp_runtime` behavior if you change halts.
- **Order routing**: Kraken MM used `LiveOrderManager` + Kraken WS; Coinbase scalp uses `coinbase_order_manager` (and related paths). Fill routing uses `cl_ord_id` prefixes where applicable (`scalp_*`).

### Activation

```bash
pip install hexital   # incremental indicators library (one-time)
```

In `config.toml`, set `scalp.enabled = true`, then Dashboard → **RESTART PROCESS**.
The bot seeds indicators from 100 historical candles before placing any trades.

### Walk-forward optimization (WFO) and LOOKBACK progress

When `wfo_enabled` is true, the scalp runtime runs `ScalpWalkForwardOptimizer`: periodic rolling train/holdout grid search over stored bars, writing a **champion** parameter set for live trading.

**WFO vs param tuner:** WFO chooses **which strategy mode** (and coarse params) and writes **`data/scalp_champion.json`**. The **param tuner** refines tunables for the **active** mode. While the champion store has a row for a pair’s **exchange symbol**, the tuner does not switch mode away from WFO’s pick for that symbol. If there is **no** champion row for a pair’s symbol, the runtime picks a mode from the last **2 hours** of simulated trades ranked by **return %** until WFO saves a matching row.

**Champion file shape:** JSON is a **map of exchange symbol → champion object** (e.g. `SLP-20DEC30-CDE` → `{ mode, params, holdout_metrics, objective, ... }`). Older single-object champion files are still read; the next successful **`save_champion`** merges into the multi-symbol format. This avoids “last pair wins” overwrites when running several CDE products.

**Scoring:** `wfo_objective` in `[scalp]` selects the WFO metric (`sharpe`, `expectancy`, `expectancy_sqrt_n`, `sortino`, `calmar`, `profit_factor`, `total_pnl`). Holdout slices require **`wfo_min_trades`** trades per window (same threshold as training gates), not a single trade.

**Telemetry:** Session JSONL includes richer WFO rows (`holdout_metrics`, `objective`), **`champion_period_start` / `champion_period_end`** (with forward realized PnL since the period start), and **`strategy_mode` + `direction`** on **`position_closed`**. The dashboard snapshot exposes **`pair_symbols`**, **`champions`** (per symbol), and a backward-compatible **`champion`** summary.

**Strategy lookback (dashboard):** loads several days of bars for indicator warmup; snapshot lists `strategy_lookback_hours` as a label and includes **expectancy** and **return_pct** per mode. Ranking for `best_mode_from_lookback` (when used) prefers **expectancy** with a minimum trade count.

**Config (`[scalp]` in `config.toml`):**

| Key | Default | Role |
|-----|---------|------|
| `wfo_enabled` | `true` | Master switch for the WFO task and UI block |
| `wfo_interval_sec` | `1800` | Seconds between scheduled passes; loop sleeps first, then runs |
| `wfo_train_hours` | `6.0` | Rolling window training period (hours) |
| `wfo_holdout_hours` | `2.0` | Rolling window holdout/validation period (hours) |
| `wfo_step_hours` | `2.0` | Rolling window step size (hours) |
| `wfo_min_trades` | (see `config.toml` / `scalp_config.py`) | Minimum trades on **train and holdout** slices; higher = stricter, fewer spurious champions |
| `wfo_objective` | `expectancy_sqrt_n` | Scoring objective (actually used by WFO; see list above) |

Windows are **hours-based** — for 1-minute scalping, yesterday's data is already a different
regime. Default total data needed: 6 + 2 + 6 + 1 = 15 hours (~900 bars at 1m).

**REST backfill:** On startup, `ScalpRuntime` calls `bar_store.backfill_from_rest()` per pair, paginating backwards through Kraken's `/0/public/OHLC` endpoint (720 candles/request, ~1.1s rate limit). The full WFO window is filled in seconds -- no waiting for candles to trickle in. The first WFO pass can run immediately after boot.

**Data readiness:** The server computes `overall_progress_pct` as the **worst** pair's readiness: calendar span must cover train+holdout hours, and there must be at least **two** rolling windows. When a champion is already active, the LOOKBACK bar shows 100%.

**Dashboard (`frontend-new`):** A **LOOKBACK** strip below the header shows progress, short status text, and countdown to the next scheduled WFO pass when `scalp.wfo` is present in snapshots. Restart the backend process after enabling WFO so the snapshot includes the field.

### Regime: “WFO risk on” (April 2026)

When **volume** or **vol-scaled price movement** crosses configured thresholds, the runtime enters a **global** regime window (see `[scalp]` keys: `regime_volume_spike_mult`, `regime_price_move_atr_mult`, `risk_on_hold_sec`, `risk_on_wfo_interval_scale`, `risk_on_bootstrap_hours`, etc.). While active:

- **WFO** sleeps less often (dynamic interval via `ScalpWalkForwardOptimizer`).
- **No-champion bootstrap** can use a **shorter** lookback hours.
- **Nemesis** (bootstrap vs tuner) may use slightly relaxed dual-gate parameters.

**Closed bar:** `regime_risk_on_triggers()` runs on each confirmed candle close (`regime_risk.py`).

**Live (WS):** `regime_risk_on_triggers_live()` runs on **tick / intra-bar** updates so the same window can open **before** the interval closes — Coinbase **candles** channel + **ticker** (`coinbase_candle_feed.py`); Kraken **ohlc** updates (`candle_feed.py` `register_tick_callback`). Uses last closed indicators (ATR, volume MA) plus the **open** bar. Config: `regime_live_vol_enabled`, `regime_live_use_volume`, `regime_live_range_atr_mult`, `regime_live_velocity_window_sec`, `regime_live_velocity_min_bps`. This path adjusts **optimizer scheduling only**; it does not place entries.

The dashboard shows **`WFO RISK ON`** on the **Scalp** indicator strip (`ScalpPanel` / **INDICATORS** banner on `ScalpTab`). Snapshot fields: `scalp.regime_risk_on` (includes `live_enabled`), and per-pair `indicators.*.wfo_risk_on_label` / `wfo_risk_on_active`. Details: **`lessons.md` §31**.

### PnL Feedback Lab (historical hypothesis testing)

In-repo artifacts and scripts for structured **hypothesis → research → multi-window backtest** workflows (complements WFO; does not replace it):

| Path | Purpose |
|------|---------|
| `.optimization/pnl-feedback-lab/` | Markdown phases (`00_recon.md`, baselines, compare, `VERIFIED_*`), `runs/*.jsonl`, `research/<H-xxx>/`, `lenses/<H-xxx>/` |
| `.optimization/pnl-feedback-lab/scripts/run_multiwindow_lab.py` | Vector backtest sweep: **thirds** of each bar series × all five modes; add `--intervals 5,15,60` to include other Parquet intervals when present (discovery / structural hints) |

```bash
# From repo root — default: each pair's config interval only
python .optimization/pnl-feedback-lab/scripts/run_multiwindow_lab.py

# Optional multi-interval discovery (skips missing Parquet)
python .optimization/pnl-feedback-lab/scripts/run_multiwindow_lab.py --intervals 5,15,60
```

**Cursor skills (optional):** a **`pnl-feedback-lab`** skill describes a **Nemesis-style dual lens** (theory + tape) with **deep-research** for Lens B; a **`deep-research`** skill section links to this lab layout. Skills live in your **Cursor skills directory** (not committed here). See **`lessons.md` §32**.

## Related external systems (research)

Notes on how other products/repos compare to this codebase (trade-prevention UX, multi-agent orchestration patterns) and what is worth adopting vs avoiding:

- **[research/related-systems-notes.md](research/related-systems-notes.md)** — [Core Alpha Systems / Trade Engine](https://www.corealphasystems.com/) and [meta-metacognition](https://github.com/pazhenchira/meta-metacognition).

## Docker

```bash
docker compose up --build -d
```

Mounts `./data`, `./config.toml` (read-only), and `.env` for API keys.
Dashboard at `http://localhost:8080`.

### Stale bar files (INTX → CDE)

If you migrated from Coinbase **INTX** product IDs to **CDE**, old parquet files under `data/coinbase_bars/` may include `INTX` in the filename. They are unused once you only trade CDE symbols. Preview, then delete, e.g. on PowerShell:

```powershell
Get-ChildItem "data\coinbase_bars" -Filter "*INTX*"
# Remove-Item "data\coinbase_bars\*INTX*"
```
