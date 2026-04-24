"""Session JSONL scalp_fill_execution on sim entry/exit."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_trader import ScalpTrader
from scalp_bot.signal_engine import ScalpSignal, SignalEngine


class _MockSessionLog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def log_scalp(self, subtype: str, **kwargs: object) -> None:
        self.calls.append((subtype, dict(kwargs)))


@pytest.fixture
def bot_state():
    from state import BotState

    return BotState()


def test_sim_entry_exit_emits_scalp_fill_execution(bot_state) -> None:
    mock_log = _MockSessionLog()
    cfg = ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        shorts_enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="T-FILL", interval=5)},
    )
    trader = ScalpTrader(bot_state, cfg, SignalEngine(), None, mock_log)
    trader.sim_mode = True
    trader._entries_paused_fn = lambda: False

    sig = ScalpSignal(
        pair_key="p1",
        symbol="T-FILL",
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
    assert ok is True

    fe = [c for c in mock_log.calls if c[0] == "scalp_fill_execution"]
    assert len(fe) == 1
    assert fe[0][1]["leg"] == "entry"
    assert fe[0][1]["fill_price"] == 100.0

    # Stop hit: long, stop 99, candle low through stop
    candle = SimpleNamespace(low=98.5, high=100.5)
    trader.check_paper_exits("p1", candle)

    fe2 = [c for c in mock_log.calls if c[0] == "scalp_fill_execution"]
    assert len(fe2) == 2
    assert fe2[1][1]["leg"] == "exit"
    assert fe2[1][1]["close_reason"] == "stop"
    assert fe2[1][1]["order_type"] == "sim_close"


def test_pending_market_exit_fill_log(bot_state) -> None:
    mock_log = _MockSessionLog()
    cfg = ScalpBotConfig(
        enabled=True,
        venue="coinbase_perps",
        pairs={"p1": ScalpPairConfig(symbol="T-MKT", interval=5)},
    )
    trader = ScalpTrader(bot_state, cfg, SignalEngine(), None, mock_log)

    from scalp_bot.scalp_trader import ScalpPosition

    p = ScalpPosition(
        pair_key="p1",
        symbol="T-MKT",
        direction="long",
        entry_price=100.0,
        stop_price=99.0,
        tp_price=102.0,
        qty=1.0,
        entry_cl_ord_id="scalp_entry_test",
        contract_size=1.0,
        status="open",
        entry_signal_price=100.0,
        entry_order_type="limit",
    )
    trader._positions["scalp_entry_test"] = p
    trader.register_pending_market_exit("scalp_tstop_abc", p, "time_stop", 100.25)
    trader.on_market_exit_fill("scalp_tstop_abc", 100.1, 1.0, fee_usd=0.01)

    fe = [c for c in mock_log.calls if c[0] == "scalp_fill_execution"]
    assert len(fe) == 1
    row = fe[0][1]
    assert row["leg"] == "exit"
    assert row["order_type"] == "market"
    assert row["fee_usd"] == 0.01
    assert row["reference_price"] == 100.25
    assert row["close_reason"] == "time_stop"
