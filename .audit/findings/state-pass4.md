# Nemesis Phase 4 — Feedback loop iteration 2 (Feynman → State)

## Step B expansion: ordering compound

**Feynman:** time-stop fires `create_task` then `_close_position`.  
**State:** `_reserved_capital` reduced in `_close_position` while market order may not fill → reserve and true margin can disagree until next entry or reconcile.

**Feynman:** `set_scalp_mode` sim calls `reset_session`.  
**State:** `_reserved_capital` stale + `active_orders` may still list Coinbase rows → `free_capital` wrong and **double-stack** risk if new entries placed.

## Step D: Convergence

Second iteration did not introduce a new severity class beyond NM-003/NM-005/NM-012 in `nemesis-verified.md`. **Converged** after 2 loop steps (within skill max 3).
