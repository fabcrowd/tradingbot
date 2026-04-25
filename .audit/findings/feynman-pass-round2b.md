# Nemesis Phase 4 — Round 2 feedback (State → Feynman)

## R2-S2 expanded

**State gap:** `CoinbaseOrderManager.reconcile_scalp_intx_positions()` always runs (balance loop), but `ScalpRuntime.apply_intx_position_reconciliation()` **no-ops** when `_cfg.enabled` is False.

**Feynman:** WHY? Likely to avoid adopting / mutating legs while operator disabled strategy.

**Consequence:** If operator sets OFF and exchange closes the leg (stop hit externally, manual close), internal `ScalpPosition` can remain `open` until user re-enables and reconcile runs — or indefinitely if they never re-enable.

**Severity:** MEDIUM (accounting / UX / false risk display), not direct unauthorized trade (entries gated by enabled).

## R2-S1 + R2-S2 joint

**Feynman:** Should OFF mean “flatten” or “pause bookkeeping”? Current = pause entries only; state retained.

**Consistency:** `set_scalp_mode` sim/live resets; **off** does not — intentional asymmetry worth documenting for operators.

## Convergence

Single feedback iteration sufficient; no new coupled pair beyond enabled↔reconcile gate.
