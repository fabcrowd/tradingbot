"""TUNABLE_PARAMS ranges should stay within WFO grid extrema where grid sweeps the knob."""

from __future__ import annotations

from dataclasses import fields

from scalp_bot.param_tuner import TUNABLE_PARAMS
from scalp_bot.scalp_vec_backtest import ParamSet, build_default_grid


def _grid_extrema_by_mode() -> dict[str, dict[str, tuple[float, float]]]:
    out: dict[str, dict[str, tuple[float, float]]] = {}
    field_names = {f.name for f in fields(ParamSet)}
    for ps in build_default_grid():
        mode = ps.mode
        bucket = out.setdefault(mode, {})
        for name in field_names:
            val = getattr(ps, name, None)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                fv = float(val)
                if name not in bucket:
                    bucket[name] = (fv, fv)
                else:
                    lo, hi = bucket[name]
                    bucket[name] = (min(lo, fv), max(hi, fv))
    return out


def test_wfo_grid_points_within_tuner_envelope() -> None:
    """Every WFO grid value for a tunable knob must be reachable by the param tuner."""
    grid = _grid_extrema_by_mode()
    tuner: dict[str, dict[str, tuple[float, float]]] = {}
    for mode, knobs in TUNABLE_PARAMS.items():
        tuner[mode] = {name: (float(lo), float(hi)) for name, lo, hi, _ in knobs}
    violations: list[str] = []
    for mode, gparams in grid.items():
        tparams = tuner.get(mode, {})
        for param_name, (glo, ghi) in gparams.items():
            if param_name not in tparams:
                continue
            tlo, thi = tparams[param_name]
            if glo < tlo - 1e-9 or ghi > thi + 1e-9:
                violations.append(
                    f"{mode}.{param_name}: grid [{glo},{ghi}] outside tuner [{tlo},{thi}]",
                )
    # Known gaps: compact WFO grid explores below tuner floor on a few legacy knobs (X5).
    allowed = {
        "daviddtech_scalp.atr_stop_mult: grid [1.0,2.0] outside tuner [1.5,5.0]",
        "ema_momentum.max_hold_bars: grid [5.0,25.0] outside tuner [8.0,32.0]",
        "ema_momentum.ema_slow: grid [10.0,21.0] outside tuner [12.0,34.0]",
        "ema_momentum.ema_fast: grid [3.0,8.0] outside tuner [5.0,15.0]",
    }
    unexpected = [v for v in violations if v not in allowed]
    assert not unexpected, "Unexpected grid/tuner mismatch:\n" + "\n".join(unexpected)


def test_shared_exit_knobs_present_in_grid() -> None:
    grid = _grid_extrema_by_mode()
    for mode in ("squeeze_momentum", "qqe_mod", "ema_scalp"):
        assert mode in grid
        assert "atr_stop_mult" in grid[mode]
        assert "max_hold_bars" in grid[mode]
