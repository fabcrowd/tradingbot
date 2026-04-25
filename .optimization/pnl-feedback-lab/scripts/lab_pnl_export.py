#!/usr/bin/env python3
"""Export full PnL grid: every pair × time window × strategy × metrics.

Reads the same lab JSONL as ``compare_report_generator`` (optional leading contract block).

CLI::

  python .optimization/pnl-feedback-lab/scripts/lab_pnl_export.py runs/lab_run_x.jsonl --prefix runs/lab_run_x

Writes ``{prefix}_pnl_long.csv``, ``{prefix}_pnl_matrix.md`` (PnL + PF + trades),
``{prefix}_profit_factor.md`` (PF by timeframe), and ``{prefix}_best_per_pair.md``
(best strategy + bar size per pair from lab scores).
"""

from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from compare_report_generator import parse_jsonl_lab, read_jsonl_text

_WINDOW_ORDER = ("early", "mid", "late", "full")


def _window_sort_key(w: str) -> int:
    try:
        return _WINDOW_ORDER.index(w)
    except ValueError:
        return 99


# Short headers for the standalone profit-factor report (full names in legend).
_STRAT_ABBREV: dict[str, str] = {
    "daviddtech_scalp": "DDtech",  # strategy id — unrelated to ``auto`` default (see auto_mode_fallback)
    "ema_momentum": "EMA_mom",
    "ema_scalp": "EMA_scp",
    "macd_scalp": "MACD",
    "rsi_reversion": "RSI_rev",
}


def _strat_abbrev(st: str) -> str:
    return _STRAT_ABBREV.get(st, st[:8])


def _interval_title(minutes: int) -> str:
    """Display label for bar size (60 → 1 hour)."""
    m = int(minutes)
    if m == 60:
        return "1 hour"
    return f"{m} min"


def _fmt_pnl_cell(r: dict[str, Any] | None) -> str:
    if r is None:
        return "—"
    p = f'{float(r["total_pnl"]):,.2f}'
    if r.get("low_n"):
        return f"{p} [LOW_N]"
    return p


def _fmt_pf_cell(r: dict[str, Any] | None) -> str:
    """Readable profit factor: 2 decimals; symbols for undefined."""
    if r is None:
        return "—"
    n = int(r.get("trades") or 0)
    if n == 0:
        return "no trades"
    pf = r.get("profit_factor")
    if pf is None:
        return "n/a"
    try:
        f = float(pf)
    except (TypeError, ValueError):
        return "n/a"
    if math.isinf(f) and f > 0:
        return "∞"
    if math.isnan(f):
        return "n/a"
    return f"{f:.2f}"


LONG_HEADER = [
    "pair_key",
    "symbol",
    "interval_m",
    "config_interval_m",
    "window",
    "bar_start",
    "bar_end",
    "n_bars",
    "ts_first",
    "ts_last",
    "strategy",
    "total_pnl",
    "trades",
    "win_rate",
    "expectancy",
    "profit_factor",
    "max_dd_pct",
    "sharpe",
    "sortino",
    "score_exp_sqrt_n",
]


def write_pnl_long_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            r["pair_key"],
            int(r["interval_m"]),
            _window_sort_key(str(r["window"])),
            r["strategy"],
        ),
    )
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LONG_HEADER, extrasaction="ignore")
        w.writeheader()
        for r in rows_sorted:
            row = {k: r.get(k) for k in LONG_HEADER}
            pf = row.get("profit_factor")
            if pf is not None and isinstance(pf, float):
                row["profit_factor"] = pf
            w.writerow(row)


def write_pnl_matrix_md(
    rows: list[dict[str, Any]],
    path: Path,
    *,
    contract: dict[str, Any] | None = None,
) -> None:
    """Per (pair_key, interval_m): tables of total_pnl and trades, strategies as columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    strategies = sorted({str(r["strategy"]) for r in rows})
    # (pair_key, interval_m) -> window -> strategy -> row
    grid: dict[tuple[str, int], dict[str, dict[str, dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    symbols: dict[tuple[str, int], str] = {}
    for r in rows:
        pk = str(r["pair_key"])
        iv = int(r["interval_m"])
        wn = str(r["window"])
        st = str(r["strategy"])
        grid[(pk, iv)][wn][st] = r
        symbols[(pk, iv)] = str(r.get("symbol", ""))

    keys = sorted(grid.keys(), key=lambda x: (x[0], x[1]))
    lines: list[str] = []
    lines.append("# Lab PnL grid — every pair × interval × window × strategy")
    lines.append("")
    lines.append("## How to read")
    lines.append("")
    lines.append("- **total_pnl** — backtester-internal PnL for that slice (not asserted live USD). Comma-separated for readability.")
    lines.append("- **profit_factor** — gross winning trades ÷ gross losing trades (same backtest). **> 1** means wins outweighed losses; **< 1** the opposite.")
    lines.append("- **no trades** — zero fills in that window (PF not defined). **n/a** — PF undefined (e.g. one-sided gross). **∞** — no gross loss side.")
    lines.append("- **trades** — round-trip count for that cell.")
    lines.append("")
    lines.append("**Units:** `total_pnl` is the vector backtester internal PnL (not asserted live USD).")
    lines.append("")
    if contract:
        lines.append("## Simulation contract")
        lines.append("")
        lines.append("| Key | Value |")
        lines.append("|-----|-------|")
        for k, v in sorted(contract.items()):
            lines.append(f"| {k} | `{v}` |")
        lines.append("")
    lines.append("---")
    lines.append("")

    for (pk, iv) in keys:
        sym = symbols.get((pk, iv), "")
        by_win = grid[(pk, iv)]
        win_list = sorted(by_win.keys(), key=_window_sort_key)
        lines.append(f"## {pk} — `{sym}` @ **{_interval_title(iv)}**")
        lines.append("")
        # total_pnl matrix
        lines.append("### total_pnl")
        lines.append("")
        hdr = "| Window | " + " | ".join(strategies) + " |"
        sep = "|--------|" + "|".join(["-----:" for _ in strategies]) + "|"
        lines.append(hdr)
        lines.append(sep)
        for wn in win_list:
            cells = [_fmt_pnl_cell(by_win[wn].get(st)) for st in strategies]
            lines.append("| " + wn + " | " + " | ".join(cells) + " |")
        lines.append("")
        # profit_factor matrix
        lines.append("### profit_factor (gross win ÷ gross loss; see *How to read* above)")
        lines.append("")
        lines.append(hdr)
        lines.append(sep)
        for wn in win_list:
            cells = [_fmt_pf_cell(by_win[wn].get(st)) for st in strategies]
            lines.append("| " + wn + " | " + " | ".join(cells) + " |")
        lines.append("")
        # trades matrix
        lines.append("### trades (count per cell above)")
        lines.append("")
        lines.append(hdr)
        lines.append(sep)
        for wn in win_list:
            cells = []
            for st in strategies:
                r = by_win[wn].get(st)
                if r is None:
                    cells.append("—")
                else:
                    cells.append(str(int(r["trades"])))
            lines.append("| " + wn + " | " + " | ".join(cells) + " |")
        lines.append("")
        lines.append("---")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_profit_factor_only_md(
    rows: list[dict[str, Any]],
    path: Path,
    *,
    contract: dict[str, Any] | None = None,
) -> None:
    """Standalone, easy-to-scan profit-factor tables (short column headers)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    strategies = sorted({str(r["strategy"]) for r in rows})
    grid: dict[tuple[str, int], dict[str, dict[str, dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    symbols: dict[tuple[str, int], str] = {}
    for r in rows:
        pk = str(r["pair_key"])
        iv = int(r["interval_m"])
        wn = str(r["window"])
        st = str(r["strategy"])
        grid[(pk, iv)][wn][st] = r
        symbols[(pk, iv)] = str(r.get("symbol", ""))

    abbr_hdr = "| Window | " + " | ".join(_strat_abbrev(s) for s in strategies) + " |"
    sep = "|--------|" + "|".join(["-----:" for _ in strategies]) + "|"

    lines: list[str] = []
    lines.append("# Profit factor only")
    lines.append("")
    lines.append("**profit_factor** = gross profits ÷ gross losses on closed trades in that window (vector backtester).")
    lines.append("")
    lines.append("## Strategy codes")
    lines.append("")
    lines.append("| Code | Full name |")
    lines.append("|------|-----------|")
    for s in strategies:
        lines.append(f"| {_strat_abbrev(s)} | `{s}` |")
    lines.append("")
    lines.append("| Cell | Meaning |")
    lines.append("|------|---------|")
    lines.append("| — | missing row |")
    lines.append("| no trades | 0 fills in window |")
    lines.append("| n/a | PF not defined |")
    lines.append("| ∞ | no losing gross (divide-by-zero side) |")
    lines.append("")
    if contract:
        lines.append("## Contract")
        lines.append("")
        for k, v in sorted(contract.items()):
            lines.append(f"- **{k}:** `{v}`")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Layout")
    lines.append("")
    lines.append(
        "Grouped by **timeframe** (5 min → 15 min → 1 hour). Under each, **every configured pair** has the same window × strategy grid."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    pair_keys = sorted({str(r["pair_key"]) for r in rows})
    interval_ms = sorted({int(r["interval_m"]) for r in rows})

    for iv in interval_ms:
        ititle = _interval_title(iv)
        lines.append(f"## {ititle}")
        lines.append("")
        for pk in pair_keys:
            gk = (pk, iv)
            if gk not in grid:
                continue
            sym = symbols.get(gk, "")
            by_win = grid[gk]
            win_list = sorted(by_win.keys(), key=_window_sort_key)
            lines.append(f"### {pk} (`{sym}`)")
            lines.append("")
            lines.append(abbr_hdr)
            lines.append(sep)
            for wn in win_list:
                cells = [_fmt_pf_cell(by_win[wn].get(st)) for st in strategies]
                lines.append("| " + wn + " | " + " | ".join(cells) + " |")
            lines.append("")
        lines.append("---")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _confidence_short(windows_active: int, total_trades: int) -> str:
    if windows_active >= 3 and total_trades >= 15:
        return "high"
    if total_trades >= 8 and windows_active >= 2:
        return "moderate"
    return "low"


def _aggregate_pair_interval_strategy(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate early/mid/late into one row per (pair, interval, strategy).

    Windows marked ``low_n`` (from ``--min-trades-per-window`` in the lab run) are
    omitted from **mean_score** and from the **windows_with_trades** count so thin
    windows cannot win the ranking; **sum_pnl** / **total_trades** still include all
    windows for an honest PnL total. If every traded window is ``low_n``, the triple
    is dropped from this ranking table.
    """
    buckets: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        buckets[(str(r["pair_key"]), int(r["interval_m"]), str(r["strategy"]))].append(r)

    out: list[dict[str, Any]] = []
    for (pk, iv, st), wins in buckets.items():
        scored = [
            float(w["score_exp_sqrt_n"])
            for w in wins
            if int(w.get("trades") or 0) > 0 and not w.get("low_n")
        ]
        if not scored:
            continue
        mean_score = sum(scored) / len(scored)
        total_tr = sum(int(w["trades"]) for w in wins)
        sum_pnl = sum(float(w["total_pnl"]) for w in wins)
        sym = str(wins[0].get("symbol", ""))
        out.append(
            {
                "pair_key": pk,
                "symbol": sym,
                "interval_m": iv,
                "strategy": st,
                "mean_score": mean_score,
                "windows_with_trades": len(scored),
                "total_trades": total_tr,
                "sum_pnl": sum_pnl,
                "worst_window_pnl": min(float(w["total_pnl"]) for w in wins),
            }
        )
    return out


def write_best_strategy_timeframe_md(
    rows: list[dict[str, Any]],
    path: Path,
    *,
    contract: dict[str, Any] | None = None,
) -> None:
    """Decision-oriented: best bar size + strategy per pair from lab scores."""
    path.parent.mkdir(parents=True, exist_ok=True)
    agg = _aggregate_pair_interval_strategy(rows)
    if not agg:
        path.write_text(
            "# Best strategy & timeframe\n\nNo (pair × interval × strategy) rows had trades in any window.\n",
            encoding="utf-8",
        )
        return

    pair_keys = sorted({a["pair_key"] for a in agg})
    interval_ms = sorted({a["interval_m"] for a in agg})

    lines: list[str] = []
    lines.append("# Best strategy & timeframe — per pair")
    lines.append("")
    lines.append(
        "**Goal:** Choose a **bar size** and **strategy** for each pair. "
        "This file ranks lab outputs toward that decision; it does **not** replace WFO/champion logic on your production schedule."
    )
    lines.append("")
    lines.append("## How winners are chosen")
    lines.append("")
    lines.append(
        "1. For each **pair × bar size × strategy**, average **score_exp_sqrt_n** over **early / mid / late**, "
        "using only windows with **≥1 trade** and **not** flagged **[LOW_N]** (when the lab was run with "
        "`--min-trades-per-window`)."
    )
    lines.append(
        "2. **Best strategy at a bar size** = highest average score among strategies that traded at that size."
    )
    lines.append(
        "3. **Best bar size for the pair** = among those per-interval winners, pick the **highest** average score "
        "(no minimum trade-count filter)."
    )
    lines.append(
        "4. **Sum PnL** = sum of window PnLs for that triple (backtest units). **Worst window PnL** = weakest third — regime risk."
    )
    lines.append("")
    lines.append("### Confidence")
    lines.append("")
    lines.append("- **high** — trades in **all 3** windows and **≥15** trades total across them.")
    lines.append("- **moderate** — **≥8** trades and **≥2** windows with trades.")
    lines.append("- **low** — thin sample (typical on **1 hour** with short history).")
    lines.append("")
    if contract:
        lines.append("## Contract (this run)")
        lines.append("")
        for k, v in sorted(contract.items()):
            lines.append(f"- **{k}:** `{v}`")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Summary — pick per pair")
    lines.append("")
    lines.append(
        "| Pair | Symbol | **Best bar size** | **Best strategy** | Mean score† | Trades | Sum PnL‡ | Worst window | Conf. |"
    )
    lines.append(
        "|------|--------|-------------------|-------------------|-------------|--------|----------|--------------|-------|"
    )

    for pk in pair_keys:
        iv_winners: dict[int, dict[str, Any]] = {}
        symbol = ""
        for iv in interval_ms:
            cands = [a for a in agg if a["pair_key"] == pk and a["interval_m"] == iv]
            if not cands:
                continue
            top = max(cands, key=lambda x: x["mean_score"])
            symbol = top["symbol"]
            iv_winners[iv] = top
        if not iv_winners:
            continue
        best_iv, best_row = max(iv_winners.items(), key=lambda t: t[1]["mean_score"])
        conf = _confidence_short(best_row["windows_with_trades"], best_row["total_trades"])
        lines.append(
            f"| {pk} | `{symbol}` | **{_interval_title(best_iv)}** | **`{best_row['strategy']}`** | {best_row['mean_score']:.4f} | "
            f"{best_row['total_trades']} | {best_row['sum_pnl']:.4f} | {best_row['worst_window_pnl']:.4f} | {conf} |"
        )

    lines.append("")
    lines.append("† Mean of window `score_exp_sqrt_n` where that window had trades.")
    lines.append("‡ Backtester-internal PnL, summed over early+mid+late for the chosen triple.")
    lines.append("")
    lines.append("---")
    lines.append("")

    for pk in pair_keys:
        symbol = next((a["symbol"] for a in agg if a["pair_key"] == pk), "")
        lines.append(f"## {pk} (`{symbol}`)")
        lines.append("")
        lines.append("### Winner at each bar size (compare timeframes)")
        lines.append("")
        lines.append(
            "| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |"
        )
        lines.append("|----------|---------------|------------|-------------------|--------------|---------|--------------|")
        for iv in interval_ms:
            cands = [a for a in agg if a["pair_key"] == pk and a["interval_m"] == iv]
            if not cands:
                lines.append(f"| {_interval_title(iv)} | — | — | — | — | — | — |")
                continue
            top = max(cands, key=lambda x: x["mean_score"])
            lines.append(
                f"| {_interval_title(iv)} | `{top['strategy']}` | {top['mean_score']:.4f} | "
                f"{top['windows_with_trades']}/3 | {top['total_trades']} | {top['sum_pnl']:.4f} | {top['worst_window_pnl']:.4f} |"
            )
        lines.append("")
        pair_agg = [a for a in agg if a["pair_key"] == pk]
        pair_agg.sort(key=lambda x: -x["mean_score"])
        lines.append("### Full ranking (all bar sizes × strategies for this pair)")
        lines.append("")
        lines.append("| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |")
        lines.append("|------|----------|----------|------------|--------|---------|--------------|")
        for i, a in enumerate(pair_agg[:12], 1):
            lines.append(
                f"| {i} | {_interval_title(a['interval_m'])} | `{a['strategy']}` | {a['mean_score']:.4f} | "
                f"{a['total_trades']} | {a['sum_pnl']:.4f} | {a['worst_window_pnl']:.4f} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_pnl_detail_artifacts(
    rows: list[dict[str, Any]],
    prefix: Path | str,
    *,
    contract: dict[str, Any] | None = None,
) -> tuple[Path, Path, Path, Path]:
    """Write CSV, matrix MD, PF MD, and best-per-pair MD next to ``prefix`` (no extension)."""
    base = Path(prefix)
    csv_path = Path(str(base) + "_pnl_long.csv")
    md_path = Path(str(base) + "_pnl_matrix.md")
    pf_path = Path(str(base) + "_profit_factor.md")
    best_path = Path(str(base) + "_best_per_pair.md")
    write_pnl_long_csv(rows, csv_path)
    write_pnl_matrix_md(rows, md_path, contract=contract)
    write_profit_factor_only_md(rows, pf_path, contract=contract)
    write_best_strategy_timeframe_md(rows, best_path, contract=contract)
    return csv_path, md_path, pf_path, best_path


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Export lab JSONL to long CSV + PnL matrices.")
    ap.add_argument("jsonl", type=str, help="Path to lab.jsonl")
    ap.add_argument(
        "--prefix",
        type=str,
        default=None,
        help="Output prefix without extension (default: jsonl path without .jsonl)",
    )
    ns = ap.parse_args()
    jp = Path(ns.jsonl).resolve()
    if not jp.is_file():
        print("not found:", jp, file=sys.stderr)
        return 2
    text = read_jsonl_text(jp)
    contract, rows = parse_jsonl_lab(text)
    if not rows:
        print("no data rows in jsonl", file=sys.stderr)
        return 2
    prefix = ns.prefix
    if not prefix:
        prefix = str(jp.with_suffix(""))
    c1, c2, c3, c4 = write_pnl_detail_artifacts(rows, prefix, contract=contract)
    print("wrote", c1)
    print("wrote", c2)
    print("wrote", c3)
    print("wrote", c4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
