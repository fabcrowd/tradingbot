"""Persisted snapshot of fee assumptions used for WFO / backtests.

Compared on startup to ``config.toml`` so operators know when to rerun WFO after
tier or fee changes. Optional champion invalidation clears stale ``scalp_champion.json``
rows when assumptions drift.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scalp_config import ScalpBotConfig

LOG = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
FEE_ASSUMPTION_STATE_PATH = DATA_DIR / "scalp_fee_assumption_state.json"

_COMPARABLE_KEYS = (
    "revision",
    "fee_bps_per_leg",
    "fee_bps_taker_per_leg",
    "fee_usd_per_contract_per_leg",
    "order_type",
)


def fee_assumption_snapshot(cfg: "ScalpBotConfig") -> dict:
    vol = getattr(cfg, "fee_tier_30d_volume_usd", None)
    return {
        "revision": int(getattr(cfg, "scalp_fee_assumption_revision", 0) or 0),
        "fee_bps_per_leg": float(cfg.fee_bps_per_leg),
        "fee_bps_taker_per_leg": float(cfg.fee_bps_taker_per_leg),
        "fee_usd_per_contract_per_leg": float(cfg.fee_usd_per_contract_per_leg),
        "order_type": str(cfg.order_type),
        "fee_tier_30d_volume_usd": (float(vol) if vol is not None else None),
        "updated_ts": time.time(),
    }


def load_fee_assumption_state(path: Path = FEE_ASSUMPTION_STATE_PATH) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else None
    except Exception:
        LOG.warning("scalp_fee_assumptions: failed to read %s", path, exc_info=True)
        return None


def save_fee_assumption_state(
    snapshot: dict,
    path: Path = FEE_ASSUMPTION_STATE_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, default=str)
    tmp.replace(path)
    LOG.info("scalp_fee_assumptions: wrote %s", path.name)


def fee_assumption_changed(prev: dict | None, cur: dict) -> bool:
    if prev is None:
        return False
    return any(prev.get(k) != cur.get(k) for k in _COMPARABLE_KEYS)


def reconcile_fee_assumptions_on_startup(
    cfg: "ScalpBotConfig",
    *,
    auto_invalidate_champions: bool,
) -> bool:
    """If on-disk fee snapshot differs from config, log and optionally clear champions.

    Always writes the current snapshot after handling so restarts do not loop.
    Returns True when champions may have been removed (caller should reload champion file).
    """
    cur = fee_assumption_snapshot(cfg)
    prev = load_fee_assumption_state()
    if prev is None:
        save_fee_assumption_state(cur)
        return False

    if not fee_assumption_changed(prev, cur):
        return False

    LOG.warning(
        "scalp_fee_assumptions: fee/tier assumptions changed vs %s "
        "(revision %s→%s maker_bps %s→%s taker_bps %s→%s order_type %s→%s). "
        "Rerun WFO after fee tier updates.",
        FEE_ASSUMPTION_STATE_PATH.name,
        prev.get("revision"),
        cur.get("revision"),
        prev.get("fee_bps_per_leg"),
        cur.get("fee_bps_per_leg"),
        prev.get("fee_bps_taker_per_leg"),
        cur.get("fee_bps_taker_per_leg"),
        prev.get("order_type"),
        cur.get("order_type"),
    )

    invalidated = False
    if auto_invalidate_champions:
        from .scalp_wfo import load_champion, remove_champion_for_symbol

        store = load_champion()
        if store:
            for sym in list(store.keys()):
                if remove_champion_for_symbol(sym):
                    invalidated = True
            if invalidated:
                LOG.warning(
                    "scalp_fee_assumptions: removed champion row(s) after fee assumption change",
                )

    save_fee_assumption_state(cur)
    return invalidated
