# nextsession.md

## Purpose

One-page orientation for the next agent or chat: product, where code lives, what changed most recently, and what is still unverified or unbuilt. **Read this first**, then `AGENTS.md`, `config.toml` `[scalp]`, and **`lessons.md` → Current Lessons**. Do not use this file as a full README replacement.

---

## What this repository is

**Primary (active):** Scalp bot on **Coinbase Derivatives Exchange (CDE)** — `[scalp]` in `config.toml`, `ScalpRuntime`, `coinbase_order_manager.py`, bar store, WFO, param tuner. Entry: `python -m backend.server.main`. Dashboard HTTP/WS: **`[server]`** host/port (often `http://127.0.0.1:8080`).

**Frontend:** `frontend-new/` (Vite + React), not legacy `frontend/`.

**Shuttered:** Kraken spot MM — dormant; scalp is the live path.

**Secrets:** `.env` only (see `.env.example` for Coinbase CDP key + PEM formatting). Never paste keys into docs.

---

## Architecture

| Subsystem | Path | Role |
|---|---|---|
| Scalp coordinator | `backend/server/scalp_bot/scalp_runtime.py` | Feed, warmup, WFO, tuner, snapshots |
| Mode resolution (`auto`) | `backend/server/scalp_bot/scalp_mode_resolution.py` | Champion mode when present; else `auto_mode_fallback` |
| Scalp config | `backend/server/scalp_bot/scalp_config.py` | `[scalp]` dataclass, all runtime config fields |
| Coinbase execution | `backend/server/coinbase_order_manager.py` | Orders, balances |
| Bar store | `backend/server/scalp_bot/bar_store.py`, `data/coinbase_bars/` | Parquet history |
| WFO | `backend/server/scalp_bot/scalp_wfo.py` | Walk-forward champion; `save_champion` validates `mode` |
| Vector backtest | `backend/server/scalp_bot/scalp_vec_backtest.py` | `evaluate_params`, `build_default_grid`, `WFO_REGISTERED_STRATEGY_MODES` |
| Regime risk-on | `backend/server/scalp_bot/regime_risk.py` | Trigger detection: volume spike, ATR move, RSI extreme, news |
| News calendar | `backend/server/scalp_bot/news_calendar.py` | Forex Factory JSON feed; `upcoming_events()`; refreshed hourly |
| Param tuner | `backend/server/scalp_bot/param_tuner.py` | Local perturbations; flat-weighted `evaluate_params` |
| Signal / live | `backend/server/scalp_bot/signal_engine.py` | Per-mode live entries |
| Strategy lookback | `backend/server/scalp_bot/strategy_lookback.py` | UI Analytics + no-champion bootstrap scoring |
| Dashboard WS | `backend/server/ws_server.py` | Snapshots, scalp actions |

---

## Recent session (2026-09-19) — GitHub sync + June 5 no-champion WFO notes

Local `main` is synced to GitHub (`0fbaad3` on `origin/main`, then this notes commit). There was **no pending code diff** beyond the June 5 runtime artifacts already pushed. Origin was 3 commits ahead of the old local HEAD (WFO continuous refactor / champion gates, 2026-05-18–19); those were kept via rebase, not overwritten.

### What is on GitHub now

- June 5 session log `data/session_20260605_160437.jsonl`
- Fee snapshot still `6.5` / `7.0` bps, `$0.15`/contract, `order_type=hybrid` (timestamp refreshed)
- 10 new `wfo_champion_promotions.jsonl` rows: all `no_champion` / `insufficient_windows`
- CDE 5m parquet **unioned** with origin (kept denser May history, added ~100 newer bars through June 5)
- `data/scalp_champion.json` is `{}` (already empty on origin before this sync)

### Left local-only on purpose (gitignore)

Do **not** dump these without an explicit ask: `data/sessions/` (old MM logs), `*.log`, `frontend-new/dist/`, `.pytest_cache/`, `data/session_observer.jsonl` (~716MB), `data/learner_state_*.json`, `data/trades_paper.jsonl`, `data/sim_report.json`, `data/cost_basis.json`, `data/fill_barriers.json`.

### Findings (session `20260605_160437`, live, operator standby)

1. **Warmup cleared champions, WFO did not replace them.** `operator_begin_warmup` unlinks `scalp_champion.json`. With `scalp_auto_invalidate_champion_on_fee_change = true`, fee drift (0→6.5 maker bps, $0→$0.15/contract, `limit`→`hybrid`) also wipes rows. Result: **0/3 champions**. Runtime falls through to no-champion bootstrap / `auto_mode_fallback`. Session ended `startup_phase=primed`, `standby=true`, `can_go_live=true`.

2. **WFO loaded ~100 bars while backfill reported thousands.** Same session: `bar_backfill` `total_in_store` BTC **9986→10086**, SOL **8151→8251**, XRP **7953→8053**. First WFO pass (13s later) BTC `n_bars=1`. Later passes all three pairs `n_bars=100`, `span_h≈9–10.6`, `roll_hours=673`, `n_windows=0`, `grid_size=0`. **100 × 5m ≈ 8.3h**, which matches the old `rest_seed_candles` default (**100**; `config.toml` is now **200**). Skip: `insufficient_windows`. Do not treat this as “not enough parquet on disk” — 5m files had 8k–10k rows.

3. **That session ran windowed WFO on the May 14 local tree**, not the May 19 **continuous** WFO now on `main`. Re-check `n_bars` vs `bar_count` / parquet on the current build before changing production WFO.

4. **Parquet is gappy.** BIP 5m: ~10k–18k rows over ~1500–1900h (dense 5m would be far more). Local-only 5m files were **sparser** than origin’s May copy; rebase merge kept origin density and appended newer timestamps.

5. **Old champions (pre-wipe) were weak / relaxed-tier.** XPP/SLP negative holdout PnL, `relaxed_quarter`; BIP one-window `any_window`. `wfo_allow_promotion_relaxation = false` now, so those would not re-promote.

### Open follow-up (not done this session)

- Trace why warmup WFO `n_bars` matched REST seed instead of parquet `total_in_store` (load path, trim, or race).
- Confirm current continuous WFO on a live warmup logs `bar_count` ≈ parquet rows before trusting “Champion found 0/3”.

---

## Recent session (2026-04-28) — Scoring windows, regime risk-on overhaul, news calendar, news AI trading plan

### 1. Scoring windows aligned to 7-day flat

All lookback windows that score/rank strategies now use **7 days (168 hours), flat (no exponential decay)**:

| Setting | Was | Now |
|---|---|---|
| `config.toml` `strategy_lookback_hours` | 24h | 168h |
| `config.toml` `risk_on_bootstrap_hours` | 1h | 168h |
| `strategy_lookback.py` `NO_CHAMPION_BOOTSTRAP_HOURS` | 2h | 168h |
| `scalp_runtime.py` tuner `lookback_h` | `wfo_train + wfo_holdout` (192h) | `wfo_train_hours` (168h) |
| `param_tuner.py` recency weighting | `half_life = n_bars/3` passed to `evaluate_params` | Removed — all calls now flat (`recency_half_life_bars=0`) |

**Goal:** simulate exactly what would have happened if each strategy traded freely over the past 7 days, with no recency bias.

### 2. Regime risk-on overhaul

**New triggers (in `regime_risk.py`):**
- RSI ≤ 20 → `"rsi_oversold"` (closed bar + live path)
- RSI ≥ 80 → `"rsi_overbought"` (closed bar + live path)
- Configurable via `regime_rsi_oversold = 20.0` / `regime_rsi_overbought = 80.0` in `config.toml`

**Volume spike threshold:** `regime_volume_spike_mult` raised from 2.5× → **3.0×** (targets top ~2–3% of bars by volume — genuine high-volatility events only).

**Hold window:** `risk_on_hold_sec` raised from 120s → **3600s (1 hour)** per trigger.

**Calm-relax:** Re-enabled at **60 seconds** (`risk_on_relax_after_calm_sec = 60`). Logic: once RSI returns inside the 20–80 band AND all other triggers clear, a 60-second calm countdown ends the risk-on window early. Prevents wasting the full hour after conditions normalize.

**New execution effects while risk-on is active (`scalp_runtime.py`):**
- `risk_on_size_mult = 1.5` — 50% larger positions (`_volatility_exec_risk_mult` now checks regime risk-on)
- `risk_on_signal_cooldown_scale = 0.5` — signal + tick cooldowns halved (`_effective_signal_cooldown_sec` + `_effective_tick_signal_cooldown_sec` both apply this)
- Both scale additively on top of any volatility filter scaling already active

**New config fields added to `scalp_config.py` + `config.toml`:**
```
regime_rsi_oversold = 20.0
regime_rsi_overbought = 80.0
risk_on_size_mult = 1.5
risk_on_signal_cooldown_scale = 0.5
```

### 3. News calendar infrastructure (BUILT, wired, running)

**`backend/server/scalp_bot/news_calendar.py`** (new file):
- Fetches `nfs.faireconomy.media/ff_calendar_thisweek.json` + `nextweek.json` — same source as the toodegrees "Live Economic Calendar" TradingView indicator
- Event fields: `title`, `country`, `date`, `impact` (High/Medium/Low/Holiday), `forecast`, `previous`
- `upcoming_events(now_ts, lookahead_sec, lookbehind_sec, min_impact, currencies)` — returns events in window
- `_refresh_if_stale()` — async, runs in thread via `asyncio.to_thread`; 1h TTL, 5min error backoff
- `cache_summary()` — diagnostic dict for dashboard

**Wired into `scalp_runtime.py` 60s heartbeat:**
- `_check_news_risk_on()` — fetches upcoming High-impact USD events; fires `_apply_regime_risk_on()` on all pairs when any event is within `news_pre_event_minutes` (15 min) or just passed (`news_post_event_minutes` = 30 min lookbehind)
- Logged at most every 5 minutes per firing (rate-limited)
- Two new state vars: `_news_risk_on_last_refresh`, `_news_risk_on_last_log`

**New config fields:**
```toml
news_risk_on_enabled = true
news_pre_event_minutes = 15.0
news_post_event_minutes = 30.0
news_min_impact = "High"
news_currencies = "USD"
news_calendar_refresh_sec = 3600.0
```

### 4. News AI trading plan (PLANNED, NOT YET BUILT)

Full architectural plan agreed with operator. **Next session should implement this.** Key decisions locked:

#### Data sources
- **Forex Factory calendar** (already live) — event metadata, forecast, previous
- **Claude AI (Sonnet)** — primary direction determination; uses web search for current analyst consensus
- **Polymarket was investigated and rejected** — their search API is broken (returns unrelated results regardless of query); crypto/economic markets are stale with $0 liquidity; not reliable for per-event direction signals

#### What the AI call does
For each High-impact event entering the 60-min watch window, fire **one async Claude API call** (cached per event, not per bar):

```python
# Input to Claude:
#   1. Event title, country, impact, forecast, previous
#   2. Web search: "[event] [month year] forecast analyst consensus"
#   3. Recent bars: last 5 bar directions + Δ% 
#
# Output (JSON):
{
  "direction": "bullish" | "bearish" | "neutral",
  "confidence": 0-100,
  "reasoning": "one sentence"
}
```

Model: `claude-sonnet-4-6`. One call per event ≈ $0.01–0.05; ~6 USD events/week → negligible.

#### State machine
```
IDLE ──(event within 60 min)──► WATCHING ──(AI advice, confidence ≥ 65%, within 10 min)──► PRIMED
  ▲                                                                                             │
  │                                                                                         (T-2 min)
  └──────────────(all events > 60 min)───── POST_EVENT ◄──(SL/TP hit)──── POSITIONED ◄────────┘
```

#### Position management (operator-confirmed behavior)
| Current position | AI direction | Confidence | Action |
|---|---|---|---|
| None | bullish | ≥65% | Enter long, tight SL |
| None | bearish | ≥65% | Enter short, tight SL |
| Long | bullish | ≥65% | Keep; tighten SL to news level |
| Long | bearish | ≥65% | **Close long → open short** (reversal) |
| Short | bearish | ≥65% | Keep; tighten SL to news level |
| Short | bullish | ≥65% | **Close short → open long** (reversal) |
| Any | neutral or <65% | — | No change |

Reversals apply to **all configured pairs simultaneously** (all are USD-correlated crypto).

#### Stop-loss behavior (operator-confirmed)
- Pre-event entry: **0.4× ATR** — tight; wrong AI guess = fast cut, no waiting
- Post-event continuation: **0.6× ATR**
- TP: **1.5× ATR** pre-event / **2.0× ATR** post-event
- No separate loss cooldown after news SL — bot stays alert for post-event continuation
- Normal WFO position SL/TP logic does NOT apply during news trade

#### Planned new files
```
backend/server/scalp_bot/news_ai_advisor.py   # Claude API call, web search, per-event cache
backend/server/scalp_bot/news_trader.py        # state machine, NewsSignal, reversal logic
```

#### Planned new config fields
```toml
news_front_run_enabled = true
news_front_run_entry_minutes = 10.0      # enter N min before event
news_front_run_cutoff_minutes = 2.0      # don't enter if < 2 min (slippage risk)
news_ai_confidence_threshold = 65        # minimum to act
news_front_run_sl_atr_mult = 0.4
news_front_run_tp_atr_mult = 1.5
news_post_event_sl_atr_mult = 0.6
news_post_event_tp_atr_mult = 2.0
```

#### Dependency check needed before building
- Confirm `anthropic` Python SDK is in `backend/requirements.txt`
- Confirm `ANTHROPIC_API_KEY` is in `.env`
- Web search tool: Claude's built-in web search via `tools=[{"type": "web_search_20250305", ...}]` or use aiohttp to hit a search API separately — decide at build time

---

## Outstanding / follow-ups

1. **Build `news_ai_advisor.py`** — Claude API call with web search; cache per event ID; structured JSON output. See plan above.
2. **Build `news_trader.py`** — state machine (IDLE→WATCHING→PRIMED→POSITIONED→POST_EVENT); `NewsSignal` dataclass; reversal logic against `scalp_trader` positions.
3. **Wire news trader into `scalp_runtime.py`** — instantiate `NewsTradeManager` in `__init__`; call on bar-close path when in news window; execute reversals via existing `_open_position` / `flatten` paths.
4. **Anthropic SDK dependency** — verify `anthropic` in `backend/requirements.txt` and `ANTHROPIC_API_KEY` in `.env` before building `news_ai_advisor.py`.
5. **Verify regime risk-on compilation** — `python -m compileall backend/server/scalp_bot/regime_risk.py backend/server/scalp_bot/scalp_config.py backend/server/scalp_bot/scalp_runtime.py` (already passes; re-verify after any merge).
6. **Live test regime RSI trigger** — confirm `"rsi_oversold"` / `"rsi_overbought"` reason tags appear in logs when RSI hits extremes. Check MONITOR logs for the tag.
7. **Live test news calendar** — on next session start, confirm `news_calendar: refreshed — N events loaded` appears in logs within 60s of startup. Check `news_calendar.cache_summary()` in dashboard snapshot if wired.
8. **`sar_chop` short condition** — still outstanding from 2026-04-27: `MA(50) <= MA(200)` death cross gate effectively never fires on 5-min bars. Needs `sar_chop_signal_dump` backtest to validate a replacement (e.g. `close < MA(50)` + `MACD < 0`).
9. **Commit + push this branch** (`feat/scalp-bootstrap-wfo-demotion`) — untracked data files are fine to leave out; push the 6 modified Python/config files.
10. **Windows port 10048** — kill stray Python before restart if port conflict (`Get-Process python | Stop-Process -Force`).

---

## Quick runbook

```powershell
# Start backend
cd C:\Users\daroo\Desktop\Repos\tradingbot-1
python -m backend.server.main
```

```powershell
# Hard restart (port conflict)
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
python -m backend.server.main
```

```powershell
# Frontend dev UI
cd frontend-new
npm install
npm run dev
# → http://localhost:5173  (proxy to 127.0.0.1:8080)
```

```powershell
# Verify compile
python -m compileall backend/server
```

```powershell
# Run tests
$env:PYTHONPATH="backend/server"
python -m pytest backend/server/scalp_bot/ -q
```

```powershell
# sar_chop signal dump (from backend/server dir)
cd backend/server
python -m scalp_bot.sar_chop_signal_dump --help
```

---

## Architecture notes carried forward

- **`require_champion_to_trade = false`** — bootstrap can trade while WFO searches (set 2026-04-27)
- **`wfo_no_candidates_demotion_passes = 5`** — stale champion demoted after 5 consecutive no_candidates WFO passes
- **WFO is 11-mode** — `WFO_REGISTERED_STRATEGY_MODES` in `scalp_vec_backtest.py`; unknown mode raises `ValueError`
- **`tick_entries_enabled = false`** — bar-close only; tick entries disabled until tick-level backtest exists
- **`vwap_bullish` is display-only** — never read by `signal_engine.py`; not an entry gate (confirmed 2026-04-27)
- **CDE `list_orders`** — `order_status` filter rejected by Coinbase perps; use empty `statuses` tuple + drop terminal rows via `_CDE_CLOSED_STATUSES`
- **`backtest_fill_model = "next_open"`** — entries fill at open[i+1], not close[i]; removes look-ahead bias
- **NM-013** — mode locked to entry mode while position open; WFO champion switch deferred until flat
- **NM-014** — `_entry_pending` set prevents dual bar+tick race

---

## Last updated

**2026-09-19** — Pushed June 5 session telemetry to GitHub; documented no-champion warmup / WFO `n_bars=100` vs 8k–10k parquet backfill (see Recent session above).

**2026-04-28** — 7-day flat scoring windows; regime risk-on RSI triggers + 1-hour hold + calm-relax; risk-on position sizing (1.5×) + cooldown halving; news calendar infrastructure live; full news AI trading plan documented (build next session).
