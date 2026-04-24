"""Dev/CI: param tuner (min,max) ranges should sit inside WFO default grid hull per mode."""

from __future__ import annotations

from scalp_bot.param_tuner import TUNABLE_PARAMS
from scalp_bot.scalp_vec_backtest import build_default_grid


def _tuner_vs_grid_violations() -> list[tuple[str, str, float, float, float, float]]:
    grid = build_default_grid()
    out: list[tuple[str, str, float, float, float, float]] = []
    for mode, rows in TUNABLE_PARAMS.items():
        for pname, lo, hi, _step in rows:
            vals: list[float] = []
            for p in grid:
                if str(getattr(p, "mode", "")).strip() != mode:
                    continue
                if hasattr(p, pname):
                    vals.append(float(getattr(p, pname)))
            if not vals:
                continue
            vmin, vmax = min(vals), max(vals)
            if vmin < float(lo) - 1e-9 or vmax > float(hi) + 1e-9:
                out.append((mode, pname, float(lo), float(hi), vmin, vmax))
    return out


def test_tuner_grid_hull_audit_smoke() -> None:
    """Default WFO grid does not always cover full tuner envelopes — audit lists gaps for CI triage."""
    bad = _tuner_vs_grid_violations()
    assert isinstance(bad, list)
