"""ScalpRuntime.on_fill routes emergency / protective market exit client IDs."""

from __future__ import annotations

import asyncio

import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_runtime import ScalpRuntime
from scalp_bot.scalp_trader import ScalpPosition


class _MockSessionLog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def log_scalp(self, subtype: str, **kwargs: object) -> None:
        self.calls.append((subtype, dict(kwargs)))


class _MockLiveMgr:
    async def cancel_all_scalp_open_orders(self) -> int:
        return 0

    async def cancel_order(self, _oid: str) -> None:
        return None


@pytest.fixture
def bot_state():
    from state import BotState

    return BotState()


@pytest.mark.parametrize(
    ("prefix", "reason"),
    [
        ("scalp_eflat_", "emergency_flatten"),
        ("scalp_mclose_", "user_manual_close"),
        ("scalp_prot_", "protective_failure_flatten"),
    ],
)
def test_on_fill_routes_market_exit_prefixes(bot_state, prefix: str, reason: str) -> None:
    mock_log = _MockSessionLog()
    cfg = ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        sim_mode=False,
        shorts_enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="TEST-PERP", interval=5)},
    )
    rt = ScalpRuntime(bot_state, cfg, live_mgr=_MockLiveMgr(), session_logger=mock_log)
    pos = ScalpPosition(
        pair_key="p1",
        symbol="TEST-PERP",
        direction="long",
        entry_price=100.0,
        stop_price=99.0,
        tp_price=102.0,
        qty=2.0,
        entry_cl_ord_id="scalp_entry_onfill",
        stop_cl_ord_id="scalp_stop_onfill",
        tp_cl_ord_id="scalp_tp_onfill",
        status="open",
        contract_size=1.0,
    )
    rt._trader._positions[pos.entry_cl_ord_id] = pos
    exit_id = f"{prefix}deadbeef"
    rt._trader._link_market_exit_order(exit_id, pos.entry_cl_ord_id)
    rt._trader.register_pending_market_exit(exit_id, pos, reason, 100.0)

    asyncio.run(rt.on_fill("p1", exit_id, 99.5, 2.0, fee_usd=0.02))

    fe = [c for c in mock_log.calls if c[0] == "scalp_fill_execution"]
    assert len(fe) == 1
    row = fe[0][1]
    assert row["leg"] == "exit"
    assert row["order_type"] == "market"
    assert row["close_reason"] == reason
    assert row["fill_price"] == 99.5
    assert row["fee_usd"] == 0.02
