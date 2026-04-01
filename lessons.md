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
