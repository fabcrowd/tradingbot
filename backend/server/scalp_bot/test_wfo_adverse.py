"""Adverse WFO holdout re-check (no full grid)."""

from __future__ import annotations

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_wfo import WFOConfig, run_adverse_wfo_holdout_check


def test_adverse_check_skips_when_no_bars(monkeypatch) -> None:
    import scalp_bot.scalp_wfo as wfo_mod

    monkeypatch.setattr(wfo_mod.bar_store, "load_bars", lambda *a, **kw: None)
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="X", interval=5)},
    )
    ok, reason, diag = run_adverse_wfo_holdout_check(
        cfg,
        WFOConfig(),
        cfg.pairs["p1"],
        {"symbol": "X", "interval": 5, "mode": "ema_momentum", "params": {}, "score": 1.0},
    )
    assert ok is True
    assert reason is None
    assert diag.get("adverse_skipped") == "no_bars_in_store"
