# AGENTS.md - Fresh Agent Operating Guide

Use this document as your first-reference prompt when you inherit this repository.

## Mission

Run and improve a Kraken spread-trading bot focused on robust spread capture with
portfolio-level risk controls. Current production focus is `USDG_USDT` and `TEL_USD`.

## First 5 Minutes (Do This First)

1. Read:
   - `config.toml`
   - `backend/server/main.py`
   - `backend/server/spread_engine.py`
   - `backend/server/live_order_manager.py`
   - `backend/server/strategy_learner.py`
   - `backend/server/session_logger.py`
   - `lessons.md` (start at "Current Lessons")
2. Confirm enabled pairs in `config.toml` `[bot].enabled_pairs`.
3. Confirm risk config is portfolio-level and non-null where intended.
4. Start bot and verify clean startup logs before making changes.

## How To Run

From repo root:

```bash
python -m backend.server.main
```

Dashboard:

- `http://localhost:8080` (or host/port from `config.toml`)

## Current Strategy Baseline

- Trade mode: live
- Enabled pairs: `USDG_USDT`, `TEL_USD`
- Portfolio-level controls (not per pair):
  - `min_total_pnl_usd`
  - `daily_profit_target_usd`
  - `daily_loss_limit_usd`
  - `max_drawdown_pct`
- Momentum hold enabled:
  - `momentum_hold_sells = 2`
  - `momentum_hold_sec = 60`

## Critical Implementation Facts

### 1) Risk Halt Behavior

- Halt is global, triggered from portfolio P&L metrics.
- In `spread_engine.py`, `_tick()` must early-return when `risk_halted` is true.
- Halt path should:
  - set `risk_halted`,
  - set `risk_halt_reason`,
  - cancel all enabled pairs,
  - log exactly once (`session_logger.log_risk_halt`).

### 2) Momentum Hold Behavior

- Momentum hold suppresses BUY placement after a sell burst.
- SELL side remains active.
- Auto-exits after cooldown with no recent sell burst.
- Events are logged via `session_logger.log_momentum`.

### 3) Session Logs Are Source of Truth

Session JSONL files in `data/` are essential for diagnosing overnight behavior.
Use them to verify:

- startup sequence,
- quote placement cadence,
- fill sequence,
- learner actions,
- pain-floor changes,
- risk halts,
- momentum transitions.

## Known Pitfalls

1. **Risk spam false-stop:** If no top-of-tick halt guard, engine keeps cycling and
   repeatedly logs halt/cancels.
2. **Overtight drawdown:** Small account + tight drawdown can halt too early.
3. **Unprofitable pair at fee tier:** High-fee pairs can lose structurally even with fills.
4. **Blind cancel-replace:** Causes churn and potential order-state divergence.
5. **Cancel failure cleanup bugs:** Never drop local order state unless exchange confirms.
6. **Stale-book quoting:** Must skip quoting on stale book.
7. **Unsupported symbol subscriptions:** Adds noise and can hide true issues.

## Verification Checklist After Any Change

1. `python -m compileall backend/server`
2. Start bot and inspect logs for:
   - websocket auth/public connect OK,
   - reconciliation OK,
   - inventory sync OK,
   - engine started,
   - initial buy/sell orders placed on enabled pairs.
3. Confirm no repeated risk-halt spam.
4. Confirm session log file created and receiving events.
5. Confirm dashboard snapshot includes:
   - `risk_halted`,
   - `risk_halt_reason`,
   - active orders/fills updating.

## File Reference Map

- Core loop: `backend/server/spread_engine.py`
- Live order execution/reconciliation: `backend/server/live_order_manager.py`
- State model and snapshot payload: `backend/server/state.py`
- Config model/loading: `backend/server/config.py`, `config.toml`
- Learning logic: `backend/server/strategy_learner.py`
- Session telemetry: `backend/server/session_logger.py`
- API/dashboard server: `backend/server/ws_server.py`
- Lessons and postmortems: `lessons.md`

## Working Rules For New Agents

1. Keep risk logic portfolio-level unless explicitly asked otherwise.
2. Prefer additive, observable changes with session log hooks.
3. Do not remove protective guards (stale-book, cancel safety, reconciliation) without replacement.
4. Validate in logs, not assumptions.
5. Document every behavior-changing change in `lessons.md`.

## Handy Prompt For A Fresh Agent

Use this prompt directly:

```text
You are inheriting a Kraken spread bot. Read AGENTS.md and lessons.md first, then inspect config.toml and the backend/server core files. Confirm current enabled pairs and portfolio-level risk settings. Run the bot, verify clean startup and quote placement, and summarize health with evidence from logs/session JSONL. If you change behavior, preserve global risk halt semantics, stale-book protection, cancel safety, and session event logging.
```
