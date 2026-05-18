"""Parse Coinbase INTX / perps position API rows for scalp reconciliation."""

from __future__ import annotations

from dataclasses import dataclass


def _safe_float(x: object) -> float:
    if x is None or x == "":
        return 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _as_plain_dict(obj: object) -> dict:
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


@dataclass(frozen=True)
class VenuePerpLeg:
    product_id: str
    pair_key: str | None
    direction: str  # "long" | "short"
    qty: float
    entry_price: float


def _signed_net_size(row: dict) -> float:
    side = str(row.get("side") or row.get("position_side") or row.get("positionSide") or "").upper()
    for key in ("number_of_contracts", "numberOfContracts", "net_size", "netSize", "size"):
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        v = _safe_float(raw)
        if v == 0.0:
            continue
        if side in ("SHORT", "SELL"):
            return -abs(v)
        if side in ("LONG", "BUY"):
            return abs(v)
        return v
    return 0.0


def _entry_vwap(row: dict) -> float:
    for key in (
        "avg_entry_price",
        "avgEntryPrice",
        "entry_vwap",
        "entryVwap",
        "average_entry_price",
        "averageEntryPrice",
        "vwap",
        "avg_entry_price",
    ):
        p = _safe_float(row.get(key))
        if p > 0:
            return p
    return 0.0


def parse_perps_position_row(row: object, product_to_key: dict[str, str]) -> VenuePerpLeg | None:
    """Normalize one ``list_perps_positions`` / ``get_perps_position`` row."""
    d = _as_plain_dict(row)
    pid = str(d.get("product_id") or d.get("productId") or d.get("symbol") or "").strip()
    if not pid:
        return None
    net = _signed_net_size(d)
    if abs(net) < 1e-12:
        return None
    direction = "long" if net > 0 else "short"
    qty = abs(net)
    entry = _entry_vwap(d)
    pk = product_to_key.get(pid.upper()) or product_to_key.get(pid)
    return VenuePerpLeg(
        product_id=pid,
        pair_key=pk,
        direction=direction,
        qty=float(qty),
        entry_price=float(entry),
    )


def venue_legs_by_product(rows: list[object], product_to_key: dict[str, str]) -> dict[str, VenuePerpLeg]:
    out: dict[str, VenuePerpLeg] = {}
    for row in rows:
        leg = parse_perps_position_row(row, product_to_key)
        if leg is None:
            continue
        out[leg.product_id.upper()] = leg
    return out


@dataclass(frozen=True)
class VenueReconcileSnapshot:
    """Exchange truth from the last FCM / perps position poll."""

    legs: tuple[VenuePerpLeg, ...]
    flat_product_ids: frozenset[str]  # configured symbols probed with zero size
    venue_ok: bool  # at least one successful venue read this cycle
