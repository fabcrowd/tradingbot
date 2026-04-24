"""Tests for ParamSet merge helpers used in champion / tuner reconstruction."""

from __future__ import annotations

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_vec_backtest import ParamSet, apply_param_dict_overrides
from scalp_bot.scalp_wfo import param_set_from_champion_row


def test_apply_param_dict_overrides_coerces_and_ignores_unknown() -> None:
    base = ParamSet(mode="daviddtech_scalp", t3_length=7)
    out = apply_param_dict_overrides(base, {"t3_length": 11.0, "bogus": 1})
    assert out.t3_length == 11
    assert out.mode == "daviddtech_scalp"


def test_param_set_from_champion_row_merges_over_pair_defaults() -> None:
    pair = ScalpPairConfig(symbol="SLP-20DEC30-CDE", interval=5, t3_length=7)
    bot = ScalpBotConfig()
    row = {
        "symbol": "SLP-20DEC30-CDE",
        "interval": 5,
        "mode": "daviddtech_scalp",
        "params": {"t3_length": 11, "atr_stop_mult": 2.5},
    }
    ps = param_set_from_champion_row(row, pair, bot)
    assert ps is not None
    assert ps.t3_length == 11
    assert ps.atr_stop_mult == 2.5
    assert ps.mode == "daviddtech_scalp"


def test_param_set_from_champion_row_none_without_params() -> None:
    pair = ScalpPairConfig(symbol="X", interval=1)
    bot = ScalpBotConfig()
    assert param_set_from_champion_row({"symbol": "X", "mode": "ema_momentum"}, pair, bot) is None
    assert param_set_from_champion_row(None, pair, bot) is None


def test_param_set_from_champion_row_rejects_interval_mismatch() -> None:
    pair = ScalpPairConfig(symbol="SLP-20DEC30-CDE", interval=5)
    bot = ScalpBotConfig()
    row = {
        "symbol": "SLP-20DEC30-CDE",
        "interval": 15,
        "mode": "daviddtech_scalp",
        "params": {"t3_length": 99},
    }
    assert param_set_from_champion_row(row, pair, bot) is None
