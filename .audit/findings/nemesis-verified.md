# NEMESIS — Verified Findings
**Date:** 2026-04-17  
**Scope:** Full scalp bot — entry/exit logic, position state, WFO/tuner, order manager, candle feed  
**Method:** 3 parallel Explore agents (runtime, WFO/tuner, order manager) + direct source verification of all Critical/High findings

---

## Verification Summary

| ID | Title | Severity | File:Line |
|----|-------|----------|-----------|
| NM-001 | Ghost order on parse failure after successful REST | **Critical** | coinbase_order_manager.py:1055–1071 |
| NM-002 | TP placement failure — position left naked (stop only) | **Critical** | scalp_trader.py:1007–1015 |
| NM-003 | WFO grid evaluation — no try-except, one bad row kills full pass | **High** | scalp_wfo.py:637–642 |
| NM-004 | Fire-and-forget cancel tasks — ghost exchange orders on failure | **High** | scalp_trader.py:1100–1108 |
| NM-005 | `auto_mode_fallback` never validated against registry | **High** | scalp_mode_resolution.py:11–16 |
| NM-006 | `save_champion` writes no param sanity check (NaN, inv. combos) | **High** | scalp_wfo.py:408–435 |
| NM-007 | `load_champion` accepts unknown/stale modes from disk | **High** | scalp_wfo.py:1610–1658 |
| NM-008 | Uncapped cancel retry — `cancel_retry` flag is write-only | **High** | coinbase_order_manager.py:1108–1135 |
| NM-009 | Partial fill — protective orders sized for full qty, not filled qty | **High** | scalp_trader.py:500–577 |
| NM-010 | Candle feed reconnect — no REST backfill for missed bars | **Medium** | coinbase_candle_feed.py:168–220 |
| NM-011 | Daily loss limit gates entries only; open position can blow through | **Medium** | scalp_trader.py:734–741 |
| NM-012 | Reversal entry bypasses `require_champion_to_trade` gate | **Medium** | scalp_trader.py:1376–1381 |
| NM-013 | WFO mode switch mid-trade (no open-position check before switch) | **Medium** | scalp_wfo.py:1393 + scalp_mode_resolution.py:35–38 |
| NM-014 | Dual bar+tick entry race (LATENT — tick entries currently disabled) | **Critical (latent)** | scalp_runtime.py:2368–2374, 2161–2162 |

---

## Verified Findings

---

### NM-001 — Ghost order on parse failure after successful REST
**Severity:** Critical  
**File:** `coinbase_order_manager.py:1055–1071`  
**Coupled pair:** Coinbase live order (accepted) vs `active_orders` dict (entry removed)

Order is submitted to Coinbase at line 975 (`order_submitted = True` at line 1046). If `_parse_create_order_id(resp)` returns empty at line 1055 (malformed response, unexpected field names after API update), the local tracking entry is **popped** at line 1064 and the function returns `""`. But Coinbase already accepted the order — it is resting on the exchange with no local record.

**Trigger sequence:**
1. REST `create_order` succeeds, `order_submitted = True`
2. Response parse fails (field missing or renamed)
3. Line 1064: `active_orders.pop(cl_ord_id, None)` — entry removed
4. Fills arrive on exchange → bot has no entry to match → position never opened locally
5. If it's a protective order (stop/TP): untracked ghost resting on exchange, fills silently

**Fix:** Before popping, query the order by `cl_ord_id` via `list_orders` to attempt recovery of the real `exchange_order_id`. Only pop if confirmed the order was rejected by Coinbase.

---

### NM-002 — TP placement failure leaves position naked (stop only)
**Severity:** Critical  
**File:** `scalp_trader.py:1007–1015`  
**Coupled pair:** `pos.tp_cl_ord_id` (unset) vs `pos.status = "open"` (set at line 964)

Stop failure → `_flatten_live_after_protective_failure()` → ✓ guarded.  
TP failure after stop success → WARNING log only → ✗ unguarded.

```python
# Verified at scalp_trader.py:1007–1015:
tp_final = tp_ok
if not tp_ok:
    pos.tp_cl_ord_id = f"scalp_tp_{uuid.uuid4().hex[:8]}"
    tp_final = await self._place_take_profit_coinbase(pos)
    if not tp_final:
        LOG.warning("ScalpTrader %s: TP still missing after retry — position has stop only", ...)
        # ← NO flatten; position continues indefinitely
```

If price reaches TP level, nothing closes the position. Position must wait for stop to be hit.

**Fix:** Apply the same flatten logic as stop-failure. If TP cannot be placed after retry, either flatten the position or set a short-duration time-stop as a forced exit backstop.

---

### NM-003 — WFO grid evaluation: one bad row crashes entire WFO pass
**Severity:** High  
**File:** `scalp_wfo.py:637–642`

```python
for pi, params in enumerate(grid):         # 4,362 rows
    m = evaluate_params(train, params, ...) # no try-except
```

Any `ValueError` or arithmetic exception in any grid row propagates through `run_once()` and kills the entire WFO cycle. No champion is saved, no partial results. A single bad param combination (e.g., `ema_fast >= ema_slow` from a degenerate grid combination) takes down the full pass.

**Fix:** Wrap the `evaluate_params` call in `try-except Exception`, log the offending `params` with the traceback, skip that row, and continue.

---

### NM-004 — Fire-and-forget cancel tasks: ghost exchange orders
**Severity:** High  
**File:** `scalp_trader.py:1100–1108`

```python
for oid in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
    if oid:
        loop.create_task(self._live_mgr.cancel_order(oid))  # untracked
```

If `cancel_order()` raises (network error, already-filled race), the exception is silently swallowed. The position is marked closed synchronously but the resting stop/TP orders remain live on the exchange. Subsequent fills from ghost orders cause state mismatch. Compounds with NM-001: a ghost order from parse failure cannot be cancelled by this path because it was never tracked.

**Fix:** Track cancel tasks in a set. Add error callbacks or await in a background cleanup coroutine. Maintain a "pending cancel" registry that retries failed cancels on the next heartbeat.

---

### NM-005 — `auto_mode_fallback` never validated against registry
**Severity:** High  
**File:** `scalp_mode_resolution.py:11–16`

```python
def normalize_auto_mode_fallback(fallback: str | None) -> str:
    fb = str(fallback or "sar_chop").strip()
    if not fb or fb == "auto":
        return "sar_chop"
    return fb  # returns any string; no registry check
```

A typo in `config.toml` (`auto_mode_fallback = "sar_chop_v2"`) passes config load silently and surfaces as a `ValueError` inside `evaluate_params()` at runtime — either during first tuner cycle or first WFO pass.

**Fix:** Import `WFO_REGISTERED_STRATEGY_MODES` and raise `ValueError` (or fall back to `"sar_chop"` with a `LOG.error`) if the fallback string is not in the registry. Fail fast at config load, not mid-execution.

---

### NM-006 — `save_champion` writes no param sanity check
**Severity:** High  
**File:** `scalp_wfo.py:408–435`

Mode is validated against the registry (lines 414–419). Params dict is not. No checks for NaN, inf, negative periods, or invalid orderings (e.g., `ema_fast >= ema_slow`). A corrupted `evaluate_params` result (from degenerate bar data or arithmetic edge case) can be written to disk and silently applied to live config on next load.

**Fix:** Add `_validate_champion_params(result)` before the disk write — check all numeric params for NaN/inf/negative values and enforce known ordering constraints.

---

### NM-007 — `load_champion` accepts unknown/stale modes from disk
**Severity:** High  
**File:** `scalp_wfo.py:1610–1658`

`param_set_from_champion_row()` does not validate `champion_row["mode"]` against `WFO_REGISTERED_STRATEGY_MODES`. A manually edited champion file or a file from a prior code version (removed mode) is silently accepted. The invalid mode is applied to `pair_cfg` and surfaces as a `ValueError` only when `evaluate_params()` is next called (tuner or WFO).

**Fix:** At load time, check `mode` against `WFO_REGISTERED_STRATEGY_MODES`. If invalid, log an error and return `None` to force re-optimization rather than running with a poisoned config.

---

### NM-008 — Uncapped cancel retry: `cancel_retry` flag is write-only
**Severity:** High  
**File:** `coinbase_order_manager.py:1108–1135`

`order.cancel_retry = True` is set on cancel failure but never read. No code path checks this flag or enforces a retry cap. Upstream callers may retry `cancel_order()` in a loop. Each attempt consumes a rate-limiter token, potentially starving all other order operations.

**Fix:** Add `cancel_attempt_count: int = 0` to `ActiveOrder`. Increment on each attempt. After 3 consecutive failures, stop retrying, log a `LOG.error`, and raise or return a permanent-fail sentinel.

---

### NM-009 — Partial fill: protective orders sized for full qty
**Severity:** High  
**File:** `scalp_trader.py:500–577`

If a limit entry order partially fills (e.g., 50/100 contracts), `pos.qty` retains the original signal quantity (100). Protective stop and TP orders are placed for `pos.qty=100`. The bot has 50 contracts of actual exposure hedged by 100-contract protectives. When the TP executes 100 contracts, 50 are position-closing but 50 are new position-opening in the opposite direction (over-execution).

**Fix:** Update `pos.qty = fill_qty` in the fill handler before placing protective orders. If `fill_qty < threshold * signal_qty` (e.g., < 80%), cancel the entry remainder and treat the position as fully filled at the smaller size.

---

### NM-010 — Candle feed reconnect: no REST backfill for missed bars
**Severity:** Medium  
**File:** `coinbase_candle_feed.py:168–220`

On WebSocket reconnect, the feed sleeps 3s and reconnects — no REST call to backfill bars missed during the disconnect. A 5-minute outage drops one full 5m bar from the bar store permanently. WFO scores the gap as flat price; live indicator calculations run on an incomplete buffer.

**Fix:** On reconnect, call `seed_from_rest()` (or equivalent) to fetch bars from `last_bar_ts` to `now` before resuming WS consumption. Deduplicate by timestamp to prevent double-counts.

---

### NM-011 — Daily loss limit gates entries only
**Severity:** Medium  
**File:** `scalp_trader.py:734–741`

Daily loss check fires only at `try_open()`. An open position can push daily PnL beyond the configured limit. The limit is a soft new-entry gate, not a hard capital circuit-breaker. Documented design decision — but the operator dashboard should show daily PnL vs limit prominently so the operator knows when they're close to the edge.

---

### NM-012 — Reversal entry bypasses `require_champion_to_trade` gate
**Severity:** Medium  
**File:** `scalp_trader.py:1376–1381`

Bar and tick entries both check `require_champion_to_trade` before `try_open()`. The reversal path in `check_counter_signal()` checks `_entries_paused_fn` (operator standby) but does not check `require_champion_to_trade`. A counter-signal reversal can open a position while the primary entry path is blocked by the champion gate.

**Fix:** Add the same `require_champion_to_trade` check before `try_open()` in the reversal path, matching the bar/tick entry gates at `scalp_runtime.py:2363–2366`.

---

### NM-013 — WFO mode switch mid-trade
**Severity:** Medium  
**File:** `scalp_wfo.py:1393` + `scalp_mode_resolution.py:35–38`

WFO promotes a new champion every `wfo_interval_sec` (300s). `resolve_auto_mode()` immediately returns the new mode. No check for open positions. If a trade is open in mode A and WFO switches to mode B, stop/TP levels remain from mode A's logic but subsequent signal checks run mode B's evaluation.

**Fix:** In the champion promotion path, check `trader.has_any_open_position()`. If yes, defer the mode switch until all positions are flat (check on each bar-close).

---

### NM-014 — Dual bar+tick entry race (LATENT)
**Severity:** Critical (latent — currently safe because `tick_entries_enabled = false`)  
**File:** `scalp_runtime.py:2368–2374, 2161–2162`

If tick entries are re-enabled, both `_on_closed_candle()` (bar-close path) and `_evaluate_tick_entry()` (tick path) can race through `has_position() → False` before either `create_task` runs. Both tasks then execute, creating two positions for the same pair. The second position is permanently orphaned in `_positions` after the first fill reconciles, blocking all future entries for that pair.

**Fix (required before re-enabling tick entries):** Use a per-pair `asyncio.Lock` or set a per-pair "entry pending" flag synchronously before the `create_task` call. Re-check `has_position()` at the start of `_open_position()` as a final guard.

---

## Feedback Loop Discoveries

- **NM-001 + NM-004 compound:** A ghost order from parse failure (NM-001) can never be cancelled by the fire-and-forget cancel path (NM-004) because it was never tracked. It persists on the exchange until expiry or manual cancellation.

- **NM-007 + NM-005 compound:** Both an invalid champion file (NM-007) and an invalid `auto_mode_fallback` (NM-005) converge on the same `ValueError` inside `evaluate_params()` — but at different callsites and different times, making root cause harder to diagnose without these being known ahead of time.

- **NM-014 (latent) + NM-013 compound:** If tick entries were re-enabled AND WFO switches modes mid-trade, two races could fire simultaneously, making position state impossible to unwind without a hard reset.

---

## False Positives Eliminated

- **Tuner promotes worse result:** Within `tune_strategy_params()`, the gate `if m.total_pnl > best_pnl` (line 468) ensures perturbations are only accepted if they improve on the baseline. The concern about a "shorter lookback window vs WFO full history" is a documented design tradeoff, not a correctness bug.
- **Data leakage train/holdout:** Signal warmup respects WFO window boundaries. No holdout data touches training. Not a bug.
- **`wfo_min_trades` semantics:** Filtering strategies with < 3 trades is intentional for statistical significance.
- **Operator standby not closing positions:** Standby correctly halts new entries while letting existing protective exits run. Intentional design.

---

## Priority Fix Order

| Priority | ID | Why |
|----------|----|-----|
| 1 | NM-002 | Active now. TP failure leaves position with no profit target. One-liner fix matching stop-failure path. |
| 2 | NM-001 | Active now. Parse failure after successful REST creates untracked live order on exchange. |
| 3 | NM-003 | Active now. One bad grid row kills entire WFO cycle — trivial try-except fix. |
| 4 | NM-005 | Active now. Invalid `auto_mode_fallback` crashes bot at runtime, not at config load. |
| 5 | NM-007 | Active now. Corrupted/stale champion file accepted silently on startup. |
| 6 | NM-004 | Active now. Ghost exchange orders accumulate from failed fire-and-forget cancel tasks. |
| 7 | NM-008 | Active now. Cancel retry uncapped — rate limiter starvation risk. |
| 8 | NM-009 | Active now. Partial fill sizes protectives for full qty, not filled qty. |
| 9 | NM-012 | Active now. Reversal bypasses champion gate. |
| 10 | NM-013 | Active now. Mode switch mid-trade. |
| 11 | NM-006 | Active now. NaN/invalid params could corrupt champion file. |
| 12 | NM-010 | Degrades WFO quality over time. |
| 13 | NM-011 | Operational awareness gap — no code change needed, UI improvement. |
| 14 | NM-014 | Latent — safe while tick_entries_enabled=false. Fix before re-enabling. |
