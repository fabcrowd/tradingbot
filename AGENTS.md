---
description: 
alwaysApply: true
---

# AGENTS.md - Complete Project Context for New Agents

Use this document as your primary reference when you inherit this repository.
It contains everything you need to understand the project, its current state,
architecture, operational patterns, and known issues.

---

## Mission

**Primary (active):** Run and improve the **scalp bot on Coinbase Derivatives Exchange (CDE)** —
directional / multi-mode signals, bar store, walk-forward optimization, and Coinbase execution
(`[scalp]` in `config.toml`, `scalp_bot/`, `coinbase_order_manager.py`).

**Shuttered (dormant until revived):** **Kraken spot market maker** — spread capture with
portfolio-level risk controls. Trading capital is **not** on Kraken; the MM codebase remains
for a future return. Do not assume MM pairs, balances, or live Kraken quoting are current
operational context unless the operator explicitly re-enables MM.

---

## How To Run

```bash
pip install -r backend/requirements.txt
python -m backend.server.main
```

Dashboard: `http://localhost:8080` (or host/port from `config.toml`)

The **Kraken MM** engine starts **paused** (press **START** if MM is enabled). **Scalp (Coinbase CDE)**
behavior follows `[scalp]` and dashboard sim/live controls — that is the **active** trading path.

**Windows port conflict:** If you get `OSError: [Errno 10048]` on restart, kill lingering
Python processes before starting:

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
python -m backend.server.main
```

---

## First 5 Minutes (Do This First)

1. Read these files in order (**scalp-first**):
   - `config.toml` — especially `[scalp]`, venue, CDE `symbol` / `product_id` values
   - `backend/server/scalp_bot/scalp_runtime.py` — coordinator, WFO, warmup, snapshots
   - `backend/server/scalp_bot/scalp_config.py` — parsed scalp config
   - `backend/server/coinbase_order_manager.py` — live Coinbase / CDE execution
   - `backend/server/ws_server.py` — dashboard actions (`set_scalp_mode`, etc.)
   - `lessons.md` — start at "Current Lessons" section
2. **Kraken MM (if ever revived):** `spread_engine.py`, `live_order_manager.py`, `[bot].enabled_pairs`
3. Confirm Coinbase credentials and PEM formatting (`.env` / `_normalize_coinbase_pem` in `config.py`)
4. Start bot and verify clean startup logs (Coinbase feed, bar store, scalp) before making changes
5. **Cursor skills (optional):** shared catalog [fabcrowd/skills](https://github.com/fabcrowd/skills) — clone it, set `FABSKILLS_REPO` to that path, run `scripts/sync_fabcrowd_skills.ps1` or `scripts/sync_fabcrowd_skills.sh` (see `.cursor/README.md`).

---

## Scalp: WFO vs param tuner

- **`strategy_mode = "auto"`:** execution mode is the WFO **champion** `mode` for that symbol when `data/scalp_champion.json` has a row; otherwise **`auto_mode_fallback`** in `[scalp]` (default **`sar_chop`** as of 2026-04-16 — WFO will still promote a different mode as champion if it scores better). Implemented in `scalp_mode_resolution.py` (replaces legacy “auto = DaviddTech” everywhere).
- **Walk-forward optimizer** (`scalp_wfo.py`): rolling train/holdout grid over stored bars;
  selects **mode + parameters** that pass stability and hard gates; writes **`data/scalp_champion.json`**
  (one object per last-optimized symbol — multi-pair setups may only have one symbol per file).
- **Param tuner** (`param_tuner.py`): after WFO has chosen a champion for a symbol, continuously
  **perturbs tunables** for the **current** mode (ATR multiples, DaviddTech windows, etc.).
  It does **not** override WFO’s mode while `scalp_champion.json` matches that pair’s `symbol`.
- **No-champion bootstrap** (`strategy_lookback.py` + `ScalpRuntime._apply_no_champion_bootstrap`):
  if there is no champion for a pair’s symbol, active mode is chosen from a **2h** trade window
  ranked by **return %** until WFO produces a matching champion.
- **CDE fee model in WFO / backtests:** `[scalp]` sets maker/taker bps (`fee_bps_per_leg`, `fee_bps_taker_per_leg`, chosen by `order_type` via `effective_scalp_fee_bps_per_leg`), flat **`fee_usd_per_contract_per_leg`**, and per-pair **`contract_size`**; `scalp_vec_backtest` scores **USD PnL for 1 contract**. Champion JSON does not freeze fee fields.
- **Sim vs live PnL:** WFO and bar backtests use `backtest_fill_model` (e.g. `close_slip` vs `next_open`) and **simulated** fees from `[scalp]` (`wfo_assume_taker_fee` stresses taker bps). Live PnL includes real fills, partials, funding, and fee tier drift — do not treat holdout `total_pnl` as net live expectancy without reconciliation.
- **WFO data / backfill:** Runtime REST backfill requests `wfo_roll_span_hours(...) + wfo_backfill_buffer_hours` (default 24h slack). After backfill, stored span is checked vs **92%** of the roll span; shortfall logs **ERROR** and raises a dashboard alert. `bar_store.load_bars(..., trim_anchor="latest_bar")` aligns Parquet trims with WFO’s “latest bar” window (avoids wall-clock trim when the machine was offline). **`wfo_allow_promotion_relaxation`** (default **false**): when false, WFO does not fall back to relaxed quarter-window or `min_windows=1` promotion tiers — tighten in TOML only after reviewing `windows_skipped_insufficient_bars` / `wfo_promotion_tier` in snapshots and `data/wfo_champion_promotions.jsonl`.
- **Lab / CLI scripts:** Any harness that claims to reproduce live WFO must load or fetch **at least** `wfo_roll_span_hours` for the target `config.toml` (see `compare_intervals.py` module docstring and `scalp_wfo.wfo_roll_span_hours`).

---

## Architecture — Kraken spot MM (shuttered)

The diagram below is the **dormant Kraken spot spread-MM** pipeline (historical reference). **Current ops center on the Coinbase CDE scalp bot** (`scalp_bot/`, `coinbase_order_manager.py`). Spread-MM code may still start with `main.py` when configured.

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

---

## Current strategy baseline (as of April 2026)

### Active: Coinbase CDE scalp

Treat **`[scalp]`** in `config.toml` as authoritative: `venue = "coinbase_perps"` (or as configured),
CDE-style product IDs, `allocated_capital_usd`, WFO keys (`wfo_*`), `strategy_lookback_hours`, and
per-pair `strategy_mode`. Confirm `.env` has `COINBASE_API_KEY` / `COINBASE_API_SECRET` (PEM) when
running live scalp.

### Shuttered: Kraken MM (historical snapshot — not current ops)

The following was the **Kraken spot MM** baseline when MM was funded; it is **not** the active
product after moving capital off Kraken. Keep for reference if MM is revived.

From `config.toml` (illustrative):

```
mode = "live"
enabled_pairs = ["USDG_USDT", "TEL_USD"]
default_cycle_ms = 3000
learner_enabled = true
per_trade_profitability = true
```

#### Pair-specific settings (Kraken MM)

| Pair | spread_bps | floor_bps | order_size | max_inv | fee_schedule | fee_bps | cycle_ms |
|------|-----------|-----------|------------|---------|-------------|---------|----------|
| USDG_USDT | 1 | 1 | 10.0 | 40.0 | usdg (0%) | 0 | 3000 |
| TEL_USD | 30 | 25 | 2500.0 | 50000.0 | maker_rebate (23bps) | 23 | 3000 |

#### Portfolio-level risk controls (Kraken MM)

| Control | Value |
|---------|-------|
| `min_total_pnl_usd` | -5.0 |
| `daily_profit_target_usd` | 20.0 |
| `daily_loss_limit_usd` | 5.0 |
| `max_drawdown_pct` | 20.0 |

#### Other MM settings (when MM was active)

- `momentum_hold_sells = 4`, `momentum_hold_sec = 30` — suppress buys after sell bursts
- `adaptive_spread_floor_bps = 1`, `min_quote_half_spread_bps = 1` — allow tight quoting
- `threat_quoting_pause = true` — pause quoting on CRITICAL threat
- `learner_interval_sec = 60`, `learner_max_daily_adjustments = 50`
- `pain_floor_decay_hours = 4` — learner re-explores after 4 hours

#### Disabled MM features (available but typically off)

- `adaptive_tuning = false` — win-rate-based spread adjustment
- `optimizer_enabled = false` — walk-forward parameter optimizer
- `trailing_stop_enabled = false` — per-pair trailing stop
- `oco_enabled = false` — stop/take OCO legs after buy fills
- `twap_enabled = false` — time-weighted buy splitting
- `btd_enabled = false` — buy-the-dip SMA detection

---

## File Reference Map

### Scalp / Coinbase (read these first)

| File | Purpose |
|------|---------|
| `config.toml` | `[scalp]` and venue; Coinbase + optional Kraken MM keys |
| `backend/server/main.py` | Entry point, startup orchestration, shutdown |
| `backend/server/scalp_bot/scalp_runtime.py` | Scalp task: feed, WFO, tuner, snapshots |
| `backend/server/scalp_bot/scalp_config.py` | Parsed `[scalp]` config |
| `backend/server/coinbase_order_manager.py` | Coinbase / CDE order execution |
| `backend/server/state.py` | `BotState`, alerts, snapshots |
| `backend/server/config.py` | `load_config()`, `_normalize_coinbase_pem`, fee resolution |

**Scalp portfolio halt and telemetry (Apr 2026):** `BotState.scalp_risk_halted` and `scalp_entries_blocked()` stop **new** scalp entries without relying on MM `risk_halted`. WebSocket: `scalp_risk_halt` / `scalp_risk_resume`; `scalp_emergency_stop` also sets scalp halt, enters operator standby, and cancels resting scalp orders (no flatten). `scalp_emergency_flatten` requires JSON `confirm: "CONFIRM_FLATTEN"` and submits **reduce-only** market exits per open leg. Session JSONL includes `scalp_fill_execution` (slip vs reference, fees when the venue reports them). WFO churn controls include `wfo_min_champion_score_delta`, `risk_on_wfo_min_base_interval_frac` (risk-on WFO sleep floor vs base interval), a vol-armed one-pass `WFOConfig` overlay, and optional `wfo_adverse_check_enabled` before writing `scalp_champion.json`.

### Kraken MM (shuttered — reference when reviving MM)

| File | Purpose |
|------|---------|
| `backend/server/spread_engine.py` | Core tick loop, spread calculation, order placement |
| `backend/server/live_order_manager.py` | Kraken WS v2 order placement, cancellation, reconciliation |
| `backend/server/strategy_learner.py` | Hill-climb optimizer: adjusts spread_bps per pair on $/min |

### Supporting components (mostly Kraken MM / shared server)

| File | Purpose |
|------|---------|
| `backend/server/book_client.py` | Kraken WS v2 public L2 order book subscription (depth-25) |
| `backend/server/inventory.py` | Balance sync from Kraken, `can_buy()`/`can_sell()` with portfolio-level checks |
| `backend/server/threat_detector.py` | Velocity, imbalance, spread blowout, realized vol |
| `backend/server/fee_schedule.py` | Kraken fee tier tables (spot, stablecoin, maker_rebate, USDG, USDe) |
| `backend/server/pnl.py` | P&L tracking, JSONL persistence, 30-day volume |
| `backend/server/session_logger.py` | JSONL session telemetry (fills, learner, halts, momentum) |
| `backend/server/ws_server.py` | HTTP server + WebSocket push to dashboard, action dispatch |
| `backend/server/runtime.py` | `BotRuntime` — manages mode switch, component lifecycle |
| `backend/server/rate_limiter.py` | Token-bucket rate limiter for Kraken API calls |
| `backend/server/order_manager.py` | Paper mode order manager (simulated fills) |
| `backend/server/adaptive_spread.py` | Win-rate-based spread tuner (alternative to learner) |

### Advanced / optional components

| File | Purpose |
|------|---------|
| `backend/server/optimizer.py` | Walk-forward parameter optimizer (grid search + holdout) |
| `backend/server/oco_manager.py` | One-cancels-other stop/take-profit order management |
| `backend/server/twap.py` | Time-weighted average price order splitting |
| `backend/server/btd.py` | Buy-the-dip SMA crossover detection |
| `backend/server/presets.py` | Pair archetype presets (stablecoin_zero_fee, altcoin_high_fee, conservative_test) |
| `backend/server/sim_runner.py` | CLI spread_bps sweep simulation harness |
| `backend/server/backtest.py` | Session-replay backtesting with `--spread-bps` and `--compare` |

### Frontend

| File | Purpose |
|------|---------|
| `frontend-new/` | **Active** dashboard (Vite + React); proxies to backend |
| `frontend/` | Legacy static assets; may still be served in some setups |

### Data files (runtime)

| File | Purpose |
|------|---------|
| `data/trades_live.jsonl` | Live fill history — cumulative P&L replayed on restart |
| `data/trades_paper.jsonl` | Paper fill history |
| `data/learner_state_live.json` | Learner persistence — only `pain_floor` used on load |
| `data/session_*.jsonl` | Per-session event logs — source of truth for diagnosis |

### Project docs

| File | Purpose |
|------|---------|
| `AGENTS.md` | This file — complete context for new agents |
| `README.md` | Architecture overview, setup, configuration reference |
| `lessons.md` | 42 lessons from live operation, postmortems, and audits |

---

## Key Implementation Details

### 1) Spread semantics

`spread_bps` is the **half-width** from microprice to each quote. Total quoted width ≈ `2 × spread_bps`.
Buy = microprice - half_spread, Sell = microprice + half_spread (before skew).

### 2) Microprice — `_clean_microprice()`

The engine uses a volume-weighted mid that **excludes our own resting orders** from top-of-book.
Without this, on thin pairs (like TEL/USD) our own large sell order becomes the best ask and
drags the microprice toward the bid, causing sell prices to spiral downward.

Location: `spread_engine.py`, `_clean_microprice()` method.

### 3) Learner session behavior

The `StrategyLearner` **resets `spread_bps` to config.toml on every session start**. Only
`pain_floor` (memory of losing spreads) persists across sessions. This means config.toml
changes to `spread_bps` take effect immediately on restart — no need to manually edit
`learner_state_live.json`.

Within a session, the learner hill-climbs on $/min, tightens on idle (no-fill decay),
and widens when recent sells average negative.

### 4) Risk halt behavior

- Halt is **global** (portfolio-level), triggered from `total_pnl`, session P&L, drawdown, or daily limits.
- `_tick()` early-returns when `risk_halted` is true — one-shot, no spam.
- Halt cancels all orders on all enabled pairs.
- Resume requires explicit action from dashboard (`resume_risk_halt: true`).

### 5) Order reconciliation at startup

`LiveOrderManager._reconcile_open_orders()` calls `cancel_all_orders()` on Kraken at startup.
This clears any orphan orders from previous sessions that would lock funds. A 2-second delay
follows before balance sync to let Kraken release locked margins.

### 6) Smart cancel logic

Orders are NOT blindly cancelled each cycle. Cancel only when:
- **Price drift** > 1.5× half-spread (constant `DRIFT_CANCEL_MULT`)
- **Stale** > 600 seconds (constant `STALE_ORDER_SEC`)
- **Near-fill protection**: Orders within 3 bps of filling are kept alive up to 300 seconds

On cancel failure, the order stays in `active_orders` with `cancel_retry = True` —
the WS executions channel is the authoritative cleanup path.

### 7) Fee schedules

Kraken fees vary by pair type. The bot resolves maker fees at runtime via `effective_fee_bps()`:
- `usdg`: 0% maker
- `usde_promo`: 0% maker/taker
- `maker_rebate`: 23 bps at lowest tier, negative at high volume (we get paid)
- `spot_crypto`: 25 bps at lowest tier, drops with volume
- `stablecoin`: 20 bps at lowest tier

### 8) Session logs are source of truth

JSONL session files in `data/` capture everything: fills, learner actions, pain-floor changes,
risk halts, momentum transitions, snapshots. Use them to diagnose overnight behavior.

### 9) Inventory management

`InventoryManager` checks **portfolio-level availability** for shared assets. If USDG_USDT and
XRP_USDT both use USDT, the available balance accounts for open buy commitments across both pairs.
`can_buy()` and `can_sell()` enforce this before any order placement.

### 10) Alert system

`BotState.push_alert(level, title, detail, source)` broadcasts toast notifications to all
dashboard WS clients. Order rejections, rate limits, risk halts, stale books — nothing fails
silently. Alerts are also logged at appropriate severity.

---

## Constants Reference (spread_engine.py)

| Constant | Value | Purpose |
|----------|-------|---------|
| `DRIFT_CANCEL_MULT` | 1.5 | Cancel if drift > 1.5× half_spread |
| `NEAR_FILL_BPS` | 3 | Protect orders within 3 bps of touch |
| `NEAR_FILL_MAX_AGE_SEC` | 300 | Near-fill protection expires after 5 min |
| `STALE_ORDER_SEC` | 600 | Cancel orders older than 10 min |
| `BOOK_STALE_SEC` | 600.0 | Skip quoting if book data older than 10 min |
| `VOL_WIDEN_THRESHOLD` | 0.0003 | Widen spread above this realized vol |
| `VOL_WIDEN_SCALE` | 0.3 | Scaling factor for vol-based widening |
| `VELOCITY_WIDEN_FLOOR_BPS` | 10.0 | Only widen above this velocity |
| `VELOCITY_WIDEN_SCALE` | 0.5 | 1 bps added per 2 bps velocity above floor |
| `PROFITABILITY_MARGIN_BPS` | 2 | Added to fee_bps for per-trade profitability floor |

---

## Known Pitfalls (hard-won lessons)

1. **Spread competitiveness matters more than fee math.** If market spread is 1 bps and you
   quote at 20 bps, you'll never fill. Always check live ticker before setting spread_bps.

2. **Microprice self-pollution.** On thin books, your own order becomes the best level and
   distorts the reference price. `_clean_microprice()` handles this — do not bypass it.

3. **Learner state override.** The learner used to persist `spread_bps` across sessions and
   silently override config.toml changes. This was fixed — now only `pain_floor` persists.
   If you change anything about persistence, preserve this behavior.

4. **Orphan orders lock funds.** If reconciliation at startup doesn't cancel all prior
   orders, Kraken holds margin and every new order gets "Insufficient funds." The bot now
   calls `cancel_all_orders()` at startup.

5. **TEL/USD minimum order is 2300 TEL.** Kraken can change minimums without notice. Verify
   via `https://api.kraken.com/0/public/AssetPairs?pair=TELUSD` (`ordermin` field).

6. **Risk halt spam.** Without the top-of-tick `risk_halted` guard, the engine loops
   continuously logging halts and cancels. The guard must stay.

7. **Stale book on stable pairs.** USDG pairs can go minutes without book updates. The
   stale threshold was raised to 600s to prevent false pauses. Don't lower it without
   understanding the pair's natural update frequency.

8. **Cancel failure phantom orders.** On cancel timeout, keep the order in `active_orders`.
   Only the WS executions channel (`canceled`/`filled`/`expired`) should remove orders.

9. **Port 10048 on Windows.** Python process cleanup after Ctrl+C is unreliable. Always
   force-kill before restart.

10. **Double-counted inventory.** If two pairs share an asset (e.g., USDG_USDT and XRP_USDT
    both use USDT), each pair sees the full balance. Portfolio-level checks in
    `can_buy()`/`can_sell()` prevent over-ordering, but be careful when enabling overlapping pairs.

---

## Verification Checklist After Any Change

1. `python -m compileall backend/server`
2. Start bot and inspect logs for:
   - **Scalp (active):** Coinbase feed / candles, bar store, `ScalpRuntime` healthy, WFO/lookback if enabled
   - **Kraken MM (if enabled):** WS auth/public connect OK, reconciliation, inventory sync, quotes on enabled pairs
3. Confirm no repeated risk-halt spam (MM path) or unintended scalp halts
4. Confirm session log file created in `data/` and receiving events
5. Confirm dashboard snapshot includes expected fields (`risk_halted` / `risk_halt_reason` when MM runs; scalp block when enabled)
6. For MM spread changes: verify orders are competitive with live market
   (`https://api.kraken.com/0/public/Ticker?pair=USDGUSDT`)

---

## Working Rules

1. **Primary system is Coinbase CDE scalp** — prefer `[scalp]` + Coinbase paths when in doubt.
2. **Keep Kraken MM risk logic portfolio-level** when MM is revived; scalp has its own limits (`daily_loss_limit_pct`, etc.).
3. **Prefer additive, observable changes** with session log hooks.
4. **Do not remove protective guards** on the MM path (stale-book, cancel safety, reconciliation, `_clean_microprice`) without replacement.
5. **Validate in logs, not assumptions.** Use session JSONL to verify behavior.
6. **Document every behavior-changing change** in `lessons.md` (user workflow).
7. **Kraken MM:** `config.toml` is authoritative for `spread_bps` at session start; learner explores within a session; pain floor persists across sessions.
8. **Kraken MM:** check market spreads before tuning `spread_bps` (Kraken ticker API).
9. **Test sim / paper first** for structural changes; go live only after verification.
10. **Scalp / WFO / tuning changes should aim for positive P&L growth** — treat “more champions,” “faster WFO,” or “looser gates” as means, not ends. Prefer a stated hypothesis (e.g. holdout `total_pnl`, forward paper/live window) and measure before/after; if a change only increases activity or CPU without an expected P&L path, reconsider or scope it as observability-only.

---

## Market Context (as of April 2026)

**Note:** The sections below describe **Kraken** pairs for the **shuttered MM** bot. Primary live
context is **Coinbase CDE** scalp — use exchange tickers and product specs for your configured
`[scalp]` symbols.

### USDG/USDT (Kraken MM — historical)
- Fee schedule: `usdg` (0% maker, 0.01% taker)
- Market spread: ~1 bps, massive resting liquidity (78K+ at top of book)
- Our spread: 1 bps half = 2 bps total
- At 0% maker fee, any positive spread is profit
- Must sit at or near top-of-book to get fills in this thick book

### TEL/USD (Kraken MM — historical)
- Fee schedule: `maker_rebate` (23 bps at lowest tier, negative at high volume)
- Market spread: ~44-53 bps
- Our spread: 30 bps half = 60 bps total
- Thin book — our 2500 TEL order can dominate the best level
- `_clean_microprice()` is critical here to prevent self-pollution
- Minimum order: 2300 TEL (config uses 2500)
- Structural breakeven requires ~160 bps spread per simulation data

### XRP/USDT (Kraken MM — disabled)
- Fee schedule: `spot_crypto` (25 bps at lowest tier)
- Previously removed from enabled_pairs due to structural losses at current fee tier
- Can be re-enabled when fee tier drops or spread competitiveness improves

---

## Startup Sequence (what happens when you run `python -m backend.server.main`)

**Scalp (Coinbase):** after config load, expect `ScalpRuntime` / bar backfill / WFO tasks when
`[scalp].enabled` is true — see `scalp_runtime.py` and logs for the live path.

**Kraken MM (shuttered):** steps 6–12 below apply when MM is configured and Kraken is in use.

1. `load_config()` reads `config.toml` + `.env`
2. `BotState` initialized, `PairState` created for each configured pair
3. `SessionLogger` opens new JSONL file
4. `PnLTracker` loads fill history from `data/trades_{mode}.jsonl`
5. `InventoryManager` created
6. **If live mode:**
   - `LiveOrderManager.initialize()` — connects WS, subscribes to executions
   - `_reconcile_open_orders()` — cancels ALL existing Kraken orders
   - API permission check (warns on withdrawal access)
   - 2-second delay for Kraken to release locked funds
   - `inventory.sync_from_kraken()` — fetches real balances
7. `SpreadEngine`, `ThreatDetector`, `AdaptiveSpreadTuner`, `StrategyLearner`, `WalkForwardOptimizer` created
8. `StrategyLearner._load_state()` — loads pain_floor from disk, spread_bps stays from config
9. `DashboardServer.start()` — HTTP + WS on configured port
10. `BookClient` connects to Kraken public WS, subscribes to L2 book for enabled pairs
11. 3-second wait for book data to arrive
12. **Engine paused** — waiting for START command from dashboard

---

## Handy Diagnostic Commands

Check live market spread for a pair:
```bash
curl "https://api.kraken.com/0/public/Ticker?pair=USDGUSDT" | python -m json.tool
```

Check minimum order size:
```bash
curl "https://api.kraken.com/0/public/AssetPairs?pair=TELUSD" | python -m json.tool
```

Compile-check all backend code:
```bash
python -m compileall backend/server
```

Run session replay backtest:
```bash
python -m backend.server.backtest --session data/session_YYYYMMDD_HHMMSS.jsonl --spread-bps 4
```

---

## Handy Prompt For A Fresh Agent

```text
You are inheriting the Fabcrowd Arceus bot (this repo). Primary active system: Coinbase CDE scalp
(scalp_bot/, coinbase_order_manager.py, [scalp] in config.toml). Kraken spot MM is
shuttered until revived. Read AGENTS.md, nextsession.md, and lessons.md ("Current Lessons"),
then config.toml. Run the bot, verify Coinbase feed / bar store / scalp logs, and
summarize health from session JSONL. If touching the dormant Kraken MM, preserve risk
halt semantics, stale-book protection, cancel safety, _clean_microprice, and session logging.
```
