#!/usr/bin/env python3
"""Live smoke test for Coinbase Advanced Trade (CDE / INTX) + optional order-shape checks.

Read-only by default (auth, portfolios, perp positions, book).

With ``--exercise-limits``: place a single resting limit BUY far below best bid (1 contract)
  on the **first** scalp pair, then cancel — verifies ``create_order`` + ``cancel_orders``.

With ``--exercise-limits-all-pairs``: same live limit BUY + cancel for **every** ``[scalp.pairs.*]``
  (real orders; still far below market so unlikely to fill).

With ``--exercise-brackets``: sends stop-limit + TP-limit payloads matching ``CoinbaseOrderManager``
    (may be rejected when flat — still validates signing + API wiring).

With ``--preview-all-pairs``: authenticated **preview** only (no orders placed) — for each
    ``[scalp.pairs.*]`` product, runs ``preview_market_order_buy`` and ``preview_market_order_sell``
    with ``base_size=1`` (contracts). Confirms sizing/wiring for BIP/SLP/XPP-style listings.

Usage (from repo root)::

    python tools/cde_live_smoke.py
    python tools/cde_live_smoke.py --preview-all-pairs
    python tools/cde_live_smoke.py --exercise-limits
    python tools/cde_live_smoke.py --exercise-limits-all-pairs
    python tools/cde_live_smoke.py --exercise-limits --exercise-brackets
    python tools/cde_live_smoke.py --exercise-limits --exercise-brackets --visible-sec 20

Requires ``COINBASE_API_KEY`` and ``COINBASE_API_SECRET`` in ``.env`` (see ``.env.example``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

# Repo root on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.server.coinbase_order_manager import _as_plain_dict, _unwrap_response
from backend.server.config import load_config
from backend.server.scalp_bot.scalp_config import load_scalp_config


def _fail(msg: str, code: int = 1) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    raise SystemExit(code)


def _ok(msg: str) -> None:
    print(f"[OK]   {msg}")


async def _pause_visible(seconds: float, what: str) -> None:
    """Leave orders working briefly so they show up in the Coinbase UI before cancel."""
    if seconds <= 0:
        return
    _ok(
        f"PAUSE {seconds:.0f}s -- Coinbase Advanced Trade > Perpetuals > Orders: {what}",
    )
    await asyncio.sleep(seconds)


async def _exercise_off_market_limit_buy_one(
    client: object,
    *,
    pair_key: str,
    product_id: str,
    lev: str,
    margin: str,
    visible_sec: float,
    get_book,
    get_product,
) -> None:
    """Place a 1-contract limit BUY far below best bid, then cancel (real order)."""
    prod = await asyncio.to_thread(get_product, product_id)
    prod = _as_plain_dict(prod) if not isinstance(prod, dict) else prod
    price_inc = float(prod.get("price_increment") or prod.get("quote_increment") or 1)
    if price_inc <= 0:
        price_inc = 1.0

    book_raw = await asyncio.to_thread(get_book, product_id)
    br = _as_plain_dict(book_raw)
    pricebook = _as_plain_dict(br.get("pricebook")) or br
    bids = pricebook.get("bids") or []
    asks = pricebook.get("asks") or []
    if bids and not isinstance(bids[0], dict):
        bids = [_as_plain_dict(x) for x in bids]
    if asks and not isinstance(asks[0], dict):
        asks = [_as_plain_dict(x) for x in asks]
    if not bids or not asks:
        _fail(f"[{pair_key}] Empty book for {product_id}: keys={list(book_raw.keys())[:12]}")

    def _px(level) -> float:
        d = _as_plain_dict(level)
        return float(d.get("price") or d.get("p") or 0)

    best_bid = _px(bids[0])
    best_ask = _px(asks[0])
    if best_bid <= 0 or best_ask <= 0:
        _fail(f"[{pair_key}] Could not parse bid/ask for {product_id}")
    _ok(f"[{pair_key}] Book {product_id}: bid={best_bid} ask={best_ask}")

    target = best_bid * 0.85
    lim_price = round(target / price_inc) * price_inc
    if price_inc >= 1:
        lim_price_str = str(int(lim_price))
    else:
        decimals = max(0, min(8, len(str(price_inc).split(".")[-1]) if "." in str(price_inc) else 4))
        lim_price_str = f"{lim_price:.{decimals}f}".rstrip("0").rstrip(".")
    clid = f"smoke_lim_{pair_key}_{uuid.uuid4().hex[:8]}"
    _ok(
        f"[{pair_key}] Placing off-market limit BUY 1 @ {lim_price_str} "
        f"(tick={price_inc}, cl_ord_id={clid})",
    )

    def _place_lim():
        return client.create_order(
            client_order_id=clid,
            product_id=product_id,
            side="BUY",
            order_configuration={
                "limit_limit_gtc": {
                    "base_size": "1",
                    "limit_price": lim_price_str,
                    "post_only": False,
                },
            },
            leverage=lev,
            margin_type=margin,
        )

    try:
        resp = await asyncio.to_thread(_place_lim)
    except Exception as e:
        _fail(f"[{pair_key}] create_order (limit) raised: {e!s}")

    oid = _parse_create_order_id(resp)
    if not oid:
        print(_unwrap_response(resp), file=sys.stderr)
        _fail(f"[{pair_key}] create_order (limit): no order_id (see stderr dump).")
    _ok(f"[{pair_key}] Limit order accepted order_id={oid[:16]}…")
    await _pause_visible(
        float(visible_sec),
        f"[{pair_key}] OPEN limit BUY {lim_price_str} on {product_id} (client id {clid})",
    )

    def _cancel():
        client.cancel_orders(order_ids=[oid])

    try:
        await asyncio.to_thread(_cancel)
    except Exception as e:
        _fail(f"[{pair_key}] cancel_orders failed: {e!s} — manual cancel order_id={oid}")
    _ok(f"[{pair_key}] Limit order cancelled")


def _parse_create_order_id(resp: object) -> str:
    raw = _unwrap_response(resp)
    sr = raw.get("success_response") or raw.get("successResponse")
    if sr is not None:
        if isinstance(sr, dict):
            oid = sr.get("order_id") or sr.get("orderId")
        else:
            oid = getattr(sr, "order_id", None) or getattr(sr, "orderId", None)
        if oid:
            return str(oid)
    order = raw.get("order")
    if order is not None:
        od = _as_plain_dict(order)
        oid = od.get("order_id") or od.get("orderId")
        if oid:
            return str(oid)
    oid = raw.get("order_id") or raw.get("orderId")
    return str(oid) if oid else ""


async def _discover_intx_portfolio_uuid(client) -> str | None:
    def _portfolios():
        gp = getattr(client, "get_portfolios", None)
        if not callable(gp):
            return []
        raw = _unwrap_response(gp())
        return raw.get("portfolios") or []

    portfolios = await asyncio.to_thread(_portfolios)
    for p in portfolios:
        d = _as_plain_dict(p)
        uid = str(d.get("uuid") or "").strip()
        if not uid:
            continue

        def _try_list(peru: str = uid):
            lp = getattr(client, "list_perps_positions", None)
            if not callable(lp):
                raise RuntimeError("list_perps_positions missing")
            return _unwrap_response(lp(peru))

        try:
            raw = await asyncio.to_thread(_try_list)
            if isinstance(raw, dict):
                return uid
        except Exception:
            continue
    return None


async def main_async(args: argparse.Namespace) -> int:
    cfg = load_config(ROOT / "config.toml")
    if not cfg.coinbase_api_key or not cfg.coinbase_api_secret:
        _fail(
            "COINBASE_API_KEY / COINBASE_API_SECRET missing (set in .env). "
            "Cannot run live Coinbase smoke test.",
        )

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    raw_toml = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    scalp_cfg = load_scalp_config(raw_toml)
    if str(getattr(scalp_cfg, "venue", "")).lower() != "coinbase_perps":
        print(
            "[WARN] config [scalp] venue is not coinbase_perps — "
            "smoke still hits Coinbase API using first scalp pair symbol if any.",
            file=sys.stderr,
        )
    pairs = list(scalp_cfg.pairs.items())
    if not pairs:
        _fail("No [scalp.pairs.*] in config.toml — need a product_id to query book / orders.")
    _pk, p0 = pairs[0]
    product_id = str(getattr(p0, "symbol", "") or "").strip()
    if not product_id:
        _fail("First scalp pair has empty symbol / product_id.")

    try:
        from coinbase.rest import RESTClient
    except ImportError:
        _fail("Install coinbase-advanced-py: pip install coinbase-advanced-py")

    client = RESTClient(api_key=cfg.coinbase_api_key, api_secret=cfg.coinbase_api_secret)

    _ok(f"REST client created; probing portfolios (product sample: {product_id})")

    lev = str(int(max(1.0, float(scalp_cfg.max_leverage))))
    margin = str(scalp_cfg.margin_mode or "CROSS").upper()

    if getattr(args, "preview_all_pairs", False):

        def _preview_errs(resp: object) -> str | None:
            d = resp.to_dict() if hasattr(resp, "to_dict") else _as_plain_dict(_unwrap_response(resp))
            e = d.get("errs") or d.get("errors") or d.get("error_response") or d.get("errorResponse")
            if e is None:
                return None
            if isinstance(e, list) and len(e) == 0:
                return None
            return str(e)

        preview_ok = True
        for pk, pc in pairs:
            pid = str(getattr(pc, "symbol", "") or "").strip()
            if not pid:
                print(f"[FAIL] {pk}: empty symbol", file=sys.stderr)
                preview_ok = False
                continue

            def _pb():
                return client.preview_market_order_buy(
                    pid, base_size="1", leverage=lev, margin_type=margin,
                )

            def _ps():
                return client.preview_market_order_sell(
                    pid, base_size="1", leverage=lev, margin_type=margin,
                )

            for label, fn in (("BUY", _pb), ("SELL", _ps)):
                try:
                    resp = await asyncio.to_thread(fn)
                    err = _preview_errs(resp)
                    if err:
                        print(f"[FAIL] {pk} {pid} preview {label}: {err}", file=sys.stderr)
                        preview_ok = False
                    else:
                        _ok(f"{pk} {pid} preview {label}: ok")
                except Exception as e:
                    print(f"[FAIL] {pk} {pid} preview {label}: {e!s}", file=sys.stderr)
                    preview_ok = False

        if not preview_ok:
            _fail("One or more market previews failed (see above).")
        if not args.exercise_limits and not args.exercise_limits_all_pairs and not args.exercise_brackets:
            _ok("Preview-only run complete (no orders placed).")
            return 0

    def _get_book(pid: str):
        gb = getattr(client, "get_product_book", None)
        if not callable(gb):
            raise RuntimeError("get_product_book missing")
        return _unwrap_response(gb(pid))

    def _get_product(pid: str):
        gp = getattr(client, "get_product", None)
        if not callable(gp):
            return {}
        return _unwrap_response(gp(pid))

    prod = await asyncio.to_thread(_get_product, product_id)
    price_inc = float(prod.get("price_increment") or prod.get("quote_increment") or 1)
    if price_inc <= 0:
        price_inc = 1.0

    book_raw = await asyncio.to_thread(_get_book, product_id)
    br = _as_plain_dict(book_raw)
    pricebook = _as_plain_dict(br.get("pricebook")) or br
    bids = pricebook.get("bids") or []
    asks = pricebook.get("asks") or []
    # SDK may return model objects inside bids/asks
    if bids and not isinstance(bids[0], dict):
        bids = [_as_plain_dict(x) for x in bids]
    if asks and not isinstance(asks[0], dict):
        asks = [_as_plain_dict(x) for x in asks]
    if not bids or not asks:
        _fail(f"Empty book for {product_id}: keys={list(book_raw.keys())[:12]}")

    def _px(level) -> float:
        d = _as_plain_dict(level)
        return float(d.get("price") or d.get("p") or 0)

    best_bid = _px(bids[0])
    best_ask = _px(asks[0])
    if best_bid <= 0 or best_ask <= 0:
        _fail(f"Could not parse bid/ask for {product_id}")
    _ok(f"Book {product_id}: bid={best_bid} ask={best_ask} (levels {len(bids)}/{len(asks)})")

    uid: str | None = None
    positions: list = []
    try:
        uid = await _discover_intx_portfolio_uuid(client)
    except Exception as e:
        print(f"[WARN] portfolio discovery raised: {e!s}", file=sys.stderr)
        uid = None
    if not uid:
        print(
            "[WARN] Could not discover INTX portfolio uuid (get_portfolios / list_perps_positions). "
            "Common fix: CDP API key needs **portfolio view** (and derivatives) permissions.",
            file=sys.stderr,
        )
    else:
        _ok(f"INTX portfolio uuid: {uid[:8]}…")

        def _list_pos():
            return _unwrap_response(client.list_perps_positions(uid))

        try:
            pos_raw = await asyncio.to_thread(_list_pos)
            positions = pos_raw.get("positions") or []
            _ok(f"list_perps_positions: {len(positions)} row(s)")
        except Exception as e:
            print(f"[WARN] list_perps_positions failed: {e!s}", file=sys.stderr)

    if args.json:
        print(
            json.dumps(
                {
                    "product_id": product_id,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "portfolio_uuid": uid,
                    "positions_n": len(positions),
                },
                indent=2,
            )
        )

    if args.exercise_limits_all_pairs:
        for pk, pc in pairs:
            pid = str(getattr(pc, "symbol", "") or "").strip()
            if not pid:
                _fail(f"empty symbol for pair {pk!r}")
            await _exercise_off_market_limit_buy_one(
                client,
                pair_key=pk,
                product_id=pid,
                lev=lev,
                margin=margin,
                visible_sec=float(args.visible_sec),
                get_book=_get_book,
                get_product=_get_product,
            )
    elif args.exercise_limits:
        await _exercise_off_market_limit_buy_one(
            client,
            pair_key=_pk,
            product_id=product_id,
            lev=lev,
            margin=margin,
            visible_sec=float(args.visible_sec),
            get_book=_get_book,
            get_product=_get_product,
        )

    if args.exercise_brackets:
        mid = (best_bid + best_ask) / 2.0
        # Shapes match CoinbaseOrderManager.add_order for stop + TP-limit.
        def _tick(px: float) -> str:
            v = round(px / price_inc) * price_inc
            if price_inc >= 1:
                return str(int(v))
            decimals = max(0, min(8, len(str(price_inc).split(".")[-1]) if "." in str(price_inc) else 4))
            return f"{v:.{decimals}f}".rstrip("0").rstrip(".")

        stop_trig = _tick(mid * 0.9)
        stop_lim = _tick(mid * 0.899)
        tp_lim = _tick(mid * 1.1)
        base_size = "1"

        cl_stop = f"smoke_stop_{uuid.uuid4().hex[:8]}"
        _ok(f"Trying stop-limit SELL (shape check) trigger={stop_trig} limit={stop_lim} id={cl_stop}")

        def _place_stop():
            return client.create_order(
                client_order_id=cl_stop,
                product_id=product_id,
                side="SELL",
                order_configuration={
                    "stop_limit_stop_limit_gtc": {
                        "base_size": base_size,
                        "stop_price": stop_trig,
                        "limit_price": stop_lim,
                        "stop_direction": "STOP_DIRECTION_STOP_DOWN",
                    },
                },
                leverage=lev,
                margin_type=margin,
            )

        try:
            sresp = await asyncio.to_thread(_place_stop)
            soid = _parse_create_order_id(sresp)
            if soid:
                _ok(f"Stop-limit accepted order_id={soid[:16]}…")
                _ok(f"       full order_id={soid}  client_order_id={cl_stop}")
                await _pause_visible(
                    float(args.visible_sec),
                    f"look for OPEN stop-limit SELL on {product_id} (client id {cl_stop})",
                )
                await asyncio.to_thread(lambda: client.cancel_orders(order_ids=[soid]))
                _ok("Stop-limit cancelled")
            else:
                print("[INFO] stop-limit response (often error when flat):", _unwrap_response(sresp), file=sys.stderr)
                _ok("Stop-limit returned no order_id (expected when no position / margin rules)")
        except Exception as e:
            _ok(f"Stop-limit rejected or error (often OK when flat): {e!s}")

        cl_tp = f"smoke_tp_{uuid.uuid4().hex[:8]}"
        _ok(f"Trying TP-style limit SELL @ {tp_lim} id={cl_tp}")

        def _place_tp():
            return client.create_order(
                client_order_id=cl_tp,
                product_id=product_id,
                side="SELL",
                order_configuration={
                    "limit_limit_gtc": {
                        "base_size": base_size,
                        "limit_price": tp_lim,
                        "post_only": False,
                    },
                },
                leverage=lev,
                margin_type=margin,
            )

        try:
            tresp = await asyncio.to_thread(_place_tp)
            toid = _parse_create_order_id(tresp)
            if toid:
                _ok(f"TP-limit sell accepted order_id={toid[:16]}…")
                _ok(f"       full order_id={toid}  client_order_id={cl_tp}")
                await _pause_visible(
                    float(args.visible_sec),
                    f"look for OPEN limit SELL @ {tp_lim} on {product_id} (client id {cl_tp})",
                )
                await asyncio.to_thread(lambda: client.cancel_orders(order_ids=[toid]))
                _ok("TP-limit cancelled")
            else:
                print("[INFO] TP-limit response:", _unwrap_response(tresp), file=sys.stderr)
                _fail("TP-limit: no order_id (see stderr)")
        except Exception as e:
            _fail(f"TP-limit create_order raised: {e!s}")

    _ok("Smoke test finished.")
    if uid:
        return 0
    if args.exercise_limits or args.exercise_limits_all_pairs or args.exercise_brackets:
        return 0
    return 2


def main() -> None:
    ap = argparse.ArgumentParser(description="Coinbase CDE live smoke test")
    ap.add_argument(
        "--preview-all-pairs",
        action="store_true",
        help="Preview market BUY+SELL (1 contract) for every scalp pair — no orders placed",
    )
    ap.add_argument("--exercise-limits", action="store_true", help="Place+cancel one off-market limit BUY (first pair)")
    ap.add_argument(
        "--exercise-limits-all-pairs",
        action="store_true",
        help="Place+cancel off-market limit BUY (1 contract) for each scalp pair — LIVE orders",
    )
    ap.add_argument(
        "--exercise-brackets",
        action="store_true",
        help="Exercise stop-limit + TP-limit order_configuration shapes (may fail when flat)",
    )
    ap.add_argument("--json", action="store_true", help="Print brief JSON summary")
    ap.add_argument(
        "--visible-sec",
        type=float,
        default=0.0,
        metavar="N",
        help="Seconds to leave each placed order open before cancel (use ~15–30 to confirm in Coinbase UI)",
    )
    args = ap.parse_args()
    if args.exercise_brackets and not args.exercise_limits and not args.exercise_limits_all_pairs:
        print(
            "[WARN] --exercise-brackets without --exercise-limits / --exercise-limits-all-pairs "
            "— running bracket exercises only.",
            file=sys.stderr,
        )
    t0 = time.time()
    try:
        code = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        raise SystemExit(130)
    print(f"[OK]   elapsed {time.time() - t0:.2f}s")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
