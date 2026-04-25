# Nemesis Phase 3 — Round 2 (State, post-fix)

## Mutation deltas verified

| Path | Before (pass 1) | After (pass 2) |
|------|-----------------|----------------|
| Pending → CANCELLED/EXPIRED/FAILED | Pop pos + orders; reserve leak | Pop pos + orders + `_release_reserved_for_position` |
| `reset_session` | Positions clear; reserve stale | Positions clear; `_reserved_capital = 0` |
| `add_order` default id | `scalp-` hyphen | `scalp_` underscore → matches fill filter |
| `set_scalp_mode` sim/live | Reset only | Cancel pending entries; warn on open; optional INTX reconcile task |
| `set_scalp_strategy` | Arbitrary `pair_key` | Rejected if not in `sr._cfg.pairs` |

## Coupled pairs re-checked

1. **`_reserved_capital` ↔ pending cancel** — write path now includes `_release_reserved_for_position` at terminal status (**closed**).
2. **`_reserved_capital` ↔ adopt after reset** — `reset_session` zeros reserve; `adopt_intx_position_from_exchange` **new** branch does `_reserved_capital += notional` (**closed** for empty-after-reset).
3. **`_cfg.enabled` ↔ `apply_intx_position_reconciliation`** — early return when disabled leaves **gap**: exchange state can change while UI shows OFF; internal open legs not auto-reconciled (**R2-S2**).

## Partial-operation ordering

- **set_scalp_mode:** cancel pending (sequential `await`) → `reset_session` → `enabled=True` → `create_task(reconcile)` — pending cancel before reset is correct ordering.
- **Open legs:** reset clears memory first; reconcile is async — acceptable if next trading cycle respects `get_position` after task completes; risk if cycle runs in same tick before task (event loop: task scheduled after handler returns — usually next iteration).

## Targets for feedback loop

- R2-S1 + R2-S2 compound: OFF mode + stale internal state vs exchange.
