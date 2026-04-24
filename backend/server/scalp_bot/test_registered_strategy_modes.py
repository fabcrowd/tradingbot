"""WFO backtest and champion I/O only accept registered strategy ``mode`` strings."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from scalp_bot.scalp_vec_backtest import (
    ParamSet,
    WFO_REGISTERED_STRATEGY_MODES,
    build_default_grid,
    evaluate_params,
)
from scalp_bot.scalp_wfo import save_champion


def _minimal_bars(n: int = 200) -> dict[str, np.ndarray]:
    ts = np.array([1_700_000_000 + i * 300 for i in range(n)], dtype=np.int64)
    o = np.ones(n, dtype=np.float64)
    z = np.zeros(n, dtype=np.int64)
    return {
        "timestamp": ts,
        "open": o,
        "high": o,
        "low": o,
        "close": o,
        "volume": o,
        "vwap": o,
        "trades": z,
    }


def test_evaluate_params_unknown_mode_raises() -> None:
    bars = _minimal_bars()
    p = replace(ParamSet(), mode="vwap_indicator_only_typo")
    with pytest.raises(ValueError, match="unknown strategy mode"):
        evaluate_params(bars, p)


def test_save_champion_rejects_unknown_mode(tmp_path) -> None:
    bad = {
        "symbol": "X-SYM",
        "mode": "not_a_registered_strategy",
        "score": 1.0,
        "params": {"mode": "not_a_registered_strategy"},
    }
    with pytest.raises(ValueError, match="save_champion refused"):
        save_champion(bad, path=tmp_path / "champ.json")


def test_build_default_grid_modes_are_registered() -> None:
    modes = {p.mode for p in build_default_grid()}
    assert modes <= WFO_REGISTERED_STRATEGY_MODES


def test_registry_contains_all_expected_modes() -> None:
    expected = {
        "daviddtech_scalp", "ema_momentum", "ema_scalp", "macd_scalp",
        "rsi_reversion", "supertrend", "squeeze_momentum", "qqe_mod",
        "utbot_alert", "hull_suite", "sar_chop",
    }
    assert expected <= WFO_REGISTERED_STRATEGY_MODES, (
        "Registry missing modes; update WFO_REGISTERED_STRATEGY_MODES."
    )


def test_sar_chop_has_grid_entries() -> None:
    """sar_chop was added 2026-04-16 — guard against grid regressions."""
    sar_chop_entries = [p for p in build_default_grid() if p.mode == "sar_chop"]
    assert len(sar_chop_entries) > 0, "sar_chop missing from build_default_grid()"


def test_sar_chop_evaluate_does_not_raise_on_flat_bars() -> None:
    """Flat bars produce zero signals, but the detector must not crash."""
    bars = _minimal_bars(n=500)
    p = replace(ParamSet(), mode="sar_chop")
    m = evaluate_params(bars, p)
    # Flat series — expect 0 trades but a well-formed metrics object.
    assert m.trade_count == 0
