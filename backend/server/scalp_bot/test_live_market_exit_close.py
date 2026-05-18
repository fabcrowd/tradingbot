"""Live market exits defer _close_position until venue fill confirms."""

from __future__ import annotations

import asyncio

import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_runtime import ScalpRuntime
from scalp_bot.scalp_trader import ScalpPosition


class _RejectMgr:
    async def add_order(self, params: dict) -> str:
        return ""

    async def flatten_scalp_leg_market(self, **kwargs: object) -> str:
        return ""


class _AcceptMgr:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def add_order(self, params: dict) -> str:
        self.calls.append(dict(params))
        return str(params.get("cl_ord_id") or "ok")

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


def test_protective_flatten_rejected_keeps_position_open(bot_state) -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        sim_mode=False,
        pairs={"SOL_USD": ScalpPairConfig(symbol="SLP-20DEC30-CDE", interval=1)},
    )
    rt = ScalpRuntime(bot_state, cfg, live_mgr=_RejectMgr(), session_logger=None)
    pos = ScalpPosition(
        pair_key="SOL_USD",
        symbol="SLP-20DEC30-CDE",
        direction="short",
        entry_price=91.0,
        stop_price=92.0,
        tp_price=89.0,
        qty=5.0,
        entry_cl_ord_id="scalp_entry_x",
        status="open",
        contract_size=1.0,
    )
    rt._trader._positions[pos.entry_cl_ord_id] = pos
    asyncio.run(
        rt._trader._flatten_live_after_protective_failure(pos, "reconcile_stop_failed", 91.2),
    )
    assert pos.status == "open"
    assert pos.entry_cl_ord_id in rt._trader._positions


def test_market_exit_fill_closes_open_leg(bot_state) -> None:
    mock_log_calls: list[tuple[str, dict]] = []

    class _Log:
        def log_scalp(self, subtype: str, **kwargs: object) -> None:
            mock_log_calls.append((subtype, dict(kwargs)))

    cfg = ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        sim_mode=False,
        pairs={"p1": ScalpPairConfig(symbol="TEST-PERP", interval=5)},
    )
    rt = ScalpRuntime(bot_state, cfg, live_mgr=_AcceptMgr(), session_logger=_Log())
    pos = ScalpPosition(
        pair_key="p1",
        symbol="TEST-PERP",
        direction="long",
        entry_price=100.0,
        stop_price=99.0,
        tp_price=102.0,
        qty=2.0,
        entry_cl_ord_id="scalp_entry_fill",
        status="open",
        contract_size=1.0,
    )
    rt._trader._positions[pos.entry_cl_ord_id] = pos
    exit_id = "scalp_prot_deadbeef"
    rt._trader._link_market_exit_order(exit_id, pos.entry_cl_ord_id)
    rt._trader.register_pending_market_exit(exit_id, pos, "reconcile_stop_failed", 99.5)

    asyncio.run(rt.on_fill("p1", exit_id, 99.4, 2.0))

    assert pos.status == "closed"
    assert pos.entry_cl_ord_id not in rt._trader._positions
    fe = [c for c in mock_log_calls if c[0] == "scalp_fill_execution"]
    assert len(fe) == 1
