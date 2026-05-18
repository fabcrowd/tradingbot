"""Protective order margin helpers and entry-throttle exclusions."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from server.coinbase_order_manager import (  # noqa: E402
    CoinbaseOrderManager,
    _is_insufficient_funds_reason,
    _is_protective_scalp_cl_ord_id,
)
from server.scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig  # noqa: E402
from server.scalp_bot.scalp_trader import ScalpPosition, ScalpTrader, SignalEngine  # noqa: E402
from server.state import BotState  # noqa: E402


def test_is_protective_scalp_cl_ord_id() -> None:
    assert _is_protective_scalp_cl_ord_id("scalp_stop_abc")
    assert _is_protective_scalp_cl_ord_id("scalp_tp_xyz")
    assert _is_protective_scalp_cl_ord_id("scalp_prot_dead")
    assert not _is_protective_scalp_cl_ord_id("scalp_entry_abc")


def test_is_insufficient_funds_reason() -> None:
    assert _is_insufficient_funds_reason("PREVIEW_INSUFFICIENT_FUNDS_FOR_FUTURES")
    assert not _is_insufficient_funds_reason("PREVIEW_INVALID_PRICE_PRECISION")


class _BalMgr:
    def __init__(self, available_margin: float) -> None:
        self._avail = available_margin

    def futures_available_margin_usd(self) -> float:
        return self._avail


def test_margin_ok_for_second_protective() -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        max_leverage=2.0,
        buying_power_buffer_usd=50.0,
        pairs={"SOL_USD": ScalpPairConfig(symbol="SLP-20DEC30-CDE", interval=5)},
    )
    tr = ScalpTrader(BotState(), cfg, SignalEngine(), _BalMgr(500.0))
    pos = ScalpPosition(
        pair_key="SOL_USD",
        symbol="SLP-20DEC30-CDE",
        direction="short",
        entry_price=90.0,
        stop_price=92.0,
        tp_price=88.0,
        qty=1.0,
        entry_cl_ord_id="e1",
        contract_size=5.0,
    )
    # entry margin = 1 * 5 * 90 / 2 = 225
    assert tr._margin_ok_for_second_protective(pos) is True
    tr._live_mgr = _BalMgr(100.0)  # type: ignore[assignment]
    assert tr._margin_ok_for_second_protective(pos) is False


def test_resting_order_kind_and_product_match() -> None:
    assert CoinbaseOrderManager._resting_order_kind({"trigger_price": 85.5}) == "stop"
    assert CoinbaseOrderManager._resting_order_kind({"client_order_id": "scalp_tp_abc"}) == "tp"
    assert CoinbaseOrderManager._product_id_matches_configured(
        "SLP-20DEC30-CDE", "SOL-PERP",
    )
    mgr = CoinbaseOrderManager.__new__(CoinbaseOrderManager)
    mgr._last_scalp_open_orders = [
        {
            "product_id": "SOL-PERP",
            "side": "BUY",
            "trigger_price": 85.6,
            "limit_price": 85.65,
            "order_id": "o1",
            "client_order_id": "",
        },
        {
            "product_id": "SOL-PERP",
            "side": "BUY",
            "trigger_price": 85.5,
            "limit_price": 85.55,
            "order_id": "o2",
            "client_order_id": "",
        },
    ]
    mgr._last_all_open_orders = []
    stops, tps = mgr.resting_protectives_for_product("SLP-20DEC30-CDE", "buy")
    assert len(stops) == 2
    assert len(tps) == 0


def test_coinbase_protective_reject_skips_entry_if_timer() -> None:
    st = BotState()
    cfg = ScalpBotConfig(enabled=True, venue="coinbase_perps")
    app_cfg = type("AC", (), {"rate_limit_order_per_sec": 1.0, "rate_limit_burst": 5})()
    mgr = CoinbaseOrderManager(st, app_cfg, cfg)  # type: ignore[arg-type]
    mgr._balances = {
        "futures": {
            "buying_power": 100.0,
            "available_margin": 10.0,
            "open_orders_hold_usd": 90.0,
        },
    }
    mgr._coinbase_note_order_reject(
        "PREVIEW_INSUFFICIENT_FUNDS_FOR_FUTURES",
        cl_ord_id="scalp_stop_deadbeef",
    )
    assert st.insufficient_funds_until == 0.0
    mgr._coinbase_note_order_reject(
        "PREVIEW_INSUFFICIENT_FUNDS_FOR_FUTURES",
        cl_ord_id="scalp_entry_deadbeef",
    )
    assert st.insufficient_funds_until > time.time()
