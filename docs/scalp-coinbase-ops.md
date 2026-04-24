# Scalp bot — Coinbase CDE operations

Operator notes for the Coinbase Derivatives Exchange (CDE) scalp path (`[scalp]`, `coinbase_order_manager.py`, dashboard).

## API keys and scope

- Use **Coinbase Advanced Trade / CDP** API keys with permission to **trade** configured products. Read access is used for balances, open orders, positions, and fee tier polling.
- The bot does **not** use withdrawal permissions; restrict keys accordingly.
- For perps, set **`COINBASE_INTX_PORTFOLIO_UUID`** when required so orders and `list_orders` target the correct INTX portfolio (see startup logs if resting TP/SL are missing from listings).

## Rate limits and batch cancel

- Order placement and cancels go through the shared token-bucket limiter (`rate_limit_order_per_sec` in app config).
- **`cancel_all_scalp_open_orders`** lists OPEN orders for configured scalp `product_id`s, then calls **`cancel_orders`** in batches of up to **50** exchange `order_id`s. Failures on one batch are logged; other batches may still run.

## Manual kill paths

1. **Dashboard — OFF / SIM** — `set_scalp_mode` stops live routing; SIM avoids real REST placement.
2. **Operator STANDBY** — blocks new entries; open positions still manage protective orders until flat.
3. **Exchange UI** — manual cancel of any stray brackets the bot did not track.
4. **WebSocket `scalp_emergency_stop`** — enters standby, runs the same **cancel-all resting scalp orders** helper as the daily-loss policy (optional). **Does not flatten positions** (no market close of open perp legs in v1).

Payload:

```json
{ "action": "scalp_emergency_stop", "reason": "optional_label" }
```

## Daily loss policy (optional)

When `[scalp]` sets:

- `daily_loss_enter_standby = true` — first breach per UTC day triggers operator standby (and optional UI flow).
- `daily_loss_cancel_open_orders = true` — schedules `cancel_all_scalp_open_orders` once on that breach.

Both default **false** (legacy behavior: only block new entries in `try_open`).

Session JSONL: `daily_loss_policy` and `scalp_emergency_stop` events.

## WFO promotion audit

- Append-only **`data/wfo_champion_promotions.jsonl`** — one JSON object per pair per WFO pass (outcome, fingerprints, grid size, gate reasons).
- **`data/wfo_champion_promotion_meta.json`** — last successful promotion time per symbol (cooldown gate).

Optional gates in `[scalp]` / `WFOConfig`:

- `wfo_champion_cooldown_sec` — minimum seconds between successful champion writes per symbol (`0` = off).
- `wfo_require_holdout_beat_prior` — new champion must beat prior `score` by `wfo_prior_beat_epsilon`.

## Fee calibration (Coinbase Help + `[scalp]`)

Align **authoritative** exchange pricing with what WFO, bar sim, and the param tuner assume. Coinbase’s published rates are the source of truth; `config.toml` is what persists across restarts.

### Operator checklist (before trusting sim/live PnL)

1. In **Coinbase** (signed in): open **Settings / Fees** or the public **Advanced / Derivatives** fee schedule and note **maker %**, **taker %** (per notional per leg) and any **per-contract** clearing/NFA-style fees for your products. Help center entry points (URLs change; search if needed): [Coinbase Help](https://help.coinbase.com/) → Advanced / fees / derivatives.
2. In **[`config.toml`](../config.toml) `[scalp]`**, set:
   - `fee_bps_per_leg` / `fee_bps_taker_per_leg` — half-spread **basis points per leg** matching Help (e.g. 0.065% → 6.5 bps). The bot stores **bps**, not raw decimals.
   - `fee_usd_per_contract_per_leg` — flat USD per contract per leg if your tier/product has one (round-trip uses two legs on entry + exit).
3. **`order_type`**: `limit` / `hybrid` → WFO uses maker bps by default; `market` → taker bps. With `wfo_assume_taker_fee = true`, WFO stresses **taker** fees even when live entries are often limit.
4. **Live sync (optional):** `fee_tier_volume_source = "exchange"` and `fee_tier_auto_apply_exchange_fee_rates = true` — the runtime polls Coinbase **`transaction_summary`** (futures/perps) and can **overwrite in-memory** maker/taker bps to match the returned `fee_tier`. **`config.toml` is not rewritten**; copy new numbers into TOML if you want the same values after restart.
5. After a tier change or manual edit, bump **`scalp_fee_assumption_revision`** so WFO champion fingerprints / promotion meta stay interpretable and optional invalidation (`scalp_auto_invalidate_champion_on_fee_change`) stays meaningful.

### `[scalp]` keys (fee-related)

| Key | Role |
|-----|------|
| `fee_bps_per_leg` | Maker bps per leg (limit / hybrid default for effective fee). |
| `fee_bps_taker_per_leg` | Taker bps per leg (`order_type = "market"` or `wfo_assume_taker_fee`). |
| `fee_usd_per_contract_per_leg` | Flat USD fee per contract per leg (CDE). |
| `fee_tier_volume_source` | `"exchange"` (poll API) vs `"manual"` (baseline only). |
| `fee_tier_poll_interval_sec` | Min seconds between automatic polls (default 900). |
| `fee_tier_auto_apply_exchange_fee_rates` | When true + exchange source, apply parsed maker/taker from API to in-memory config. |
| `scalp_fee_assumption_revision` | Integer bump when you intentionally change fee assumptions. |
| `scalp_auto_invalidate_champion_on_fee_change` | Clear champion rows when applied rates change (if enabled). |

### Session JSONL (audit)

- **`scalp_fee_tier_refresh`** — every successful **automatic** poll (`trigger=auto_poll`) and every **dashboard “Refresh fee tier”** attempt (`trigger=manual`): `success`, `detail`, `maker_bps` / `taker_bps`, optional `prev_*`, `rates_changed`, `total_volume_30d_usd`, `exchange_pricing_tier` when the API returns them.
- **`exchange_fee_rates_applied`** — emitted only when a poll **changes** maker/taker bps in memory (see `scalp_runtime._apply_exchange_fee_rates_from_summary`).

API surface used for polling: Coinbase Advanced Trade **`get_transaction_summary`** (wrapped in `CoinbaseOrderManager.fetch_futures_transaction_summary`). Reference: [Coinbase Developer Platform — Advanced Trade API](https://docs.cdp.coinbase.com/) (search for transaction summary / fee tier).

## Optional watchdog (out of band)

The dashboard exposes **`GET /health`** (`{"ok": true, "service": "tradingbot-dashboard"}`). For unattended runs, use an external watchdog (cron, Windows Task Scheduler, or a small monitor) to alert if HTTP health fails or the process exits — not implemented inside the bot core.
