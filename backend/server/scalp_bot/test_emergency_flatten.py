"""Emergency flatten: reduce-only market + halt."""

from __future__ import annotations

import asyncio

import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_runtime import ScalpRuntime
from scalp_bot.scalp_trader import ScalpPosition


class _MockLiveMgr:
    def __init__(self) -> None:
        self.add_calls: list[dict] = []

    async def cancel_all_scalp_open_orders(self) -> int:
        return 0

    async def cancel_order(self, _oid: str) -> None:
        return None

    async def add_order(self, params: dict) -> str:
        self.add_calls.append(dict(params))
        return "ok"

    async def flatten_scalp_leg_market(self, **kwargs: object) -> str:
        return await self.add_order(
            {
                "symbol": kwargs["symbol"],
                "side": kwargs["side"],
                "order_type": "market",
                "order_qty": kwargs["order_qty"],
                "cl_ord_id": kwargs["cl_ord_id"],
                "reduce_only": kwargs.get("reduce_only", True),
            },
        )


@pytest.fixture
def bot_state():
    from state import BotState

    return BotState()


def test_emergency_flatten_live_reduce_only_market(bot_state) -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        sim_mode=False,
        shorts_enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="TEST-PERP", interval=5)},
    )
    mgr = _MockLiveMgr()
    rt = ScalpRuntime(bot_state, cfg, live_mgr=mgr, session_logger=None)
    pos = ScalpPosition(
        pair_key="p1",
        symbol="TEST-PERP",
        direction="long",
        entry_price=100.0,
        stop_price=99.0,
        tp_price=102.0,
        qty=2.0,
        entry_cl_ord_id="scalp_entry_x1",
        stop_cl_ord_id="scalp_stop_x1",
        tp_cl_ord_id="scalp_tp_x1",
        status="open",
        contract_size=1.0,
    )
    rt._trader._positions[pos.entry_cl_ord_id] = pos

    n = asyncio.run(rt.emergency_flatten_all_positions("unit_test", source="pytest"))
    assert n == 1
    assert len(mgr.add_calls) == 1
    assert mgr.add_calls[0]["order_type"] == "market"
    assert mgr.add_calls[0]["reduce_only"] is True
    assert mgr.add_calls[0]["side"] == "sell"
    assert bot_state.scalp_risk_halted is True
    # Live flatten defers close until fill — leg stays open until on_fill.
    assert pos.status == "open"
    assert pos.entry_cl_ord_id in rt._trader._positions


def test_manual_close_live_reduce_only_no_halt(bot_state) -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        sim_mode=False,
        shorts_enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="TEST-PERP", interval=5)},
    )
    mgr = _MockLiveMgr()
    rt = ScalpRuntime(bot_state, cfg, live_mgr=mgr, session_logger=None)
    pos = ScalpPosition(
        pair_key="p1",
        symbol="TEST-PERP",
        direction="long",
        entry_price=100.0,
        stop_price=99.0,
        tp_price=102.0,
        qty=2.0,
        entry_cl_ord_id="scalp_entry_m1",
        stop_cl_ord_id="scalp_stop_m1",
        tp_cl_ord_id="scalp_tp_m1",
        status="open",
        contract_size=1.0,
    )
    rt._trader._positions[pos.entry_cl_ord_id] = pos

    n = asyncio.run(rt.manual_close_all_open_positions("unit_test", source="pytest"))
    assert n == 1
    assert len(mgr.add_calls) == 1
    assert mgr.add_calls[0]["cl_ord_id"].startswith("scalp_mclose_")
    assert bot_state.scalp_risk_halted is False
