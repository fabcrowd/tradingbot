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
| Vector backtest | `backend/server/scalp_bot/scalp_vec_backtest.py` | `evaluate_params`, `build_default_grid`, **`WFO_REGISTERED_STRATEGY_MODES`** |
| Param tuner | `backend/server/scalp_bot/param_tuner.py` | Local perturbations; scores via `evaluate_params`; **PnL-first** nudges (not win-rate–first) |
| Signal / live | `backend/server/scalp_bot/signal_engine.py` | Per-mode live entries (unknown mode still falls through to EMA path — see Outstanding) |
| Dashboard WS | `backend/server/ws_server.py` | Snapshots, scalp actions |

## Recent session (2026-04-14)

### WFO “real strategy” guarantee (code)

- **`WFO_REGISTERED_STRATEGY_MODES`** in `scalp_vec_backtest.py` — canonical **10** modes: `daviddtech_scalp`, `ema_momentum`, `ema_scalp`, `macd_scalp`, `rsi_reversion`, `supertrend`, `squeeze_momentum`, `qqe_mod`, `utbot_alert`, `hull_suite`.
- **`evaluate_params`**: `ema_momentum` is an explicit branch; **unknown `mode` raises `ValueError`** (no silent fallback to EMA). Legacy `"auto"` still normalizes to champion fallback before dispatch.
- **`save_champion`** (`scalp_wfo.py`): refuses disk write if `result["mode"]` is not in `WFO_REGISTERED_STRATEGY_MODES`.
- **Docstring** in `scalp_vec_backtest.py`: clarifies that **win rate** = fraction of simulated **`TradeResult`** with net PnL &gt; 0 (not raw indicator direction).
- **Tests:** `backend/server/scalp_bot/test_registered_strategy_modes.py` — unknown mode raises, bad champion save raises, `build_default_grid` modes ⊆ registry. Run: `python -m pytest backend/server/scalp_bot/test_registered_strategy_modes.py -q` (with `PYTHONPATH=backend/server` if needed).

### Concepts clarified (no extra code)

- WFO always scored **full** signal + **trade simulation** per grid row; the bug was **wrong `mode` label** on unknown strings (they ran EMA backtest logic). Registry fixes that.
- **WFO** explores **~2886** discrete `ParamSet` rows (`build_default_grid`), **10 strategy families**; ranking uses **`WFOConfig.objective`** (e.g. Sharpe / expectancy), not win rate; **`min_win_rate`** is a **gate** (default 0.20).
- **Param tuner** uses the same `evaluate_params` on stored bars; **perturbation acceptance is by `total_pnl` (+ PF tiebreak)**, not maximizing win rate. Cross-mode pick uses expectancy → PF → win rate → PnL as tie-breakers.
- **Grid vs tuner:** WFO grid is finite; tuner can hit parameter values **outside** the grid — possible strength only after promotion + tuner, unless grid is expanded or a joint search is added.

### Earlier sessions (carry-forward, brief)

- **`strategy_mode = "auto"`** resolves via WFO champion + **`auto_mode_fallback`** (not implicit DaviddTech). Wiring across `scalp_mode_resolution`, runtime, WS, lab scripts; tests `test_scalp_mode_resolution.py`.
- **Fee tier snapshot** poll, in-memory bps apply, champion invalidation on drift — see `lessons.md` / README.

## Outstanding / follow-ups

1. **Persist work in git:** Worktree was **ahead of origin** with many modified/untracked files at last check — **commit + push** (or stash) so “tomorrow” is not only this markdown file. Do not commit secrets or full `data/*.jsonl` if policy excludes them; tune `.gitignore` as needed.
2. **Live vs WFO strictness parity (optional):** `signal_engine.py` still uses a final `else` → EMA momentum for **unknown** live `mode`. Consider matching backtester behavior (log + reject or explicit list) if operators want fail-closed semantics.
3. **WFO vs live tick / fill model gap** — still in `AGENTS.md` / prior outstanding: bar backtest vs `tick_entries_enabled`; champion holdout is a loose guide unless fill models align.
4. **Grid–tuner gap:** If product goal is “never miss tuner-only optima,” consider denser `build_default_grid`, post-grid local refine inside WFO, or documented two-phase workflow.
5. **Residual grep / UI:** `daviddtech` + `auto` phrasing, `AnalyticsTab` strategy order — cosmetic unless product cares.
6. **Verification sweep:** `python -m compileall backend/server`; `pytest` on `backend/server/scalp_bot/` after large merges.
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
$env:PYTHONPATH="backend/server"
python -m pytest backend/server/scalp_bot/test_registered_strategy_modes.py backend/server/scalp_bot/test_wfo_promotion_gates.py -q
```

## Last updated

Session handoff **2026-04-14** — WFO registered strategy modes, `evaluate_params` / `save_champion` hardening, `test_registered_strategy_modes.py`, WFO vs tuner vs win-rate semantics; merged architecture + git persistence reminder.
