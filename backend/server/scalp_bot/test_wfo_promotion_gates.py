"""WFO promotion fingerprints, JSONL, meta, and optional cooldown / beat-prior gates."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scalp_bot.scalp_config import ScalpBotConfig, ScalpPairConfig
from scalp_bot.scalp_wfo import (
    ScalpWalkForwardOptimizer,
    WFOConfig,
    append_wfo_promotion_record,
    load_wfo_promotion_meta,
    save_wfo_promotion_meta,
    wfo_champion_fingerprint,
)


def _minimal_champion(symbol: str, score: float) -> dict:
    return {
        "symbol": symbol,
        "interval": 1,
        "timestamp": 0.0,
        "objective": "expectancy_sqrt_n",
        "score": score,
        "stability": 0.1,
        "baseline_score": None,
        "mode": "ema_momentum",
        "windows_evaluated": 2,
        "windows_passed": 2,
        "wfo_promotion_tier": "primary",
        "wfo_min_windows_used": 2,
        "params": {
            "mode": "ema_momentum",
            "max_hold_bars": 15,
            "atr_stop_mult": 1.0,
            "atr_tp_mult": 1.5,
            "min_signals": 2,
            "ema_fast": 5,
            "ema_slow": 13,
        },
        "holdout_metrics": {},
        "holdout_metrics_mean": {},
        "grid_size": 3,
        "candidates_after_filter": 1,
    }


def _diag() -> dict:
    return {
        "grid_size": 3,
        "n_windows": 2,
        "n_bars": 50,
        "span_hours": 12.0,
        "train_gate_diag": {},
    }


def test_wfo_champion_fingerprint_stable() -> None:
    row = _minimal_champion("X", 1.25)
    a = wfo_champion_fingerprint(row, fee_revision=0)
    b = wfo_champion_fingerprint(row, fee_revision=0)
    assert a == b
    assert a != wfo_champion_fingerprint(row, fee_revision=1)


def test_promotion_meta_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "meta.json"
    save_wfo_promotion_meta(p, {"SBTC": {"last_promoted_ts": 123.0, "last_saved_fingerprint": "abc"}})
    loaded = load_wfo_promotion_meta(p)
    assert loaded["SBTC"]["last_promoted_ts"] == 123.0


def test_append_promotion_record(tmp_path: Path) -> None:
    log = tmp_path / "p.jsonl"
    append_wfo_promotion_record(log, {"a": 1})
    append_wfo_promotion_record(log, {"b": 2})
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["a"] == 1


def test_run_once_cooldown_gates_second_pass(tmp_path: Path) -> None:
    sym = "T-COOLDOWN"
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol=sym, interval=1)},
        scalp_fee_assumption_revision=0,
    )
    champ = tmp_path / "champ.json"
    log = tmp_path / "promo.jsonl"
    meta = tmp_path / "meta.json"
    wfo = WFOConfig(enabled=True, champion_cooldown_sec=86_400.0)
    opt = ScalpWalkForwardOptimizer(
        cfg,
        wfo,
        champion_path=champ,
        promotion_log_path=log,
        promotion_meta_path=meta,
    )

    def fake_optimize(*_a, **_k):
        return _minimal_champion(sym, 1.0), None, _diag()

    with patch("scalp_bot.scalp_wfo.optimize_pair", side_effect=fake_optimize):
        r1 = opt.run_once()
        r2 = opt.run_once()
    assert r1["p1"] is not None
    assert r2["p1"] is None
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["outcome"] == "champion_gated"
    assert json.loads(lines[1])["gate_reason"] == "champion_cooldown"


def test_run_once_beat_prior_gates(tmp_path: Path) -> None:
    sym = "T-BEAT"
    cfg = ScalpBotConfig(
        enabled=True,
        pairs={"p1": ScalpPairConfig(symbol=sym, interval=1)},
        scalp_fee_assumption_revision=0,
    )
    champ = tmp_path / "champ.json"
    log = tmp_path / "promo.jsonl"
    meta = tmp_path / "meta.json"
    wfo = WFOConfig(enabled=True, require_holdout_beat_prior=True, prior_beat_epsilon=0.01)
    opt = ScalpWalkForwardOptimizer(
        cfg,
        wfo,
        champion_path=champ,
        promotion_log_path=log,
        promotion_meta_path=meta,
    )
    prior = _minimal_champion(sym, score=5.0)
    from scalp_bot.scalp_wfo import save_champion

    save_champion(prior, path=champ)

    def fake_optimize(*_a, **_k):
        return _minimal_champion(sym, score=4.0), None, _diag()

    with patch("scalp_bot.scalp_wfo.optimize_pair", side_effect=fake_optimize):
        r = opt.run_once()
    assert r["p1"] is None
    last = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["outcome"] == "champion_gated"
    assert last["gate_reason"] == "champion_not_better_than_prior"


def test_daily_loss_breach_notify_once() -> None:
    from state import BotState

    from scalp_bot.signal_engine import SignalEngine
    from scalp_bot.scalp_trader import ScalpTrader

    cfg = ScalpBotConfig(
        enabled=True,
        allocated_capital_usd=100.0,
        daily_loss_limit_pct=5.0,
        pairs={"p": ScalpPairConfig(symbol="S", interval=1)},
    )
    fired: list[int] = []
    t = ScalpTrader(BotState(), cfg, SignalEngine(), None, session_logger=None)
    t._daily_loss_breach_fn = lambda: fired.append(1)
    t._daily_pnl = -6.0
    t._maybe_notify_daily_loss_breach()
    t._maybe_notify_daily_loss_breach()
    assert fired == [1]
