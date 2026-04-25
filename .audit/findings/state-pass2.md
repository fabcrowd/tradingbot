# Nemesis Phase 3 — State Pass 2 (full, Feynman-enriched)

**Scope:** Scalp + Coinbase + WS + `main.py` post-MM-gating.  
**Sources:** Subagents add441f7… (matrix), 9330b54f… (mutation matrix) + parent reads.

## Mutation matrix (summary)

| Op | Updates A | Gap on B |
|----|-----------|----------|
| `try_open` | pending position, reserve, `active_orders` | Exchange until ack |
| `on_entry_filled` | open, stops | `_reserved_capital` not recomputed |
| `_close_position` | trader ledger | Does not pop `active_orders` |
| `check_time_stop` / `check_rsi_exit` (live) | `_close_position` sync | Venue flat lags (async cancel/market) |
| `reset_session` | clears `_positions`, history | **No** reserve zero, **no** venue cancel, **no** `active_orders` |
| Pending poll terminal | `pop` position + `active_orders` | **No** `_reserved_capital` release (see `coinbase_order_manager.py` ~733–739) |
| `add_order` | `active_orders` | Exchange truth on failure paths |

## Coupled pairs (priority)

1. `active_orders` ↔ exchange orders ↔ `ScalpTrader._positions`  
2. `_reserved_capital` ↔ pending/open legs  
3. `mm_spread_bot_enabled` ↔ `risk_halted` ↔ scalp entries  
4. `config.toml` `[bot].spread_bot_enabled` ↔ `BotState.mm_spread_bot_enabled` (stale after hot reload)  
5. Global `config.mode` ↔ PnL file ↔ scalp venue  

## Feynman-enriched targets

- **C2+C1** → confirms fill stream can miss `scalp-*` and `test-*` client ids.  
- **F5** (pending cancel) → State agent: pop without reserve release **verified** at lines 733–739.  
- **F1** → `reset_session` + `set_scalp_mode` **verified** no Coinbase cancel in `ws_server`.

## Subagents

add441f7-c09d-4149-9dff-816213c876a1, 9330b54f-3f9b-4bee-bf04-81f5967a9525
