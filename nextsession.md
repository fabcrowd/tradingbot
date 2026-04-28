# nextsession.md

## Purpose

One-page orientation for the next agent or chat: product, where code lives, what changed most recently, and what is still unverified. **Read this first**, then `AGENTS.md`, `config.toml` `[scalp]`, and **`lessons.md` → Current Lessons**. Do not use this file as a full README replacement.

## What this repository is

**Primary (active):** Scalp bot on **Coinbase Derivatives Exchange (CDE)** — `[scalp]` in `config.toml`, `ScalpRuntime`, `coinbase_order_manager.py`, bar store, WFO, param tuner. Entry: `python -m backend.server.main`. Dashboard HTTP/WS: **`[server]`** host/port (often `http://127.0.0.1:8080`).

**Frontend:** `frontend-new/` (Vite + React), not legacy `frontend/`.

**Shuttered:** Kraken spot MM — dormant in this tree; scalp is the live path.

**Secrets:** `.env` only (see `.env.example` for Coinbase CDP key + PEM formatting). Never paste keys into docs.

## Architecture

| Subsystem | Path | Role |
|-----------|------|------|
| Scalp coordinator | `backend/server/scalp_bot/scalp_runtime.py` | Feed, warmup, WFO, tuner, snapshots |
| Mode resolution (`auto`) | `backend/server/scalp_bot/scalp_mode_resolution.py` | Champion mode when present; else `auto_mode_fallback` |
| Scalp config | `backend/server/scalp_bot/scalp_config.py` | `[scalp]` dataclass, fees, WFO/tuner keys |
| Coinbase execution | `backend/server/coinbase_order_manager.py` | Orders, balances |
| Bar store | `backend/server/scalp_bot/bar_store.py`, `data/coinbase_bars/` | Parquet history |
| WFO | `backend/server/scalp_bot/scalp_wfo.py` | Walk-forward champion; `save_champion` validates `mode` |
| Vector backtest | `backend/server/scalp_bot/scalp_vec_backtest.py` | `evaluate_params`, `build_default_grid`, **`WFO_REGISTERED_STRATEGY_MODES`**, **`sar_chop_diagnostic_frame`** (masks + internals for CSV / chart checks) |
| sar_chop Parquet dump | `backend/server/scalp_bot/sar_chop_signal_dump.py` | CLI: from `backend/server`, `python -m scalp_bot.sar_chop_signal_dump` — loads `bar_store` Parquet, optional `--since`/`--until`/`--csv`/trim flags |
| TradingView decode skill | `.cursor/skills/tradingview-extract/SKILL.md` | Pine facade / `pubscripts-suggest-json` workflow; pairs with **`lessons.md` §35** |
| Param tuner | `backend/server/scalp_bot/param_tuner.py` | Local perturbations; scores via `evaluate_params`; **PnL-first** nudges (not win-rate–first) |
| Signal / live | `backend/server/scalp_bot/signal_engine.py` | Per-mode live entries; unknown `strategy_mode` is explicit error + `None` (no EMA fall-through; 2026-04-16) |
| Dashboard WS | `backend/server/ws_server.py` | Snapshots, scalp actions |

## Recent session (2026-04-27) — First live session: crash night, zero entries, architecture review

### What happened
- Bot went live ~22:54 UTC on 2026-04-26. Ran until ~07:32 UTC on 2026-04-27 (~8.5 hours).
- **Zero entries taken** the entire session. Bot functioned correctly per its design — but design has structural gaps.
- **Major crypto crash** started ~01:05 UTC (BTC/SOL/XRP RSI lows: ~15). Two crash legs total (~01:05 and ~07:10 UTC).
- Post-crash recovery stalled: BTC peaked at RSI 63.0 (~05:55 UTC) then drifted back down.

### Why zero entries
1. **Pre-crash (~22:54–01:05 UTC):** CHOP filter (CHOP ≥ 38.2) blocked sar_chop — market was ranging. Correct behavior.
2. **During crash (01:05–05:06 UTC):** sar_chop CHOP filter continued to block; ema_momentum/ema_scalp modes blocked by ema=bear (those strategies gate on EMA cross). sar_chop shorts never fired — death cross gate (MA50≤MA200) requires days of downtrend on 5m bars.
3. **Post-crash recovery (05:06–07:32 UTC):** BTC flipped ema=bull at 05:06, but sar_chop had no valid long signals (CHOP still elevated post-crash). Note: `vwap=False` shown in MONITOR logs is **display-only** — it is not an entry gate for any strategy (see Finding 3).
4. **XRP entirely blocked:** `require_champion_to_trade = true` + XRP has no WFO champion → all bootstrap signals (hull_suite, utbot_alert, macd_scalp) ignored. XRP had 3+ actionable long signals and 1 short.

### Architecture findings (critical — act on these next session)

**Finding 1: sar_chop shorts require death cross (effectively never fires)**
- `shorts_enabled = true` is already set in config.toml:342 — infrastructure is there
- BUT `sar_chop_short_setup` requires `MA(50) <= MA(200)` on 5-min bars — that's a death cross (days of downtrend to develop)
- We never saw a single `SIGNAL [sar_chop] BTC_USD: short` in the logs
- Bootstrap strategies (hull_suite, utbot_alert) DO fire short signals and were directionally correct during the crash — but blocked by require_champion_to_trade on XRP

**Finding 2: WFO champions are sticky in crash environments**
- WFO IS multi-strategy (tests all 11 modes in `WFO_REGISTERED_STRATEGY_MODES`)
- But when WFO returns `no_candidates_after_stability_filters` (all three pairs, all session), the old champion (sar_chop, set pre-crash) stays active indefinitely
- Paradox: bootstrap picks the best recent strategy adaptively, but `require_champion_to_trade` blocks it. WFO would promote a better champion if it could, but crash environment makes everything unstable.

**Finding 3: vwap=False in MONITOR is display-only — NOT an entry gate**
- `vwap_bullish` (shown as `vwap=True/False` in MONITOR logs) is computed in `indicators.py`
  and surfaced in WS snapshots and the MONITOR log. It is **never read by `signal_engine.py`**
  and does not gate entries for any of the 11 strategy modes. Grep `signal_engine.py` for
  "vwap" — zero hits.
- The actual silent blocker post-crash was the **CHOP filter** (CHOP ≥ threshold) preventing
  sar_chop from firing longs, combined with `require_champion_to_trade = true` blocking all
  bootstrap strategies on XRP.

### Decisions needed next session
1. **`require_champion_to_trade = false`** ✅ DONE — lets bootstrap trade while WFO searches.
2. **Fix sar_chop short conditions** — remove/replace the `MA(50) <= MA(200)` death cross gate for shorts. Replace with `close < MA(50)` (keep existing `MACD < 0` condition). Needs backtesting first via `sar_chop_signal_dump`.
3. ~~**VWAP gate**~~ — **does not exist**. `vwap_bullish` is display-only (see Finding 3). No code change needed.
4. **WFO champion demotion on staleness** ✅ DONE — `wfo_no_candidates_demotion_passes = 5` in config; after 5 consecutive no_candidates passes, active wfo_champion is demoted to bootstrap.

### Config changes made this session
- `config.toml:381`: `wfo_min_holdout_trades = 1` (lowered from 3 — still not enough for XRP champion)

### Log files preserved
- `data/server_20260427_session.log` — full session log (8 MB, ~9 hours)
- `data/scalp_champion.json` / `data/scalp_champion_20260427.json` — BTC+SOL on sar_chop, XRP no champion
- `data/scalp_tuner_state_20260427.json` — ParamTuner state at session end

### WFO state at session end
- BTC (BIP-20DEC30-CDE): champion=sar_chop (from prior session), WFO returning no_candidates all session
- SOL (SLP-20DEC30-CDE): champion=sar_chop (from prior session), same
- XRP (XPP-20DEC30-CDE): no champion, bootstrap only (macd_scalp at session end)
- All three pairs: 21 windows, min_window_fraction=0.35 (needs 7/21), consistently getting 5-6/21

### Bootstrap state at session end (07:32 UTC)
- BTC: daviddtech_scalp -0.4172% (2h) — all negative, crash candles in window
- SOL: qqe_mod -0.2854% (2h) — all negative
- XRP: macd_scalp (2h) — rotating

### Market state at session end (07:32 UTC)
- BTC: RSI 57.2, ema=UP (bouncing from leg 2 low of 42.6), vwap=False (display only)
- SOL: RSI 45.8, ema=bear, vwap=False (display only)
- XRP: RSI 43.5, ema=bear, vwap=False (display only)

### How to review the session logs
```powershell
# Full session log
cat data/server_20260427_session.log | grep -E "SIGNAL|ENTRY|SKIP|champion|no_candidates" | head -100

# WFO passes
grep "no_candidates\|champion=" data/server_20260427_session.log

# All signals fired (most were skipped)
grep "SIGNAL" data/server_20260427_session.log

# EMA trend timeline (vwap= in MONITOR is display-only, not a gate)
grep "MONITOR" data/server_20260427_session.log | grep -v "ema=bear"
```

## Recent session (2026-04-21 PM) — CDE `list_orders`, WFO gates, protective stops

- **`coinbase_order_manager.py`:** For **`venue = coinbase_perps`**, Coinbase CDE rejects **`order_status`** filters (OPEN, PENDING, and FILLED-only fetches). **`_list_orders_merged`** with an **empty** `statuses` tuple calls `list_orders` **without** `order_status`, then drops terminal rows via **`_CDE_CLOSED_STATUSES`**. **`cde_search_include_filled`** (used by **`_find_order_by_client_id`**) keeps **FILLED** rows while still skipping cancelled / expired / failed. **`cancel_all_scalp_open_orders`**, startup cancel sweep, **`_fetch_open_orders_scalp`**, and **`_fetch_all_open_orders`** all use the same perp path.
- **`config.toml`:** Stricter WFO / entry gating — e.g. **`wfo_allow_promotion_relaxation = false`**, **`wfo_min_trades`** and **`wfo_min_holdout_trades` = 3**, **`wfo_min_mean_score = 0`**, **`wfo_require_positive_holdout = true`**, **`require_champion_to_trade = true`**. Expect **no new entries** until WFO promotes a champion that passes the gates; **`data/scalp_champion.json`** may have been cleared in-session — let WFO repopulate or restore from backup if needed.
- **`scalp_vec_backtest.py` (`sar_chop` grid):** **`sar_chop_chop_period`** dimension **`(10, 14)`** (TV-style 10 vs default 14), Lucid SAR hardcoded **`True`** (grid row count unchanged), **`atr_stop_mult`** floor **1.5** (was 1.0) to reduce CDE stop preview rejects.
- **`scalp_trader.py`:** Protective resting stop first clamp uses **`epsilon_bps=30`**; **one retry** with **`epsilon_bps=100`** before logging failure and protective market fallback.
- **`scalp_wfo.py` + `frontend-new`:** **`wfo_mode_scoreboard`** on WFO diagnostics, **`last_wfo_pass.pairs[]`**, and champion JSON; **`last_wfo_pass.objective`**; Analytics **WFO_MODE_SCOREBOARD** panel (mean holdout score per strategy mode, champion row marked).

## Recent session (2026-04-21) — `sar_chop` diagnostics + signal dump

- **`scalp_vec_backtest.py`:** `sar_chop` path refactored into **`_sar_chop_common_mats`** / **`_sar_chop_fill_masks`** (same signal behavior); new **`sar_chop_diagnostic_frame(...)`** returns warmup, CHOP threshold, entry masks, and diagnostic arrays for export or TV comparison.
- **`sar_chop_signal_dump.py`:** operator CLI (run **`cd backend/server`** then **`python -m scalp_bot.sar_chop_signal_dump`**). Defaults align with CDE-style naming (`--symbol`, `--interval`). Supports **`--last-n-days`**, **`--trim-anchor`**, **`--since`/`--until`**, **`--include-flips`**, **`--max-rows`**, **`--csv`**, **`--no-lucid`**, **`--no-utbot`**. Requires Parquet under **`data/coinbase_bars/`**; backfill first if missing.
- **Bugfix:** `--since` / `--until` now mask **`open`** the same as OHLC (was indexing full-length `bars["open"]` against filtered rows).
- **Cursor skill:** **`.cursor/skills/tradingview-extract/SKILL.md`** — replicate TV script metadata / inputs via Pine facade; use with §35 when reconciling chart vs bot.
- **Tests run in-session:** `test_sar_chop_ohlc_hist_len`, `test_sar_chop_loop_start_matches_prefix`, `test_sar_chop_evaluate_does_not_raise_on_flat_bars` — all pass; **`compileall`** on touched modules clean.

## Recent session (2026-04-20) — ops UI, tracking, dev connectivity

- **Bar store / Windows:** Parquet paths use per-file locks + retries; `bar_store.notify_ui_alert` + `ScalpRuntime` wires `set_ui_alert_notifier` → `BotState.push_alert`; WFO short-span path calls `notify_ui_alert`. **`BotState._alert_loop`** set early in `DashboardServer.__init__` so worker-thread alerts use `run_coroutine_threadsafe` (not dropped before `start()`).
- **Settings → Portfolio risk:** **`scalp_operator_manual_cancel_orders`** (resting cancels only) and **`scalp_operator_manual_close_positions`** (reduce-only exits, **`user_manual_close`** reason) — no scalp halt / no standby; `scalp_runtime._flatten_all_legs_core` shared with emergency flatten; `scalp_mclose_` routed in `on_fill`. Tests: `test_emergency_flatten.py`, `test_scalp_on_fill_market_prefixes.py`.
- **CDE_RESTING empty hint (`App.tsx`):** lists distinct `product_id` counts from `exchange_open_orders_all` and mentions `exchange_open_orders_outside_pairs`.
- **Open-leg tracking:** Bot lists only **`ScalpTrader._positions`** (`open_positions` snapshot). **Race fix:** register pending **`before`** `add_order` in `try_open` (live). **Fill routing:** `_poll_fills_once` falls back to `position_by_entry(cid)` / `active_orders` when `product_id` does not map. **Chart:** `snapBarTime` anchors marker to first bar if entry is older than visible candles.
- **“Server offline” on Vite:** `frontend-new/vite.config.ts` proxy targets **`http://127.0.0.1:8080`** (not `localhost`) to match `[server].host` and avoid Windows **::1 vs IPv4** mismatch.
- **Runbook:** Full stop = `Stop-Process python` + free **5173** if Vite dev was used. Backend alone: `python -m backend.server.main` on **8080**; dev UI: `npm run dev` in `frontend-new` on **5173**.

## Recent session (2026-04-16) — `sar_chop` 11th WFO mode

- **New strategy mode `sar_chop`** wired end-to-end (decode of TV “5 min bot scalper” — Parabolic SAR + Lucid SAR + MACD + MA50/MA200 + UT Bot trail + CHOP(10) <38.2). See **`lessons.md` §35** for the full entry/exit logic, decode methodology, and file list.
- **`WFO_REGISTERED_STRATEGY_MODES`** now contains **11** modes (added `sar_chop`). `build_default_grid()` contributes **1,728** `sar_chop` rows (total **3,471** — `ema_scalp`, `squeeze_momentum`, `qqe_mod`, `utbot_alert` intentionally excluded from WFO grid per docstring at line 3258–3263).
- **`evaluate_tick()` strictness parity fix (resolves outstanding #2 from 2026-04-14):** `signal_engine.py` no longer falls through to `_eval_tick_ema_momentum` for unknown modes; each mode is dispatched explicitly and an unknown mode now logs `LOG.error(...)` and returns `None` — matching `evaluate()` / `evaluate_counter()`.
- **Tests extended:** `test_registered_strategy_modes.py` now asserts the full 11-mode expected set, that `sar_chop` has grid entries, and that `evaluate_params(mode="sar_chop")` on flat bars does not crash (`6 passed in 0.86s`).
- Wiring touched: `scalp_vec_backtest.py` (detector, live bundle, ParamSet fields, grid, registry), `scalp_config.py` (14 config fields), `indicators.py` (live bundle on both paths), `signal_engine.py` (closed-bar + counter + tick eval), `scalp_wfo.py` (champion I/O), `param_tuner.py` (mode + tunables + attr_map), `strategy_lookback.py`, `ws_server.py` (valid-mode set), lab + optimization scripts.

## Recent session (2026-04-14)

### WFO “real strategy” guarantee (code)

- **`WFO_REGISTERED_STRATEGY_MODES`** in `scalp_vec_backtest.py` — canonical modes registry. As of 2026-04-16 it contains **11** modes: `daviddtech_scalp`, `ema_momentum`, `ema_scalp`, `macd_scalp`, `rsi_reversion`, `supertrend`, `squeeze_momentum`, `qqe_mod`, `utbot_alert`, `hull_suite`, `sar_chop`.
- **`evaluate_params`**: `ema_momentum` is an explicit branch; **unknown `mode` raises `ValueError`** (no silent fallback to EMA). Legacy `"auto"` still normalizes to champion fallback before dispatch.
- **`save_champion`** (`scalp_wfo.py`): refuses disk write if `result["mode"]` is not in `WFO_REGISTERED_STRATEGY_MODES`.
- **Docstring** in `scalp_vec_backtest.py`: clarifies that **win rate** = fraction of simulated **`TradeResult`** with net PnL &gt; 0 (not raw indicator direction).
- **Tests:** `backend/server/scalp_bot/test_registered_strategy_modes.py` — unknown mode raises, bad champion save raises, `build_default_grid` modes ⊆ registry, registry contains all 11 expected modes, `sar_chop` has grid entries, and `sar_chop` evaluates on flat bars without crashing. Run: `python -m pytest backend/server/scalp_bot/test_registered_strategy_modes.py -q` (with `PYTHONPATH=backend/server` if needed).

### Concepts clarified (no extra code)

- WFO always scored **full** signal + **trade simulation** per grid row; the bug was **wrong `mode` label** on unknown strings (they ran EMA backtest logic). Registry fixes that.
- **WFO** explores **~2886** discrete `ParamSet` rows (`build_default_grid`), **10 strategy families**; ranking uses **`WFOConfig.objective`** (e.g. Sharpe / expectancy), not win rate; **`min_win_rate`** is a **gate** (default 0.20).
- **Param tuner** uses the same `evaluate_params` on stored bars; **perturbation acceptance is by `total_pnl` (+ PF tiebreak)**, not maximizing win rate. Cross-mode pick uses expectancy → PF → win rate → PnL as tie-breakers.
- **Grid vs tuner:** WFO grid is finite; tuner can hit parameter values **outside** the grid — possible strength only after promotion + tuner, unless grid is expanded or a joint search is added.

### Earlier sessions (carry-forward, brief)

- **`strategy_mode = "auto"`** resolves via WFO champion + **`auto_mode_fallback`** (not implicit DaviddTech). Wiring across `scalp_mode_resolution`, runtime, WS, lab scripts; tests `test_scalp_mode_resolution.py`.
- **Fee tier snapshot** poll, in-memory bps apply, champion invalidation on drift — see `lessons.md` / README.

## Outstanding / follow-ups

1. **Persist work in git:** Worktree is still ahead of origin — **commit + push** before next session. Exclude secrets (`.env`) and large `data/*.jsonl` per `.gitignore`.
2. **~~Live vs WFO strictness parity~~ RESOLVED 2026-04-16**
3. **~~WFO vs live tick / fill model gap~~ RESOLVED 2026-04-17:** `tick_entries_enabled = false`. WFO and live now use the same bar-close fill model. Re-enable only when a tick-level backtest simulation exists.
4. **~~Grid–tuner gap~~ RESOLVED 2026-04-17:** Confirmed working by design — tuner perturbs freely within bounds, all results scored via `evaluate_params`. No constraint needed.
5. **~~Residual grep / UI~~ RESOLVED 2026-04-17:** AnalyticsTab expanded to 11 modes; stale “DaviddTech” comments cleaned from `scalp_config.py`.
6. **~~Verification sweep~~ RESOLVED 2026-04-17:** 13/13 tests pass, `compileall` clean.
7. **Windows port 10048:** Kill stray Python before restart (`AGENTS.md` runbook).

## Quick runbook

```powershell
cd C:\Users\daroo\Desktop\Repos\tradingbot-1
pip install -r backend/requirements.txt
python -m backend.server.main
```

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
python -m backend.server.main
```

```powershell
cd frontend-new
npm install
npm run dev
```

```powershell
python -m compileall backend/server
```

```powershell
cd backend/server
python -m scalp_bot.sar_chop_signal_dump --help
```

```powershell
$env:PYTHONPATH="backend/server"
python -m pytest backend/server/scalp_bot/test_registered_strategy_modes.py backend/server/scalp_bot/test_wfo_promotion_gates.py -q
```

## Last updated

**2026-04-21** — **`sar_chop_diagnostic_frame`**, **`sar_chop_signal_dump`** CLI, TV extract skill path in Architecture; **`open`** masking fix for filtered date ranges. Prior **2026-04-20** session (dashboard alerts, manual cancel/close, CDE resting copy, position fill/chart fixes, Vite proxy): operator requested **full bot shutdown** after that handoff; confirm no stray `python` / port **8080** / **5173** before next start.

Session handoff **2026-04-17** — Full Nemesis audit (3 parallel agents) + 14 findings. All Critical/High fixed:

| Fix | File | What |
|-----|------|------|
| NM-002 | `scalp_trader.py` | TP placement failure now flattens (not just logs warning) |
| NM-003 | `scalp_wfo.py` | WFO grid loop wrapped in try-except; bad rows skip instead of killing the pass |
| NM-004 | `scalp_trader.py` | Fire-and-forget cancel tasks replaced with error-capturing coroutines |
| NM-005 | `scalp_mode_resolution.py` | `auto_mode_fallback` validated against registry at call time |
| NM-006 | `scalp_wfo.py` | `save_champion` rejects NaN/inf params before disk write |
| NM-007 | `scalp_wfo.py` | `param_set_from_champion_row` rejects unregistered modes from disk |
| NM-008 | `coinbase_order_manager.py`, `state.py` | Cancel retries capped at 3; `cancel_attempt_count` field added |
| NM-001 | `coinbase_order_manager.py` | Parse-failure on order response keeps tracking entry (fill-poll recovers exchange_order_id) |
| NM-010 | `coinbase_candle_feed.py` | REST backfill on WS reconnect to patch missed bars |
| NM-012 | `scalp_trader.py`, `scalp_runtime.py` | Reversal entry now respects `require_champion_to_trade` gate |
| NM-013 | `scalp_runtime.py` | Mode locked to entry mode while position open; WFO switch deferred until flat |
| NM-014 | `scalp_runtime.py` | `_entry_pending` set prevents dual bar+tick race (latent guard) |

Also resolved all outstanding items from 2026-04-16 handoff (tick_entries disabled, AnalyticsTab 11 modes, daviddtech comments cleaned, verification clean).

Full audit report: `.audit/findings/nemesis-verified.md`

Previous: **2026-04-16** — Added `sar_chop` as 11th registered WFO mode. See `lessons.md` §35.
