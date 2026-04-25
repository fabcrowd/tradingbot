# Nemesis Phase 4 — Feedback loop iteration 1 (State → Feynman)

## Step A: State gaps → Feynman re-interrogation

| Gap | Question | Answer |
|-----|----------|--------|
| Pending poll removes position but not reserve | Why no `_release_reserved`? | Reserve tied to `_close_position` / successful open lifecycle; terminal entry path is a **shortcut** that mirrors pop-only cleanup. |
| `reset_session` vs venue | Why no `cancel_all` Coinbase? | Session reset is **in-process** only; WS handler does not orchestrate venue flatten. **Assumption:** operator flattens or restarts. |
| `test_trade` uses `test-` prefix | Why not `scalp_`? | Test harness id; fills **skipped** by `scalp_` filter — relies on order-status poll / unrelated paths. **Inconsistent observability.** |

## Step B: Feynman → expanded pairs

- **Pair:** `_seen_fill_keys` ↔ fill poll prefix — any non-`scalp_` client id is invisible to `_poll_fills_once` primary path.  
- **Pair:** `hot_reload_config` ↔ `mm_spread_bot_enabled` — disk can change `spread_bot_enabled` without updating `BotState.mm_spread_bot_enabled` until restart.

## Step C: Masking joint check

- `on_fill` branches for `pending`/`open` early-returns **mask** duplicate exchange events — intentional idempotency, not broken invariant.  
- `max(0, reserved - notional)` in `_close_position` **masks** reserve underflow — signals prior desync.

## Step D: Convergence

New unique item vs Pass 2: **test_trade client id** fill-path gap → folded into NM-011 in verified report. No further loop iteration required (diminishing returns).
