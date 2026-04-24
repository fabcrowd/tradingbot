"""Scalp portfolio halt (BotState.scalp_risk_halted) and entry gates."""

from __future__ import annotations

import asyncio

import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_trader import ScalpTrader
from scalp_bot.signal_engine import ScalpSignal, SignalEngine


@pytest.fixture
def bot_state():
    from state import BotState

    return BotState()


def test_scalp_entries_blocked_only_scalp_halt(bot_state) -> None:
    bot_state.scalp_risk_halted = True
    assert bot_state.scalp_entries_blocked() is True


def test_scalp_entries_blocked_mm_combo(bot_state) -> None:
    bot_state.mm_spread_bot_enabled = True
    bot_state.risk_halted = True
    assert bot_state.scalp_entries_blocked() is True


def test_scalp_entries_not_blocked_mm_off(bot_state) -> None:
    bot_state.mm_spread_bot_enabled = False
    bot_state.risk_halted = True
    assert bot_state.scalp_entries_blocked() is False


def test_try_open_blocked_when_scalp_halt(bot_state) -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        shorts_enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="T-HALT", interval=5)},
    )
    trader = ScalpTrader(bot_state, cfg, SignalEngine(), None, None)
    trader.sim_mode = True
    trader._entries_paused_fn = lambda: False
    bot_state.scalp_risk_halted = True
    sig = ScalpSignal(
        pair_key="p1",
        symbol="T-HALT",
        direction="long",
        entry_price=100.0,
        stop_price=99.0,
        tp_price=102.0,
        atr=1.0,
        signals_hit=["test"],
        confidence=0.5,
        mode="ema_momentum",
    )
    ok = asyncio.run(trader.try_open(sig, cfg.pairs["p1"], 10_000.0))
    assert ok is False


def test_set_clear_scalp_risk_halt_runtime(bot_state) -> None:
    from scalp_bot.scalp_runtime import ScalpRuntime

    state = bot_state
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
    )
    rt = ScalpRuntime(state, cfg, live_mgr=None, session_logger=None)
    rt.set_scalp_risk_halt("test", "unit")
    assert state.scalp_risk_halted is True
    rt.clear_scalp_risk_halt("unit")
    assert state.scalp_risk_halted is False
