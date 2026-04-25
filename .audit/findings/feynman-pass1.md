# Nemesis Phase 2 — Feynman Pass 1 (full)

**Scope:** `backend/server` after `spread_bot_enabled` + Coinbase scalp isolation.  
**Method:** Seven categories per Nemesis skill + parallel subagent c46c8683… + parent verification.

## Category reference

1. Purpose · 2. Preconditions/assumptions · 3. Boundaries · 4. Ordering/concurrency · 5. Failure/error · 6. Trust/exposure · 7. State consistency

## SUSPECT register (verified excerpts)

| ID | File | Focus | Cat | Verdict |
|----|------|-------|-----|---------|
| S1 | `ws_server.py` | `/ws` no auth | 6 | **SUSPECT** — LAN-trust |
| S2 | `ws_server.py` | Unknown `action` silent | 5 | **LOW** — UX/abuse probe |
| S3 | `ws_server.py` | `restart_process` / `os.execv` unguarded by MM flag | 6 | **SUSPECT** |
| S4 | `ws_server.py` | `test_trade` real markets, no scalp enabled/sim check | 6 | **SUSPECT** |
| S5 | `ws_server.py` | `set_mode` live skips `ensure_live` when MM off | 2,7 | **BY DESIGN** + doc risk |
| S6 | `ws_server.py` | `set_mode` paper → `live_mgr.cancel_all()` only (not Coinbase) | 7 | **SUSPECT** |
| S7 | `ws_server.py` | `/api/bots/...` proxy SSRF class | 6 | **SUSPECT** |
| S8 | `ws_server.py` | `set_scalp_mode` live without Coinbase mgr check | 2 | **SUSPECT** |
| S9 | `ws_server.py` | `set_scalp_strategy` arbitrary `pair_key` | 3 | **SUSPECT** LOW |
| S10 | `ws_server.py` | MM `_reject_if_mm_disabled` vs `update_risk`/`reset_pnl`/`hot_reload` | 3 | **SUSPECT** — inconsistent boundary |
| C1/C2 | `coinbase_order_manager.py` | `scalp-` default id vs `scalp_` fill filter | 7 | **TRUE POSITIVE** |
| C3 | `coinbase_order_manager.py` | `create_task(on_fill)` ordering | 4 | **REVIEW** |
| M1 | `main.py` | `scalp_exec=None` when CB init fails | 7 | **TRUE POSITIVE** |
| M3 | `main.py` | No watchdog when MM off | 3 | **BY DESIGN** |
| M4 | `main.py` | Watchdog `cancel_all` via `_active_order_mgr` only | 7 | **TRUE** when MM on |

## Top 8 fed to State Pass 2

1. C2+C1 fill prefix mismatch  
2. M1+S8 live UI vs `scalp_exec None`  
3. S6 paper mode + Coinbase rests  
4. M4 watchdog cancel scope (MM-on scenario)  
5. S3+S4 restart + test_trade  
6. S5 global mode vs venue  
7. C3 fire-and-forget `on_fill`  
8. S9 strategy map pollution  

## Subagent

c46c8683-2c06-4e06-bd76-702cff69d746
