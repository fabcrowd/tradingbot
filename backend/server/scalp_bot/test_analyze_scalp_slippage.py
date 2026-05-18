"""Tests for tools/analyze_scalp_slippage (P2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools"))

from analyze_scalp_slippage import analyze_session  # noqa: E402


def test_analyze_session_fixture(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    rows = [
        {"event": "scalp_fill_execution", "symbol": "T", "leg": "entry", "slip_bps": 4.0},
        {"event": "scalp_fill_execution", "symbol": "T", "leg": "exit", "slip_bps": 6.0},
        {"event": "other", "slip_bps": 99.0},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = analyze_session(p, config_bps=1.0)
    assert out["count"] == 2
    assert out["median_bps"] == 5.0
    assert "Consider raising" in out["recommendation"]
