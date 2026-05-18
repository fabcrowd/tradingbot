"""Ghost legs cleared when exchange reports flat for symbol."""

from __future__ import annotations

import asyncio

import pytest

from server.scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from server.scalp_bot.scalp_runtime import ScalpRuntime
from server.scalp_bot.scalp_trader import ScalpPosition


@pytest.fixture
def bot_state():
    from state import BotState

    return BotState()


def test_apply_reconcile_clears_ghost_when_exchange_flat(bot_state) -> None:
    from server.coinbase_intx_reconcile import VenueReconcileSnapshot

    cfg = ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        sim_mode=False,
        pairs={
            "BTC_USD": ScalpPairConfig(symbol="BIP-20DEC30-CDE", interval=5),
            "SOL_USD": ScalpPairConfig(symbol="SLP-20DEC30-CDE", interval=5),
        },
    )
    rt = ScalpRuntime(bot_state, cfg, live_mgr=None, session_logger=None)
    ghost = ScalpPosition(
        pair_key="BTC_USD",
        symbol="BIP-20DEC30-CDE",
        direction="long",
        entry_price=80000.0,
        stop_price=79000.0,
        tp_price=82000.0,
        qty=1.0,
        entry_cl_ord_id="scalp_entry_ghost",
        status="open",
        contract_size=1.0,
    )
    rt._trader._positions[ghost.entry_cl_ord_id] = ghost

    snap = VenueReconcileSnapshot(
        legs=(),
        flat_product_ids=frozenset({"BIP-20DEC30-CDE", "SLP-20DEC30-CDE"}),
        venue_ok=True,
    )
    asyncio.run(rt.apply_intx_position_reconciliation(snap))
    assert ghost.status == "closed"
    assert "scalp_entry_ghost" not in rt._trader._positions
