"""Live circuit breaker (P3) — default disabled; unit behavior when enabled."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_runtime import ScalpRuntime


def test_circuit_breaker_disabled_by_default() -> None:
    from state import BotState

    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="T-CB", interval=5)},
        wfo_live_circuit_breaker_enabled=False,
    )
    rt = ScalpRuntime(BotState(), cfg, live_mgr=None, session_logger=None)
    rt._mode_source["p1"] = "wfo_champion"
    rt._champion_period_start["p1"] = time.time() - 3600
    rt._champion_data = {
        "T-CB": {
            "symbol": "T-CB",
            "holdout_metrics": {"max_drawdown": 10.0, "expectancy": 1.0},
        },
    }
    rt._trader.forward_pnl_since = MagicMock(return_value=-1000.0)  # type: ignore[method-assign]
    rt._trader.forward_trades_since = MagicMock(return_value=20)  # type: ignore[method-assign]
    rt._check_live_circuit_breaker()
    assert rt._mode_source["p1"] == "wfo_champion"


def test_circuit_breaker_trips_when_enabled() -> None:
    from state import BotState

    cfg = ScalpBotConfig(
        enabled=True,
        auto_mode_fallback="sar_chop",
        pairs={"p1": ScalpPairConfig(symbol="T-CB2", interval=5)},
        wfo_live_circuit_breaker_enabled=True,
        wfo_live_circuit_breaker_dd_mult=2.0,
        wfo_forward_min_trades=5,
    )
    rt = ScalpRuntime(BotState(), cfg, live_mgr=None, session_logger=None)
    rt._active_mode["p1"] = "ema_momentum"
    rt._mode_source["p1"] = "wfo_champion"
    rt._champion_period_start["p1"] = time.time() - 3600
    rt._champion_data = {
        "T-CB2": {
            "symbol": "T-CB2",
            "holdout_metrics": {"max_drawdown": 50.0, "expectancy": 2.0},
        },
    }
    rt._trader.forward_pnl_since = MagicMock(return_value=-200.0)  # type: ignore[method-assign]
    rt._trader.forward_trades_since = MagicMock(return_value=10)  # type: ignore[method-assign]
    with patch("scalp_bot.scalp_runtime.remove_champion_for_symbol", return_value=True):
        rt._check_live_circuit_breaker()
    assert rt._mode_source["p1"] == "live_circuit_breaker"
    assert rt._active_mode["p1"] == "sar_chop"
