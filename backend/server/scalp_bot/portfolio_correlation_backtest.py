"""Portfolio-level PnL scaling for correlation-style sizing (backtest / A–B tests).

The live bot applies ``dollar_risk / (1 + correlated_open)`` in ``ScalpTrader.try_open``
for same-``correlation_group``, same-direction positions. Vector backtests run **per symbol**
and never see that rule. This module **post-processes** trade lists from ``evaluate_params``
so you can compare two sizing policies on **the same** simulated trades.

**Limitations (read before trusting numbers):**
- Uses **aligned bar indices** across pairs: all ``TradeResult.entry_bar`` / ``exit_bar``
  must refer to the **same** n-bar series (calendar-aligned Parquet intersection).
- **Event order:** within a bar, **exits** are processed before **entries** (same convention
  as counting open positions before a new open).
- **Sizing** is a scalar multiplier on each trade's **already-simulated** ``pnl`` (price
  units per contract). It does not re-run fill logic or change which trades exist.
- Does **not** model capital constraints, partial fills, or cross-pair signal priority
  beyond exit-before-entry on the same bar index.

Typical use: baseline ``lambda k: 1.0`` vs live-shaped ``lambda k: 1.0 / (1 + k)``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from .scalp_vec_backtest import TradeResult


def infer_trade_direction(tr: TradeResult) -> str:
    """Long if stop is below entry (long protective stop); else short."""
    if tr.stop_price < tr.entry_price:
        return "long"
    return "short"


@dataclass
class TaggedTrade:
    pair_key: str
    group: str
    direction: str
    entry_bar: int
    exit_bar: int
    pnl: float


@dataclass
class ScaledTradeDetail:
    pair_key: str
    group: str
    direction: str
    entry_bar: int
    exit_bar: int
    pnl: float
    correlated_open: int
    scale: float
    scaled_pnl: float


@dataclass
class PortfolioSizingResult:
    """Aggregate after applying one sizing function to all trades."""

    total_scaled_pnl: float
    trade_count: int
    details: list[ScaledTradeDetail] = field(default_factory=list)


def _tag_trades(
    trades_by_pair: Mapping[str, list[TradeResult]],
    pair_keys: Sequence[str],
    group_for_pair: Mapping[str, str],
) -> list[TaggedTrade]:
    out: list[TaggedTrade] = []
    for pk in pair_keys:
        grp = (group_for_pair.get(pk) or "").strip()
        if not grp:
            continue
        for tr in trades_by_pair.get(pk) or []:
            out.append(
                TaggedTrade(
                    pair_key=pk,
                    group=grp,
                    direction=infer_trade_direction(tr),
                    entry_bar=int(tr.entry_bar),
                    exit_bar=int(tr.exit_bar),
                    pnl=float(tr.pnl),
                )
            )
    return out


def _build_events(tagged: list[TaggedTrade]) -> list[tuple[int, int, str, TaggedTrade]]:
    """(bar, kind_order, kind, trade) — kind_order 0=exit, 1=entry (exits first)."""
    ev: list[tuple[int, int, str, TaggedTrade]] = []
    for t in tagged:
        ev.append((t.exit_bar, 0, "exit", t))
        ev.append((t.entry_bar, 1, "entry", t))
    ev.sort(key=lambda x: (x[0], x[1], x[3].pair_key))
    return ev


def apply_portfolio_sizing(
    trades_by_pair: Mapping[str, list[TradeResult]],
    pair_keys: Sequence[str],
    group_for_pair: Mapping[str, str],
    sizing_fn: Callable[[int], float],
) -> PortfolioSizingResult:
    """Replay cross-pair open interest and scale each trade's PnL by ``sizing_fn(k)``.

    ``k`` = count of **other** open trades in the same non-empty group and same direction
    at the **entry** event (after processing exits on that bar).
    """
    tagged = _tag_trades(trades_by_pair, pair_keys, group_for_pair)
    if not tagged:
        return PortfolioSizingResult(total_scaled_pnl=0.0, trade_count=0, details=[])

    events = _build_events(tagged)
    active: set[int] = set()  # id(TaggedTrade) for open positions
    open_by_id: dict[int, TaggedTrade] = {}
    details: list[ScaledTradeDetail] = []
    total = 0.0

    for _bar, _ko, kind, t in events:
        tid = id(t)
        if kind == "exit":
            active.discard(tid)
            open_by_id.pop(tid, None)
            continue

        # entry
        k = 0
        for oid in active:
            u = open_by_id[oid]
            if u.pair_key == t.pair_key:
                continue
            if u.group != t.group or u.direction != t.direction:
                continue
            k += 1

        scale = float(sizing_fn(k))
        scaled = t.pnl * scale
        total += scaled
        details.append(
            ScaledTradeDetail(
                pair_key=t.pair_key,
                group=t.group,
                direction=t.direction,
                entry_bar=t.entry_bar,
                exit_bar=t.exit_bar,
                pnl=t.pnl,
                correlated_open=k,
                scale=scale,
                scaled_pnl=scaled,
            )
        )
        active.add(tid)
        open_by_id[tid] = t

    return PortfolioSizingResult(
        total_scaled_pnl=total,
        trade_count=len(details),
        details=details,
    )


def sizing_baseline(_correlated_open: int) -> float:
    """No correlation scaling (independent-backtest equivalent)."""
    return 1.0


def sizing_live_mirror(correlated_open: int) -> float:
    """Match ``ScalpTrader.try_open``: ``1 / (1 + k)`` when ``k`` peers open (``k>=0``)."""
    return 1.0 / (1.0 + max(0, int(correlated_open)))


@dataclass
class SizingAbComparison:
    label_a: str
    label_b: str
    total_a: float
    total_b: float
    delta_b_minus_a: float
    trade_count: int


def compare_sizing_functions(
    trades_by_pair: Mapping[str, list[TradeResult]],
    pair_keys: Sequence[str],
    group_for_pair: Mapping[str, str],
    fn_a: Callable[[int], float],
    fn_b: Callable[[int], float],
    *,
    label_a: str = "A",
    label_b: str = "B",
) -> SizingAbComparison:
    """Run two sizing policies on the same trades; return totals and delta."""
    ra = apply_portfolio_sizing(trades_by_pair, pair_keys, group_for_pair, fn_a)
    rb = apply_portfolio_sizing(trades_by_pair, pair_keys, group_for_pair, fn_b)
    return SizingAbComparison(
        label_a=label_a,
        label_b=label_b,
        total_a=ra.total_scaled_pnl,
        total_b=rb.total_scaled_pnl,
        delta_b_minus_a=rb.total_scaled_pnl - ra.total_scaled_pnl,
        trade_count=ra.trade_count,
    )
