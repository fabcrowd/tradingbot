# Nemesis Phase 2 — Round 2 (targeted, post-fix)

**Scope delta:** `coinbase_order_manager.py` (default `cl_ord_id`, pending terminal path), `scalp_trader.py` (`reset_session`, `_release_reserved_for_position`), `ws_server.py` (`set_scalp_mode`, `test_trade`, `set_scalp_strategy`).

## Prior findings — resolution status

| Prior ID | Resolution |
|----------|------------|
| NM-001 | **FIXED** — default id is `scalp_{uuid}` (underscore) |
| NM-002 | **PARTIAL** — `test_trade` still uses `test-` ids (fills skipped by `scalp_` filter); acceptable for diagnostic orders |
| NM-003 | **MITIGATED** — pending cancels before reset; warning + `reconcile_scalp_intx_positions` task when open legs |
| NM-004 | **FIXED** — `reset_session` sets `_reserved_capital = 0.0` |
| NM-005 | **FIXED** — terminal pending calls `trader._release_reserved_for_position(pos)` |
| NM-006 | **MITIGATED** — `test_trade` requires `confirm_live` when not sim |
| NM-009 | **FIXED** — `pair_key` must be in `sr._cfg.pairs` |

## New / residual SUSPECT (Feynman)

| ID | Topic | Category | Notes |
|----|-------|----------|-------|
| R2-S1 | `set_scalp_mode` **off** — no `reset_session`, no cancel sweep | 2,7 | Positions + reserve persist; trading stops via `enabled=False` only |
| R2-S2 | `apply_intx_position_reconciliation` returns if `not _cfg.enabled` | 4,7 | While scalp OFF, ghost / exchange-flat reconciliation **does not run** even though `reconcile_scalp_intx_positions()` is still invoked from balance poll |
| R2-S3 | Mode switch: `reset_session` then `create_task(reconcile)` | 4 | Brief window: `_positions` empty, `_reserved_capital` 0, until task completes — mitigated if reconcile is fast |
| R2-S4 | `_release_reserved_for_position` vs `_close_position` notional | 3 | Pending uses signal `entry_price`; reserve added with same notional in `try_open` — **consistent** for cancel path |
| R2-S5 | `test_trade` + `confirm_live` still places `test-` orders | 6 | No strategy `on_fill`; may leave entries in `BotState.active_orders` until other cleanup |
| R2-S6 | `live_order_manager.py` still defaults `scalp-{uuid}` | 6 | Kraken / non-Coinbase path only — low priority for scalp-primary ops |

## Convergence

No additional SUSPECT beyond R2-S1–S3 warrants a third Feynman iteration after State cross-check.
