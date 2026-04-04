# Lessons & Feature Reference

Compiled from build conversations between Mitch and collaborators.
Each section maps a design decision or friend's instruction to where it lives in code.

---

## Current Lessons (March-April 2026)

This section is the up-to-date operating guidance from live runs, overnight logs,
and post-mortem fixes. If this conflicts with older sections below, prefer this section.

### 1) Risk control must be portfolio-level, not pair-level

- Risk gates are evaluated from global state (`total_pnl`, `session_start_pnl`, `peak_pnl`).
- Halt action is global: when tripped, the engine cancels all enabled pairs and stops quoting.
- A one-shot halt path prevents repeated halt spam and repeated cancel attempts.

**Why:** Pair-level checks can miss total account risk and create inconsistent behavior.

### 2) Halt behavior needs an explicit top-of-tick guard

- `risk_halted` is checked at the top of the engine tick.
- Without this, the loop keeps running and repeatedly logs/cancels, which looked like a shutdown.
- `risk_halt_reason` is persisted in state and exposed to snapshots for dashboard visibility.

### 3) Drawdown thresholds must match capital reality

- `max_drawdown_pct = 10` was too sensitive for this account and realized fee drag.
- Raised to `20` to allow normal variance and avoid early stop-outs.

**Why:** Tight drawdown on a small absolute P&L base causes premature halts.

### 4) Drop structurally unprofitable pairs at current fee tier

- XRP/USDT repeatedly lost after fees at the observed tier.
- It was removed from `enabled_pairs`; active focus is USDG/USDT + TEL/USD.

**Why:** If natural spread + fill quality cannot beat fees, "more tuning" does not fix edge.

### 5) Momentum hold prevents buying back tops after sell bursts

- New logic: if a pair sees at least `momentum_hold_sells` sells within `momentum_hold_sec`,
  bot enters sell-only mode (suppresses buy placement).
- Normal two-sided quoting resumes after cooldown.

**Why:** TEL sold well during spikes, then re-bought too high and gave back edge.

### 6) Session logging is mandatory for diagnosis

- JSONL session logs should capture:
  - startup context,
  - fills,
  - learner actions,
  - pain-floor changes,
  - snapshots,
  - risk halt events,
  - momentum mode events.

**Why:** Most real issues were only obvious after overnight event reconstruction.

### 7) Smart cancel needs guardrails and retry-safe behavior

- Avoid blind cancel/replace every cycle.
- Keep near-fill protection, stale checks, drift checks, and cancel failure safety.
- Never drop a locally tracked order on cancel timeout/error unless exchange confirms status.

**Why:** Phantom orders and fill/accounting drift are high-severity live risks.

### 8) Book staleness checks are required before quoting

- Skip ticks on stale order book data.
- Do not place or reprice quotes using outdated mids/touch levels.

**Why:** WS hiccups can otherwise create stale-price orders and false risk events.

### 9) Learner + spread policy must balance fill acquisition vs protection

- Strict per-trade profitability floors can choke early fills.
- Portfolio-level risk caps plus adaptive spread behavior let the bot explore safely.
- Pain-floor and no-fill decay mechanisms reduce repeated unproductive quoting regimes.

### 10) Live startup/restart discipline matters

- Clean restart should:
  - reconnect WS,
  - reconcile open orders,
  - sync balances,
  - reseed cost basis from live mids,
  - place initial two-sided quotes on enabled pairs.
- Keep startup logs visible and verify first orders after every deploy.

### 11) Track only tradable/valid symbols in market subscriptions

- Unsupported-symbol warnings can appear from non-enabled configured pairs.
- This may not stop enabled trading, but it adds noise and hides real issues.

**Action:** Keep subscribed symbols aligned with exchange-supported list and active strategy.

### 12) Practical strategy focus for this account

- Core objective is spread capture, not directional swing behavior.
- Current practical focus:
  - trade pairs with fee/edge viability,
  - maintain inventory-aware quoting,
  - suppress bad re-entry after momentum exits,
  - enforce portfolio-level stops with clear halt reason.

### 13) Live no-fill diagnostic (validated runtime state)

Observed during active live session (`session_20260401_105104.jsonl` + runtime logs):

- Bot health was normal:
  - WS connections/auth succeeded,
  - engine running,
  - recurring order placement on enabled pairs,
  - stale-cancel and re-quote loop functioning,
  - no risk-halt trigger, no crash/traceback.
- Fills were still zero over extended runtime windows.

Interpretation:

- This pattern indicates **quote competitiveness** (or low touch-through frequency),
  not a dead engine.
- TEL often quoted wider than prevailing actionable market conditions.
- USDG remained active but did not get crossed within observed windows.

Operational guidance:

- If health is good but fills remain zero, tune for fill acquisition first:
  - tighten spread incrementally on affected pair(s),
  - optionally shorten stale window for faster repricing,
  - reassess after a fixed observation window.
- Keep risk controls portfolio-level while tuning; do not remove halt guards.
- Treat unsupported subscription warnings as cleanup tasks (noise), not immediate root cause.

### 14) Shared-asset inventory is currently double-counted across pairs (critical when enabling overlapping pairs)

- `InventoryManager.sync_from_kraken()` writes balances per pair from asset symbols.
- If two configured pairs share the same base or quote asset, each pair receives the full balance copy.
  - Example quote overlap: `USDG_USDT` and `XRP_USDT` both get full `USDT`.
  - Example base overlap: `XRP_USDT` and `XRP_USD` both get full `XXRP`.
- `can_buy()` and `can_sell()` then validate against per-pair balances, so aggregate buying/selling power can be overstated when overlapping pairs are enabled together.

**Why:** This can create portfolio over-allocation and unexpected rejects/live behavior when multiple pairs share inventory.

### 15) Book subscription scope is all configured pairs, not only enabled trading pairs

- `start_book_client()` subscribes using `config.symbols()` (every `[pairs.*]` block), not `pair_keys_for_trading()`.
- Engine/order placement still uses enabled pairs only.

**Why:** Unsupported or noisy symbols in config can still generate WS warnings and hide signal, even if they are not enabled for trading.

### 16) Runtime defaults have drifted from earlier lesson assumptions

Current code/config differs from earlier guidance text:

- `default_cycle_ms` is currently `3000` in active `config.toml` (not 500 baseline).
- `per_trade_profitability` is currently `false` in active `config.toml`.
- `PROFITABILITY_MARGIN_BPS` in code is `2` (`config.py`), while older notes refer to `4`.
- Cancel/stale guards now use `STALE_ORDER_SEC = 600` and `BOOK_STALE_SEC = 120.0`.

**Why:** Operators should trust current runtime constants over historical narrative when tuning live behavior.

### 17) Risk limit updates explicitly clear halt state from the dashboard path

- `ws_server.py` action `update_risk` sets new limits and then sets `state.risk_halted = False`.
- This allows resume after a risk stop without process restart.

**Why:** Useful for operations, but also means UI risk edits have immediate behavioral effect and should be treated as privileged actions.

### 18) Threat detector does not directly halt quoting in current engine

- `ThreatDetector` computes `threat_level`, imbalance, spread blowout, velocity, and realized volatility.
- `SpreadEngine` currently uses velocity/volatility widening, but does not branch on `threat_level == CRITICAL` to halt by itself.

**Why:** "Critical threat auto-halt" should not be assumed unless explicitly added in engine logic.

### 19) Fee-tier visibility is spot schedule-biased in UI

- Dashboard `fee_tier` summary is computed from `current_tier_info(vol_30d, "spot_crypto")`.
- Per-pair effective fee used by quoting is schedule-specific (`usdg`, `maker_rebate`, etc.).

**Why:** The global tier bar can mislead when active pairs are on non-spot schedules; per-pair fee is authoritative for decisions.

### 20) Implemented guard: shared-asset pairs now use portfolio-level availability checks

Code update applied:

- `InventoryManager.can_buy()` now checks quote availability at portfolio level for shared quote assets:
  - estimate wallet quote as `max(inventory_quote)` across pairs sharing that quote asset,
  - subtract open buy commitments (`qty * price * 1.05`) across all such pairs,
  - allow new buy only if remaining quote can fund it.
- `InventoryManager.can_sell()` now checks base availability at portfolio level for shared base assets:
  - estimate wallet base as `max(inventory_base)` across pairs sharing base asset,
  - subtract open sell commitments across all such pairs.

**Why:** Prevents over-ordering when multiple pairs reference the same wallet assets.

### 21) Implemented noise-control: order-book subscriptions now follow enabled trading pairs

Code update applied:

- `start_book_client()` now subscribes by `pair_keys_for_trading()` symbols (deduped), with fallback to all symbols only if no trading pairs are enabled.

**Why:** Reduces unsupported-symbol warning noise from disabled config pairs and keeps market data aligned with actively traded scope.

### 22) Implemented explicit risk resume semantics in dashboard risk flow

Code update applied:

- `update_risk` no longer auto-clears `risk_halted`.
- Resume now requires explicit `resume_risk_halt: true`.
- UI adds a dedicated **RESUME AFTER HALT** button which applies limits and clears halt intentionally.

**Why:** Prevents accidental restart of trading when operators only intended to edit limits.

### 23) Implemented active-pair fee-tier display alignment

Code update applied:

- Server now sends `fee_tier_by_pair` using each pair's `fee_schedule`.
- UI fee tier bar now prefers active pair tier info, with legacy fallback to spot global.

**Why:** Reduces operator confusion where global spot tier differed from the active pair's effective fee schedule.

### 24) Google Stitch integration should be export-ingest, not UI imitation

Validated workflow in this repo:

- Generate design externally in Google Stitch.
- Export ZIP/assets.
- Ingest assets into project via script (`tools/ingest-stitch.mjs`).
- Build/serve dashboard using real backend WebSocket contract.

**Why:** Keeps design source authentic to Stitch while preserving deterministic engineering control in-repo.

### 25) Safe migration path for frontend replacement

Operational migration pattern used:

- New workspace lives in `frontend-new/` (React + Vite) to avoid breaking legacy UI during development.
- Legacy frontend is replaced only at build-cutover time (`BUILD_TO_LEGACY=1`), with rollback copy kept as `frontend/index.legacy.html`.

**Why:** Enables iterative delivery and fast rollback without touching backend routes.

### 26) Stitch ingest requires deterministic file normalization

Implemented guardrails in ingest script:

- Normalize exported asset names (lowercase + hyphenated).
- Write mapping metadata to `frontend-new/src/stitch-manifest.json`.
- Keep token baseline in `frontend-new/src/styles/tokens.css`.

**Why:** Prevents drift across repeated Stitch exports and keeps frontend updates reproducible.

---

## 1. Dynamic Spread Widening (Volatility Guard)

**Instruction:** "dynamic spread that widens when prices move fast"

**Implementation:** `backend/server/spread_engine.py`, `_tick()` lines 247–249

When `realized_vol` exceeds `VOL_WIDEN_THRESHOLD` (0.0005), the engine adds
extra basis points to the effective half-spread proportional to volatility.

```
Constants:
  VOL_WIDEN_THRESHOLD = 0.0005
  VOL_WIDEN_SCALE     = 0.3
```

The widening is symmetric — both bid and ask move out equally.

---

## 2. Inventory Rebalancing (Skew Formula)

**Instruction:** "Inventory rebalancing formula — automatically skews bid/ask to clear excess inventory"

**Implementation:** `backend/server/spread_engine.py`, `_tick()` lines 265–271

Classic Avellaneda-Stoikov style. Computes a normalized inventory ratio
`q = inventory / max_inventory` clamped to [-1, 1], then shifts the
reservation price: `skew = -q * INVENTORY_SKEW_SCALE * half_spread`.

When long (excess base), the reservation drops — bids become less aggressive,
asks become more aggressive — encouraging sells to rebalance.

```
Constant:
  INVENTORY_SKEW_SCALE = 0.4
```

---

## 3. VPIN / Toxicity Detection (Price Velocity Proxy)

**Instruction:** "Basic toxicity detection using price velocity as a proxy for VPIN"
and "Make sure you include VPIN Toxicity Detection. It looks if anyone is hunting your liquidity"

**Implementation:**
- Velocity tracking: `backend/server/threat_detector.py`, `update()` lines 73–81
- Spread widening: `backend/server/spread_engine.py`, `_tick()` lines 252–257

Mid-price velocity is computed over a ~10-second rolling window in basis points.
When velocity exceeds `VELOCITY_WIDEN_FLOOR_BPS` (10 bps), the engine widens
the spread proportionally. Critical velocity (50+ bps) triggers CRITICAL threat
level which can halt quoting.

```
Constants:
  VELOCITY_WIDEN_FLOOR_BPS = 10.0
  VELOCITY_WIDEN_SCALE     = 0.5   (1 bps added per 2 bps velocity above floor)
```

Additional threat signals in `threat_detector.py`:
- **Book imbalance** (lines 53–59): top-5 bid/ask volume ratio
- **Spread blowout ratio** (lines 65–71): current spread vs rolling average

Note: This is price-velocity-based, not true volume-bucketed VPIN from
Easley/Lopez de Prado. It serves the same practical purpose — detecting
informed flow — using data available from the order book feed.

---

## 4. Faster Cycle Time (500ms)

**Instruction:** "Faster cycle time — 500ms instead of 3000ms"

**Implementation:** `config.toml` line 8: `default_cycle_ms = 500`

The engine's inner loop polls every 50ms (`spread_engine.py` line 136),
but each pair fires on its own interval via `pair_cycle_ms()`. The global
default in `config.py` is still 3000ms as a safe fallback, but `config.toml`
overrides it to 500ms, and all smart default presets also use 500ms.

TEL/USD is an exception at 6000ms (thin book, slower cycle needed).

---

## 5. Pair Focus: XRP/USDT

**Instruction:** "I will keep mine simple and focus on spread between USDT/USDC pair and XRP/USDT or XRP/USDC"

**Implementation:** `config.toml` line 9: `enabled_pairs = ["XRP_USDT"]`

XRP/USDT is the primary enabled pair. Additional pairs (XRP/USD, SOL/USD,
XBT/USDT, ETH/USDT, TEL/USD) are defined but disabled — they can be
toggled on from the dashboard when ready.

Defaults match the FAQ documentation:
- `spread_bps = 8` (desired edge; fee floor raises effective spread)
- `order_size = 30.0` XRP
- `max_inventory = 300.0` XRP

---

## 6. Emergency Liquidation (Sell Back to Quote)

**Instruction:** "When it says cancel all quotes does that also sell any remaining balance you have? For example mine works by buying usdc btc eth or xrp and selling into usdt..so i would need critical to sell those back into usdt."

**Implementation:** `backend/server/spread_engine.py`
- `_emergency_liquidate()` lines 517–555
- `kill()` lines 611–619

When triggered (depeg circuit breaker or manual kill), the bot does NOT just
cancel orders. It actively sells all base inventory at `best_bid`:
- Paper mode: instant simulated fill with P&L tracking
- Live mode: `place_aggressive_sell()` — a limit sell at best_bid WITHOUT
  post-only flag, so it crosses the spread and fills immediately

The `kill()` method iterates all pairs and liquidates any with inventory >= 0.001.

---

## 7. Strategy Learner (P&L-Based Spread Optimization)

**Instruction:** "Slowly nudge spread_bps toward the bucket that maximizes net P&L per cycle (not just win rate — a 90% win rate at 0.001 bps net is worse than 60% win rate at 5 bps net)."

**Implementation:** `backend/server/strategy_learner.py`, `_step()` lines 128–271

Hill-climbs on EMA-smoothed profit rate ($/min), not win rate. Each interval:
1. Measures P&L delta since last check
2. Converts to $/min rate, EMA-smooths it
3. If rate improved → keep direction (tighten or widen)
4. If rate degraded → reverse direction
5. Secondary: if recent sell average P&L is negative → force widen

The adjustment is applied via `engine.update_pair_config(pair_key, spread_bps=new_spread)`.

---

## 8. Hard Spread Bounds

**Instruction:** "Respect hard bounds: never go below fee_bps + 1, never go above ceiling."
and "Having a 100% dynamic bps means you have no safe guard. I would have a minimum."
and "My suggestion would be to set a minimum bps for the bot to work within"

**Implementation:**
- Floor: `spread_engine.py` lines 217–225, `strategy_learner.py` lines 94–114
- Ceiling: `spread_engine.py` line 260, `strategy_learner.py` lines 116–118

With `per_trade_profitability = true`, the half-spread floor is:
```
floor = max(fee_bps + PROFITABILITY_MARGIN_BPS, pair_spread_floor_bps)
```

Where `PROFITABILITY_MARGIN_BPS = 4` (in `config.py`). At the lowest volume
tier (25 bps maker fee), this means a minimum 29 bps half-spread = 58 bps
total width. The 8 bps config effectively represents the target edge above fees.

Ceiling: `adaptive_spread_ceiling_bps = 120` (config.toml). The engine clamps
the effective spread to never exceed this, even under volatility/velocity widening.

---

## 9. Daily Adjustment Cap

**Instruction:** "Cap adjustments per day to prevent oscillation."

**Implementation:** `backend/server/strategy_learner.py` lines 131–139, 248

`learner_max_daily_adjustments` (default 50 in config.toml) sets the cap.
A day counter resets at UTC midnight. If the count is reached, the learner
skips all further adjustments until the next day.

Additional dampening: a one-interval cooldown after loss-triggered widens.

---

## 10. Persist Learner State Across Restarts

**Instruction:** "Persist across restarts via data/learner_state.json."

**Implementation:** `backend/server/strategy_learner.py`
- Save: `_save_state()` lines 273–289
- Load: `_load_state()` lines 291–319

File: `data/learner_state_{mode}.json` (separate files for paper and live).
Stores per-pair: `spread_bps`, `rate_per_min`, `direction`, `updated` timestamp.
On load, saved spreads are clamped to current `[floor, ceiling]` bounds.

---

## 11. Minimum Spread Profitability

**Instruction:** "I found below 50 is not profitable on any trades where the exchange takes 16 both ways"
and "So 80 is the sweet spot right now for me"

**Implementation:** `backend/server/spread_engine.py` lines 216–244

The `per_trade_profitability` system guarantees every quote covers fees:
- At 25 bps maker fee (lowest tier): floor = 29 bps half = 58 bps total
- At 20 bps (next tier, $10K+ volume): floor = 24 bps half = 48 bps total
- At 16 bps ($50K+ volume): floor = 20 bps half = 40 bps total

The friend's observation that "below 50 is not profitable at 16 bps each way"
aligns: 16 bps × 2 sides = 32 bps cost, so total spread must exceed 32 bps.
The engine enforces `fee + 4 bps margin` per side = 40 bps total at that tier.

The "80 bps sweet spot" corresponds to 40 bps half-spread — which the
learner can discover and lock onto as the profit-maximizing bucket.

---

## 12. Smart Cancel Logic (No Blind Cancel-and-Replace)

**Instruction:** "The bot is placing buy/sell pairs at fixed prices every 3000ms cycle, then cancelling and replacing them each cycle. Write some logic to avoid it cancelling them every cycle."
and "Turn off cancel and replace functionality — add a balance checked before cancel command"

**Implementation:** `backend/server/spread_engine.py`, `_should_cancel_order()` lines 423–457

Orders are only cancelled when one of three conditions is met:
1. **Price drift > 1.5× half-spread** (`DRIFT_CANCEL_MULT = 1.5`) — price moved significantly
2. **Stale** — order has been open > 90 seconds (`STALE_ORDER_SEC = 90`)
3. Neither condition met → **order stays alive** (no cancel)

Before placing new orders, the engine checks `can_buy()` / `can_sell()` which
verifies quote balance covers the order cost (balance-checked before placing).

---

## 13. Near-Fill Protection

**Instruction:** "Orders close to filling get more time"
and "I added logic into mine too that stops those trades getting cancelled and actually getting filled"

**Implementation:** `backend/server/spread_engine.py`, `_should_cancel_order()` lines 438–449

```
Constant:
  NEAR_FILL_BPS = 3
```

If a buy order is within 3 bps of `best_ask`, or a sell is within 3 bps of
`best_bid`, the method returns `None` immediately — skipping ALL cancel checks
(both stale and drift). The order gets unlimited time to fill.

This prevents the bot from pulling orders that are about to execute.

---

## 14. Cancel Reason on Dashboard

**Instruction:** "Cancel reason shown live in the status bar"

**Implementation:**
- State: `backend/server/state.py` line 125 — `last_cancel_reason: dict[str, str]`
- Snapshot: included in `BotState.snapshot()` line 207
- Frontend: `frontend/index.html` lines 731–734 — reads `state.last_cancel_reason`

Every cancel path sets a reason string (from `CancelReason` enum or descriptive
text for risk stops). The dashboard WebSocket pushes snapshots every 0.5s,
so the cancel reason appears live in the UI.

---

## 15. Smart Default Configuration

**Instruction:** "Add smart default configuration to your buttons so that when you select a trading pair it automatically detects the best config to run"

**Implementation:** `backend/server/spread_engine.py`
- `smart_defaults()` lines 633–682
- `_PAIR_SMART_DEFAULTS` dict lines 685–762

Two-tier system:
1. **Hardcoded presets** for known pairs (XRP, TEL, USDC, USDE, BTC, ETH, SOL)
   with tuned spread/size/inventory/cycle values
2. **Auto-detection fallback** based on mid-price ranges — picks appropriate
   order size and max inventory for the asset's price level

Fee-aware: when `per_trade_profitability` is on, the preset spread is floored
at `fee + PROFITABILITY_MARGIN_BPS`.

Triggered from the dashboard via the `smart_defaults` WebSocket action.

---

## 16. Risk Management Stops

**Implementation:** `backend/server/spread_engine.py`, `_tick()` lines 164–214

Four independent stops, each cancels all orders and sets `risk_halted = True`:

| Stop | Config Key | Default | Description |
|------|-----------|---------|-------------|
| P&L floor | `min_total_pnl_usd` | -8.0 | Halt if cumulative P&L drops below this |
| Daily profit | `daily_profit_target_usd` | disabled | Halt after reaching daily target |
| Daily loss | `daily_loss_limit_usd` | disabled | Halt if daily loss exceeds limit |
| Max drawdown | `max_drawdown_pct` | disabled | Halt on % drawdown from peak |

---

## 17. Depeg Circuit Breaker

**Implementation:** `backend/server/spread_engine.py`, `_tick()` lines 149–162

For pairs with a `peg_price` (stablecoins), if the midprice deviates by more
than `depeg_threshold_bps` (default 50 bps = 0.5%), the bot:
1. Cancels all orders for that pair
2. Triggers `_emergency_liquidate()` — sells all inventory at best_bid
3. Returns immediately (no further quoting)

---

## Architecture Summary

```
config.toml + .env
       │
       ▼
   load_config() ──► AppConfig (server, bot, pairs, API keys)
       │
       ▼
   main.run()
       ├─► BotState + PairState (in-memory state)
       ├─► PnLTracker (JSONL persistence, 30d volume)
       ├─► InventoryManager (balances, can_buy/can_sell)
       ├─► LiveOrderManager (Kraken WS auth, executions channel)
       ├─► BookClient (public order book WS → microprice)
       ├─► ThreatDetector (velocity, imbalance, spread blowout)
       ├─► SpreadEngine._tick() loop every 50ms, per-pair cycle
       │     ├─ risk gates → fee floor → base spread → vol/velocity widen
       │     ├─ inventory skew → reservation price → buy/sell prices
       │     └─ smart cancel → place if missing + can_buy/can_sell
       ├─► StrategyLearner (hill-climb $/min, adjusts spread_bps)
       ├─► AdaptiveSpreadTuner (win-rate nudge, optional)
       └─► DashboardServer (HTTP + WS push every 0.5s)
```

---

## Deep Audit: Findings and Fixes (April 2026)

Full code audit of Proper Fill Detection, Rebate Pairs, Dynamic Spread,
and Smart Cancel logic. Findings labeled A1-A6, B1-B4, C1-C2.

### A. Smart Cancel Logic

**Location:** `spread_engine.py` `_should_cancel_order()` lines 434-470

Decision tree:
1. Is order "near fill" (within 3 bps of touch) AND age <= 300s? -> KEEP
2. Is age > 90 seconds? -> CANCEL (STALE)
3. Is drift > 1.5x half_spread? -> CANCEL (PRICE_DRIFT)
4. Otherwise -> KEEP

**BUG FIXED (A3): Near-fill orders could get stuck forever.**
The near-fill check previously returned None unconditionally, bypassing
the stale check entirely. An order at 2.9 bps from the touch would never
be cancelled. Added `NEAR_FILL_MAX_AGE_SEC = 300` — after 5 minutes,
near-fill protection expires and normal stale/drift checks apply.

**BUG FIXED (A5): Cancel failure caused phantom orders (HIGH).**
In `live_order_manager.py`, on cancel timeout/error the order was removed
from `active_orders` even though it might still be live on Kraken. This
caused: duplicate orders placed next tick, orphaned fills creating untracked
inventory drift. Fix: on cancel failure, keep order in `active_orders` with
`cancel_retry = True`. The WS executions channel's `canceled`/`filled`/
`expired` events are the authoritative cleanup path.

**Issue noted (A4): Aggressive cancellation on tight spreads.**
For pairs with 3 bps half-spread (e.g., USDG), drift threshold is only
$0.00015 — normal microprice jitter triggers PRICE_DRIFT on every tick.
Not critical for XRP-only deployment (29+ bps effective half-spread).

### B. Fill Detection

**What works:**
- Kraken WS v2 `executions` channel for live fills
- Partial fills handled correctly (`last_qty` / `last_price` per event)
- Buy vs sell routing correct
- Cancel-vs-fill race is safe (`_process_fill` is stateless)

**FIXED: Added `filled_qty` to `ActiveOrder`.**
New field tracks cumulative filled quantity per order, updated on each
`trade` execution event. Dashboard now shows partial fill progress.

**FIXED: Added order reconciliation on WS connect.**
`_reconcile_open_orders()` queries Kraken REST for open orders and prunes
any local `active_orders` entries that Kraken no longer knows about. Runs
on every `initialize()` call. Prevents ghost orders from accumulating
after WS reconnects.

### C. Rebate Pairs

**Status: Fully correct. No changes needed.**

`MAKER_REBATE_FEE_TIERS` correctly models negative maker fees (-2 bps at
$10M+ volume). The spread floor formula `fee_bps + PROFITABILITY_MARGIN_BPS`
handles negatives: at -2 bps, floor = 2 bps half-spread = 4 bps total.
Revenue = 4 bps spread + 4 bps rebate = 8 bps net per round-trip.

P&L tracking correctly handles negative fees in `record_buy` (reduces
cost basis) and `record_sell` (increases revenue).

### D. Dynamic Spread

**The chain works end-to-end:**
```
book_client.on_book_update() -> threat_detector.update()
  -> computes realized_vol, mid_velocity_bps -> stores on PairState
spread_engine._tick() reads both -> widens if thresholds exceeded
```

**FIXED: VOL_WIDEN_THRESHOLD lowered from 0.0005 to 0.0003.**
At 0.0005, only triggered during sharp moves (5+ bps std dev of returns).
XRP in calm markets has ~1-2 bps std dev — never triggered. At 0.0003,
moderate volatility (3+ bps) activates widening.

**FIXED: Dynamic spread widen log promoted from DEBUG to INFO.**
Previously invisible at default log level. Now logs at INFO with both
vol and velocity components: `Dynamic widen XRP_USDT: +5bps (vol=0.00042 vel=18.3bps)`.

**FIXED: `dynamic_widen_bps` tracked as a combined metric.**
Both vol guard and velocity guard contributions are summed into a single
`dynamic_widen_bps` value for clearer logging and future dashboard exposure.

### E. Book Staleness Guard

**NEW: Added `last_book_update_ts` to PairState.**
Set by `book_client.on_book_update()` on every valid book message.
Checked in `spread_engine._tick()` — if book data is older than
`BOOK_STALE_SEC` (5 seconds), the tick is skipped with a warning log.
Prevents placing orders at stale prices during WS disconnects.

### F. Cycle Time Default

**FIXED: `default_cycle_ms` changed from 3000 to 500 in config.py.**
The code-level default now matches `config.toml` and the friend's design.
If config.toml fails to load this field, the bot won't silently fall
back to 3-second cycles.

### Severity Summary

| Finding | Severity | Status |
|---------|----------|--------|
| A5: Cancel-failure phantom orders | HIGH | Fixed |
| E: Book staleness (trading on stale data) | HIGH | Fixed |
| B: Fill reconciliation on reconnect | MEDIUM | Fixed |
| B: Partial fill tracking (filled_qty) | MEDIUM | Fixed |
| A3: Near-fill stuck forever | MEDIUM | Fixed |
| D: Dynamic spread rarely triggers | LOW | Fixed |
| F: Cycle time code default | LOW | Fixed |
| C: Rebate pairs | None | Correct |


### 27) Rate limiting is now explicit in live order flow

Added a token-bucket limiter (`backend/server/rate_limiter.py`) and wired it into live add/cancel message sends.
On Kraken rate-limit style errors, the limiter applies a temporary penalty window to reduce message pressure.

### 28) Threat levels now directly influence quoting

`spread_engine.py` now consumes `PairState.threat_level`:
- `HIGH`: multiplies spread by `threat_spread_multiplier`
- `CRITICAL` + `threat_quoting_pause=true`: skips quoting for that tick

This closes the gap where threat level was computed but not used.

### 29) Startup security check warns on potentially over-privileged API keys

`main.py` now performs a withdrawal-permission probe in live mode.
If key scope appears to include withdrawal capability, the bot logs a security warning and can abort startup when `abort_on_withdraw_permission=true`.

### 30) Added trailing-stop / take-profit controls on per-pair realized PnL

`spread_engine.py` now tracks per-pair realized PnL and supports:
- trailing stop trigger (`trailing_stop_enabled`, `trailing_stop_pct`)
- take-profit trigger (`take_profit_usd`)

Triggered exits cancel pair orders and emergency-liquidate the pair inventory.

### 31) Added OCO, TWAP, and BTD support scaffolding

New modules:
- `backend/server/oco_manager.py`
- `backend/server/twap.py`
- `backend/server/btd.py`

Live fill handling can register and reconcile OCO siblings, TWAP can split buy placement into timed slices, and BTD uses SMA downtrend detection to bias buys lower with optional size multiplier.

### 32) Added session-replay backtesting CLI and container deployment files

- `python -m backend.server.backtest --session <file> --spread-bps <n>`
- Optional `--compare` for side-by-side spread assumptions

Deployment artifacts added:
- `Dockerfile`
- `docker-compose.yml`

### 33) Dashboard/backend config contract expanded for advanced controls

`ws_server.py` now exposes advanced config fields and supports `update_trailing` actions.
Frontend risk panel includes trailing stop visibility and toggle action, and static asset routing now explicitly serves `/assets`.

### 34) Walk-forward optimizer added for multi-parameter backtest feedback

Implemented a new `WalkForwardOptimizer` loop in `backend/server/optimizer.py` with the flow:
`load recent fills -> split train/holdout -> grid search -> holdout validation -> safety gate -> apply`.

What is now in place:

- New bot config fields for optimizer controls in `config.toml` / `backend/server/config.py`:
  `optimizer_enabled`, `optimizer_interval_sec`, `optimizer_train_hours`,
  `optimizer_holdout_pct`, `optimizer_max_delta_spread_bps`,
  `optimizer_max_delta_size_pct`, `optimizer_min_fills`, `optimizer_objective`.
- Backtest metrics now include `total_win_dollars`, and `backtest.py` exposes `score_config()`.
- Inventory skew is now pair-configurable (`PairConfig.inventory_skew_scale`) and can be updated live via `update_pair_config()`.
- State and telemetry now include optimizer output:
  - `BotState.optimizer_info` in dashboard snapshots
  - `SessionLogger.log_optimizer()` with `optimizer_run`, `optimizer_apply`, `optimizer_reject`.
- Runtime wiring:
  - Optimizer starts/stops in `main.py`
  - mode switch propagation through `runtime.py` + `ws_server.py`
  - mutual exclusion added so learner/adaptive do not run when optimizer is enabled.

Safety constraints implemented:

- Skip optimization if training data is too thin (`optimizer_min_fills`).
- Reject config changes during `risk_halted` state.
- Reject config changes when pair threat level is `high` or `critical`.
- Clamp spread/size jump size per run with max-delta guards.
- Stability guard: reject broad multi-axis changes on already high win-rate regimes.

### 35) Research: "From Trading Bot to Trading Agent" (Liu, Medium, Nov 2025) — Future Ideas

Source: https://medium.com/@gwrx2005/from-trading-bot-to-trading-agent-how-to-build-an-ai-based-investment-system-313d4c370c60

**Context**: Article covers LLM-powered directional trading agents. Our bot is a spread-capture/market-making
system, so most concepts don't apply directly. Four ideas are worth revisiting once the baseline is profitable:

1. **Reflective learning loop (CryptoTrade model, EMNLP 2024)**
   After each session, an LLM reviews fill logs and generates natural-language postmortems.
   Example output: "TEL_USD spread was below fee breakeven for 3 hours; widen floor."
   We already have `strategy_learner` and `adaptive_spread` doing numeric versions.
   An LLM pass could catch subtler patterns (e.g., time-of-day effects, correlated pair behavior).
   Prerequisite: stable profitability so the LLM has meaningful wins/losses to analyze.
   Reference project: https://github.com/Xtra-Computing/CryptoTrade

2. **Sentiment-gated quoting (FinBERT / news feed integration)**
   Score crypto news sentiment in real-time. When strongly negative for an asset,
   widen spread or pause quoting *before* price velocity triggers `threat_detector`.
   This would be a proactive complement to our reactive threat system.
   Concern: latency of LLM inference vs. speed of crypto price moves; may arrive too late
   for HFT-style events but useful for slow-developing regime shifts (regulatory news, hacks).
   Reference: ProsusAI/finbert, FinGPT (https://github.com/AI4Finance-Foundation/FinGPT)

3. **Dedicated risk-reviewer agent (multi-agent pattern)**
   A separate async loop that periodically audits portfolio state with an LLM:
   "Is the current exposure reasonable given market conditions?"
   Could catch risks our numeric limits miss (e.g., "both pairs are in the same asset class
   and correlated — effective exposure is 2x what it looks like").
   Heavy to implement; defer until portfolio grows beyond 2-3 pairs.

4. **FinGPT fine-tuning on our own fill data**
   Fine-tune a small open-source LLM (e.g., Llama via LoRA) on our JSONL session logs
   to learn pair-specific microstructure quirks. Could eventually replace or augment
   the numeric `strategy_learner` hill-climber.
   Requires: significant fill history (months), GPU compute, careful evaluation.

**Not applicable to our architecture (directional trading concepts)**:
- LLM as directional strategy generator/selector (we are always market-making)
- RL for portfolio allocation across assets (we provide liquidity, not direction)
- DeFi/Web3 execution (we are on Kraken CEX)
- Multi-agent "debate" for long/short calls (not relevant to spread capture)
- Alpha Arena competition insights (directional crypto bets, not market-making)

### 36) Paper Simulation Harness, Quality Scoring, and Preset Profiles

Implemented a 4-phase simulation pipeline and pair archetype presets.

**New files:**
- `backend/server/sim_runner.py` — CLI spread_bps sweep harness
- `backend/server/presets.py` — 3 archetype presets with `detect_archetype()` and `apply_preset()`

**sim_runner.py pipeline (per pair):**
1. Grid sweep: test spread_bps from 1-200 in 1-bps steps via `run_backtest(mode="simulated")`
2. Fee stress: rerun top-5 spreads at 1.5x fees to test robustness
3. Walk-forward: 3-fold temporal train/holdout to detect overfitting
4. Quality scoring: 5-dimension composite (sample size, expectancy, risk mgmt, robustness, execution)

**Critical limitation acknowledged:** `run_backtest(mode="simulated")` can only vary `spread_bps`
by re-pricing existing fills. It cannot simulate which fills would occur under different `order_size`,
`cycle_ms`, or `momentum_hold_sells`. Only `spread_bps` is swept; other preset params are heuristic.

**Simulation results (data/trades_live.jsonl, 142 records):**

TEL_USD (130 events, 63 sells, fee_schedule=maker_rebate, 23 bps maker fee):
- Structurally unprofitable below 160 bps spread (fee eats all margin)
- Best result at 200 bps: PnL $7.88, 25.4% win rate, Sharpe 2.323
- Fee stress at 1.5x: retains 43% of PnL (acceptable)
- Walk-forward: all 3 folds agree on 200 bps optimal
- Plateau: 192-200 bps (narrow, at ceiling)
- Quality score: 0.886 composite
- Key lesson: TEL_USD **must** have spread_floor >= 50 bps minimum, ideally >= 160 bps
  to break even. Previous config of spread_bps=30 was structurally losing money.

USDG_USDT (4 events total, 4 sells): SKIPPED — insufficient data for statistical analysis.
Heuristic preset only: 0% maker fee means any positive spread is profitable.

XRP_USDT (5 events, 0 sells): SKIPPED — no sell data.

**Preset archetypes (backend/server/presets.py):**

1. `stablecoin_zero_fee` — For USDG, USDe pairs (0% maker fee):
   spread_bps=3, floor=1, order=10, max_inv=50, cycle=2000ms, momentum_hold=3/30s.
   Rationale: heuristic (zero fees = any positive spread profits; maximize fill rate).

2. `altcoin_high_fee` — For TEL, XRP, etc (>=20 bps maker fee):
   spread_bps=60, floor=50, order=1000, max_inv=50000, cycle=3000ms, momentum_hold=2/90s.
   Rationale: sim data shows TEL_USD needs ~160 bps to break even; floor at 50 bps
   ensures per_trade_profitability mode can't go below structural minimum.

3. `conservative_test` — Overlay for safe testing:
   order=5, max_inv=40, pnl_floor=-3, daily_loss=3.
   Rationale: minimizes real-money exposure during strategy honing.

**Dashboard integration:**
- `ws_server.py` — new `apply_preset` action, `pair_archetypes` + `presets` in config snapshot
- `SystemsPanel.tsx` — PRESETS section (Section 00) with per-pair recommended badge
- Preset buttons show "REC" badge when the preset matches the selected pair's archetype

### 37) Orphan orders on Kraken lock funds silently — cancel ALL at startup

**Problem:** Previous reconciliation used `cl_ord_id` (e.g., `mitch-7fdbdb021521`) as the `txid` param
in Kraken's REST `cancel_order`. Kraken expects the native order ID (`OXXXXX-XXXXX-XXXXXX`), not the
client order ID. Every cancel attempt returned "Invalid order" and was silently logged as a warning.
The orphan orders stayed open, locking USDT balance. The bot's `can_buy()` passed (it checked local
inventory, not Kraken-held margins), but Kraken rejected every new order with "Insufficient funds."

**Root cause:** 5 orphan orders from a prior session were holding margin on Kraken's side.
`get_account_balance()` returns total balance including locked amounts — so local inventory showed
$441 USDT available when the real free balance was much less.

**Fix applied:**
- `_reconcile_open_orders()` now calls `cancel_all_orders()` at startup instead of individual orphan cancels.
  The engine re-places its own orders from scratch, so there's no reason to preserve prior orders.
- Added 2-second delay between order cancellation and balance sync to let Kraken release locked funds.
- Added diagnostic logging to `can_buy()` showing wallet balance, committed amount, and required amount
  so future inventory-vs-exchange mismatches are immediately visible.

**Files:** `live_order_manager.py`, `inventory.py`, `main.py`

### 38) TEL/USD minimum order size is 2300 TEL, not 2000

**Problem:** After fixing the insufficient funds issue, a new error appeared:
`EGeneral:Invalid arguments:volume minimum not met` on every TEL/USD order.

**Root cause:** Kraken's minimum order for TEL/USD is 2300 TEL (verified via public AssetPairs API).
Config had `order_size = 2000.0`.

**Fix:** Updated `config.toml` to `order_size = 2500.0` for TEL_USD.

**Lesson:** Always verify order minimums against the exchange API. Kraken can change these without notice.
Use `https://api.kraken.com/0/public/AssetPairs?pair=TELUSD` to check `ordermin` field.

### 39) Real-time alert/toast system — nothing should fail silently

**Problem:** Multiple error paths only logged to the server console or browser `console.error()`.
The operator had no way to see failures without watching terminal output. Specific silent failures:
- Order rejections (insufficient funds, volume minimum)
- Rate limit penalties from Kraken
- Orphan order reconciliation issues
- Balance sync failures
- Risk halt triggers
- Engine consecutive errors / circuit breaker
- Stale order book data
- WS server dispatch errors

**Fix applied:**

Backend:
- Added `BotState.push_alert(level, title, detail, source)` — callable from any component, no circular imports.
  Logs the alert at appropriate severity and pushes `{type: "alert"}` to all dashboard WS clients.
- `DashboardServer.broadcast_alert()` sends structured alert payloads.
- Wired alerts into: `live_order_manager.py` (order rejects, rate limits, reconciliation),
  `spread_engine.py` (risk halts, engine errors, stale books), `inventory.py` (balance sync),
  `ws_server.py` (dispatch errors).

Frontend:
- Added `Alert` type and `onAlert` handler to WS client.
- Replaced intrusive `alert()` popup with toast notification system.
- Toasts stack in top-right, color-coded by severity (error=red, warning=amber, info=blue, success=teal).
- Auto-dismiss: errors 15s, warnings 10s, info 6s, success 5s. Manual dismiss via close button.
- Duplicate suppression: same title within 5s is ignored.
- Max 8 toasts visible at once.

**Files:** `state.py`, `ws_server.py`, `live_order_manager.py`, `spread_engine.py`, `inventory.py`,
`types.ts`, `wsClient.ts`, `App.tsx`, `app.css`

### 40) Spreads must be competitive with market spread — not just above fees

**Problem:** The bot ran for an entire session with zero fills. Startup was healthy, orders placed,
no errors — but the quotes were so wide nobody crossed them.

**Root cause:** USDG/USDT had a 1 bps natural market spread with massive resting liquidity (78K+ at
top of book), but the bot was quoting at 20 bps half-spread (40 bps total, 40x wider than market).
TEL/USD had a ~53 bps market spread but the bot was quoting at 58 bps half-spread (116 bps total).

**Fix applied:**
- USDG/USDT: spread_bps reduced from 20 to 1. At 0% maker fee, any positive spread is profit.
  Floor lowered to 1 bps. Orders now sit at or near top-of-book.
- TEL/USD: spread_bps reduced from 58 to 30. Floor lowered to 25 bps.
  With inventory skew, sells price tighter than the market ask — best offer in the book.
- Global floors lowered: `adaptive_spread_floor_bps` 2→1, `min_quote_half_spread_bps` 2→1.
- Removed hard-coded `max(4, ...)` floor in `strategy_learner._pair_floor()` that prevented
  spreads below 4 bps regardless of config.
- `BOOK_STALE_SEC` raised from 30s to 600s — stablecoin pairs routinely go minutes without
  book updates; 30s caused constant false-stale alerts and quoting pauses.
- Stale-book log spam fixed: warning now fires once per stale event instead of every tick.

**Lesson:** Always compare your configured spread to the actual market spread. If the natural spread
is 1 bps and you're quoting at 20 bps, you will never fill. Check with:
`https://api.kraken.com/0/public/Ticker?pair=USDGUSDT` — compare `a[0]` and `b[0]`.

**Files:** `config.toml`, `spread_engine.py`, `strategy_learner.py`

### 41) Learner must reset to config spread each session — not persist stale state

**Problem:** The learner saved `spread_bps` to `learner_state_live.json` and `_load_state()` would
overwrite `pc.spread_bps` with the saved value on startup. If the config changed between sessions
(e.g., USDG lowered from 20 to 1 bps), the saved value (4 bps) silently overrode the config.
Config changes were ignored until the learner file was manually edited.

**Root cause:** `_load_state()` treated saved `spread_bps` as authoritative and overwrote
`PairConfig.spread_bps`. Combined with a hard-coded `max(4, ...)` floor, this meant the bot
could never start below 4 bps regardless of config.

**Fix applied:**
- `_load_state()` now only restores `pain_floor` (memory of losing spreads) from disk.
  `spread_bps`, `direction`, and `ema_rate` start fresh from `config.toml` each session.
- `_save_state()` still writes a full snapshot for diagnostics, but transient fields are
  ignored on load.
- Startup log now says `starting at config spread_bps=X, pain_floor=Y` instead of
  `Learner loaded: spread_bps=X (clamped from Y)`.

**Lesson:** The learner explores within a session. Config.toml is the authoritative starting point.
The only cross-session persistence that makes sense is the pain floor — remembering which spreads
caused losses so we don't blindly re-explore them.

**Files:** `strategy_learner.py`

### 42) Microprice self-pollution — own orders on thin books poison the reference price

**Problem:** On TEL/USD the sell order was placed below the market ask (0.002245 vs market 0.002248).
On subsequent ticks it got even worse — sells drifted toward the bid.

**Root cause:** The microprice formula `(bid * ask_vol + ask * bid_vol) / (bid_vol + ask_vol)`
uses top-of-book volumes. Kraken's order book includes our own orders. On TEL/USD our 2500-lot
sell was the largest order at the best ask. This dragged the microprice down to near the bid price.
Then: sell_price = microprice + half_spread → still below the real market ask.
Next tick, our sell IS the best ask, so the microprice drops even further. Feedback loop.

**Fix applied:**
- Added `_clean_microprice()` in `spread_engine.py` that strips our own active order quantities
  from the top-of-book levels before computing the volume-weighted mid.
- If our order is the entire volume at a level, the method falls back to the next book level.
- This prevents self-referential pricing on thin pairs where our order dominates.

**Lesson:** On thin-book pairs, your own order can become the dominant level. Always exclude
own orders from reference price calculations. This is a well-known market microstructure issue
called "self-crossing" or "toxicity from own flow."

**Files:** `spread_engine.py`

### 43) Engine watchdog detects frozen loops — the silent killer for market makers

**Problem:** The circuit breaker (50 consecutive exceptions = halt) only catches *thrown* errors.
If the engine loop freezes — blocked await, deadlock, CPU spin without yielding — the try/except
never fires. The bot sits with `running=True`, holding inventory with no active quotes. A dead bot
with open positions is worse than no bot (HFT Systems + Inside the Black Box).

**Fix applied:**
- `BotState.last_engine_heartbeat_ts` — set at the top of every `_run_loop` iteration.
- Async watchdog task in `main.py` polls every 5s. If heartbeat is stale >30s:
  1. Push CRITICAL alert + attempt `soft_restart()`.
  2. If still frozen 30s after restart: risk halt, cancel all orders, set `running=False`.
- Watchdog resets when engine resumes normal heartbeat.

**Files:** `state.py`, `spread_engine.py`, `main.py`

### 45) Ping-pong must cancel existing opposite-side orders, not just suppress new placements

**Problem:** After a sell fill, `last_fill_side="sell"` correctly suppressed *new* sell placements.
But 2 sell orders placed *before* the fill were still resting on Kraken. Nothing cancelled them.
If either filled, it would be another loss from pre-existing inventory at the blended cost basis.

**Root cause:** Ping-pong only gated new `place_order()` calls. The cancel loop checked stale/drift
but had no concept of "this side is suppressed — pull it." Orders placed before the fill lived on.

**Fix:** Added a ping-pong enforcement block in `_tick()`. Before dispatching to `_smart_live_tick()`,
it iterates `active_orders` and cancels any resting orders whose side is suppressed. This ensures
the book state matches the ping-pong intent within one tick of a fill.

**Files:** `spread_engine.py`

### 46) Startup with pre-existing inventory must force buy-first posture

**Problem:** Every restart, the bot placed sell orders immediately from pre-existing wallet inventory.
`last_fill_side=""` meant ping-pong allowed both sides. The sell always appeared as a loss because
the blended cost basis ($371/$8274 = $0.0449 avg) was above market (~$0.039).

**Fix:** On engine START, for any trading pair with pre-existing `inventory_base > 0` and empty
`last_fill_side`, initialize `last_fill_side = "sell"`. This tells ping-pong "last action was sell,
so only allow buys." The bot won't sell pre-existing inventory until it first accumulates new
inventory at known cost through a buy fill.

**Files:** `ws_server.py`

### 47) Dynamic widen log spam hides real events on volatile pairs

**Problem:** DRIFT/USD has baseline realized vol ~0.0003-0.0005, slightly above `VOL_WIDEN_THRESHOLD`
(0.0003). This triggered +1bps dynamic widen logs every 3-second tick — 20 lines per minute of noise
that drowned out real fill/cancel/order events.

**Fix:** Raised the log threshold from `> 0` to `>= 3` bps. Only meaningful widenings (3+ bps)
are logged at INFO. Trivial +1bps additions still apply to the spread math but don't pollute logs.

**Files:** `spread_engine.py`

### 49) Never place orders on startup without observing the market first

**Problem:** The bot placed buy orders the instant the engine started, at whatever the current
microprice happened to be. No consideration of whether the price was at a local high, trending down,
or in the middle of a volatile spike. This led to consistently bad entries — buying at the top of
a short-term range, then watching the price drop.

**Fix applied:**
- Added `warmup_sec` (default 30s) config. For the first N seconds after engine start, the bot
  collects microprice samples but places zero orders.
- After warmup, it computes the observed price range (high/low) and targets buy placement near
  the `warmup_buy_percentile` (default 25th percentile — near the bottom of the range).
- The warmup window is rolling — prices keep updating after warmup, so the "recent range" stays
  fresh rather than being a stale startup snapshot.
- On full P&L reset, warmup state is cleared so the bot re-observes.

**How it works:**
1. Engine starts → "WARMUP DRIFT_USD: observing market for 30s"
2. For 30 seconds: collects prices every tick, no orders placed
3. After warmup: "range=0.0370–0.0395, target entry (p25)=0.0376"
4. Buy orders biased toward 0.0376 instead of wherever mid happens to be
5. Rolling window keeps updating — if the range shifts, so do the buy targets

**Files:** `config.py`, `state.py`, `spread_engine.py`, `config.toml`, `ws_server.py`

### 48) Per-fill cost basis must persist across sessions — blended average is a trap

**Problem:** The bot had pre-existing inventory (8274 DRIFT) at a blended cost basis of $0.0449
(from earlier buys at higher prices). Market price was ~$0.039. Every sell looked like a loss
because `record_sell()` used the blended average, not the actual cost of the specific lot being sold.
This caused: (a) false consecutive-loss halts, (b) startup sells at a loss, (c) the buy-first
workaround was a band-aid that prevented the bot from selling profitable recent buys.

**Root cause:** `pending_barriers` (per-fill cost tracking) only lived in memory. Every restart
wiped them. The blended `position_cost_quote` couldn't distinguish "$0.039 buys from today" from
"$0.05 buys from last week."

**Fix applied:**
- New `data/fill_barriers.json` persists every `FillBarrier` to disk on each buy/sell fill.
- `load_barriers()` restores them on startup. If no file exists, bootstraps a single barrier
  from the blended cost basis as a safe fallback.
- `min_profitable_sell_price()` calculates the minimum sell price that covers the cheapest
  barrier's cost + round-trip fees (2x fee_bps).
- `_tick()` in `spread_engine.py` now floors the sell price at `min_profitable_sell_price`.
  If the market mid is below breakeven, sells are fully suppressed — no orders placed at a loss.
- Removed the earlier "buy-first on startup" hack (lesson 46). It's no longer needed because
  the per-fill cost basis now protects against unprofitable sells directly.

**How it works now across restarts:**
1. Bot buys 200 DRIFT at $0.0385 → barrier saved to disk
2. Bot restarts
3. Barrier loaded: cheapest buy = $0.0385
4. min_profitable_sell = $0.0385 * 1.0046 = $0.03868 (covers 23bps fees each way)
5. If market mid is $0.039 → sell orders placed above $0.03868
6. If market mid is $0.037 → sells suppressed entirely, only buys placed

**Files:** `inventory.py`, `spread_engine.py`, `ws_server.py`, `state.py`

### 50) Pain floor trap: learner can lock itself at maximum conservatism

**Problem:** DRIFT/USD buy orders were placed 3%+ below mid ($0.0373 vs market $0.0385),
completely outside the active trading range. The bot had zero chance of filling.

**Root cause — three compounding factors:**

1. **Pain floor locked at 100 bps (the ceiling).** The learner tried tighter spreads,
   lost money on some, and raised `pain_floor` to 100. The `_effective_floor()` function
   returns `max(config_floor, pain_floor)`, so the learner could never go below 100 bps.
   With `pain_floor_decay_hours = 4` (1 bps per 4 hours), going from 100 → 50 would take
   200 hours (~8 days). The bot was effectively stuck.

2. **spread_bps = 100 means 1% per side.** At `ref ≈ 0.0378`, `half_spread = $0.000378`.
   Before any skew, the buy is already 1% below mid.

3. **Inventory skew compounds the problem.** Holding 8074/10000 = 80.7% of max inventory
   with `inventory_skew_scale = 0.6` pushes the reservation price down another ~18 bps.
   Combined: buy ends up 3%+ below mid.

**Math walkthrough (first tick after warmup):**
```
ref (microprice) ≈ 0.0378
spread_bps = 100 → half_spread = 0.000378
inventory q = 8074/10000 = 0.8074
skew = -0.8074 × 0.6 × 0.000378 = -0.000183
reservation = 0.0378 - 0.000183 = 0.03762
buy_price = 0.03762 - 0.000378 = 0.03724
level prices (40 bps steps): 0.0372, 0.0371, 0.0369
→ Placed: 0.0373, 0.0372, 0.0370 (matches log output)
```

**Fixes applied:**

1. **Reset pain_floor from 100 to 50** in `learner_state_live.json`. The floor of 50
   matches the config `spread_floor_bps` — no memory below that makes sense.

2. **Lowered config spread_bps from 100 to 50** in `config.toml`. At 50 bps half-spread
   (100 bps total), the bot quotes competitively within the ~260 bps market spread while
   still clearing the 23 bps maker fee with margin.

3. **Accelerated pain floor decay from 4h to 1h per bps** in `config.toml`. Going from
   a stuck pain_floor of 100 → 50 now takes 50 hours instead of 200. Still slow enough
   to remember recent losses, fast enough to adapt within days.

4. **Added ceiling trap detection in the learner.** If `pain_floor >= ceiling`, the learner
   is stuck — it can never tighten. New logic resets pain to the midpoint between config
   floor and ceiling. This prevents the trap from ever happening again.

5. **Capped pain_floor raises at `ceiling - 10`.** The pain floor can never be pushed
   higher than 10 bps below the ceiling, preserving room for the learner to explore.

6. **Pain floor capped on load.** When loading from disk, saved pain_floor is clamped
   to `ceiling - 10` so stale high values from old sessions don't re-trap the learner.

**Key insight:** The pain floor is supposed to be a safety net, not a cage. It should
prevent re-exploring spreads that lost money, but it must never consume the entire
exploration range. Without a ceiling cap, any sustained losing streak can push the pain
floor to max, where the learner becomes permanently conservative and stops competing.

**Expected behavior after fix:**
- Level 1 buy at ~$0.0376 instead of $0.0373 (inside the trading range)
- Inventory skew still discourages excessive buying (correct — already heavy)
- Learner can explore 50–110 bps range instead of being locked at 100
- Pain floor decays 1 bps/hour, reaching 50 (config floor) in ~50 hours if no new losses

**Files:** `strategy_learner.py`, `config.toml`, `data/learner_state_live.json`

### 51) 30-day volume should sync from Kraken, not just local fill replay

**Problem:** The bot computed 30-day volume by replaying `trades_live.jsonl`, counting
only its own fills. Kraken reported $9,111 while the bot showed $7,376 — the difference
being manual trades on the same account. Fee tier calculations used the understated number.

**Fix applied:**
- New background task `_volume_sync_loop()` in `main.py` calls `User.get_trade_volume()`
  every 5 minutes via `asyncio.to_thread` (never blocks the order path).
- Writes the Kraken-reported volume to `state.volume_30d`, overwriting the local estimate.
- Added `volume_30d_source` ("local" or "kraken") and `volume_30d_synced_at` to state for
  dashboard visibility.
- Between syncs, each fill still increments the counter locally — Kraken re-corrects on
  the next sync.
- The spread engine reads `state.volume_30d` as a plain float — zero latency impact.

**Files:** `main.py`, `state.py`, `ws_server.py`

### 44) Session fill logs must use effective spread, not config spread

**Problem:** `session_logger.log_fill()` was called with `spread_bps=pc.spread_bps` (the static
config value). When dynamic widening raised the actual spread from 30 to 45 bps, the session log
still said 30. Post-session analysis was misleading — couldn't distinguish fills at different
effective spread levels.

**Fix applied:**
- Paper fill path in `spread_engine.py` now uses `self._state.current_spread_bps` (set earlier
  in `_tick()` to the actual effective spread after vol/velocity/threat widening).
- Live fill path in `live_order_manager.py` now uses `self._state.current_spread_bps`.
- Fallback to `pc.spread_bps` if effective spread is 0 (pre-first-tick edge case).

**Files:** `spread_engine.py`, `live_order_manager.py`

### 52) Suppression systems fighting each other → deadlock

**Problem:** Six independent systems can suppress or cancel orders:
1. **Ping-pong** (after buy fill → suppress buys, after sell → suppress sells)
2. **suppress_sell_no_profit** (sell price below cost basis breakeven)
3. **Consecutive loss pause** (sell_paused_until after 3 losing sells)
4. **Momentum hold** (suppress buys during sell cascades)
5. **Risk halt** (daily loss limit, drawdown, session P&L floor)
6. **_should_cancel_order** (price drift, stale, reprice)

These were implemented independently and never tested in combination. After a buy fill
at $0.0375 with 8000 units of legacy inventory at $0.0449 average:
- Ping-pong suppressed buys (correct).
- suppress_sell_no_profit oscillated because microprice jittered around breakeven.
- When both fired the same tick: zero orders rested, zero fills, permanent deadlock.
- Meanwhile, the blended cost basis P&L reported the profitable per-fill trades as
  losses (-$2.19), tripping the $2.00 daily loss limit and halting the bot entirely.

**Root causes:**
- No anti-deadlock guarantee — each system set its boolean independently.
- Ping-pong enforcement cancelled orders placed *this same tick*, creating churn.
- P&L tracker used blended average cost instead of per-fill lot matching.
- `can_sell()` checked `order_size` instead of `sell_order_size`, creating a phantom block.
- No hysteresis on the profitability suppression boundary.

**Fixes applied:**
1. **Priority hierarchy** in the suppression decision block with explicit anti-deadlock:
   both sides can never be suppressed simultaneously. If both would be, ping-pong intent
   wins (after buy → allow sells; after sell → allow buys). If stuck >30s, force-clear
   `last_fill_side` entirely.
2. **Ping-pong enforcement age gate**: only cancel orders older than 1.5× cycle time,
   preventing the "place then immediately cancel" churn.
3. **Hysteresis on profitability suppression**: once suppressed, ref must clear min_sell
   by 5 bps before re-enabling sells (prevents oscillation at the boundary).
4. **Per-fill P&L for session tracking**: `total_pnl` now uses per-fill lot matching
   from `pending_barriers`, not blended average cost. Daily loss limit reflects actual
   trading performance.
5. **`can_sell` fix**: uses `sell_order_size` consistently (was using `order_size`).
6. **Daily loss limit raised** from $2 to $10 (old value was too tight with underwater
   legacy inventory).

**Design principle:** Any system that suppresses one side of the book must verify it isn't
creating a deadlock with systems suppressing the other side. The anti-deadlock guarantee
is the last line of defense — individual systems should avoid creating the conflict in the
first place.

**Files:** `spread_engine.py`, `live_order_manager.py`, `inventory.py`, `config.toml`

### 53) Fill cascade protection: cancel same-side orders and cooldown after every fill

**Problem:** On thin-liquidity pairs like DRIFT/USD, the bot had 3 buy levels resting.
When one filled, the remaining two were still live at stale prices. If the market kept
moving into those levels, all three could fill in quick succession — 600 units bought
in a few seconds on a book with $20-50 at top of book. The position was 3x larger than
intended, and the price impact of that much buying pushed the average entry well above
where the first fill landed.

Worse: on the sell side, after accumulating 600 units the sell cap allowed selling all
of them. But only 400 were from cheap recent buys — the third sell matched against old
expensive inventory at $0.0449, creating a genuine loss that tripped the risk halt.

**Root cause:** No post-fill cleanup. Orders placed before a fill remained live at prices
that were no longer appropriate.

**Fix applied:**
- After every buy fill: cancel all resting buy orders, set `buy_cooldown_until = now + fill_cooldown_sec`.
- After every sell fill: cancel all resting sell orders, set `sell_cooldown_until = now + fill_cooldown_sec`.
- `fill_cooldown_sec` defaults to 5.0 (configurable in `[bot]` section of `config.toml`).
- The spread engine checks cooldown timestamps in the suppression block — no new orders on
  the cooled-down side until the timer expires.
- `_cancel_same_side()` in `live_order_manager.py` finds all active orders for the pair+side
  and cancels them asynchronously.
- The anti-deadlock guarantee explicitly ignores cooldown-based suppressions — they're
  short-lived and resolve on their own.

**Design insight:** On illiquid pairs, a fill is a strong signal that the book just moved.
Stale same-side orders are almost certainly mispriced. Cancel first, re-evaluate after a
brief cooldown, then re-place at current market levels.

**Files:** `live_order_manager.py`, `state.py`, `spread_engine.py`, `config.py`, `config.toml`

### 55) Underwater cost basis permanently locks sells — auto-reseed on START

**Problem:** After accumulating 8,754 DRIFT at an average cost of ~$0.0433, the market
dropped to ~$0.038. The fill barrier (`fill_barriers.json`) stored the buy at $0.04492.
`min_profitable_sell_price()` returned $0.04513. Every sell was suppressed because
`ref < min_sell`. The bot ran for 40+ minutes across multiple sessions with zero fills —
placing only buys, slowly pushing inventory toward the 10,000-unit cap.

The suppression was working as designed: it was protecting against realizing a loss. But
with the entire position underwater, there was no sell price the bot would ever accept.
It had entered a silent accumulation trap with no automated exit.

**Compounding factors discovered simultaneously:**

1. **880-unit inventory gap**: `cost_basis.json` showed 8,754 units but `fill_barriers.json`
   only tracked 7,874. The 880-unit gap had no barrier entry. Even if a cheap barrier had
   been added for the gap, the `min_profitable_sell_price()` would still use the $0.04492
   barrier as the floor because it was the cheapest tracked.

2. **Triple barrier dead on existing inventory**: The barrier had `stop_price=0, tp_price=0,
   max_hold_until=0`. All three `_check_triple_barriers()` conditions require `> 0`. The
   position had zero downside protection despite `triple_barrier_enabled=true`. This is
   because the fill predated the feature being enabled — the code only sets stop/tp/max_hold
   at fill time, it never backfills existing barriers.

3. **Learner frozen at floor**: `pain_floor=50`, `spread_floor_bps=50`. `eff_floor = 50`.
   `no_fill_decay` returned early (`cur <= eff_floor`). Spread locked at 50 bps half =
   100 bps total. Market natural spread was 28–57 bps. We were quoting 2–4× wider than
   market — no fills possible on either side.

**Fixes applied:**

- `inventory.py`: New `reseed_barriers_at_mid(pair_key)` method clears all barriers for a
  pair and creates a fresh barrier at the current live mid price. If `triple_barrier_enabled`,
  also sets proper `stop_price`, `tp_price`, and `max_hold_until` from config. Persists
  immediately to disk. Fires a dashboard toast on completion.

- `inventory.py` — `load_barriers()` fallback now prefers `ps.mid_price` (populated by
  BookClient before START is pressed) over blended historical cost when bootstrapping from
  an empty barrier file. This means: full reset → start → fresh barrier at current market.

- `inventory.py` — `load_barriers()` gap reconciliation: after loading barriers, if
  `sum(barrier.qty) < inventory_base`, a synthetic barrier at blended cost is added for
  the untracked units. Prevents phantom inventory gaps going forward.

- `ws_server.py` — `reset_pnl(scope=all)` now saves barriers and cost basis to disk after
  clearing (previously cleared memory only — file stayed stale, reload restored old data).
  Also clears `ps._sell_profit_suppressed` and calls `learner.reset_pair()` for all pairs.

- `ws_server.py` — New `reseed_barriers` action: sends `{action: "reseed_barriers",
  pair_key: "DRIFT_USD"}` via WebSocket to reset the barrier live without restart.

- `strategy_learner.py` — New `reset_pair(pair_key)` method clears pain floor, regime
  floors, EMA rate, direction, and all cooldown state. Persists clean state to
  `learner_state_live.json` so the learner re-explores from the config floor on next cycle.

- `config.toml` — `spread_floor_bps` for DRIFT_USD: 50 → 25 (matching the profitability
  floor: 23 bps fee + 2 bps margin). `adaptive_spread_floor_bps`: 50 → 25 globally.
  `spread_bps` for DRIFT_USD: 50 → 25 (50 bps total, inside the natural market spread).
  `order_size` / `sell_order_size`: 200 → 225 (225/3 levels = 75 each = Kraken minimum).

**Auto-reseed on START (added after initial fix):**

The manual workflow above was a first step, but the real answer is the bot should detect and
fix this itself. `barrier_auto_reseed_pct` (default `5.0`) enables this: at the top of the
START handler, after `load_barriers()` runs, the engine checks every pair:

```
if min_profitable_sell_price(pair) > mid_price × (1 + barrier_auto_reseed_pct / 100):
    reseed_barriers_at_mid(pair)
    learner.reset_pair(pair)
```

For DRIFT with `min_sell=$0.0451` and `mid=$0.038`: threshold is `$0.038 × 1.05 = $0.0399`.
Since `0.0451 > 0.0399`, the reseed fires automatically — no operator action needed. The
dashboard shows a green "Barriers Reseeded" toast on completion.

Configure `barrier_auto_reseed_pct` in `config.toml`:
- `5.0` (default): reseed if sell floor is >5% above market — handles overnight dips
- `10.0`: more tolerant of temporary drawdowns before accepting the loss
- `0`: disable auto-reseed entirely (manual-only via `reseed_barriers` action)

**Manual override still available (for mid-session use):**
```
Option A (hot, no restart):
  Send: { "action": "reseed_barriers", "pair_key": "DRIFT_USD" }
  from browser console — bot reseeds and unlocks sells on next tick.

Option B (clean restart):
  Dashboard → Full Reset (shift-click) → START
  Barriers saved as empty → load_barriers() seeds from current mid → auto-reseed check
  is a no-op (no barriers loaded means no underwater check needed).
```

**Key lessons:**
- The fill barrier system protects against realizing losses on individual fills. It does NOT
  protect against a regime where the entire position is permanently underwater. An automated
  detection-and-reset path is required — not just an operator escape hatch.
- Auto-reseed fires at START time when the book is live and `mid_price` is populated. If
  the book hasn't connected yet (mid=0), the check is skipped and the stale barrier persists
  until the next START. This is rare but worth knowing.
- Triple barrier protection only applies to NEW fills. Pre-existing inventory has no
  stop-loss unless explicitly backfilled. `reseed_barriers_at_mid()` fixes this by creating
  a fresh barrier with current-price stop/tp on the entire position.
- Always verify `data/fill_barriers.json` and `data/learner_state_live.json` after a session
  with zero fills — these are the first places to look when the bot is placing orders but
  not getting any traction.

**Files:** `inventory.py`, `ws_server.py`, `strategy_learner.py`, `config.toml`

### 56) Bot detection oscillation on thin books pollutes logs and jitters spread

**Problem:** On DRIFT/USD, session logs were 97% `bot_detection` events alternating between
`"elevated"` (score ~0.55) and `"clean"` (score ~0.43) every 2 seconds. The `composite_score`
straddled the `mev_bot_score_threshold = 0.5` on every other classification window, causing
the spread multiplier to alternate between widening (~8.5%) and tightening (~10%) every tick.
Effective spread jittered constantly with no real signal change.

**Root cause:** `phantom_liquidity(100%)` appeared on nearly every DRIFT classification.
The phantom liquidity detector fires when >65% of top-of-book volume removal happens
without a price move. DRIFT's thin book ($20–$50 at the top) means our own order is often
the dominant level. When it's cancelled and replaced (normal smart-cancel behavior), 100%
of the book volume disappears without a trade — permanently triggering the phantom signal.
This is a structural characteristic of thin books, not actual bot activity.

The 60-second detector window was short enough that individual cancel events dominated the
rolling average. The 0.5 threshold was too low for a signal this noisy.

**Fix applied:**
- `mev_bot_score_threshold`: 0.5 → 0.65 in `config.toml`. Scores in the 0.3–0.6 range
  no longer trigger "elevated" — only sustained, strong bot signals cross the threshold.
- `mev_detector_window_sec`: 60 → 120 in `config.toml`. Doubles the rolling window,
  smoothing individual cancel events into a slower-moving average.

**Lesson:** MEV detection parameters must be calibrated to the book depth of the active pair.
A threshold and window appropriate for a liquid pair (USDG/USDT with 78K+ at top) will
over-trigger on a thin pair (DRIFT/USD with $20–$50 at top). Consider per-pair MEV
threshold overrides if multiple pairs with very different liquidity profiles are enabled.

**Files:** `config.toml`

### 54) Multi-level sell sizing must respect exchange minimum order volume

**Problem:** After a buy fill of 200 DRIFT, the sell cap correctly limited total sell qty
to 200 (only one cheap barrier existed). But the engine divided this across 3 levels:
200 / 3 = 66.67 per level. Kraken's minimum for DRIFT/USD is 75 units. Every sell order
was rejected with "volume minimum not met" — 3 rejections per tick, every 30 seconds,
for as long as the bot ran. The ping-pong sell cycle was completely broken.

**Root cause:** `per_level_sell` was computed as `total_sell_qty / slots_to_fill_sell`
without checking if the result exceeded the exchange minimum.

**Fix applied:**
- Added `min_order_qty` to `PairConfig` (default 75.0, matching DRIFT's Kraken minimum).
- Parsed from `[pairs.DRIFT_USD]` in `config.toml` as `min_order_qty = 75.0`.
- Before dividing sell qty across levels, `spread_engine.py` now computes:
  `max_levels = floor(total_sell_qty / min_order_qty)`
  `effective_sell_levels = max(1, min(slots_to_fill_sell, max_levels))`
- If per-level sell is still below minimum after reducing levels, set to 0.0 (skip sells).
- Result: 200 units / 75 min → max 2 levels → 100 per level. Both orders pass Kraken's
  validation instead of 3 rejected orders every tick.

**Lesson:** Always verify order minimums against the exchange API before placing.
`https://api.kraken.com/0/public/AssetPairs?pair=DRIFTUSD` → `ordermin` field.
When splitting quantity across multiple levels, reduce levels first — don't place
sub-minimum orders that will get rejected in a loop.

**Files:** `spread_engine.py`, `config.py`, `config.toml`

### 55) Phantom liquidity detection needs a notional floor on thin books

**Problem:** The bot classifier flip-flopped between "clean" and "elevated" every 6-12
seconds on DRIFT/USD, driven entirely by `phantom_liquidity(100%)`. The log noise was
significant — dozens of classification changes per minute — and while it didn't affect
spread widening at these score levels, it obscured real events in the logs.

**Root cause:** The phantom liquidity tracker counted *any* volume removal > 50% at top
of book. On DRIFT's thin book, a handful of tokens ($0.35 worth) vanishing was enough to
trigger. This is normal order book activity on a low-liquidity pair, not phantom liquidity
manipulation.

**Fix applied:**
- Added `phantom_min_notional` parameter to `CEXBotDetector.__init__()` (default $5.00).
- `_track_phantom_liquidity()` now only counts removals where `removed * mid >= min_notional`.
- Trivial removals (< $5 notional) are ignored entirely.
- On DRIFT at $0.035, this means only removals of 143+ units register. Top-of-book on DRIFT
  is typically 200-500 units, so genuine large removals still trigger — but 10-unit fluctuations
  (the primary noise source) are filtered.

**Lesson:** Microstructure detectors built for liquid markets need notional floors when applied
to thin pairs. Percentage-based thresholds ("> 50% removed") are meaningless when the absolute
amount is a few dollars. Always gate on dollar value, not just percentage.

**Files:** `cex_bot_detector.py`

### 56) Bot classifier oscillation doesn't affect profits at current score levels

**Analysis performed (not a code fix — operational understanding):**

The classifier oscillates between "clean" (score ~0.30) and "elevated" (score ~0.50-0.60).
This looks alarming in the logs but has near-zero spread impact because:

1. "Elevated" classification at these scores doesn't trigger spread widening. The `recommended_spread_mult`
   stays at 1.0 because `competing_mm` requires both `symmetry_score > 0.7` AND `quote_flicker_rate > 2.0`,
   and DRIFT doesn't hit the flicker threshold.
2. "Clean" classification tightens spread by 10% (`mev_clean_tighten_scale = 0.10`).
3. Net effect: spread oscillates between ~45 bps (clean, tightened) and ~50 bps (elevated, no change).
   That's a $0.00017 difference on a $0.035 token — irrelevant to fill probability.
4. A longer EMA to smooth the score would actually hurt slightly — it would park the score at ~0.45
   (permanently "clean"), losing the ability to react quickly if real bot activity appears.

**Decision:** Leave the classifier as-is. The phantom liquidity floor (lesson 55) reduces the worst
log noise. The remaining oscillation is cosmetic and not worth adding EMA smoothing complexity.
