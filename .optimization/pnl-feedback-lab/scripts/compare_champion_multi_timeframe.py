#!/usr/bin/env python3
"""Champion vs PnL-lab presets on 5m / 15m / 60m with identical calendar lookback per pair.

For each of BTC_USD, SOL_USD, XRP_USD:
  - Load Parquet for intervals 5, 15, 60 (same ``last_n_days`` or full file).
  - **Align** all three to the intersection of [first_ts, last_ts] so every timeframe
    covers the **same wall-clock window** (fair baseline).
  - Evaluate **saved champion** ParamSet vs each **STRATEGIES** preset from config
    (same contract as ``run_multiwindow_lab.py``).

**1-day equivalent score:** ``score_exp_sqrt_n`` = ``expectancy * sqrt(trades)``. Assuming roughly
steady trade intensity over the sample, extrapolate to one calendar day::

    score_1d_eq = score_exp_sqrt_n * sqrt(86400 / span_seconds)

``span_seconds`` = first bar open to **end** of last bar for that timeframe's aligned series.
Winners (best lab + champion vs lab) use ``score_1d_eq`` so 5m/15m/60m are comparable on a per-day basis.

Repo root:

  python .optimization/pnl-feedback-lab/scripts/compare_champion_multi_timeframe.py
  python .../compare_champion_multi_timeframe.py --lookback days:14
  python .../compare_champion_multi_timeframe.py --lookback full --roll-windows 14 --window-hours 24 --min-bars 20
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any
from collections import defaultdict
from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[3]
_SERVER = _REPO / "backend" / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

import tomllib

from scalp_bot import bar_store
from scalp_bot.scalp_config import effective_scalp_fee_bps_per_leg, load_scalp_config
from scalp_bot.scalp_vec_backtest import ParamSet, evaluate_params
from scalp_bot.scalp_wfo import CHAMPION_PATH, load_champion_for_symbol
from scalp_bot.scalp_wfo import _params_from_config

# Same tuple as run_multiwindow_lab.STRATEGIES (macd_scalp historically excluded from the
# lab oracle; sar_chop added 2026-04-16 following the TV "5 min bot scalper" decode)
STRATEGIES = (
    "daviddtech_scalp",
    "ema_momentum",
    "rsi_reversion",
    "ema_scalp",
    "supertrend",
    "squeeze_momentum",
    "qqe_mod",
    "utbot_alert",
    "hull_suite",
    "sar_chop",
)

PAIR_KEYS = ("BTC_USD", "SOL_USD", "XRP_USD")
DEFAULT_INTERVALS = (5, 15, 60)
_DAY_SEC = 86400.0
_MIN_SPAN_SEC = 60.0


def _parse_intervals_csv(s: str) -> tuple[int, ...]:
    parts = [int(x.strip()) for x in str(s).split(",") if x.strip()]
    return tuple(parts) if parts else DEFAULT_INTERVALS


def _wfo_load_days(train_h: float, holdout_h: float, step_h: float) -> float:
    total_hours = train_h + holdout_h + step_h * 3.0
    return total_hours / 24.0 + 0.5


def _bars_per_year(interval_minutes: int) -> float:
    return 365.25 * 24.0 * 60.0 / float(max(1, interval_minutes))


def _param_set_from_champion_blob(raw: dict, *, fill_model: str) -> ParamSet:
    names = {f.name for f in fields(ParamSet)}
    kwargs = {k: raw[k] for k in raw if k in names}
    kwargs.setdefault("fill_model", fill_model)
    return ParamSet(**kwargs)


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _score_exp(m) -> float:
    return float(m.expectancy) * math.sqrt(max(1, int(m.trade_count)))


def _span_seconds(bars: dict[str, np.ndarray], interval_minutes: int) -> float:
    """Wall time from first bar open through end of last bar (seconds)."""
    ts = bars["timestamp"]
    if len(ts) < 1:
        return _DAY_SEC
    bar_sec = float(max(1, interval_minutes) * 60)
    if len(ts) < 2:
        return max(_MIN_SPAN_SEC, bar_sec)
    return max(_MIN_SPAN_SEC, float(ts[-1] - ts[0]) + bar_sec)


def _score_exp_per_day(score_exp: float, span_sec: float) -> float:
    """Extrapolate score_exp_sqrt_n to a nominal 86400s window (sqrt scaling in trade count)."""
    span = max(float(span_sec), _MIN_SPAN_SEC)
    return float(score_exp) * math.sqrt(_DAY_SEC / span)


def _pnl_usd(total_pnl_backtest: float, contract_size: float) -> float:
    """``evaluate_params`` ``total_pnl`` is already USD for 1 contract (includes contract_size + fees)."""
    return float(total_pnl_backtest)


def _fmt_usd(x: float) -> str:
    ax = abs(x)
    if ax >= 1_000_000:
        return f"${x:,.0f}"
    if ax >= 1000:
        return f"${x:,.2f}"
    return f"${x:,.4f}"


def _load_align(
    symbol: str,
    intervals: tuple[int, ...],
    last_n_days: float | None,
    min_bars: int,
) -> tuple[dict[int, dict[str, np.ndarray]], float, float] | None:
    """Return aligned bars per interval and (ts0, ts1) of common window, or None."""
    loaded: dict[int, dict[str, np.ndarray]] = {}
    for iv in intervals:
        b = bar_store.load_bars(symbol, iv, last_n_days=last_n_days)
        if b is None or len(b["close"]) < min_bars:
            return None
        loaded[iv] = b

    ts0 = max(float(loaded[iv]["timestamp"][0]) for iv in intervals)
    ts1 = min(float(loaded[iv]["timestamp"][-1]) for iv in intervals)
    if ts1 <= ts0:
        return None

    aligned: dict[int, dict[str, np.ndarray]] = {}
    for iv, b in loaded.items():
        ts = b["timestamp"]
        mask = (ts >= ts0) & (ts <= ts1)
        if int(mask.sum()) < min_bars:
            return None
        aligned[iv] = {k: np.asarray(v)[mask].copy() for k, v in b.items()}

    return aligned, ts0, ts1


def _clip_aligned_last_hours(
    al_dict: dict[int, dict[str, np.ndarray]],
    intervals: tuple[int, ...],
    hours: float,
) -> tuple[dict[int, dict[str, np.ndarray]], float, float] | None:
    """Keep only the last ``hours`` wall-clock window, then re-intersect all TFs."""
    if hours <= 0:
        return None
    t_end = min(float(al_dict[iv]["timestamp"][-1]) for iv in intervals)
    t_cut = t_end - float(hours) * 3600.0
    clipped: dict[int, dict[str, np.ndarray]] = {}
    for iv in intervals:
        b = al_dict[iv]
        ts = b["timestamp"]
        mask = ts >= t_cut
        if int(mask.sum()) < 1:
            return None
        clipped[iv] = {k: np.asarray(v)[mask].copy() for k, v in b.items()}
    ts0 = max(float(clipped[iv]["timestamp"][0]) for iv in intervals)
    ts1 = min(float(clipped[iv]["timestamp"][-1]) for iv in intervals)
    if ts1 <= ts0:
        return None
    final: dict[int, dict[str, np.ndarray]] = {}
    for iv in intervals:
        ts = clipped[iv]["timestamp"]
        m = (ts >= ts0) & (ts <= ts1)
        if int(m.sum()) < 1:
            return None
        final[iv] = {k: np.asarray(v)[m].copy() for k, v in clipped[iv].items()}
    return final, ts0, ts1


def _slice_aligned_range(
    al_dict: dict[int, dict[str, np.ndarray]],
    intervals: tuple[int, ...],
    t_lo: float,
    t_hi_excl: float,
) -> tuple[dict[int, dict[str, np.ndarray]], float, float] | None:
    """Keep bars with open time in ``[t_lo, t_hi_excl)``, then re-intersect across ``intervals``."""
    if t_hi_excl <= t_lo:
        return None
    clipped: dict[int, dict[str, np.ndarray]] = {}
    for iv in intervals:
        b = al_dict[iv]
        ts = b["timestamp"]
        mask = (ts >= t_lo) & (ts < t_hi_excl)
        if int(mask.sum()) < 1:
            return None
        clipped[iv] = {k: np.asarray(v)[mask].copy() for k, v in b.items()}
    ts0 = max(float(clipped[iv]["timestamp"][0]) for iv in intervals)
    ts1 = min(float(clipped[iv]["timestamp"][-1]) for iv in intervals)
    if ts1 <= ts0:
        return None
    final: dict[int, dict[str, np.ndarray]] = {}
    for iv in intervals:
        ts = clipped[iv]["timestamp"]
        m = (ts >= ts0) & (ts <= ts1)
        if int(m.sum()) < 1:
            return None
        final[iv] = {k: np.asarray(v)[m].copy() for k, v in clipped[iv].items()}
    return final, ts0, ts1


def _plan_contiguous_windows(
    ts0: float, ts1: float, n_target: int, window_sec: float
) -> tuple[int, list[tuple[float, float]]]:
    """Last ``n_actual`` disjoint half-open windows of length ``window_sec`` ending at or before ``ts1``."""
    span = float(ts1) - float(ts0)
    n_max = int(span // window_sec) if window_sec > 0 else 0
    n = min(int(n_target), n_max)
    if n <= 0:
        return 0, []
    t_first = float(ts1) - n * window_sec
    if t_first < ts0:
        t_first = float(ts0)
        n = int((float(ts1) - t_first) // window_sec)
    if n <= 0:
        return 0, []
    windows = [(t_first + i * window_sec, t_first + (i + 1) * window_sec) for i in range(n)]
    return n, windows


def _evaluate_one_pair_one_slice(
    pk: str,
    symbol: str,
    al_dict: dict[int, dict[str, np.ndarray]],
    champion_ps: ParamSet,
    base: ParamSet,
    contract_size: float,
    *,
    intervals: tuple[int, ...],
    min_bars: int,
    verbose: bool,
) -> tuple[list[dict], list[dict[str, str | int | float]], bool]:
    """Evaluate champion + lab on aligned ``al_dict``. Returns (rows, winner_summary, ok)."""
    rows_out: list[dict] = []
    winner_summary: list[dict[str, str | int | float]] = []
    for iv in intervals:
        if len(al_dict[iv]["close"]) < min_bars:
            return rows_out, winner_summary, False

    for iv in intervals:
        bars = al_dict[iv]
        n_bars = len(bars["close"])
        span_sec = _span_seconds(bars, iv)
        span_h = span_sec / 3600.0
        bpy = _bars_per_year(iv)

        m_ch = evaluate_params(bars, champion_ps, recency_half_life_bars=0.0, bars_per_year=bpy)
        pf_c = m_ch.profit_factor
        pf_cs = "inf" if pf_c == float("inf") else f"{pf_c:.3f}"
        sc_ch = _score_exp(m_ch)
        sc_ch_1d = _score_exp_per_day(sc_ch, span_sec)
        usd_ch = _pnl_usd(m_ch.total_pnl, contract_size)
        wch = 100.0 * float(m_ch.win_rate)
        if verbose:
            print(
                f"| {iv}m | {n_bars} | {span_h:.2f} | champion | `{champion_ps.mode}` | {m_ch.trade_count} | "
                f"{wch:.1f}% | {_fmt_usd(usd_ch)} | {m_ch.total_pnl:.4f} | {pf_cs} | {m_ch.sharpe:.3f} | "
                f"{sc_ch:.4f} | {sc_ch_1d:.4f} |"
            )
        rows_out.append(
            {
                "pair_key": pk,
                "symbol": symbol,
                "interval_m": iv,
                "side": "champion",
                "mode": champion_ps.mode,
                "trades": m_ch.trade_count,
                "win_rate": m_ch.win_rate,
                "win_pct": round(wch, 2),
                "total_pnl": m_ch.total_pnl,
                "pnl_usd_approx": round(usd_ch, 6),
                "contract_size": contract_size,
                "profit_factor": pf_c if pf_c != float("inf") else None,
                "sharpe": m_ch.sharpe,
                "score_exp_sqrt_n": sc_ch,
                "score_1d_eq": sc_ch_1d,
                "span_sec": round(span_sec, 3),
                "bars": n_bars,
            }
        )

        best_lab_score_1d = -float("inf")
        best_lab_mode = ""
        best_lab_m = None
        lab_cache: dict[str, tuple[Any, float, float]] = {}
        for mode in STRATEGIES:
            ps = replace(base, mode=mode)
            m = evaluate_params(bars, ps, recency_half_life_bars=0.0, bars_per_year=bpy)
            sc = _score_exp(m)
            sc_1d = _score_exp_per_day(sc, span_sec)
            lab_cache[mode] = (m, sc, sc_1d)
            if sc_1d > best_lab_score_1d:
                best_lab_score_1d = sc_1d
                best_lab_mode = mode
                best_lab_m = m

        for mode in STRATEGIES:
            m, sc_raw, sc_1d = lab_cache[mode]
            pf = m.profit_factor
            pfs = "inf" if pf == float("inf") else f"{pf:.3f}"
            tag = " **best lab**" if mode == best_lab_mode else ""
            usd_m = _pnl_usd(m.total_pnl, contract_size)
            wm = 100.0 * float(m.win_rate)
            if verbose:
                print(
                    f"| {iv}m | {n_bars} | {span_h:.2f} | lab | `{mode}`{tag} | {m.trade_count} | "
                    f"{wm:.1f}% | {_fmt_usd(usd_m)} | {m.total_pnl:.4f} | {pfs} | {m.sharpe:.3f} | "
                    f"{sc_raw:.4f} | {sc_1d:.4f} |"
                )
            rows_out.append(
                {
                    "pair_key": pk,
                    "symbol": symbol,
                    "interval_m": iv,
                    "side": "lab",
                    "mode": mode,
                    "trades": m.trade_count,
                    "win_rate": m.win_rate,
                    "win_pct": round(wm, 2),
                    "total_pnl": m.total_pnl,
                    "pnl_usd_approx": round(usd_m, 6),
                    "contract_size": contract_size,
                    "profit_factor": pf if pf != float("inf") else None,
                    "sharpe": m.sharpe,
                    "score_exp_sqrt_n": sc_raw,
                    "score_1d_eq": sc_1d,
                    "span_sec": round(span_sec, 3),
                    "bars": n_bars,
                }
            )

        if best_lab_m is not None:
            raw_ch = _score_exp(m_ch)
            raw_lb = _score_exp(best_lab_m)
            cmp_ch = _score_exp_per_day(raw_ch, span_sec)
            cmp_lb = _score_exp_per_day(raw_lb, span_sec)
            win = "champion" if cmp_ch > cmp_lb else ("lab" if cmp_lb > cmp_ch else "tie")
            usd_ch_v = _pnl_usd(m_ch.total_pnl, contract_size)
            usd_lb_v = _pnl_usd(best_lab_m.total_pnl, contract_size)
            wch_v = 100.0 * float(m_ch.win_rate)
            wlb_v = 100.0 * float(best_lab_m.win_rate)
            if verbose:
                print(
                    f"| {iv}m | {n_bars} | {span_h:.2f} | **vs** | `champ vs {best_lab_mode}` | - | "
                    f"{wch_v:.1f}% / {wlb_v:.1f}% | {_fmt_usd(usd_ch_v)} / {_fmt_usd(usd_lb_v)} | - | - | - | "
                    f"{raw_ch:.3f}/{raw_lb:.3f} | {cmp_ch:.3f}/{cmp_lb:.3f} **{win}** |"
                )
            winner_summary.append(
                {
                    "pair_key": pk,
                    "symbol": symbol,
                    "interval_m": iv,
                    "winner": win,
                    "champion_mode": champion_ps.mode,
                    "best_lab_mode": best_lab_mode,
                    "win_pct_champion": round(wch_v, 2),
                    "win_pct_best_lab": round(wlb_v, 2),
                    "pnl_usd_champion": round(usd_ch_v, 6),
                    "pnl_usd_best_lab": round(usd_lb_v, 6),
                    "score_champion_raw": round(_score_exp(m_ch), 6),
                    "score_best_lab_raw": round(_score_exp(best_lab_m), 6),
                    "score_champion_1d": round(cmp_ch, 6),
                    "score_best_lab_1d": round(cmp_lb, 6),
                    "span_sec": round(span_sec, 3),
                    "span_h": round(span_h, 4),
                    "trades_champion": int(m_ch.trade_count),
                    "trades_best_lab": int(best_lab_m.trade_count),
                    "bars": n_bars,
                }
            )
        if verbose:
            print()

    return rows_out, winner_summary, True


def _print_comparison_charts(winner_summary: list[dict[str, str | int | float]], *, title: str) -> None:
    """Markdown tables: winner per pair x TF (1d score, USD, win %)."""
    if not winner_summary:
        return
    print(title)
    print()
    print(
        "`score_1d_eq` = `expectancy*sqrt(trades)` * `sqrt(86400/span_sec)`; "
        "**pnl_USD** = `total_pnl` (already USD per 1 contract for the pair, incl. fees/size). "
        "Winner = higher `score_1d_eq` (champion vs best lab preset)."
    )
    print()
    hdr = (
        "| Pair | Symbol | TF | span_h | Winner | Champion | Best lab | "
        "win_%_ch | win_%_lab | pnl_USD_ch | pnl_USD_lab | "
        "score_1d_ch | score_1d_lab | tr_ch | tr_lab | bars |"
    )
    sep = (
        "|------|--------|----|--------|--------|----------|----------|----------|----------|"
        "------------|-------------|-------------|--------------|-------|--------|------|"
    )
    print(hdr)
    print(sep)
    for w in winner_summary:
        pk = str(w["pair_key"])
        sym = str(w["symbol"])
        iv = int(w["interval_m"])
        win = str(w["winner"])
        cm = str(w["champion_mode"])
        lm = str(w["best_lab_mode"])
        wch = float(w["win_pct_champion"])
        wlb = float(w["win_pct_best_lab"])
        uch = float(w["pnl_usd_champion"])
        ulb = float(w["pnl_usd_best_lab"])
        sch_1 = float(w["score_champion_1d"])
        slb_1 = float(w["score_best_lab_1d"])
        sh = float(w["span_h"])
        tc = int(w["trades_champion"])
        tl = int(w["trades_best_lab"])
        nb = int(w["bars"])
        win_cell = f"**{win}**"
        print(
            f"| `{pk}` | `{sym}` | {iv}m | {sh:.2f} | {win_cell} | `{cm}` | `{lm}` | "
            f"{wch:.1f}% | {wlb:.1f}% | {_fmt_usd(uch)} | {_fmt_usd(ulb)} | "
            f"{sch_1:.4f} | {slb_1:.4f} | {tc} | {tl} | {nb} |"
        )
    print()


def _roll_aggregate_to_winner_rows(
    roll_agg_rows: list[dict[str, Any]],
    *,
    window_hours: float,
) -> list[dict[str, str | int | float]]:
    """Map rolling means to the same row shape as ``winner_summary`` for `_print_comparison_charts`."""
    out: list[dict[str, str | int | float]] = []
    for ra in roll_agg_rows:
        sch = float(ra["avg_score_1d_champion"])
        slb = float(ra["avg_score_1d_best_lab"])
        if sch > slb:
            win = "champion"
        elif slb > sch:
            win = "lab"
        else:
            win = "tie"
        counts = ra.get("best_lab_mode_counts") or {}
        primary = str(ra.get("best_lab_mode_primary") or "")
        nwin = int(ra.get("windows_in_avg") or 0)
        top_c = max(counts.values()) if counts else 0
        lab_show = primary + ("*" if (counts and top_c < nwin) else "")
        span_h = float(window_hours)
        span_sec = span_h * 3600.0
        out.append(
            {
                "pair_key": ra["pair_key"],
                "symbol": ra["symbol"],
                "interval_m": int(ra["interval_m"]),
                "winner": win,
                "champion_mode": str(ra.get("champion_mode", "")),
                "best_lab_mode": lab_show,
                "win_pct_champion": float(ra["avg_win_pct_champion"]),
                "win_pct_best_lab": float(ra["avg_win_pct_best_lab"]),
                "pnl_usd_champion": float(ra["avg_pnl_usd_champion"]),
                "pnl_usd_best_lab": float(ra["avg_pnl_usd_best_lab"]),
                "score_champion_raw": 0.0,
                "score_best_lab_raw": 0.0,
                "score_champion_1d": float(ra["avg_score_1d_champion"]),
                "score_best_lab_1d": float(ra["avg_score_1d_best_lab"]),
                "span_sec": round(span_sec, 3),
                "span_h": round(span_h, 4),
                "trades_champion": int(round(float(ra["avg_trades_champion"]))),
                "trades_best_lab": int(round(float(ra["avg_trades_best_lab"]))),
                "bars": int(round(float(ra["avg_bars"]))),
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Champion vs lab strategies, 5/15/60m, aligned calendar.")
    ap.add_argument("--config", type=str, default=None)
    ap.add_argument("--champion-path", type=str, default=str(CHAMPION_PATH))
    ap.add_argument(
        "--lookback",
        type=str,
        default="wfo",
        help='"wfo" | "full" | "days:FLOAT" — applied before calendar alignment',
    )
    ap.add_argument("--min-bars", type=int, default=40, help="minimum bars per interval after align / clip")
    ap.add_argument(
        "--last-hours",
        type=float,
        default=None,
        metavar="H",
        help="After calendar align, keep only the last H hours (e.g. 24 = one full day). "
        "Uses min-bars floor 20 when set unless you pass a higher --min-bars.",
    )
    ap.add_argument(
        "--roll-windows",
        type=int,
        default=0,
        metavar="N",
        help="If N>0, run N disjoint wall-clock windows of --window-hours each (no overlap), "
        "anchored at the end of the aligned range; average metrics across windows. "
        "Incompatible with --last-hours.",
    )
    ap.add_argument(
        "--window-hours",
        type=float,
        default=24.0,
        metavar="H",
        help="Length of each window when --roll-windows is set (default 24).",
    )
    ap.add_argument(
        "--fallback-config-champion",
        action="store_true",
        help="If no row in scalp_champion.json for a symbol, use [scalp.pairs.*] + config params "
        "as the champion ParamSet (same as _params_from_config) instead of skipping the pair.",
    )
    ap.add_argument(
        "--intervals",
        type=str,
        default="5,15,60",
        metavar="M,M,...",
        help="Comma-separated bar intervals in minutes to align and compare (default 5,15,60). "
        "Use e.g. 5,15 when 60m Parquet is missing.",
    )
    args = ap.parse_args()
    eval_intervals = _parse_intervals_csv(args.intervals)

    if args.roll_windows > 0 and args.last_hours is not None:
        print("Use either --roll-windows or --last-hours, not both.", file=sys.stderr)
        return 2

    cfg_path = Path(args.config).resolve() if args.config else _REPO / "config.toml"
    if not cfg_path.exists():
        print("config not found:", cfg_path, file=sys.stderr)
        return 2

    with cfg_path.open("rb") as f:
        raw = tomllib.load(f)
    bot = load_scalp_config(raw)

    venue = (bot.venue or "coinbase_perps").strip().lower()
    bar_store.set_bar_store_venue("coinbase_perps")

    if args.lookback == "full":
        last_n_days: float | None = None
        lb_label = "full Parquet (then calendar intersection 5m/15m/60m)"
    elif args.lookback == "wfo":
        scalp_raw = raw.get("scalp", {})
        train_h = float(scalp_raw.get("wfo_train_hours", 24.0))
        hold_h = float(scalp_raw.get("wfo_holdout_hours", 8.0))
        step_h = float(scalp_raw.get("wfo_step_hours", 4.0))
        last_n_days = _wfo_load_days(train_h, hold_h, step_h)
        lb_label = f"wfo load_days={last_n_days:.3f} (train={train_h}h holdout={hold_h}h step={step_h}h), then align"
    elif args.lookback.startswith("days:"):
        last_n_days = float(args.lookback.split(":", 1)[1])
        lb_label = f"last_n_days={last_n_days}, then align 5m/15m/60m"
    else:
        print('--lookback must be "wfo", "full", or "days:FLOAT"', file=sys.stderr)
        return 2

    fill_model = getattr(bot, "backtest_fill_model", "next_open") or "next_open"
    fee_bps_eff = effective_scalp_fee_bps_per_leg(bot)
    fee = fee_bps_eff / 10_000.0
    slip = bot.slippage_bps / 10_000.0

    champ_path = Path(args.champion_path)

    print("# Champion vs PnL lab presets (multi-timeframe, aligned calendar)")
    print()
    print(f"- **Lookback (pre-align):** {lb_label}")
    print(f"- **Venue / bars dir:** `{venue}`")
    print(
        f"- **Fill:** `{fill_model}`  fee_leg={fee:.6f} ({fee_bps_eff:.3f} bps, "
        f"order_type={bot.order_type})  fee_usd/leg/contract={getattr(bot, 'fee_usd_per_contract_per_leg', 0):.3f}  "
        f"slip={slip:.6f}",
    )
    iv_label = " / ".join(f"{x}m" for x in eval_intervals)
    print(f"- **Bar intervals:** {iv_label} (calendar intersection across TFs)")
    print(f"- **Lab strategies:** {', '.join(STRATEGIES)} (same as `run_multiwindow_lab.py`)")
    print(f"- **recency_half_life_bars:** 0 (lab parity)")
    print(
        "- **Ranking:** `score_1d_eq` = `expectancy*sqrt(trades)` * `sqrt(86400/span_sec)` "
        "(span = first open to last close for that TF); **winner uses score_1d_eq**."
    )
    if args.last_hours is not None:
        print(f"- **Window clip:** last **{args.last_hours:g}h** of the aligned series (per pair).")
    if args.roll_windows > 0:
        print(
            f"- **Rolling windows:** **{args.roll_windows}** disjoint **{args.window_hours:g}h** segments "
            "(newest last), min-bars per slice; metrics **averaged** over windows that pass."
        )
    print()

    rows_out: list[dict] = []
    winner_summary: list[dict[str, str | int | float]] = []
    roll_agg_rows: list[dict[str, Any]] = []

    window_sec = float(args.window_hours) * 3600.0

    for pk in PAIR_KEYS:
        if pk not in bot.pairs:
            print(f"## SKIP `{pk}` — not in config")
            print()
            continue
        pc = bot.pairs[pk]
        symbol = pc.symbol

        aligned = _load_align(symbol, eval_intervals, last_n_days, args.min_bars)
        if aligned is None:
            print(f"## SKIP `{pk}` `{symbol}` — missing bar files or insufficient overlap after align")
            print()
            continue

        al_dict, ts0, ts1 = aligned
        min_need = max(20, int(args.min_bars)) if args.last_hours is not None else int(args.min_bars)
        slice_min_roll = max(20, int(args.min_bars))

        if args.last_hours is not None:
            clipped = _clip_aligned_last_hours(al_dict, eval_intervals, float(args.last_hours))
            if clipped is None:
                print(f"## SKIP `{pk}` — clip last {args.last_hours:g}h failed (no overlap)")
                print()
                continue
            al_dict, ts0, ts1 = clipped
            skip_pair = False
            for iv in eval_intervals:
                if len(al_dict[iv]["close"]) < min_need:
                    print(
                        f"## SKIP `{pk}` — after {args.last_hours:g}h clip, {iv}m has "
                        f"{len(al_dict[iv]['close'])} bars (need >= {min_need})"
                    )
                    print()
                    skip_pair = True
                    break
            if skip_pair:
                continue

        print(f"## {pk} (`{symbol}`)")
        print()
        win_note = (
            f" (last {args.last_hours:g}h)" if args.last_hours is not None else ""
        )
        iv_lbl = " / ".join(f"{x}m" for x in eval_intervals)
        if args.roll_windows > 0:
            print(
                f"**Full aligned calendar (rolling source):** `{_fmt_ts(ts0)}` -> `{_fmt_ts(ts1)}` "
                f"({iv_lbl} intersection)"
            )
        else:
            print(
                f"**Eval window{win_note}:** `{_fmt_ts(ts0)}` -> `{_fmt_ts(ts1)}` "
                f"(same timestamps for {iv_lbl})"
            )
        print()

        base = _params_from_config(pc, bot)
        base = replace(base, slippage_pct=slip, fill_model=fill_model)

        champ_row = load_champion_for_symbol(symbol, path=champ_path)
        missing_champ = champ_row is None or not isinstance(champ_row.get("params"), dict)
        if missing_champ:
            if not args.fallback_config_champion:
                print(f"*No champion row for `{symbol}` — skipped.*")
                print()
                continue
            champion_ps = base
            champ_row = {
                "params": {fn.name: getattr(base, fn.name) for fn in fields(ParamSet)},
                "mode": base.mode,
                "note": "synthetic baseline from config.toml (--fallback-config-champion)",
            }
            print(
                f"*No WFO champion for `{symbol}` — using **config ParamSet** "
                f"(mode=`{base.mode}`) as baseline vs lab oracle.*"
            )
            print()
        else:
            champion_ps = _param_set_from_champion_blob(champ_row["params"], fill_model=fill_model)
            champion_ps = replace(
                champion_ps,
                slippage_pct=slip,
                fill_model=fill_model,
                contract_size=float(getattr(pc, "contract_size", 1.0) or 1.0),
                fee_pct=fee,
                fee_usd_per_contract_per_leg=float(
                    getattr(bot, "fee_usd_per_contract_per_leg", 0.0) or 0.0
                ),
            )

        contract_size = float(getattr(pc, "contract_size", 1.0) or 1.0)
        print(
            f"*Backtest `total_pnl` is **USD per 1 contract** for `{symbol}` "
            f"(underlying multiplier **{contract_size}** + fees from config).*"
        )
        print()

        if args.roll_windows > 0:
            n_plan, windows = _plan_contiguous_windows(ts0, ts1, args.roll_windows, window_sec)
            print(
                f"**Aligned span (all TFs):** `{_fmt_ts(ts0)}` -> `{_fmt_ts(ts1)}` "
                f"({(ts1 - ts0) / 3600.0:.1f}h wall time)"
            )
            print(
                f"**Rolling:** requested **{args.roll_windows}** x **{args.window_hours:g}h** disjoint windows; "
                f"planned **{n_plan}** full windows (limited by history). "
                f"Slice min bars per TF: **{slice_min_roll}**."
            )
            if n_plan < args.roll_windows:
                print(
                    f"*Warning: only **{n_plan}** windows fit in the aligned range "
                    f"(need {(args.roll_windows * args.window_hours):g}h contiguous history).*"
                )
            print()
            if n_plan <= 0:
                print("*No full windows fit; skipped rolling eval for this pair.*")
                print()
                print("---")
                print()
                continue

            agg: dict[tuple[str, int], dict[str, Any]] = defaultdict(
                lambda: {
                    "champ_wins": 0,
                    "ties": 0,
                    "lab_wins": 0,
                    "pnl_usd_ch": [],
                    "pnl_usd_lab": [],
                    "win_pct_ch": [],
                    "win_pct_lab": [],
                    "score_1d_ch": [],
                    "score_1d_lab": [],
                    "trades_ch": [],
                    "trades_lab": [],
                    "best_lab_modes": [],
                    "bars": [],
                }
            )
            windows_ok = 0
            for wi, (w_lo, w_hi) in enumerate(windows):
                sl = _slice_aligned_range(al_dict, eval_intervals, w_lo, w_hi)
                if sl is None:
                    continue
                al_w, _, _ = sl
                rows_w, win_w, ok = _evaluate_one_pair_one_slice(
                    pk,
                    symbol,
                    al_w,
                    champion_ps,
                    base,
                    contract_size,
                    intervals=eval_intervals,
                    min_bars=slice_min_roll,
                    verbose=False,
                )
                if not ok:
                    continue
                windows_ok += 1
                for r in rows_w:
                    r2 = dict(r)
                    r2["roll_window_idx"] = wi
                    r2["roll_window_utc"] = f"{_fmt_ts(w_lo)} .. {_fmt_ts(w_hi)} (excl end)"
                    rows_out.append(r2)
                for wrow in win_w:
                    key = (pk, int(wrow["interval_m"]))
                    b = agg[key]
                    win = str(wrow["winner"])
                    if win == "champion":
                        b["champ_wins"] += 1
                    elif win == "lab":
                        b["lab_wins"] += 1
                    else:
                        b["ties"] += 1
                    b["pnl_usd_ch"].append(float(wrow["pnl_usd_champion"]))
                    b["pnl_usd_lab"].append(float(wrow["pnl_usd_best_lab"]))
                    b["win_pct_ch"].append(float(wrow["win_pct_champion"]))
                    b["win_pct_lab"].append(float(wrow["win_pct_best_lab"]))
                    b["score_1d_ch"].append(float(wrow["score_champion_1d"]))
                    b["score_1d_lab"].append(float(wrow["score_best_lab_1d"]))
                    b["trades_ch"].append(int(wrow["trades_champion"]))
                    b["trades_lab"].append(int(wrow["trades_best_lab"]))
                    b["best_lab_modes"].append(str(wrow["best_lab_mode"]))
                    b["bars"].append(int(wrow["bars"]))

            print(f"**Windows evaluated (all TFs passed min bars):** {windows_ok} / {n_plan}")
            print()
            if windows_ok <= 0:
                print("*No window produced enough bars on every timeframe; skipped averages.*")
                print()
            else:

                def _avg(xs: list[float]) -> float:
                    return float(sum(xs)) / float(len(xs)) if xs else 0.0

                print(
                    "| TF | n | champ_win_% | tie_% | lab_win_% | "
                    "avg_pnl_USD_ch | avg_pnl_USD_lab | avg_win_%_ch | avg_win_%_lab | "
                    "avg_score_1d_ch | avg_score_1d_lab | avg_tr_ch | avg_tr_lab |"
                )
                print(
                    "|----|---|-------------|-------|-----------|----------------|-----------------|"
                    "--------------|--------------|-----------------|-----------------|----------|----------|"
                )
                for iv in eval_intervals:
                    key = (pk, iv)
                    b = agg[key]
                    nwin = len(b["pnl_usd_ch"])
                    if nwin <= 0:
                        continue
                    cw = 100.0 * b["champ_wins"] / nwin
                    tw = 100.0 * b["ties"] / nwin
                    lw = 100.0 * b["lab_wins"] / nwin
                    mode_counts = dict(
                        sorted(
                            {m: b["best_lab_modes"].count(m) for m in set(b["best_lab_modes"])}.items(),
                            key=lambda kv: (-kv[1], kv[0]),
                        )
                    )
                    primary_lab = next(iter(mode_counts)) if mode_counts else ""
                    roll_agg_rows.append(
                        {
                            "pair_key": pk,
                            "symbol": symbol,
                            "interval_m": iv,
                            "champion_mode": str(champion_ps.mode),
                            "best_lab_mode_primary": primary_lab,
                            "windows_requested": int(args.roll_windows),
                            "windows_planned": n_plan,
                            "windows_evaluated": windows_ok,
                            "windows_in_avg": nwin,
                            "champion_win_pct": round(cw, 2),
                            "tie_pct": round(tw, 2),
                            "lab_win_pct": round(lw, 2),
                            "avg_pnl_usd_champion": round(_avg(b["pnl_usd_ch"]), 6),
                            "avg_pnl_usd_best_lab": round(_avg(b["pnl_usd_lab"]), 6),
                            "avg_win_pct_champion": round(_avg(b["win_pct_ch"]), 4),
                            "avg_win_pct_best_lab": round(_avg(b["win_pct_lab"]), 4),
                            "avg_score_1d_champion": round(_avg(b["score_1d_ch"]), 6),
                            "avg_score_1d_best_lab": round(_avg(b["score_1d_lab"]), 6),
                            "avg_trades_champion": round(_avg([float(x) for x in b["trades_ch"]]), 4),
                            "avg_trades_best_lab": round(_avg([float(x) for x in b["trades_lab"]]), 4),
                            "avg_bars": round(_avg([float(x) for x in b["bars"]]), 2),
                            "best_lab_mode_counts": mode_counts,
                        }
                    )
                    ra = roll_agg_rows[-1]
                    print(
                        f"| {iv}m | {nwin} | {cw:.1f}% | {tw:.1f}% | {lw:.1f}% | "
                        f"{_fmt_usd(ra['avg_pnl_usd_champion'])} | {_fmt_usd(ra['avg_pnl_usd_best_lab'])} | "
                        f"{ra['avg_win_pct_champion']:.2f}% | {ra['avg_win_pct_best_lab']:.2f}% | "
                        f"{ra['avg_score_1d_champion']:.4f} | {ra['avg_score_1d_best_lab']:.4f} | "
                        f"{ra['avg_trades_champion']:.2f} | {ra['avg_trades_best_lab']:.2f} |"
                    )
                print()
                print("*`n` = windows contributing to that TF row (same as evaluated if all TFs pass each window).*")
                print()

            print("Champion params (from JSON) for this symbol:")
            print(json.dumps(champ_row.get("params", {}), indent=2)[:1200])
            print()
            print("---")
            print()
            continue

        print(
            "| TF | bars | span_h | side | mode | trades | win_% | pnl_USD | pnl_pts | PF | Sharpe | "
            "score_raw | score_1d_eq |"
        )
        print(
            "|----|------|--------|------|------|--------|-------|---------|---------|-----|--------|"
            "-----------|-------------|"
        )

        rows_p, win_p, ok_ev = _evaluate_one_pair_one_slice(
            pk,
            symbol,
            al_dict,
            champion_ps,
            base,
            contract_size,
            intervals=eval_intervals,
            min_bars=min_need,
            verbose=True,
        )
        if not ok_ev:
            print(f"*Evaluation skipped: one or more timeframes have fewer than {min_need} bars after clip.*")
            print()
        else:
            rows_out.extend(rows_p)
            winner_summary.extend(win_p)

        print("Champion params (from JSON) for this symbol:")
        print(json.dumps(champ_row.get("params", {}), indent=2)[:1200])
        print()
        print("---")
        print()

    roll_winner_rows: list[dict[str, str | int | float]] = []
    if args.roll_windows > 0 and roll_agg_rows:
        roll_winner_rows = _roll_aggregate_to_winner_rows(
            roll_agg_rows,
            window_hours=float(args.window_hours),
        )

    # --- Winner summary (mid-doc) -------------------------------------------------
    if winner_summary:
        _print_comparison_charts(
            winner_summary,
            title="## Winner summary (higher **1-day equivalent** score wins)",
        )
        print("*60m rows often have few bars/trades; treat as noisy.*")
        print()
    elif roll_winner_rows:
        _print_comparison_charts(
            roll_winner_rows,
            title="## Winner summary (mean over disjoint windows; winner = higher **mean** score_1d_eq)",
        )
        print(
            "*Rolling run: **pnl / win_% / score_1d_eq / trades / bars** are **averages** per window. "
            "**Best lab*** = most frequent winning lab preset across windows (`*` if not unanimous).*"
        )
        print("*60m rows often have few bars/trades; treat as noisy.*")
        print()

    # --- Comparison charts (end) --------------------------------------------------
    if winner_summary:
        print()
        print("=" * 76)
        _print_comparison_charts(
            winner_summary,
            title="## Comparison charts - full run summary (pair x timeframe)",
        )
        print("=" * 76)
        print()
    elif roll_winner_rows:
        print()
        print("=" * 76)
        _print_comparison_charts(
            roll_winner_rows,
            title="## Comparison charts - full run summary (pair x timeframe)",
        )
        print("=" * 76)
        print()

    # Compact JSON summary for tooling
    print("<!-- JSON_SUMMARY")
    print(json.dumps(rows_out, indent=2))
    print("JSON_SUMMARY -->")
    if roll_agg_rows:
        print("<!-- JSON_ROLL_AGGREGATE")
        print(json.dumps(roll_agg_rows, indent=2))
        print("JSON_ROLL_AGGREGATE -->")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
