"""Coinbase Advanced Trade order manager for scalp CDE / FCM perps (REST + fill polling)."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, NamedTuple

from .rate_limiter import RateLimiter
from .state import ActiveOrder

if TYPE_CHECKING:
    from .config import AppConfig
    from .scalp_bot.scalp_config import ScalpBotConfig
    from .scalp_bot.scalp_runtime import ScalpRuntime
    from .session_logger import SessionLogger
    from .state import BotState

LOG = logging.getLogger(__name__)


def _unwrap_response(resp: object) -> dict:
    if isinstance(resp, dict):
        return resp
    d = getattr(resp, "__dict__", None)
    if isinstance(d, dict):
        return d
    try:
        return dict(resp)  # type: ignore[arg-type]
    except Exception:
        return {}


def _as_plain_dict(obj: object) -> dict:
    """Coinbase SDK returns ``BaseResponse`` models; normalize to dict for ``.get()`` access."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    td = getattr(obj, "to_dict", None)
    if callable(td):
        try:
            out = td()
            return out if isinstance(out, dict) else {}
        except Exception:
            pass
    ud = getattr(obj, "__dict__", None)
    if isinstance(ud, dict) and ud:
        return {k: v for k, v in ud.items() if not str(k).startswith("_")}
    return {}


def _safe_float(x: object) -> float:
    if x is None or x == "":
        return 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _snapshot_prices_from_list_order(o: dict) -> tuple[float, float]:
    """Extract (limit_price, stop_or_trigger_price) from a Coinbase ``list_orders`` row.

    Advanced Trade nests prices under ``order_configuration`` (e.g. ``limit_limit_gtc``,
    ``stop_limit_stop_limit_gtc``). Top-level ``limit_price`` / ``stop_price`` are often absent,
    which made dashboard CDE_RESTING show em dashes despite valid resting orders.
    """
    lp = _safe_float(o.get("limit_price") or o.get("limitPrice") or o.get("price") or 0)
    tr = _safe_float(
        o.get("stop_price")
        or o.get("stopPrice")
        or o.get("trigger_price")
        or o.get("triggerPrice")
        or 0
    )
    if lp > 0 or tr > 0:
        return lp, tr
    oc = o.get("order_configuration") or o.get("orderConfiguration")
    if not isinstance(oc, dict):
        return 0.0, 0.0
    for inner in oc.values():
        if not isinstance(inner, dict):
            continue
        ilp = _safe_float(
            inner.get("limit_price") or inner.get("limitPrice") or inner.get("price") or 0
        )
        itr = _safe_float(
            inner.get("stop_price")
            or inner.get("stopPrice")
            or inner.get("trigger_price")
            or inner.get("triggerPrice")
            or 0
        )
        if ilp > 0 or itr > 0:
            return ilp, itr
    return 0.0, 0.0


def _scalp_fill_fee_usd(f: dict) -> float | None:
    """Best-effort absolute fee (quote / USD) from a Coinbase fill record."""
    if not f:
        return None
    for key in ("commission", "fee", "total_fees", "quote_commission", "fill_fee"):
        v = f.get(key)
        if v is None:
            continue
        if isinstance(v, dict):
            v = v.get("value", v)
        fv = _safe_float(v)
        if fv != 0.0:
            return abs(fv)
    return None


def _money_val(obj: object) -> float:
    """Coinbase balance_summary fields are often ``{value: str}`` or SDK wrappers."""
    if obj is None:
        return 0.0
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, dict):
        if "value" in obj:
            return _safe_float(obj.get("value"))
        return 0.0
    v = getattr(obj, "value", None)
    if v is not None:
        return _safe_float(v)
    return 0.0


def _balance_summary_field(bs: object, key: str) -> float:
    if bs is None:
        return 0.0
    if isinstance(bs, dict):
        return _money_val(bs.get(key))
    return _money_val(getattr(bs, key, None))


def _retry_price_precision_from_coinbase(err_msg: str) -> bool:
    """Detect venue preview rejects where busting tick cache + re-fetch may fix formatting."""
    u = (err_msg or "").upper()
    return any(
        token in u
        for token in (
            "PREVIEW_INVALID_PRICE_PRECISION",
            "INVALID_PRICE_PRECISION",
            "PREVIEW_INVALID_STOP_PRICE_PRECISION",
            "INVALID_STOP_PRICE_PRECISION",
        )
    )


def _retry_exit_without_reduce_only(err_msg: str) -> bool:
    """When reduce_only exits fail with venue-specific errors, retry without the flag.

    OpenAPI ``NewOrderFailureReason`` includes REDUCE_ONLY_NOT_ALLOWED_ON_VENUE, etc.
    """
    u = (err_msg or "").upper()
    return any(
        n in u
        for n in (
            "REDUCE_ONLY_NOT_ALLOWED",
            "PREVIEW_REDUCE_ONLY_NOT_ALLOWED",
        )
    )


def _coinbase_order_error_text(err: object) -> str:
    """Human-readable text from Coinbase ``error_response`` object or dict."""
    if err is None:
        return "unknown_error"
    d = _as_plain_dict(err)
    parts: list[str] = []
    for k in (
        "error",
        "message",
        "error_details",
        "errorDetails",
        "new_order_failure_reason",
        "new_order_failure_reason_message",
        "preview_failure_reason",
    ):
        v = d.get(k)
        if v is not None and str(v).strip():
            parts.append(f"{k}={v}")
    return "; ".join(parts) if parts else str(d)[:800]


def _mark_price_from_product(raw: dict) -> float | None:
    """Best-effort mid / index price from ``get_product`` (aligns uPnL with Advanced Trade UI)."""
    for key in ("mid_market_price", "price"):
        v = raw.get(key)
        if v is None or v == "":
            continue
        p = _safe_float(v)
        if p > 0:
            return p
    bid = _safe_float(raw.get("best_bid_price") or raw.get("best_bid") or 0)
    ask = _safe_float(raw.get("best_ask_price") or raw.get("best_ask") or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if bid > 0:
        return bid
    if ask > 0:
        return ask
    return None


def _estimated_funding_bps_per_hour_from_product(raw: object) -> float | None:
    """Map ``future_product_details.funding_rate`` to bps/hour (best-effort for alerts).

    Coinbase encodes a small decimal; we compare magnitude to ``funding_warn_bps_per_hour``.
    Returns None when missing or the value does not fit heuristics.
    """
    d = _normalize_get_product_payload(raw)
    nested = d.get("future_product_details") or d.get("futureProductDetails") or {}
    nd = _as_plain_dict(nested)
    s = nd.get("funding_rate") or nd.get("fundingRate")
    if s is None or s == "":
        return None
    try:
        v = abs(float(str(s).strip().rstrip("%")))
    except ValueError:
        return None
    if v < 1e-15:
        return None
    bps = v * 10_000.0
    if bps <= 250.0:
        return bps
    if v < 50.0:
        return min(v * 100.0, 250.0)
    return None


def _normalize_get_product_payload(raw: object) -> dict:
    """Unwrap SDK response and merge nested ``product`` (CDP sometimes nests fields there)."""
    top = dict(_unwrap_response(raw) or {})
    inner = top.get("product")
    if isinstance(inner, dict):
        top = {**top, **inner}
    return top


def _first_positive_float_in_dict(d: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        v = d.get(key)
        if v is None or v == "":
            continue
        f = _safe_float(v)
        if f > 0:
            return f
    return None


def _cde_price_increment_from_product(raw: object) -> float | None:
    """Parse order price tick from ``get_product`` (prefer ``price_increment`` over ``quote_increment``).

    When both are present, return the **coarser** (larger) tick so we do not send sub-tick decimals
    (e.g. BIP index $1 tick vs finer ``quote_increment``).
    """
    d = _normalize_get_product_payload(raw)
    fpd = d.get("future_product_details") or d.get("futureProductDetails")
    fpd_d = _as_plain_dict(fpd) if fpd else {}

    price_keys = ("price_increment", "priceIncrement")
    quote_keys = ("quote_increment", "quoteIncrement")

    price_candidates: list[float] = []
    for src in (d, fpd_d):
        p = _first_positive_float_in_dict(src, price_keys)
        if p is not None:
            price_candidates.append(p)
    price_inc = max(price_candidates) if price_candidates else None

    quote_candidates: list[float] = []
    for src in (d, fpd_d):
        q = _first_positive_float_in_dict(src, quote_keys)
        if q is not None:
            quote_candidates.append(q)
    quote_inc = max(quote_candidates) if quote_candidates else None

    if price_inc is not None and quote_inc is not None:
        return float(max(Decimal(str(price_inc)), Decimal(str(quote_inc))))
    if price_inc is not None:
        return float(price_inc)
    if quote_inc is not None:
        return float(quote_inc)
    return None


def _fallback_cde_price_increment(product_id: str) -> float:
    """Last-resort tick when ``get_product`` is unavailable — keeps common CDE prefixes sane."""
    u = str(product_id or "").strip().upper()
    if u.startswith("XPP"):
        return 0.0001
    if u.startswith("SLP"):
        return 0.01
    if u.startswith("BIP"):
        return 1.0
    return 0.01


def _decimal_step(inc: float) -> Decimal:
    return Decimal(str(inc))


def _quantize_decimal_price(price: float, inc: float) -> Decimal:
    """Snap ``price`` to the nearest multiple of ``inc`` (half-up), using ``Decimal`` (no float drift)."""
    if inc <= 0:
        return Decimal(str(price))
    step = _decimal_step(inc)
    p = Decimal(str(price))
    n = (p / step).to_integral_value(rounding=ROUND_HALF_UP)
    return n * step


def _format_decimal_cde_order_price(q: Decimal, inc: float) -> str:
    """Format a Decimal already on-tick for REST (no scientific notation, no extra decimals)."""
    step = _decimal_step(inc)
    q2 = q.quantize(step, rounding=ROUND_HALF_UP)
    if inc >= 1:
        return str(int(q2))
    s = format(q2, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _format_cde_order_price(price: float, inc: float) -> str:
    """Quantize to venue tick and format for REST (no scientific notation, no extra decimals)."""
    q = _quantize_decimal_price(price, inc)
    return _format_decimal_cde_order_price(q, inc)


def _cde_stop_trigger_limit_strings(
    side: str,
    trigger: float,
    limit: float,
    inc: float,
) -> tuple[str, str]:
    """Quantize stop-limit prices; keep limit on the correct side of trigger for preview."""
    step = _decimal_step(inc)
    trig_d = _quantize_decimal_price(trigger, inc)
    lim_d = _quantize_decimal_price(limit, inc)
    if side == "SELL":
        if lim_d >= trig_d:
            lim_d = trig_d - step
    else:
        if lim_d <= trig_d:
            lim_d = trig_d + step
    return _format_decimal_cde_order_price(trig_d, inc), _format_decimal_cde_order_price(lim_d, inc)


def _build_coinbase_scalp_order_configurations(
    *,
    order_type: str,
    side: str,
    base_size: str,
    is_perp: bool,
    cde_inc: float | None,
    params: dict,
) -> list[dict]:
    """Build ``order_configuration`` list(s) for ``create_order`` (stop/TP may emit two variants)."""
    configurations: list[dict] = []

    if order_type == "market":
        base_inner: dict = {"base_size": base_size}
        if is_perp and params.get("reduce_only"):
            configurations.append({"market_market_ioc": {**base_inner, "reduce_only": True}})
            configurations.append({"market_market_ioc": dict(base_inner)})
        else:
            configurations.append({"market_market_ioc": base_inner})
    elif order_type in ("limit",):
        lp = params.get("limit_price")
        if lp is None:
            raise RuntimeError("limit_price required for limit order")
        if cde_inc is not None and cde_inc > 0:
            lim_s = _format_cde_order_price(float(lp), cde_inc)
        else:
            lim_s = str(round(float(lp), 5))
        configurations.append({
            "limit_limit_gtc": {
                "base_size": base_size,
                "limit_price": lim_s,
                "post_only": False,
            },
        })
    elif order_type in ("stop-loss-limit", "stop_limit"):
        trig = params.get("trigger_price") or params.get("stop_price")
        lim = params.get("limit_price")
        if trig is None or lim is None:
            raise RuntimeError("trigger_price and limit_price required for stop")
        if side == "SELL":
            direction = "STOP_DIRECTION_STOP_DOWN"
        else:
            direction = "STOP_DIRECTION_STOP_UP"
        if cde_inc is not None and cde_inc > 0:
            sp_s, lp_s = _cde_stop_trigger_limit_strings(
                side, float(trig), float(lim), cde_inc,
            )
        else:
            sp_s, lp_s = str(round(float(trig), 5)), str(round(float(lim), 5))
        stop_inner: dict = {
            "base_size": base_size,
            "stop_price": sp_s,
            "limit_price": lp_s,
            "stop_direction": direction,
        }
        if is_perp:
            configurations.append(
                {"stop_limit_stop_limit_gtc": {**stop_inner, "reduce_only": True}},
            )
            configurations.append({"stop_limit_stop_limit_gtc": copy.deepcopy(stop_inner)})
        else:
            configurations.append({"stop_limit_stop_limit_gtc": stop_inner})
    elif order_type in ("take-profit-limit",):
        lim = params.get("limit_price")
        if lim is None:
            raise RuntimeError("limit_price required for take-profit-limit")
        if cde_inc is not None and cde_inc > 0:
            lim_s = _format_cde_order_price(float(lim), cde_inc)
        else:
            lim_s = str(round(float(lim), 5))
        tp_inner: dict = {
            "base_size": base_size,
            "limit_price": lim_s,
            "post_only": False,
        }
        if is_perp:
            configurations.append(
                {"limit_limit_gtc": {**tp_inner, "reduce_only": True}},
            )
            configurations.append({"limit_limit_gtc": copy.deepcopy(tp_inner)})
        else:
            configurations.append({"limit_limit_gtc": tp_inner})
    else:
        raise RuntimeError(f"Unsupported Coinbase order_type {order_type!r}")
    return configurations


def _fcm_cde_order_base_size(order_qty_contracts: float) -> str:
    """Return ``base_size`` for Coinbase FCM / CDE futures ``create_order``.

    ``get_public_product`` for nano listings (e.g. ``BIP-*-CDE``) reports ``base_min_size`` and
    ``base_increment`` of **1** with ``future_product_details.contract_size`` describing underlying
    per contract (e.g. 0.01 BTC). The order API expects ``base_size`` in **whole contracts**, not
    BTC notional — sending ``\"0.01\"`` for one contract triggers ``PREVIEW_INVALID_BASE_SIZE_TOO_SMALL``
    because the venue minimum is **1** (contract), not 0.01.
    """
    n = max(1, int(round(float(order_qty_contracts or 0.0))))
    return str(n)


class ListOrdersMergeResult(NamedTuple):
    orders: list[dict]
    merge_ok: bool
    failed_statuses: tuple[str, ...]


_CDE_CLOSED_STATUSES = frozenset({"FILLED", "CANCELLED", "EXPIRED", "FAILED", "UNKNOWN_ORDER_STATUS"})
# When listing without ``order_status`` for client-id search, keep FILLED rows; drop only hard-dead states.
_CDE_SEARCH_DROP_STATUSES = frozenset({"CANCELLED", "EXPIRED", "FAILED"})


def _list_orders_merged(
    lo,
    base_kwargs: dict,
    statuses: tuple[str, ...],
    *,
    cde_search_include_filled: bool = False,
) -> ListOrdersMergeResult:
    """Advanced Trade rejects ``order_status=[OPEN, PENDING]`` — fetch each status and merge by order_id.

    If ``statuses`` is empty (CDE perps mode), call without any ``order_status`` filter and filter
    out clearly-closed orders client-side (CDE rejects active-status query parameters entirely).

    When ``cde_search_include_filled`` is true (only meaningful with empty ``statuses``), keep FILLED
    orders so callers can resolve a ``client_order_id`` against recent history.
    """
    by_oid: dict[str, dict] = {}
    failed: list[str] = []

    if not statuses:
        # CDE perps: order_status filter not supported — fetch all, keep only non-closed
        try:
            resp = lo(**base_kwargs)
            raw = _unwrap_response(resp)
            orders = raw.get("orders") or raw.get("order") or []
            if isinstance(orders, dict):
                orders = [orders]
            for o in orders:
                od = _as_plain_dict(o)
                if not od:
                    continue
                st = str(od.get("status") or od.get("order_status") or "").upper()
                if cde_search_include_filled:
                    if st in _CDE_SEARCH_DROP_STATUSES:
                        continue
                elif st in _CDE_CLOSED_STATUSES:
                    continue
                oid = str(od.get("order_id") or od.get("orderId") or "")
                if oid:
                    by_oid[oid] = od
        except Exception:
            LOG.debug("list_orders merge: no-status-filter fetch failed", exc_info=True)
            return ListOrdersMergeResult([], False, ("(no-filter)",))
        return ListOrdersMergeResult(list(by_oid.values()), True, ())

    for st in statuses:
        kw = {**base_kwargs, "order_status": st}
        try:
            resp = lo(**kw)
            raw = _unwrap_response(resp)
            orders = raw.get("orders") or raw.get("order") or []
            if isinstance(orders, dict):
                orders = [orders]
            for o in orders:
                od = _as_plain_dict(o)
                if not od:
                    continue
                oid = str(od.get("order_id") or od.get("orderId") or "")
                if oid:
                    by_oid[oid] = od
        except Exception:
            failed.append(st)
            LOG.debug("list_orders merge: status=%s failed", st, exc_info=True)
    if len(failed) == len(statuses) and statuses:
        LOG.warning(
            "list_orders merge: all %d status fetch(es) failed (%s) — snapshot/cancel may be incomplete",
            len(statuses),
            ", ".join(failed),
        )
    merge_ok = len(failed) < len(statuses) if statuses else True
    return ListOrdersMergeResult(list(by_oid.values()), merge_ok, tuple(failed))


class CoinbaseOrderManager:
    """Authenticated REST client for Coinbase Advanced Trade + scalp fill routing."""

    def __init__(
        self,
        state: "BotState",
        app_config: "AppConfig",
        scalp_config: "ScalpBotConfig",
        session_logger: "SessionLogger | None" = None,
    ) -> None:
        self._state = state
        self._app_config = app_config
        self._scalp_cfg = scalp_config
        self._session_logger = session_logger
        self._client = None
        self._scalp_runtime: "ScalpRuntime | None" = None
        self._product_to_key: dict[str, str] = {}
        self._seen_fill_keys: set[str] = set()
        self._fill_poll_logged: bool = False
        self._poll_task: asyncio.Task | None = None
        self._protective_poll_cycle: int = 0  # throttle stop/TP status checks
        self._balance_task: asyncio.Task | None = None
        self._balances: dict = {}
        self._stop = asyncio.Event()
        self._balance_poll_cycles: int = 0
        self._last_scalp_open_orders: list[dict] = []
        self._last_all_open_orders: list[dict] = []
        self._last_outside_open_orders_sig: str = ""
        self._exchange_orders_refresh_tick: int = 0
        self._cde_price_increment_cache: dict[str, float] = {}
        self._cde_tick_info_logged: set[str] = set()
        self._funding_warn_last: dict[str, float] = {}
        self._futures_summary_ok_ts: float = 0.0
        self._balance_poll_failures: int = 0
        self._fill_poll_failures: int = 0
        self._limiter = RateLimiter(
            rate_per_sec=max(0.5, float(app_config.rate_limit_order_per_sec)),
            burst=max(5, int(app_config.rate_limit_burst)),
        )

    def futures_summary_last_ok_ts(self) -> float:
        """Monotonic wall time when futures balance summary last succeeded (for buying-power cap)."""
        return float(self._futures_summary_ok_ts or 0.0)

    def _coinbase_note_order_reject(self, reason: str) -> None:
        self._state.note_order_reject(
            reason,
            source="coinbase",
            max_consecutive=int(getattr(self._scalp_cfg, "order_reject_max_consecutive", 3) or 3),
            consecutive_pause_sec=float(getattr(self._scalp_cfg, "order_reject_cooldown_sec", 120.0) or 120.0),
            insufficient_funds_cooldown_sec=float(
                getattr(self._scalp_cfg, "insufficient_funds_cooldown_sec", 300.0) or 300.0,
            ),
        )

    def _coinbase_note_order_success(self) -> None:
        self._state.note_order_success()

    def _maybe_penalize_on_transport_error(self, e: BaseException) -> None:
        base = float(getattr(self._scalp_cfg, "exchange_penalize_base_sec", 15.0) or 15.0)
        raw = str(e)
        s = raw.lower()
        if "403" in raw or "forbidden" in s or "permission_denied" in s.replace(" ", ""):
            self._limiter.penalize(max(base, 15.0))
            return
        if any(
            x in s
            for x in ("connection", "timeout", "gaierror", "name resolution", "temporarily unavailable")
        ):
            self._limiter.penalize(base)

    def _scalp_product_ids(self) -> list[str]:
        """Product ids for REST/WS — works before register_scalp_runtime fills _product_to_key."""
        if self._product_to_key:
            return list(self._product_to_key.keys())
        out: list[str] = []
        for pc in self._scalp_cfg.pairs.values():
            s = str(getattr(pc, "symbol", "") or "").strip()
            if s:
                out.append(s)
        return out

    def _pair_key_for_product(self, product_id: str) -> str:
        pid = str(product_id or "").strip()
        pk = self._product_to_key.get(pid, "")
        if pk:
            return pk
        pu = pid.upper()
        for k, pc in self._scalp_cfg.pairs.items():
            s = str(getattr(pc, "symbol", "") or "").strip()
            if s == pid or s.upper() == pu:
                return k
        return ""

    def register_scalp_runtime(self, runtime: "ScalpRuntime") -> None:
        self._scalp_runtime = runtime
        m: dict[str, str] = {}
        for key, pc in runtime.config.pairs.items():
            sym = pc.symbol.strip()
            m[sym] = key
        self._product_to_key = m
        LOG.info(
            "CoinbaseOrderManager: scalp registered pair_keys=%s products=%s",
            list(runtime.config.pairs.keys()),
            list(m.keys()),
        )

    def _ensure_client(self):
        if self._client is None:
            try:
                from coinbase.rest import RESTClient
            except ImportError as e:
                raise RuntimeError("coinbase-advanced-py is required for Coinbase execution") from e
            key = self._app_config.coinbase_api_key
            secret = self._app_config.coinbase_api_secret
            if not key or not secret:
                raise RuntimeError("COINBASE_API_KEY / COINBASE_API_SECRET missing")
            self._client = RESTClient(api_key=key, api_secret=secret)
        return self._client

    def get_futures_transaction_summary_sync(self) -> dict | None:
        """Call Advanced Trade ``GET …/transaction_summary`` for **derivatives** volume + fee tier.

        Must match Coinbase **Derivatives** row (e.g. ~6.5 / 7.0 bps Advanced 2), not Spot (~12.5 / 25 bps).
        Tries ``product_type`` / ``product_venue`` combinations (INTX / FCM naming varies).
        Returns a plain dict or ``None`` if all variants fail.
        """
        try:
            client = self._ensure_client()
        except Exception:
            return None
        gt = getattr(client, "get_transaction_summary", None)
        if not callable(gt):
            return None
        variants: tuple[dict[str, str], ...] = (
            {"product_type": "FUTURE", "contract_expiry_type": "PERPETUAL", "product_venue": "FCM"},
            {"product_type": "FUTURE", "contract_expiry_type": "PERPETUAL", "product_venue": "INTX"},
            {"product_type": "FUTURE", "contract_expiry_type": "PERPETUAL"},
            {"product_type": "FUTURE"},
            {},
        )
        last_err: str | None = None
        for kwargs in variants:
            try:
                resp = gt(**kwargs)
                tv = getattr(resp, "total_volume", None)
                if tv is None:
                    continue
                tvf = float(tv)
                aov_raw = getattr(resp, "advanced_trade_only_volumes", None)
                aov = float(aov_raw) if aov_raw is not None else None
                ft_obj = getattr(resp, "fee_tier", None)
                tier: dict[str, object] = {}
                if ft_obj is not None:
                    tier = {
                        "pricing_tier": getattr(ft_obj, "pricing_tier", None),
                        "usd_from": getattr(ft_obj, "usd_from", None),
                        "usd_to": getattr(ft_obj, "usd_to", None),
                        "maker_fee_rate": getattr(ft_obj, "maker_fee_rate", None),
                        "taker_fee_rate": getattr(ft_obj, "taker_fee_rate", None),
                    }
                return {
                    "ok": True,
                    "total_volume": tvf,
                    "advanced_trade_only_volumes": aov,
                    "fee_tier": tier,
                    "query": dict(kwargs),
                }
            except Exception as e:
                last_err = str(e)
                continue
        if last_err:
            LOG.info("Coinbase: get_transaction_summary exhausted variants — last error: %s", last_err[:500])
        return None

    async def fetch_futures_transaction_summary(self) -> dict | None:
        """Rate-limited async wrapper around :meth:`get_futures_transaction_summary_sync`."""
        await self._limiter.acquire()
        return await asyncio.to_thread(self.get_futures_transaction_summary_sync)

    async def initialize(self) -> None:
        self._ensure_client()
        await self._cancel_open_scalp_orders()
        self._stop.clear()
        self._poll_task = asyncio.create_task(self._fill_poll_loop(), name="coinbase_fill_poll")
        self._balance_task = asyncio.create_task(self._balance_poll_loop(), name="coinbase_balance_poll")
        LOG.info("CoinbaseOrderManager: initialized (fill polling started)")

    async def close(self) -> None:
        self._stop.set()
        for task in (self._poll_task, self._balance_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._poll_task = None
        self._balance_task = None

    def balance_snapshot(self) -> dict:
        """Latest cached account balances for the dashboard."""
        return dict(self._balances)

    def scalp_open_orders_snapshot(self) -> list[dict]:
        """Last resting Advanced Trade orders for configured scalp products (refreshed on exchange poll)."""
        return list(self._last_scalp_open_orders)

    def scalp_open_orders_all_snapshot(self) -> list[dict]:
        """All OPEN Advanced Trade orders for this key/portfolio (venue truth; refreshed with reconcile)."""
        return list(self._last_all_open_orders)

    def scalp_open_orders_outside_config_snapshot(self) -> list[dict]:
        """OPEN orders whose product_id is not in ``[scalp.pairs.*].symbol`` (manual legs, other contracts)."""
        allowed = {p.upper() for p in self._scalp_product_ids()}
        out: list[dict] = []
        for o in self._last_all_open_orders:
            pid = str(o.get("product_id") or "").strip().upper()
            if pid and pid not in allowed:
                out.append(o)
        return out

    async def _balance_poll_loop(self) -> None:
        """Poll Coinbase for USDC balance + futures summary (back off on repeated errors)."""
        client = self._ensure_client()
        base_sleep = 30.0
        max_sleep = 300.0
        while not self._stop.is_set():
            cycle_ok = True
            try:
                await self._fetch_balances_once(client)
            except asyncio.CancelledError:
                break
            except Exception:
                cycle_ok = False
                logfn = LOG.warning if self._balance_poll_failures >= 2 else LOG.debug
                logfn("CoinbaseOrderManager: balance poll error", exc_info=True)
            self._balance_poll_cycles += 1
            try:
                await self.refresh_scalp_exchange_snapshots()
            except asyncio.CancelledError:
                break
            except Exception:
                cycle_ok = False
                logfn = LOG.warning if self._balance_poll_failures >= 2 else LOG.debug
                logfn(
                    "CoinbaseOrderManager: exchange snapshot refresh error",
                    exc_info=True,
                )
            if cycle_ok:
                self._balance_poll_failures = 0
            else:
                self._balance_poll_failures += 1
            sleep_s = (
                min(max_sleep, base_sleep * (2 ** min(self._balance_poll_failures, 8)))
                if self._balance_poll_failures > 0
                else base_sleep
            )
            await asyncio.sleep(sleep_s)

    async def _fetch_balances_once(self, client) -> None:
        def _call():
            result: dict = {}
            # USDC + spot accounts
            try:
                resp = client.get_accounts(limit=50)
                raw = _unwrap_response(resp)
                accounts = raw.get("accounts") or []
                spot: list[dict] = []
                for a in accounts:
                    if not isinstance(a, dict):
                        av = getattr(a, "available_balance", {})
                        cur = str(getattr(a, "currency", "") or "")
                        val = float(av.get("value", 0) if isinstance(av, dict) else getattr(av, "value", 0) or 0)
                    else:
                        av = a.get("available_balance") or {}
                        cur = str(a.get("currency") or "")
                        val = float(av.get("value", 0) if isinstance(av, dict) else 0)
                    if val > 0 or cur in ("USDC", "USD"):
                        spot.append({"currency": cur, "available": round(val, 8)})
                result["spot_accounts"] = spot
                usd_like = 0.0
                for row in spot:
                    cur = str(row.get("currency") or "").upper()
                    if cur in ("USDC", "USD"):
                        usd_like += float(row.get("available") or 0)
                result["spot_usd_available"] = round(usd_like, 4)
            except Exception as e:
                LOG.debug("balance_poll: accounts error: %s", e)

            # Futures / perp summary
            try:
                resp = client.get_futures_balance_summary()
                raw = _unwrap_response(resp)
                bs = raw.get("balance_summary")
                result["futures"] = {
                    "total_usd_balance": round(_balance_summary_field(bs, "total_usd_balance"), 4),
                    "buying_power": round(_balance_summary_field(bs, "futures_buying_power"), 4),
                    "unrealized_pnl": round(_balance_summary_field(bs, "unrealized_pnl"), 4),
                    "daily_realized_pnl": round(_balance_summary_field(bs, "daily_realized_pnl"), 4),
                    "initial_margin": round(_balance_summary_field(bs, "initial_margin"), 4),
                    "available_margin": round(_balance_summary_field(bs, "available_margin"), 4),
                    "open_orders_hold_usd": round(
                        _balance_summary_field(bs, "total_open_orders_hold_amount"),
                        4,
                    ),
                }
            except Exception as e:
                LOG.debug("balance_poll: futures summary error: %s", e)

            return result

        data = await asyncio.to_thread(_call)
        self._balances = data
        fut = data.get("futures") if isinstance(data, dict) else None
        if isinstance(fut, dict) and fut:
            self._futures_summary_ok_ts = time.time()

    async def _cancel_open_scalp_orders(self) -> None:
        """Best-effort: cancel working orders for configured scalp product_ids."""
        client = self._ensure_client()
        products = self._scalp_product_ids()
        if not products:
            return
        try:
            await self._limiter.acquire()
            lo = getattr(client, "list_orders", None)
            if callable(lo):
                base_kw: dict = {"product_ids": products, "limit": 250}

                _is_perp_cs = str(getattr(self._scalp_cfg, "venue", "") or "").strip().lower() == "coinbase_perps"
                _cancel_statuses = () if _is_perp_cs else ("OPEN", "PENDING")

                def _pull_orders() -> ListOrdersMergeResult:
                    return _list_orders_merged(lo, base_kw, _cancel_statuses)

                merged = await asyncio.to_thread(_pull_orders)
                if not merged.merge_ok:
                    LOG.warning(
                        "CoinbaseOrderManager: startup cancel sweep list_orders incomplete failed_statuses=%s",
                        merged.failed_statuses,
                    )
                orders = merged.orders
                allow = {p.upper() for p in products}
                ids: list[str] = []
                for o in orders:
                    od = _as_plain_dict(o)
                    if not od:
                        continue
                    pid = str(od.get("product_id") or od.get("productId") or "").strip()
                    if pid.upper() not in allow:
                        continue
                    oid = od.get("order_id") or od.get("orderId") or od.get("id")
                    if oid:
                        ids.append(str(oid))
                if ids:
                    await self._limiter.acquire()
                    co = getattr(client, "cancel_orders", None)
                    if callable(co):
                        await asyncio.to_thread(co, order_ids=ids)
                        LOG.info("CoinbaseOrderManager: cancelled %d open orders on startup", len(ids))
        except Exception:
            LOG.warning("CoinbaseOrderManager: startup cancel sweep failed", exc_info=True)

    def _parse_create_order_id(self, resp: object) -> str:
        raw = _unwrap_response(resp) or _as_plain_dict(resp)
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
        if oid:
            return str(oid)
        LOG.warning(
            "CoinbaseOrderManager: could not parse order_id from create_order response keys=%s",
            list(raw.keys())[:20],
        )
        return ""

    async def _get_cde_price_increment(self, client, product_id: str) -> float:
        """Cached ``price_increment`` from ``get_product`` — required for CDE stop/limit preview."""
        pid = str(product_id or "").strip()
        if not pid:
            return _fallback_cde_price_increment(pid)
        hit = self._cde_price_increment_cache.get(pid)
        if hit is not None and hit > 0:
            return hit
        gp = getattr(client, "get_product", None)
        inc: float | None = None
        if callable(gp):

            def _pull() -> object:
                return _unwrap_response(gp(pid))

            try:
                await self._limiter.acquire()
                raw = await asyncio.to_thread(_pull)
                inc = _cde_price_increment_from_product(raw)
            except Exception:
                LOG.warning(
                    "CoinbaseOrderManager: get_product(%s) failed while resolving price_increment",
                    pid,
                    exc_info=True,
                )
        if (inc is None or inc <= 0) and callable(gp):
            gpp = getattr(client, "get_public_product", None)
            if callable(gpp):

                def _pub() -> object:
                    return _unwrap_response(gpp(pid))

                try:
                    await self._limiter.acquire()
                    raw_pub = await asyncio.to_thread(_pub)
                    inc = _cde_price_increment_from_product(raw_pub)
                    if inc is not None and inc > 0:
                        LOG.info(
                            "CoinbaseOrderManager: %s price_increment from get_public_product "
                            "(authenticated get_product unavailable)",
                            pid,
                        )
                except Exception:
                    LOG.debug(
                        "CoinbaseOrderManager: get_public_product(%s) also failed",
                        pid,
                        exc_info=True,
                    )
        if inc is None or inc <= 0:
            fb = _fallback_cde_price_increment(pid)
            LOG.warning(
                "CoinbaseOrderManager: missing price_increment for %s — using fallback tick %.8g",
                pid,
                fb,
            )
            self._cde_price_increment_cache[pid] = fb
            return fb
        self._cde_price_increment_cache[pid] = inc
        if pid not in self._cde_tick_info_logged:
            self._cde_tick_info_logged.add(pid)
            LOG.info("CoinbaseOrderManager: %s CDE order price_increment=%s", pid, inc)
        return inc

    async def add_order(self, params: dict) -> str:
        """Map internal scalp order params to Coinbase Advanced Trade ``create_order``."""
        client = self._ensure_client()
        cl_ord_id = params.get("cl_ord_id") or f"scalp_{uuid.uuid4().hex[:12]}"
        product_id = str(params.get("symbol", "")).strip()
        side_raw = str(params.get("side", "buy")).lower()
        side = "BUY" if side_raw == "buy" else "SELL"
        order_type = str(params.get("order_type", "limit")).lower()
        qty = float(params.get("order_qty", 0) or 0)
        pair_key = self._pair_key_for_product(product_id)
        is_perp = str(getattr(self._scalp_cfg, "venue", "") or "").strip().lower() == "coinbase_perps"

        cde_inc: float | None = None
        if is_perp:
            cde_inc = await self._get_cde_price_increment(client, product_id)

        if is_perp:
            # FCM/CDE: ``base_size`` is whole contracts (see ``_fcm_cde_order_base_size`` docstring).
            base_size = _fcm_cde_order_base_size(qty)
        else:
            base_size = str(max(1, int(round(qty)))) if qty > 0 else "1"

        lev = str(int(max(1.0, float(self._scalp_cfg.max_leverage))))
        margin = self._scalp_cfg.margin_mode or "CROSS"

        configurations = _build_coinbase_scalp_order_configurations(
            order_type=order_type,
            side=side,
            base_size=base_size,
            is_perp=is_perp,
            cde_inc=cde_inc,
            params=params,
        )

        # Always track orders so fill-poll can map exchange order_id → client_order_id even if
        # product_id casing or registration order differed from _product_to_key.
        self._state.active_orders[cl_ord_id] = ActiveOrder(
            cl_ord_id=cl_ord_id,
            pair_key=pair_key or "_unmapped_product",
            symbol=product_id,
            side=side_raw,
            price=float(params.get("limit_price", 0) or params.get("trigger_price", 0) or 0),
            qty=float(qty),
            placed_at=time.time(),
        )

        resp: object | None = None
        raw_out: dict = {}
        last_err_msg = ""
        precision_repilot_done = False
        order_submitted = False

        while True:
            for attempt, order_configuration in enumerate(configurations):
                await self._limiter.acquire()

                def _send(oc: dict = order_configuration) -> object:
                    return client.create_order(
                        client_order_id=cl_ord_id,
                        product_id=product_id,
                        side=side,
                        order_configuration=oc,
                        leverage=lev,
                        margin_type=margin,
                    )

                try:
                    resp = await asyncio.to_thread(_send)
                except Exception as e:
                    LOG.error("CoinbaseOrderManager: create_order failed: %s", e)
                    self._maybe_penalize_on_transport_error(e)
                    self._state.active_orders.pop(cl_ord_id, None)
                    self._state.record_exchange_error(
                        "error", "Coinbase order rejected", str(e), "coinbase",
                    )
                    self._coinbase_note_order_reject(str(e))
                    return ""

                raw_out = _unwrap_response(resp) or _as_plain_dict(resp)
                if raw_out.get("success") is False:
                    err_obj = raw_out.get("error_response") or raw_out.get("errorResponse")
                    last_err_msg = _coinbase_order_error_text(err_obj)
                    retry_ro = (
                        attempt == 0
                        and len(configurations) > 1
                        and _retry_exit_without_reduce_only(last_err_msg)
                    )
                    if retry_ro:
                        LOG.warning(
                            "CoinbaseOrderManager: %s %s reduce_only rejected (%s) — retrying without reduce_only",
                            order_type,
                            product_id,
                            last_err_msg[:300],
                        )
                        continue
                    if (
                        (not precision_repilot_done)
                        and is_perp
                        and _retry_price_precision_from_coinbase(last_err_msg)
                    ):
                        LOG.warning(
                            "CoinbaseOrderManager: %s %s price precision reject — busting tick cache "
                            "and rebuilding order_configuration (once) | %s",
                            product_id,
                            order_type,
                            last_err_msg[:200],
                        )
                        self._cde_price_increment_cache.pop(product_id, None)
                        cde_inc = await self._get_cde_price_increment(client, product_id)
                        configurations = _build_coinbase_scalp_order_configurations(
                            order_type=order_type,
                            side=side,
                            base_size=base_size,
                            is_perp=is_perp,
                            cde_inc=cde_inc,
                            params=params,
                        )
                        precision_repilot_done = True
                        break
                    LOG.error(
                        "CoinbaseOrderManager: create_order success=false %s %s %s cl=%s | %s | raw_keys=%s",
                        side,
                        product_id,
                        order_type,
                        cl_ord_id[:20],
                        last_err_msg,
                        list(raw_out.keys())[:25],
                    )
                    self._state.active_orders.pop(cl_ord_id, None)
                    self._state.record_exchange_error(
                        "error",
                        f"Coinbase {order_type} rejected",
                        f"{product_id}: {last_err_msg}",
                        "coinbase",
                    )
                    self._coinbase_note_order_reject(last_err_msg or "create_order success=false")
                    return ""

                order_submitted = True
                break
            if order_submitted:
                break
            if not precision_repilot_done:
                self._state.active_orders.pop(cl_ord_id, None)
                return ""
            continue

        oid = self._parse_create_order_id(resp)  # type: ignore[arg-type]
        if not oid:
            # NM-001: order was submitted (order_submitted=True) so Coinbase may hold it.
            # DO NOT pop the tracking entry — leave it with exchange_order_id="" so the
            # fill-poll can recover the real order_id via list_orders on the next cycle.
            LOG.error(
                "CoinbaseOrderManager: create_order missing order_id cl=%s %s %s keys=%s "
                "— keeping tracking entry for fill-poll recovery",
                cl_ord_id[:20],
                side,
                product_id,
                list(raw_out.keys())[:25],
            )
            self._state.record_exchange_error(
                "warning",
                "Coinbase create_order missing order id — awaiting fill-poll recovery",
                f"{product_id} {side} {order_type} cl={cl_ord_id[:20]}…",
                "coinbase",
            )
            return cl_ord_id  # return cl_ord_id so caller can track; exchange_order_id="" until poll recovers it

        order = self._state.active_orders.get(cl_ord_id)
        if order is not None:
            order.exchange_order_id = oid
        LOG.info(
            "CoinbaseOrderManager: placed %s %s %s cl=%s exchange_id=%s",
            side,
            product_id,
            order_type,
            cl_ord_id[:16],
            oid[:16] if len(oid) > 16 else oid,
        )
        self._coinbase_note_order_success()
        return cl_ord_id

    async def flatten_scalp_leg_market(
        self,
        *,
        symbol: str,
        side: str,
        order_qty: float,
        cl_ord_id: str,
        reduce_only: bool = True,
    ) -> str:
        """Submit a market order to close or reduce a scalp leg (perps: reduce_only by default)."""
        return await self.add_order(
            {
                "symbol": str(symbol).strip(),
                "side": str(side).lower(),
                "order_type": "market",
                "order_qty": float(order_qty),
                "cl_ord_id": str(cl_ord_id),
                "reduce_only": bool(reduce_only),
            },
        )

    async def cancel_order(self, cl_ord_id: str) -> bool:
        order = self._state.active_orders.get(cl_ord_id)
        if order is None:
            return False
        # NM-008: cap cancel attempts to avoid rate-limiter starvation
        _MAX_CANCEL_ATTEMPTS = 3
        if order.cancel_attempt_count >= _MAX_CANCEL_ATTEMPTS:
            LOG.error(
                "CoinbaseOrderManager: cancel_order %s exceeded %d attempts — giving up",
                cl_ord_id[:16], _MAX_CANCEL_ATTEMPTS,
            )
            return False
        oid = order.exchange_order_id or ""
        if not oid:
            LOG.warning("CoinbaseOrderManager: missing exchange_order_id for %s", cl_ord_id[:16])
            return False
        order.cancel_attempt_count += 1
        client = self._ensure_client()
        await self._limiter.acquire()

        def _cancel() -> None:
            client.cancel_orders(order_ids=[oid])

        try:
            await asyncio.to_thread(_cancel)
            self._state.active_orders.pop(cl_ord_id, None)
            return True
        except Exception as e:
            LOG.warning("CoinbaseOrderManager: cancel failed %s (attempt %d/%d): %s",
                        cl_ord_id[:16], order.cancel_attempt_count, _MAX_CANCEL_ATTEMPTS, e)
            order.cancel_retry = True
            self._state.record_exchange_error(
                "warning",
                "Coinbase cancel failed",
                f"{cl_ord_id[:20]}…: {e}",
                "coinbase",
            )
            return False

    async def cancel_all_scalp_open_orders(self) -> int:
        """Cancel all resting OPEN orders for configured scalp products (entries / protectives).

        Does **not** flatten positions. No-op in sim_mode or without a REST client.
        Returns the number of exchange order_ids we attempted to cancel.
        """
        if bool(getattr(self._scalp_cfg, "sim_mode", False)):
            LOG.info("CoinbaseOrderManager: cancel_all_scalp_open_orders skipped (sim_mode)")
            return 0
        client = self._ensure_client()
        products = self._scalp_product_ids()
        if not products:
            return 0
        lo = getattr(client, "list_orders", None)
        co = getattr(client, "cancel_orders", None)
        if not callable(lo) or not callable(co):
            LOG.warning("CoinbaseOrderManager: cancel_all_scalp_open_orders — missing list_orders/cancel_orders")
            return 0
        _is_perp_ca = str(getattr(self._scalp_cfg, "venue", "") or "").strip().lower() == "coinbase_perps"
        _ca_statuses = () if _is_perp_ca else ("OPEN", "PENDING")

        def _list_open() -> list[str]:
            base_kw: dict = {"product_ids": products, "limit": 250}
            mr = _list_orders_merged(lo, base_kw, _ca_statuses)
            if not mr.merge_ok:
                LOG.warning(
                    "CoinbaseOrderManager: cancel_all list_orders incomplete failed_statuses=%s",
                    mr.failed_statuses,
                )
            ids: list[str] = []
            allow = {p.upper() for p in products}
            for od in mr.orders:
                pid = str(od.get("product_id") or od.get("productId") or "").strip()
                if pid.upper() not in allow:
                    continue
                oid = od.get("order_id") or od.get("orderId") or od.get("id")
                if oid:
                    ids.append(str(oid))
            return ids

        try:
            await self._limiter.acquire()
            order_ids = await asyncio.to_thread(_list_open)
        except Exception:
            LOG.warning("CoinbaseOrderManager: cancel_all list_orders failed", exc_info=True)
            return 0
        if not order_ids:
            return 0
        cancelled: set[str] = set()
        batch_size = 50
        for i in range(0, len(order_ids), batch_size):
            chunk = order_ids[i : i + batch_size]
            try:
                await self._limiter.acquire()
                await asyncio.to_thread(lambda ids=chunk: co(order_ids=ids))
                cancelled.update(chunk)
            except Exception:
                LOG.warning(
                    "CoinbaseOrderManager: cancel_orders batch failed (%d ids)",
                    len(chunk),
                    exc_info=True,
                )
        # Drop local tracking for any order whose exchange id was cancelled
        if cancelled:
            for cl_id, ao in list(self._state.active_orders.items()):
                eid = str(getattr(ao, "exchange_order_id", "") or "")
                if eid and eid in cancelled:
                    self._state.active_orders.pop(cl_id, None)
        LOG.info(
            "CoinbaseOrderManager: cancel_all_scalp_open_orders — attempted %d cancel(s)",
            len(cancelled),
        )
        return len(cancelled)

    async def _fill_poll_loop(self) -> None:
        client = self._ensure_client()
        base_sleep = 2.0
        max_sleep = 300.0
        while not self._stop.is_set():
            # Open-order snapshots also run with the 30s balance poll; this adds ~12s cadence.
            self._exchange_orders_refresh_tick += 1
            if self._exchange_orders_refresh_tick % 6 == 0:
                try:
                    await self.refresh_scalp_exchange_snapshots()
                except asyncio.CancelledError:
                    break
                except Exception:
                    LOG.debug("CoinbaseOrderManager: exchange snapshot (fill-loop) error", exc_info=True)
            products = self._scalp_product_ids()
            try:
                await self._poll_fills_once(client, products)
            except asyncio.CancelledError:
                break
            except Exception:
                LOG.warning("CoinbaseOrderManager: fill poll error", exc_info=True)
                self._fill_poll_failures += 1
            else:
                self._fill_poll_failures = 0
            try:
                await self._refresh_scalp_marks(client)
            except asyncio.CancelledError:
                break
            except Exception:
                LOG.debug("CoinbaseOrderManager: mark refresh error", exc_info=True)
            try:
                rt = self._scalp_runtime
                if rt is not None and any(
                    p.status == "pending" for p in rt._trader._positions.values()
                ):
                    await self._poll_pending_orders(client)
            except asyncio.CancelledError:
                break
            except Exception:
                LOG.warning("CoinbaseOrderManager: pending order poll error", exc_info=True)
            # Every 5 cycles (~10s) check stop/TP order status directly for open positions.
            # This catches fills that get_fills() missed (>100 fills, API gaps, etc.).
            self._protective_poll_cycle += 1
            if self._protective_poll_cycle % 5 == 0:
                try:
                    await self._poll_protective_orders(client)
                except asyncio.CancelledError:
                    break
                except Exception:
                    LOG.debug("CoinbaseOrderManager: protective order poll error", exc_info=True)
            sleep_s = (
                min(max_sleep, base_sleep * (2 ** min(self._fill_poll_failures, 8)))
                if self._fill_poll_failures > 0
                else base_sleep
            )
            await asyncio.sleep(sleep_s)

    async def _fetch_product_mark_price(self, client, product_id: str) -> float:
        """Single-product mid/mark for TTL / empirical missed-move snapshots."""
        pid = str(product_id or "").strip()
        if not pid:
            return 0.0
        gp = getattr(client, "get_product", None)
        if not callable(gp):
            return 0.0

        def _fetch() -> dict:
            return _unwrap_response(gp(pid))

        try:
            await self._limiter.acquire()
            raw = await asyncio.to_thread(_fetch)
        except Exception:
            return 0.0
        top = _normalize_get_product_payload(raw)
        m = _mark_price_from_product(top)
        return float(m) if m is not None and m > 0 else 0.0

    async def _refresh_scalp_marks(self, client) -> None:
        """Push exchange mid/mark into ``ScalpTrader`` (open + pending + empirical watches)."""
        rt = self._scalp_runtime
        if rt is None:
            return
        if bool(getattr(rt._trader, "sim_mode", False)):
            return
        trader = rt._trader
        gp = getattr(client, "get_product", None)
        if not callable(gp):
            return
        targets: list[tuple[str, str]] = []
        seen_pid: set[str] = set()
        for pos in list(trader._positions.values()):
            if pos.status not in ("open", "pending"):
                continue
            pid = str(pos.symbol or "").strip()
            pair_key = pos.pair_key
            if not pid or pid in seen_pid:
                continue
            targets.append((pair_key, pid))
            seen_pid.add(pid)
        em = getattr(trader, "_empirical", None)
        seen_pk: set[str] = set()
        if em is not None:
            for pk, sym in em.active_watch_symbols():
                if pk in seen_pk:
                    continue
                s = str(sym or "").strip()
                if s:
                    targets.append((pk, s))
                    seen_pk.add(pk)

        for pair_key, pid in targets:

            def _fetch(product_id: str = pid):
                return _unwrap_response(gp(product_id))

            try:
                await self._limiter.acquire()
                raw = await asyncio.to_thread(_fetch)
            except Exception:
                continue
            top = _normalize_get_product_payload(raw)
            fund_bps = _estimated_funding_bps_per_hour_from_product(top)
            warn_thr = float(getattr(self._scalp_cfg, "funding_warn_bps_per_hour", 0.0) or 0.0)
            if fund_bps is not None and warn_thr > 0 and fund_bps >= warn_thr:
                now = time.time()
                last = self._funding_warn_last.get(pid, 0.0)
                if now - last >= 1800.0:
                    self._funding_warn_last[pid] = now
                    self._state.push_alert(
                        "warning",
                        f"Scalp high funding {pair_key}",
                        f"~{fund_bps:.2f} bps/hr est (threshold {warn_thr}) on {pid} — confirm vs Coinbase",
                        "scalp_perps",
                    )
            for pos in trader.positions_for_pair(pair_key):
                if pos.status == "open" and fund_bps is not None:
                    pos.funding_rate = fund_bps / 10_000.0
            mark = _mark_price_from_product(top)
            if mark is None or mark <= 0:
                continue
            trader.update_position_mark(pair_key, float(mark))

    async def _poll_pending_orders(self, client) -> None:
        """Check pending scalp positions via get_order / list_orders to detect fills."""
        if self._scalp_runtime is None:
            return
        trader = self._scalp_runtime._trader
        for pos in list(trader._positions.values()):
            if pos.status != "pending":
                continue
            pair_key = pos.pair_key
            ao = self._state.active_orders.get(pos.entry_cl_ord_id)
            oid = ao.exchange_order_id if ao else ""

            await self._limiter.acquire()

            if oid:
                raw = await self._get_order_by_id(client, oid)
            else:
                raw = await self._find_order_by_client_id(client, pos.entry_cl_ord_id, pos.symbol)

            if not raw:
                continue
            if isinstance(raw, dict):
                top = dict(raw)
                o_nested = top.get("order")
                if o_nested is not None and not isinstance(o_nested, dict):
                    top["order"] = _as_plain_dict(o_nested)
            else:
                top = _as_plain_dict(raw)
            order_raw = top.get("order") or top
            order = _as_plain_dict(order_raw)
            status = str(order.get("status", "") or "").strip().upper()

            found_oid = str(order.get("order_id") or order.get("orderId") or oid or "")
            if found_oid and ao and not ao.exchange_order_id:
                ao.exchange_order_id = found_oid
                LOG.info("CoinbaseOrderManager: late-discovered order_id=%s for %s", found_oid[:16], pair_key)

            filled_sz = _safe_float(
                order.get("filled_size") or order.get("cumulative_quantity") or 0,
            )
            pct = _safe_float(str(order.get("completion_percentage") or "0").replace("%", ""))
            is_done = status in ("FILLED", "COMPLETED", "DONE") or pct >= 99.9 or (
                filled_sz > 0 and filled_sz + 1e-9 >= float(pos.qty)
            )

            ttl = float(getattr(self._scalp_cfg, "entry_limit_ttl_sec", 0.0) or 0.0)
            if (
                ttl > 0
                and not is_done
                and status not in ("CANCELLED", "EXPIRED", "FAILED")
            ):
                age = time.time() - float(getattr(pos, "opened_at", 0.0) or 0.0)
                if age > ttl:
                    lp = float(getattr(pos, "pending_limit_price", 0.0) or 0.0)
                    if lp <= 0:
                        lp = float(pos.entry_price)
                    mk = float(getattr(pos, "mark_price", 0.0) or 0.0)
                    if mk <= 0 and pos.symbol:
                        mk = await self._fetch_product_mark_price(client, pos.symbol)
                    trader.note_entry_ttl_cancel_for_empirical(
                        pair_key,
                        str(pos.symbol or ""),
                        pos.direction,
                        lp,
                        mk,
                    )
                    LOG.warning(
                        "CoinbaseOrderManager: entry limit TTL exceeded (age=%.0fs > %.0fs) — "
                        "cancelling pending entry %s",
                        age, ttl, pair_key,
                    )
                    c_ok = await self.cancel_order(pos.entry_cl_ord_id)
                    if not c_ok and found_oid:
                        await self._limiter.acquire()

                        def _cx() -> None:
                            client.cancel_orders(order_ids=[found_oid])

                        try:
                            await asyncio.to_thread(_cx)
                            self._state.active_orders.pop(pos.entry_cl_ord_id, None)
                        except Exception as e:
                            LOG.warning(
                                "CoinbaseOrderManager: TTL cancel by order_id failed %s: %s",
                                found_oid[:16], e,
                            )
                    # Purge the pending position from trader state so has_position()
                    # unblocks future entries. Without this, the phantom pending persists
                    # indefinitely and blocks all new entries for this pair.
                    trader._release_reserved_for_position(pos)
                    try:
                        del trader._positions[pos.entry_cl_ord_id]
                    except KeyError:
                        pass
                    LOG.warning(
                        "CoinbaseOrderManager: phantom pending purged after TTL cancel %s",
                        pair_key,
                    )
                    continue

            if is_done:
                afp = order.get("average_filled_price") or order.get("averageFilledPrice")
                avg_px = _safe_float(afp)
                if filled_sz <= 0:
                    filled_sz = float(pos.qty)
                if avg_px <= 0 and filled_sz > 0:
                    # Some responses expose notional; derive VWAP without using our limit assumption.
                    for k in ("filled_value", "filledValue", "total_value_after_fees", "quote_size"):
                        raw_v = order.get(k)
                        if isinstance(raw_v, dict):
                            raw_v = raw_v.get("value") or raw_v.get("amount")
                        nv = _safe_float(raw_v)
                        if nv > 0:
                            avg_px = nv / filled_sz
                            break
                if avg_px <= 0:
                    LOG.error(
                        "CoinbaseOrderManager: FILLED order %s has no average/notional — cannot set entry (pair=%s)",
                        found_oid[:16], pair_key,
                    )
                    continue
                LOG.info(
                    "CoinbaseOrderManager: order %s FILLED via status poll — pair=%s px=%.6f qty=%.4f",
                    found_oid[:16], pair_key, avg_px, filled_sz,
                )
                fill_key = f"order_poll_{found_oid or pos.entry_cl_ord_id}"
                if fill_key not in self._seen_fill_keys:
                    self._seen_fill_keys.add(fill_key)
                    # NM-007: fire-and-forget ordering — safe because (a) asyncio is
                    # single-threaded so this task won't interleave with the current
                    # poll cycle, and (b) _seen_fill_keys provides idempotency if the
                    # task and a concurrent fill-poll path both see the same fill.
                    asyncio.create_task(
                        self._scalp_runtime.on_entry_fill_authoritative(
                            pair_key, avg_px, filled_sz, entry_cl_ord_id=pos.entry_cl_ord_id,
                        ),
                        name=f"coinbase_order_poll_fill_{pair_key}",
                    )
            elif status in ("CANCELLED", "EXPIRED", "FAILED"):
                LOG.warning(
                    "CoinbaseOrderManager: order %s status=%s — removing pending position %s",
                    found_oid[:16], status, pair_key,
                )
                trader._positions.pop(pos.entry_cl_ord_id, None)
                trader._release_reserved_for_position(pos)
                self._state.active_orders.pop(pos.entry_cl_ord_id, None)

    async def _get_order_by_id(self, client, oid: str) -> dict | None:
        def _get():
            go = getattr(client, "get_order", None)
            if not callable(go):
                return None
            return _unwrap_response(go(order_id=oid))
        try:
            return await asyncio.to_thread(_get)
        except Exception:
            LOG.debug("CoinbaseOrderManager: get_order failed for %s", oid[:16], exc_info=True)
            return None

    async def _find_order_by_client_id(self, client, cl_ord_id: str, product_id: str) -> dict | None:
        """Fallback: search recent orders by product to find one matching our client_order_id."""

        def _search():
            lo = getattr(client, "list_orders", None)
            if not callable(lo):
                return None
            base_kw: dict = {"product_ids": [product_id], "limit": 50}
            _is_perp_fd = str(getattr(self._scalp_cfg, "venue", "") or "").strip().lower() == "coinbase_perps"
            if _is_perp_fd:
                mr = _list_orders_merged(
                    lo, base_kw, (), cde_search_include_filled=True,
                )
            else:
                mr = _list_orders_merged(lo, base_kw, ("OPEN", "PENDING", "FILLED"))
            if not mr.merge_ok:
                LOG.debug(
                    "CoinbaseOrderManager: list_orders search incomplete failed_statuses=%s",
                    mr.failed_statuses,
                )
            for od in mr.orders:
                cid = str(od.get("client_order_id") or od.get("clientOrderId") or "")
                if cid == cl_ord_id:
                    return od
            return None
        try:
            result = await asyncio.to_thread(_search)
            if result:
                LOG.info(
                    "CoinbaseOrderManager: found order via list_orders for cl_id=%s status=%s",
                    cl_ord_id[:16], result.get("status"),
                )
            return result
        except Exception:
            LOG.warning("CoinbaseOrderManager: list_orders search failed for %s", cl_ord_id[:16], exc_info=True)
            return None

    async def is_resting_protective_open(self, cl_ord_id: str, product_id: str) -> bool:
        """Return True if this client order id is still a live resting order on Coinbase.

        Used to detect missing stop/TP (e.g. UI shows ``Add`` for TP/SL).
        """
        cid = str(cl_ord_id or "").strip()
        pid = str(product_id or "").strip()
        if not cid or not pid:
            return False
        client = self._ensure_client()
        ao = self._state.active_orders.get(cid)
        oid = (ao.exchange_order_id if ao else "") or ""

        async def _order_status_from_get(o: str) -> str:
            await self._limiter.acquire()
            raw = await self._get_order_by_id(client, o)
            if not raw:
                return ""
            od = _as_plain_dict(raw.get("order") or raw)
            return str(od.get("status", "") or "").strip().upper()

        if oid:
            st = await _order_status_from_get(oid)
            if st in ("OPEN", "PENDING"):
                return True
            if st in ("FILLED", "CANCELLED", "EXPIRED", "FAILED", "DONE", "COMPLETED"):
                return False

        await self._limiter.acquire()
        found = await self._find_order_by_client_id(client, cid, pid)
        if not found:
            return False
        od = _as_plain_dict(found)
        st = str(od.get("status", "") or "").strip().upper()
        found_oid = str(od.get("order_id") or od.get("orderId") or "")
        if not found_oid:
            return False
        if st not in ("OPEN", "PENDING"):
            return False
        pk = self._pair_key_for_product(pid)
        if ao is None:
            side_raw = str(od.get("side", "SELL") or "SELL").upper()
            side_l = "sell" if side_raw == "SELL" else "buy"
            lp = _safe_float(od.get("limit_price") or od.get("limitPrice") or 0)
            self._state.active_orders[cid] = ActiveOrder(
                cl_ord_id=cid,
                pair_key=pk or "_unmapped_product",
                symbol=pid,
                side=side_l,
                price=float(lp),
                qty=float(_safe_float(od.get("base_size") or od.get("size") or 1) or 1.0),
                placed_at=time.time(),
                exchange_order_id=found_oid,
            )
        else:
            ao.exchange_order_id = found_oid
        return True

    async def _poll_protective_orders(self, client) -> None:
        """For each open scalp position, directly query the stop and TP order status.

        This is a fallback for when get_fills() misses a fill (>100 fills between polls,
        API gap, client_order_id absent from fill record, etc.).
        If either protective order is FILLED on the exchange, route it as an exit fill.
        If it's CANCELLED/EXPIRED and the position is still open, log a warning — the
        position is unprotected and reconciliation will catch it at the next cycle.
        """
        rt = self._scalp_runtime
        if rt is None:
            return
        trader = rt._trader
        for pos in list(trader._positions.values()):
            if pos.status != "open":
                continue
            pair_key = pos.pair_key
            for cl_id, label in (
                (pos.stop_cl_ord_id, "stop"),
                (pos.tp_cl_ord_id, "tp"),
            ):
                if not cl_id:
                    continue
                ao = self._state.active_orders.get(cl_id)
                if ao is None:
                    continue
                oid = ao.exchange_order_id
                if not oid:
                    continue
                await self._limiter.acquire()
                raw = await self._get_order_by_id(client, oid)
                if not raw:
                    continue
                order = _as_plain_dict(raw.get("order") or raw)
                status = str(order.get("status", "") or "").strip().upper()
                if status in ("FILLED", "COMPLETED", "DONE"):
                    fill_key = f"protective_poll_{oid}"
                    if fill_key in self._seen_fill_keys:
                        continue
                    afp = order.get("average_filled_price") or order.get("averageFilledPrice")
                    px = _safe_float(afp)
                    if px <= 0:
                        for k in ("filled_value", "filledValue", "total_value_after_fees"):
                            nv = _safe_float(order.get(k))
                            sz = _safe_float(order.get("filled_size") or order.get("base_size") or pos.qty)
                            if nv > 0 and sz > 0:
                                px = nv / sz
                                break
                    if px <= 0:
                        LOG.warning(
                            "CoinbaseOrderManager: protective poll — %s FILLED but no fill price for %s",
                            label, pair_key,
                        )
                        continue
                    self._seen_fill_keys.add(fill_key)
                    LOG.warning(
                        "CoinbaseOrderManager: protective poll detected missed %s fill pair=%s @ %.5f",
                        label, pair_key, px,
                    )
                    asyncio.create_task(
                        rt.on_fill(pair_key, cl_id, px, pos.qty),
                        name=f"protective_poll_fill_{pair_key}_{label}",
                    )
                elif status in ("CANCELLED", "EXPIRED", "FAILED"):
                    LOG.warning(
                        "CoinbaseOrderManager: %s order %s for %s is %s — position unprotected",
                        label, cl_id[:20], pair_key, status,
                    )
                    self._state.active_orders.pop(cl_id, None)
                    self._state.record_exchange_error(
                        "error",
                        f"Scalp {label.upper()} order gone: {pair_key}",
                        f"Exchange reports {label} order {cl_id[:16]} as {status}. Position is unprotected.",
                        "scalp_protective",
                    )

    async def _poll_fills_once(self, client, products: list[str]) -> None:
        if not products or self._scalp_runtime is None:
            return

        def _pull():
            gf = getattr(client, "get_fills", None)
            if not callable(gf):
                return [], {}
            resp = gf(product_ids=products, limit=100)
            raw = _unwrap_response(resp)
            return raw.get("fills") or [], raw

        fills, raw_resp = await asyncio.to_thread(_pull)
        if not self._fill_poll_logged:
            self._fill_poll_logged = True
            sample = fills[:3] if isinstance(fills, list) else fills
            LOG.info(
                "CoinbaseOrderManager: first fill poll result — %d fills, keys=%s, sample=%s",
                len(fills) if isinstance(fills, list) else -1,
                list(raw_resp.keys()),
                sample,
            )
        if not isinstance(fills, list):
            return

        oid_to_cl: dict[str, tuple[str, str]] = {}
        for cl_id, ao in self._state.active_orders.items():
            if ao.exchange_order_id:
                oid_to_cl[ao.exchange_order_id] = (cl_id, ao.pair_key)

        for f in fills:
            f = _as_plain_dict(f)
            if not f:
                continue
            trade_id = str(f.get("trade_id") or f.get("tradeId") or f.get("fill_id") or "")
            key = trade_id or f"{f.get('order_id')}_{f.get('product_id')}_{f.get('trade_time')}"
            if key in self._seen_fill_keys:
                continue

            exchange_oid = str(f.get("order_id") or "")
            cid = str(f.get("client_order_id") or f.get("clientOrderId") or "")
            if not cid and exchange_oid in oid_to_cl:
                cid, _ = oid_to_cl[exchange_oid]

            if not str(cid).strip().lower().startswith("scalp_"):
                continue

            product_id = str(f.get("product_id", ""))
            pair_key = self._pair_key_for_product(product_id)
            rt = self._scalp_runtime
            if not pair_key and rt is not None and cid:
                pend = rt._trader.position_by_entry(cid)
                if pend is not None:
                    pair_key = pend.pair_key
                else:
                    ao = self._state.active_orders.get(cid)
                    if ao is not None and ao.pair_key and ao.pair_key != "_unmapped_product":
                        pair_key = ao.pair_key
            if not pair_key:
                LOG.warning(
                    "CoinbaseOrderManager: cannot route scalp fill (product_id=%r client_order_id=%s) — "
                    "check [scalp.pairs.*].symbol vs venue fill payload",
                    product_id,
                    (cid or "")[:36],
                )
                self._seen_fill_keys.add(key)
                continue

            try:
                px = float(f.get("price", 0) or 0)
                sz = float(f.get("size", 0) or f.get("base_quantity", 0) or 0)
            except (TypeError, ValueError):
                continue
            if px <= 0 or sz <= 0:
                continue

            self._seen_fill_keys.add(key)
            fee_usd = _scalp_fill_fee_usd(f)
            LOG.info(
                "Coinbase SCALP FILL pair=%s product=%s id=%s oid=%s qty=%.8f @ %.8f",
                pair_key, product_id, cid[:24], exchange_oid[:16], sz, px,
            )
            asyncio.create_task(
                self._scalp_runtime.on_fill(pair_key, cid, px, sz, fee_usd=fee_usd),
                name=f"coinbase_scalp_fill_{pair_key}",
            )

    @staticmethod
    def _slim_open_order_for_snapshot(o: dict) -> dict:
        lp, tr = _snapshot_prices_from_list_order(o)
        bs = _safe_float(o.get("base_size") or o.get("size") or o.get("order_size") or 0)
        if bs <= 0:
            oc = o.get("order_configuration") or o.get("orderConfiguration")
            if isinstance(oc, dict):
                for inner in oc.values():
                    if not isinstance(inner, dict):
                        continue
                    bs = _safe_float(
                        inner.get("base_size")
                        or inner.get("baseSize")
                        or inner.get("size")
                        or inner.get("order_size")
                        or 0
                    )
                    if bs > 0:
                        break
        return {
            "product_id": str(o.get("product_id") or o.get("productId") or ""),
            "side": str(o.get("side") or ""),
            "status": str(o.get("status") or ""),
            "order_type": str(
                o.get("order_type") or o.get("orderType") or o.get("type") or "",
            ),
            "client_order_id": str(
                o.get("client_order_id") or o.get("clientOrderId") or "",
            )[:48],
            "order_id": str(o.get("order_id") or o.get("orderId") or "")[:32],
            "filled_base": _safe_float(o.get("filled_size") or o.get("filledSize") or 0),
            "limit_price": round(lp, 8) if lp > 0 else 0.0,
            "trigger_price": round(tr, 8) if tr > 0 else 0.0,
            "base_size": round(bs, 8) if bs > 0 else 0.0,
        }

    async def _fetch_open_orders_scalp(self, client) -> list[dict]:
        """Resting Advanced Trade orders for configured scalp products (entries / protectives)."""
        products = self._scalp_product_ids()
        if not products:
            return []
        lo = getattr(client, "list_orders", None)
        if not callable(lo):
            return []

        _is_perp = str(getattr(self._scalp_cfg, "venue", "") or "").strip().lower() == "coinbase_perps"
        _statuses = () if _is_perp else ("OPEN", "PENDING")  # CDE rejects all active-status filters

        def _call():
            base_kw: dict = {"product_ids": products, "limit": 100}
            mr = _list_orders_merged(lo, base_kw, _statuses)
            if not mr.merge_ok:
                LOG.warning(
                    "CoinbaseOrderManager: scalp open-order snapshot incomplete failed_statuses=%s",
                    mr.failed_statuses,
                )
            return [CoinbaseOrderManager._slim_open_order_for_snapshot(od) for od in mr.orders if od]

        try:
            await self._limiter.acquire()
            return await asyncio.to_thread(_call)
        except Exception as e:
            LOG.warning(
                "CoinbaseOrderManager: list_orders resting failed for scalp products — %s",
                e,
                exc_info=True,
            )
            return []

    async def _fetch_all_open_orders(self, client) -> list[dict]:
        """Every resting order visible to this API key (not limited to configured scalp products)."""
        lo = getattr(client, "list_orders", None)
        if not callable(lo):
            return []

        _is_perp = str(getattr(self._scalp_cfg, "venue", "") or "").strip().lower() == "coinbase_perps"
        _statuses = () if _is_perp else ("OPEN", "PENDING")  # CDE rejects all active-status filters

        def _call():
            base_kw: dict = {"limit": 100}
            mr = _list_orders_merged(lo, base_kw, _statuses)
            if not mr.merge_ok:
                LOG.warning(
                    "CoinbaseOrderManager: all-products open-order snapshot incomplete failed_statuses=%s",
                    mr.failed_statuses,
                )
            return [CoinbaseOrderManager._slim_open_order_for_snapshot(od) for od in mr.orders if od]

        try:
            await self._limiter.acquire()
            return await asyncio.to_thread(_call)
        except Exception as e:
            LOG.warning(
                "CoinbaseOrderManager: list_orders resting (all products) failed — %s",
                e,
                exc_info=True,
            )
            return []

    async def refresh_scalp_exchange_snapshots(self) -> None:
        """Refresh cached open-order lists; re-check resting stop/TP for open legs (no perps-portfolio APIs)."""
        if self._scalp_runtime is None:
            return
        if bool(getattr(self._scalp_cfg, "sim_mode", False)):
            return
        if str(getattr(self._scalp_cfg, "venue", "") or "").strip().lower() != "coinbase_perps":
            return

        client = self._ensure_client()
        try:
            self._last_scalp_open_orders = await self._fetch_open_orders_scalp(client)
        except Exception:
            LOG.debug("CoinbaseOrderManager: open-order fetch failed", exc_info=True)

        try:
            self._last_all_open_orders = await self._fetch_all_open_orders(client)
        except Exception:
            LOG.debug("CoinbaseOrderManager: open-order fetch (all products) failed", exc_info=True)

        outside = self.scalp_open_orders_outside_config_snapshot()
        if outside:
            sig = "|".join(
                sorted(f"{o.get('product_id')}:{o.get('order_id')}" for o in outside),
            )
            if sig != self._last_outside_open_orders_sig:
                self._last_outside_open_orders_sig = sig
                LOG.info(
                    "CoinbaseOrderManager: %d resting order(s) outside configured scalp symbols: %s",
                    len(outside),
                    ", ".join(str(o.get("product_id") or "") for o in outside[:12]),
                )

        rt = self._scalp_runtime
        if rt is not None and bool(rt._cfg.enabled) and not bool(rt._trader.sim_mode):
            seen: set[str] = set()
            for pos in list(rt._trader._positions.values()):
                if pos.status != "open" or pos.pair_key in seen:
                    continue
                seen.add(pos.pair_key)
                try:
                    await rt._trader.ensure_coinbase_protectives_match_exchange(pos.pair_key)
                except Exception:
                    LOG.debug(
                        "CoinbaseOrderManager: ensure_coinbase_protectives_match_exchange failed",
                        exc_info=True,
                    )
