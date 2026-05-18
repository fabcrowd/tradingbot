"""Legacy spread-MM fill replay and ``spread_bps`` sweep helpers.

Used by ``sim_runner`` to re-score recorded JSONL fills under hypothetical half-spreads.
Each sell row is treated as closing a round-trip; ``pnl_delta`` is the logged net for that fill.

When ``gross_spread`` is present on a sell (newer logs), simulated PnL replaces the gross edge with
``notional * 2 * spread_bps / 10_000`` while preserving fee / residual embedded in ``pnl_delta``.
Otherwise we shift net PnL by the change in round-trip half-spread capture vs the logged
``spread_bps`` on the event.

Fee stress (``sim_runner`` multiplying ``fee`` on events) only flows through when the simulated
PnL is tied to the current ``fee`` field; rows using the ``gross_spread`` replacement path still
anchor on ``pnl_delta``/``gross_spread`` from the log, so stress may barely move PnL for those rows.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from pathlib import Path
from typing import Any

import numpy as np

LOG = logging.getLogger(__name__)


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _sell_pnl_affine(event: dict[str, Any]) -> tuple[float, float]:
    """Return ``(a, b)`` such that simulated net PnL for half-spread ``s`` bps is ``a + b * s``."""
    pnl_delta = _f(event.get("pnl_delta"))
    price = _f(event.get("price"))
    qty = _f(event.get("qty"))
    notional = abs(price * qty)
    evt_spread = _f(event.get("spread_bps"))
    b = notional * 2.0 / 10_000.0
    gross = event.get("gross_spread")
    if gross is not None:
        g0 = _f(gross, float("nan"))
        if math.isfinite(g0):
            return pnl_delta - g0, b
    return pnl_delta - b * evt_spread, b


def _adjusted_sell_pnl(event: dict[str, Any], spread_bps: float) -> float:
    """Net PnL for one sell under simulated half-spread ``spread_bps`` (bps of notional, one side)."""
    a, b = _sell_pnl_affine(event)
    return a + b * float(spread_bps)


def _aggregate_sell_pnls(pnls: list[float]) -> dict[str, float]:
    """Metrics from an ordered list of per-sell net PnLs (same logic as :func:`run_backtest`)."""
    n = len(pnls)
    if n == 0:
        return {
            "realized_pnl": 0.0,
            "win_rate": 0.0,
            "total_wins": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "avg_pnl_per_sell": 0.0,
            "total_win_dollars": 0.0,
        }

    realized = float(sum(pnls))
    wins = sum(1 for p in pnls if p > 0.0)
    win_rate = (100.0 * wins / n) if n else 0.0
    total_win_dollars = float(sum(p for p in pnls if p > 0.0))

    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    if n < 2:
        sharpe = 0.0
    else:
        m = statistics.fmean(pnls)
        try:
            sd = statistics.pstdev(pnls)
        except statistics.StatisticsError:
            sd = 0.0
        sharpe = (m / sd * math.sqrt(n)) if sd > 1e-12 else 0.0

    return {
        "realized_pnl": realized,
        "win_rate": win_rate,
        "total_wins": float(wins),
        "max_drawdown": float(max_dd),
        "sharpe_ratio": float(sharpe),
        "avg_pnl_per_sell": (realized / n) if n else 0.0,
        "total_win_dollars": total_win_dollars,
    }


def _empty_result() -> dict[str, Any]:
    return {
        "realized_pnl": 0.0,
        "win_rate": 0.0,
        "total_sells": 0,
        "total_wins": 0,
        "max_drawdown": 0.0,
        "sharpe_ratio": 0.0,
        "avg_pnl_per_sell": 0.0,
        "total_win_dollars": 0.0,
    }


def sweep_spread_simulated(
    events: list[dict[str, Any]],
    spread_floor: int,
    spread_ceiling: int,
    spread_step: int = 1,
) -> list[dict[str, Any]]:
    """Vectorized ``spread_bps`` grid — bit-identical to repeated :func:`run_backtest` (simulated).

    Builds all per-sell PnLs in one NumPy ``(n_sells × n_grid)`` matmul, then runs the same Python
    aggregation as :func:`run_backtest` per column (avoids NumPy ``cumsum`` float-order quirks vs
    the scalar harness for ``max_drawdown`` / ``sharpe_ratio``).
    """
    if spread_ceiling < spread_floor or spread_step < 1:
        return []

    ordered = sorted(events, key=lambda e: _f(e.get("timestamp")))
    sells = [e for e in ordered if str(e.get("side", "")).lower() == "sell"]
    n = len(sells)
    if n == 0:
        return []

    ab = [_sell_pnl_affine(e) for e in sells]
    a = np.fromiter((x[0] for x in ab), dtype=np.float64, count=n)
    b = np.fromiter((x[1] for x in ab), dtype=np.float64, count=n)

    s = np.arange(spread_floor, spread_ceiling + 1, spread_step, dtype=np.int64)
    sf = s.astype(np.float64)
    # (n, G)
    pnl = a[:, np.newaxis] + b[:, np.newaxis] * sf[np.newaxis, :]

    out: list[dict[str, Any]] = []
    for j in range(s.shape[0]):
        col = [float(pnl[i, j]) for i in range(n)]
        agg = _aggregate_sell_pnls(col)
        out.append(
            {
                "spread_bps": int(s[j]),
                "realized_pnl": agg["realized_pnl"],
                "win_rate": agg["win_rate"],
                "total_sells": int(n),
                "total_wins": int(agg["total_wins"]),
                "max_drawdown": agg["max_drawdown"],
                "sharpe_ratio": agg["sharpe_ratio"],
                "avg_pnl_per_sell": agg["avg_pnl_per_sell"],
                "total_win_dollars": agg["total_win_dollars"],
            }
        )
    return out


def run_backtest(
    events: list[dict[str, Any]],
    *,
    mode: str = "simulated",
    spread_bps: float = 8.0,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Aggregate per-sell PnL metrics.

    ``mode="simulated"`` — re-price each **sell** using ``spread_bps`` (half-spread in bps).
    Any other mode — use recorded ``pnl_delta`` on sells as-is (no spread override).
    """
    if not events:
        return _empty_result()

    ordered = sorted(events, key=lambda e: _f(e.get("timestamp")))
    sells = [e for e in ordered if str(e.get("side", "")).lower() == "sell"]
    if not sells:
        return _empty_result()

    sim_spread = _f(spread_bps, 0.0)
    pnls: list[float] = []
    for e in sells:
        if str(mode).lower() == "simulated":
            pnls.append(_adjusted_sell_pnl(e, sim_spread))
        else:
            pnls.append(_f(e.get("pnl_delta")))

    n = len(pnls)
    agg = _aggregate_sell_pnls(pnls)
    return {
        "realized_pnl": agg["realized_pnl"],
        "win_rate": agg["win_rate"],
        "total_sells": n,
        "total_wins": int(agg["total_wins"]),
        "max_drawdown": agg["max_drawdown"],
        "sharpe_ratio": agg["sharpe_ratio"],
        "avg_pnl_per_sell": agg["avg_pnl_per_sell"],
        "total_win_dollars": agg["total_win_dollars"],
    }


def mp_sim_runner_pair(
    packed: tuple[str, list[dict[str, Any]]],
) -> tuple[str, dict[str, Any]]:
    """Picklable worker for :mod:`multiprocessing` / ``ProcessPoolExecutor`` (Windows spawn).

    Import ``sim_runner`` lazily so importing ``backtest`` from ``sim_runner`` does not cycle at load time.
    """
    from . import sim_runner as _sr

    pk, events = packed
    mb, lab = _sr._infer_pair_maker_fee_bps(pk)
    return pk, _sr._run_pair(pk, events, mb, lab)


def _load_events(path: Path | str) -> list[dict[str, Any]]:
    """Load fill events from JSONL (one JSON object per non-empty line)."""
    p = Path(path)
    if not p.is_file():
        LOG.warning("backtest: file not found: %s", p)
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            LOG.debug("backtest: skip bad JSON line in %s", p)
            continue
        if isinstance(row, dict):
            out.append(row)
    return out
