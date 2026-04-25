# N E M E S I S — Scalp + Coinbase only (pass 2)

## Scope (explicit)

- **In scope:** Coinbase Advanced Trade / INTX execution for scalp (`coinbase_order_manager.py`), `scalp_bot/*`, scalp-related WebSocket actions in `ws_server.py`, and `main.py` wiring for `ScalpRuntime` + `CoinbaseOrderManager`.
- **Out of scope for this pass:** Kraken market-making, `spread_engine`, `book_client`, `live_order_manager` **except** where `main.py` still routes scalp execution through `live_mgr` when Coinbase init fails (called out as an ops bug for a **Coinbase-only** deployment).

**Parallel subagents:** Coinbase execution (e065a66f…), `scalp_bot/` core (ee6e823e…), WS scalp surface (a21766e4…). Parent verified claims below in-tree.

---

## Attacker / adversary model (scalp-only)

1. Anyone who can reach the dashboard WebSocket can change scalp mode, strategy, concurrency, operator flows, and run `test_trade` (real market orders).
2. Malformed or partial REST/WS responses from Coinbase can desync `BotState.active_orders`, `ScalpTrader._positions`, and protectives.
3. Local filesystem write to champion/tuner JSON still shifts live tunables (unchanged from prior audit).

---

## Verified findings (TRUE POSITIVES)

### SC-001 — Default `cl_ord_id` breaks fill-poll routing (HIGH)

**Coupled pair:** Exchange fills ↔ `_poll_fills_once` filter ↔ `on_fill`

`add_order` defaults client id to **`scalp-` + uuid** (hyphen):

```366:369:c:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\coinbase_order_manager.py
    async def add_order(self, params: dict) -> str:
        """Translate Kraken-shaped scalp params into Coinbase Advanced Trade create_order."""
        client = self._ensure_client()
        cl_ord_id = params.get("cl_ord_id") or f"scalp-{uuid.uuid4().hex[:12]}"
```

Fill polling keeps only ids starting with **`scalp_`** (underscore):

```961:962:c:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\coinbase_order_manager.py
            if not str(cid).strip().lower().startswith("scalp_"):
                continue
```

`"scalp-abc".startswith("scalp_")` is **false**. Any order that relies on the default id will **not** produce fill callbacks through this poll path (other paths may partially compensate; this is still a sharp, verified inconsistency).

**Fix:** Use `scalp_` in the default id, or broaden the filter to `scalp_` OR `scalp-` (prefer one canonical prefix).

---

### SC-002 — Counter-exit fills logged as generic `scalp_` ignore (MEDIUM)

**Coupled pair:** `scalp_ctr_*` market exit ↔ `ScalpRuntime.on_fill`

Counter-reversal uses `scalp_ctr_` client ids (`scalp_trader.py` ~881+). `on_fill` handles `scalp_tstop_` / `scalp_rsi_` specially, then treats any other `scalp_*` prefix as **debug-ignore**:

```1757:1776:c:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_runtime.py
        elif cl_ord_id in (pos.stop_cl_ord_id, pos.tp_cl_ord_id):
            await self._trader.on_exit_filled(pair_key, cl_ord_id, fill_price)
        elif cl_ord_id.startswith(("scalp_tstop_", "scalp_rsi_")):
            ...
        elif cl_ord_id.startswith("scalp_"):
            LOG.debug(
                "ScalpRuntime on_fill: ignored scalp id=%s pair=%s status=%s",
                cl_ord_id[:28], pair_key, pos.status,
            )
```

`scalp_ctr_...` matches the last branch. If the position were still `open`, **`on_exit_filled` would not run** for that fill (unlike stop/tp). The awaited counter path may usually close first; this remains an ordering / race hole.

**Fix:** Treat `scalp_ctr_` like `scalp_tstop_` / `scalp_rsi_`, or route to `on_exit_filled` when `open`.

---

### SC-003 — Time-stop / RSI live exit: fire-and-forget then immediate `_close_position` (HIGH)

**Coupled pair:** Venue position ↔ `ScalpTrader._positions` / PnL / reserve

Verified in `scalp_trader.py`: live branch schedules `cancel_order` and `add_order` via `create_task`, then **synchronously** calls `_close_position` (see ~682–711 time stop; ~736–762 RSI — subagent cited). Internal book is flat before the market order is confirmed.

**Consequence:** Classic desync: exchange still has size or protectives while the bot thinks flat; PnL/reserve move on estimated close price.

**Fix:** Await cancel + market placement (or confirm via fill) before `_close_position`, or only mark pending-exit state until `on_exit_filled`.

---

### SC-004 — `sim_mode`: `_scalp_cfg` vs `_trader` (MEDIUM)

**Coupled pair:** `ScalpBotConfig.sim_mode` ↔ `ScalpTrader._sim_mode`

INTX reconcile early-returns on **`_scalp_cfg.sim_mode`** (`coinbase_order_manager.py` 1064–1065). Dashboard `set_scalp_mode` updates **`sr._trader.sim_mode`** only (`ws_server.py` 693–701). Operator can believe SIM while reconcile REST still runs per file config.

**Fix:** Keep cfg and trader flags in lockstep in `set_scalp_mode`.

---

### SC-005 — `reset_session` leaves `_reserved_capital` (MEDIUM)

```1205:1216:c:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_trader.py
    def reset_session(self) -> None:
        ...
        self._positions.clear()
```

No `_reserved_capital = 0.0`. Called from `set_scalp_mode` sim/live (`ws_server.py` 693–701).

**Fix:** Reset reserve (and any pending legs) consistently with position clears.

---

### SC-006 — Coinbase init failure → `scalp_exec = live_mgr` (HIGH for Coinbase-only ops)

```195:196:c:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\main.py
    scalp_exec = coinbase_mgr if coinbase_mgr is not None else live_mgr
    scalp_runtime = ScalpRuntime(state, scalp_cfg, scalp_exec, session_logger=session_log)
```

If `venue == coinbase_perps` but `coinbase_mgr` is `None`, scalp still attaches **`live_mgr`** (Kraken-shaped `LiveOrderManager`) when present. For a **Coinbase-only** deployment this is the wrong execution adapter and shares `BotState.active_orders` with a different semantic.

**Fix:** If `scalp_cfg.venue == "coinbase_perps"` and `coinbase_mgr is None`, bind `paper_mgr` or fail fast with a clear log — do not silently fall back to `live_mgr`.

---

### SC-007 — Daily loss limit is entry-only (LOW–MEDIUM)

`try_open` returns `False` when daily loss exceeded; no flatten, no halt of open Coinbase risk (`scalp_trader.py` ~424–431). Intended or not, it is a **state policy** gap vs “halt for the day” wording in the log message.

---

### SC-008 — WS scalp control surface (conditional CRITICAL)

Unauthenticated `/ws` can set scalp live/sim, operator flows, and **`test_trade`** (market 1-lot BUY+SELL) (`ws_server.py` ~687–815). Severity = **CRITICAL** if the server is reachable off-host without a proxy auth; **LOW** on localhost-only.

Additional gaps verified:

- **`set_scalp_strategy`:** arbitrary `pair_key` (`ws_server.py` 712–724).
- **`set_scalp_max_concurrent_positions`:** no `config` broadcast after change (726–746).

---

## Nemesis loop note (Feynman × State)

- **SC-003** (ordering) + **SC-002** / **SC-001** (fill routing) compound: internal flat + missed or misclassified fills delays or prevents reconciliation from correcting ledger in one pass.

## False positives rejected

- “Scalp ignores `risk_halted` for reconcile” — **by design** for ghost flatten; entries respect halt in `scalp_runtime` (~824–825). Not scored as a bug here.

## Summary

For **Coinbase-only scalp**, the highest-impact verified issues are **SC-001** (prefix mismatch on fills), **SC-003** (time/RSI exit ordering), **SC-006** (wrong exec fallback), and **exposed unauthenticated WS** (**SC-008**). Configuration / session issues **SC-004** and **SC-005** remain medium operational risk.

---

*Pass date: 2026-04-07. Method: 3 parallel explore subagents + parent grep/read verification.*
