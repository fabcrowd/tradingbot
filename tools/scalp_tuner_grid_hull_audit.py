#!/usr/bin/env python3
"""Print param-tuner (min,max) vs WFO default grid extrema per mode (§2 audit)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.server.scalp_bot.param_tuner import TUNABLE_PARAMS  # noqa: E402
from backend.server.scalp_bot.scalp_vec_backtest import build_default_grid  # noqa: E402


def main() -> int:
    grid = build_default_grid()
    any_bad = False
    for mode, rows in sorted(TUNABLE_PARAMS.items()):
        print(f"\n== {mode} ==")
        for pname, lo, hi, step in rows:
            vals = [
                float(getattr(p, pname))
                for p in grid
                if str(getattr(p, "mode", "")).strip() == mode and hasattr(p, pname)
            ]
            if not vals:
                print(f"  {pname}: no grid points")
                continue
            vmin, vmax = min(vals), max(vals)
            ok = vmin >= float(lo) - 1e-9 and vmax <= float(hi) + 1e-9
            flag = "OK" if ok else "OUTSIDE_HULL"
            if not ok:
                any_bad = True
            print(
                f"  {pname}: tuner[{lo}, {hi}] step={step} | grid_min={vmin:.6g} grid_max={vmax:.6g} | {flag}",
            )
    return 1 if any_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
