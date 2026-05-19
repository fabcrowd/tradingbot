"""Tests for require_champion_to_trade and warm-up gating."""

from __future__ import annotations

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_runtime import ScalpRuntime, WarmupPhase
from scalp_bot.strategy_lookback import live_entry_allowed_champion_gate
from state import BotState


def _runtime(**cfg_kw: object) -> ScalpRuntime:
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="T", interval=5)},
        warmup_min_bars=10,
        warmup_require_champion=True,
        require_champion_to_trade=True,
        wfo_enabled=True,
        **cfg_kw,
    )
    rt = ScalpRuntime(BotState(), cfg, live_mgr=None, session_logger=None)
    rt._warmup_phase = WarmupPhase.COLLECTING
    rt._startup_wfo_done = True
    rt._startup_wfo_succeeded = True
    rt._warmup_bars_collected = {"p1": 100}
    return rt


def test_warmup_blocks_without_champion_when_require_champion_to_trade() -> None:
    rt = _runtime()
    rt._warmup_champion_found = False
    assert rt._check_warmup_complete() is False
    assert rt._warmup_phase == WarmupPhase.COLLECTING


def test_warmup_completes_when_champion_found() -> None:
    rt = _runtime(require_manual_go_live=True)
    rt._warmup_champion_found = True
    assert rt._check_warmup_complete() is True
    assert rt._warmup_phase == WarmupPhase.READY


def test_champion_gate_blocks_stale_wfo_champion_label_without_disk_row() -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="T", interval=5)},
        require_champion_to_trade=True,
    )
    pc = cfg.pairs["p1"]
    assert live_entry_allowed_champion_gate(cfg, {}, pc, "wfo_champion") is False
    assert live_entry_allowed_champion_gate(cfg, {}, pc, "bootstrap") is False


def test_champion_gate_allows_disk_row_and_wfo_source() -> None:
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol="T", interval=5)},
        require_champion_to_trade=True,
    )
    pc = cfg.pairs["p1"]
    store = {"T": {"symbol": "T", "interval": 5, "mode": "sar_chop", "params": {"mode": "sar_chop"}}}
    assert live_entry_allowed_champion_gate(cfg, store, pc, "wfo_champion") is True
    assert live_entry_allowed_champion_gate(cfg, store, pc, "bootstrap") is False
    assert live_entry_allowed_champion_gate(cfg, store, pc, "nemesis_tuner") is False


def test_apply_no_champion_bootstrap_demotes_stale_wfo_champion_source() -> None:
    rt = _runtime()
    rt._mode_source["p1"] = "wfo_champion"
    rt._active_mode["p1"] = "sar_chop"
    rt._champion_data = {}
    rt._apply_no_champion_bootstrap(champ_store={})
    assert rt._mode_source["p1"] == "bootstrap"
