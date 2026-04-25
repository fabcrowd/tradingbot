# Scalp vs Kraken isolation sweep

**Goal:** Ensure Kraken-specific subsystems do not silently drive or corrupt Coinbase perps scalp.

## Verdict summary

| Area | Interfering? | Notes |
|------|--------------|--------|
| **Scalp execution adapter (`scalp_exec`)** | **Was YES** | `main.py` used `live_mgr` when `coinbase_mgr` was missing, even for `venue = coinbase_perps`. **Fixed:** perps scalp no longer falls back to `LiveOrderManager`. |
| **Fill registration (boot)** | **Was YES** | `live_mgr.register_scalp_runtime` ran when Coinbase absent but Kraken live present. **Fixed:** only register Kraken manager when venue is not `coinbase_perps`. |
| **`BotRuntime.ensure_live` (paper→live)** | **Was YES** | Always called `live_mgr.register_scalp_runtime` for any enabled scalp. **Fixed:** skipped when `venue == coinbase_perps`. |
| **Dashboard `set_mode` live** | **Kraken-gated** | Still requires `config.api_key` (Kraken) to call `ensure_live()`. Coinbase-only operators may use global `paper` + `set_scalp_mode` live, or need Kraken keys present — product gap, not changed here. |
| **Candle feed** | **Conditional** | `start_candle_feed(..., venue=...)` uses Coinbase WS only when `[scalp].venue = coinbase_perps`. Default `kraken_spot` in `scalp_config.py` would start **Kraken** `CandleFeed` — config must match intent. |
| **Bar store / WFO backfill** | **Conditional** | `bar_store.set_bar_store_venue` follows `[scalp].venue`. Wrong venue → `data/kraken_bars/` vs `data/coinbase_bars/`. |
| **`BotState.risk_halted`** | **YES (shared)** | Set by spread engine (Kraken MM). Scalp **skips new entries** when `risk_halted` (`scalp_runtime.py` ~696, ~824). Not Kraken *execution*, but MM subsystem still gates scalp. |
| **`BotState.active_orders`** | **Shared** | MM and scalp both use the same dict (different `cl_ord_id` prefixes). Low cross-talk if prefixes never collide; reconcile/cancel paths are per-manager. |
| **Inventory / `sync_from_kraken`** | **Indirect** | Scalp trader does not call `InventoryManager`. Kraken sync affects MM/dashboard only, not scalp sizing. |
| **`start_book_client` (Kraken L2)** | **Parallel** | Runs after scalp start; feeds MM `BookClient`, not `scalp_bot` candles. Operational cost / noise only unless MM errors affect global state. |
| **`set_mode` paper** | **Side effect** | `ws_server` cancels **all** Kraken orders via `live_mgr.cancel_all()` when switching to paper — does **not** cancel Coinbase scalp orders (separate manager). |

## Code references (pre-fix behavior)

- **Execution fallback:** `main.py` — `scalp_exec = coinbase_mgr if coinbase_mgr is not None else live_mgr`
- **Scalp on_fill wiring:** `elif live_mgr is not None and scalp_cfg.enabled and scalp_cfg.pairs: live_mgr.register_scalp_runtime(...)`
- **Feed branch:** `scalp_bot/candle_feed.py` ~233–239 — `coinbase_perps` → `start_coinbase_candle_feed`, else Kraken `CandleFeed`
- **Config default:** `scalp_config.py` — `venue` default `kraken_spot` if key missing or invalid

## Recommendations (not all implemented)

1. **Done in repo:** Perps scalp must not use `LiveOrderManager` as execution backend.
2. **Config:** Keep `[scalp] venue = "coinbase_perps"` for Coinbase-only scalp; never rely on defaults for production.
3. **Optional:** If scalp-only operation is desired, consider binding `risk_halted` to MM only or a separate `scalp_halted` (product decision).
4. **Optional:** When switching global mode to paper, evaluate whether Coinbase resting orders should be cancelled (symmetric with Kraken `cancel_all`).

---

*Sweep date: 2026-04-07. Paired with `main.py` isolation fix.*
