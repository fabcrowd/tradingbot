#!/usr/bin/env python3
"""Validate champion atr_tp_mult: sweep TP multiples on disk champion params per window.

Windows match the PnL lab: **full** series plus **early / mid / late** bar-index thirds
(``run_multiwindow_lab._window_thirds``). If n < 90 bars, only **full** is used.

Examples (repo root)::

  python .optimization/pnl-feedback-lab/scripts/sweep_champion_atr_tp_validate.py
  python .../sweep_champion_atr_tp_validate.py --lookbacks wfo,full --min-trades-per-window 3
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import fields, replace
from pathlib import Path

import numpy as np
import tomllib

_REPO = Path(__file__).resolve().parents[3]
_SERVER = _REPO / "backend" / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from scalp_bot import bar_store
from scalp_bot.scalp_config import effective_scalp_fee_bps_per_leg, load_scalp_config
from scalp_bot.scalp_vec_backtest import ParamSet, evaluate_params
from scalp_bot.scalp_wfo import CHAMPION_PATH, load_champion_for_symbol


def _wfo_load_days(train_h: float, holdout_h: float, step_h: float) -> float:
    return (train_h + holdout_h + step_h * 3.0) / 24.0 + 0.5


def _bars_per_year(interval_minutes: int) -> float:
    return 365.25 * 24.0 * 60.0 / float(max(1, interval_minutes))


def _param_set_from_champion_blob(raw: dict, *, fill_model: str) -> ParamSet:
    names = {f.name for f in fields(ParamSet)}
    kwargs = {k: raw[k] for k in raw if k in names}
    kwargs.setdefault("fill_model", fill_model)
    return ParamSet(**kwargs)


def _iter_lab_windows(bars: dict[str, np.ndarray]) -> list[tuple[str, dict[str, np.ndarray]]]:
    n = len(bars["close"])
    out: list[tuple[str, dict[str, np.ndarray]]] = [("full", bars)]
    if n < 90:
        return out
    t = n // 3
    for name, a, b in (("early", 0, t), ("mid", t, 2 * t), ("late", 2 * t, n)):
        sliced = {k: np.asarray(v)[a:b].copy() for k, v in bars.items()}
        out.append((name, sliced))
    return out


def _parse_lookbacks(s: str, scalp_raw: dict) -> list[tuple[str, float | None]]:
    """Return list of (label, last_n_days or None for full)."""
    train_h = float(scalp_raw.get("wfo_train_hours", 24.0))
    hold_h = float(scalp_raw.get("wfo_holdout_hours", 8.0))
    step_h = float(scalp_raw.get("wfo_step_hours", 4.0))
    out: list[tuple[str, float | None]] = []
    for part in s.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part == "wfo":
            out.append(("wfo", _wfo_load_days(train_h, hold_h, step_h)))
        elif part == "full":
            out.append(("full", None))
        elif part.startswith("days:"):
            out.append((part, float(part.split(":", 1)[1])))
        else:
            raise ValueError(f"unknown lookback token: {part!r} (use wfo, full, days:FLOAT)")
    return out


def _median_atr_over_close(bars: dict[str, np.ndarray], atr_period: int) -> float:
    c = bars["close"]
    h, lo = bars["high"], bars["low"]
    n = len(c)
    if n < atr_period + 2:
        return float("nan")
    tr = np.zeros(n)
    tr[0] = h[0] - lo[0]
    for i in range(1, n):
        tr[i] = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
    atr = np.zeros(n)
    atr[atr_period - 1] = float(np.mean(tr[:atr_period]))
    for i in range(atr_period, n):
        atr[i] = (atr[i - 1] * (atr_period - 1) + tr[i]) / atr_period
    ratio = atr[atr_period:] / np.maximum(c[atr_period:], 1e-12)
    return float(np.median(ratio))


def main() -> int:
    ap = argparse.ArgumentParser(description="Multi-window atr_tp_mult validation on champion params.")
    ap.add_argument("--config", type=str, default=None, help="config.toml path")
    ap.add_argument(
        "--pair-keys",
        type=str,
        default="",
        help="Comma-separated [scalp.pairs.*] keys; default = all pairs in config",
    )
    ap.add_argument("--champion-path", type=str, default=str(CHAMPION_PATH))
    ap.add_argument(
        "--lookbacks",
        type=str,
        default="wfo,full",
        help="Comma-separated: wfo, full, days:FLOAT",
    )
    ap.add_argument(
        "--tp-grid",
        type=str,
        default="1.0,1.5,2.0,2.5,3.0,3.5,4.0,5.0,6.0",
        help="Comma-separated atr_tp_mult values",
    )
    ap.add_argument("--min-bars-window", type=int, default=40, help="Skip window if fewer bars")
    ap.add_argument(
        "--min-trades-per-window",
        type=int,
        default=3,
        help="Segment considered insufficient below this trade count",
    )
    ap.add_argument("--json-out", type=str, default=None, help="Write full result JSON to path")
    ap.add_argument(
        "--report-md",
        type=str,
        default=None,
        help="Write UTF-8 markdown summary (verdicts + disk vs pick); avoids Windows shell UTF-16 redirect",
    )
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve() if args.config else _REPO / "config.toml"
    if not cfg_path.exists():
        print("config not found:", cfg_path, file=sys.stderr)
        return 2

    with cfg_path.open("rb") as f:
        raw = tomllib.load(f)
    bot = load_scalp_config(raw)
    venue = (bot.venue or "coinbase_perps").strip().lower()
    bar_store.set_bar_store_venue("coinbase_perps")

    scalp_raw = raw.get("scalp", {}) or {}
    try:
        lookback_specs = _parse_lookbacks(args.lookbacks, scalp_raw)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    tp_grid = [float(x.strip()) for x in args.tp_grid.split(",") if x.strip()]
    fill_model = getattr(bot, "backtest_fill_model", "next_open") or "next_open"
    fee = effective_scalp_fee_bps_per_leg(bot) / 10_000.0
    slip = bot.slippage_bps / 10_000.0

    if args.pair_keys.strip():
        pair_list = [p.strip() for p in args.pair_keys.split(",") if p.strip()]
    else:
        pair_list = list(bot.pairs.keys())

    champ_path = Path(args.champion_path)
    all_results: dict[str, object] = {"lookbacks": args.lookbacks, "pairs": {}}

    print("# Champion `atr_tp_mult` - multi-window validation")
    print()
    print(f"- **Champion file:** `{champ_path}`")
    print(f"- **Lookbacks:** `{args.lookbacks}`")
    print(f"- **Fill / costs:** `{fill_model}`  fee_leg={fee}  slip={slip}")
    print(f"- **TP grid:** {tp_grid}")
    print(f"- **Min bars / window:** {args.min_bars_window}  **Min trades (segment ok):** {args.min_trades_per_window}")
    print()

    for pk in pair_list:
        if pk not in bot.pairs:
            print(f"## {pk} - not in config, skip\n")
            continue
        pc = bot.pairs[pk]
        sym = pc.symbol
        iv = int(pc.interval)
        cs = float(getattr(pc, "contract_size", 1.0) or 1.0)
        champ_row = load_champion_for_symbol(sym, path=champ_path)
        if not champ_row or not isinstance(champ_row.get("params"), dict):
            print(f"## `{pk}` (`{sym}`) - no champion row\n")
            continue

        base = _param_set_from_champion_blob(champ_row["params"], fill_model=fill_model)
        base = replace(
            base,
            fee_pct=fee,
            slippage_pct=slip,
            fill_model=fill_model,
            contract_size=cs,
            fee_usd_per_contract_per_leg=float(
                getattr(bot, "fee_usd_per_contract_per_leg", 0.0) or 0.0
            ),
        )
        bpy = _bars_per_year(iv)

        pair_entry: dict[str, object] = {"symbol": sym, "champion_mode": base.mode, "baseline_tp": base.atr_tp_mult}
        all_results["pairs"][pk] = pair_entry

        print(f"## `{pk}` - `{sym}` @ {iv}m - champion `{base.mode}` - disk **atr_tp_mult={base.atr_tp_mult}**")
        print()

        for lb_label, last_n_days in lookback_specs:
            bars = bar_store.load_bars(sym, iv, last_n_days=last_n_days)
            if bars is None or len(bars["close"]) < args.min_bars_window:
                print(f"### Lookback `{lb_label}` - insufficient bars\n")
                continue

            med_ac = _median_atr_over_close(bars, base.atr_period)
            n_full = len(bars["close"])
            windows = _iter_lab_windows(bars)
            print(f"### Lookback **`{lb_label}`** - bars={n_full}  median ATR/close ~ {med_ac * 100:.4f}%")
            print()

            lb_data: dict[str, object] = {"n_bars": n_full, "windows": {}}
            pair_entry[f"lookback_{lb_label}"] = lb_data

            # Collect per-tp metrics across segment windows only (not full) for robustness
            segment_names = [w for w, _ in windows if w != "full"]

            print(
                "| tp | window | n_bars | trades | win% | pnl_USD | Sharpe | PF | ok |"
            )
            print("|---:|---|---:|---:|---:|---:|---:|---|:---:|")

            tp_summaries: list[dict[str, object]] = []

            for tp in tp_grid:
                ps = replace(base, atr_tp_mult=float(tp))
                row_min_sharpe = math.inf
                row_min_pnl = math.inf
                seg_ok = 0
                seg_n = 0
                full_pnl_usd: float | None = None
                full_sharpe: float | None = None

                for wname, wb in windows:
                    nb = len(wb["close"])
                    if nb < args.min_bars_window:
                        continue
                    m = evaluate_params(wb, ps, bars_per_year=bpy)
                    pnl_usd = float(m.total_pnl)
                    pf = m.profit_factor
                    pfs = "inf" if pf == float("inf") else (f"{pf:.2f}" if pf == pf else "n/a")
                    ok = m.trade_count >= args.min_trades_per_window
                    ok_mark = "yes" if ok else "no"
                    approx_tp_pct = med_ac * tp * 100.0 if med_ac == med_ac else float("nan")
                    print(
                        f"| {tp} | {wname} | {nb} | {m.trade_count} | {100 * m.win_rate:.1f} | "
                        f"{pnl_usd:,.2f} | {m.sharpe:.2f} | {pfs} | {ok_mark} |"
                    )

                    if wname == "full":
                        full_pnl_usd = pnl_usd
                        full_sharpe = float(m.sharpe)
                    elif wname in segment_names:
                        seg_n += 1
                        if ok:
                            seg_ok += 1
                            row_min_sharpe = min(row_min_sharpe, float(m.sharpe))
                            row_min_pnl = min(row_min_pnl, pnl_usd)

                if row_min_sharpe == math.inf:
                    row_min_sharpe = float("nan")
                if row_min_pnl == math.inf:
                    row_min_pnl = float("nan")

                robust = (
                    seg_n >= 3
                    and seg_ok == seg_n
                    and math.isfinite(row_min_sharpe)
                    and row_min_sharpe > 0
                    and math.isfinite(row_min_pnl)
                    and row_min_pnl > 0
                )
                tp_summaries.append(
                    {
                        "atr_tp_mult": tp,
                        "approx_tp_pct_median_atr": med_ac * tp * 100.0 if med_ac == med_ac else None,
                        "full_pnl_usd": full_pnl_usd,
                        "full_sharpe": full_sharpe,
                        "segments_with_min_trades": seg_ok,
                        "segments_total": seg_n,
                        "min_sharpe_segments": row_min_sharpe,
                        "min_pnl_usd_segments": row_min_pnl,
                        "robust_all_segments_positive": robust,
                    }
                )
                lb_data[f"tp_{tp}"] = tp_summaries[-1]

            # Pick recommendation: among robust rows, max min_sharpe; else max full_sharpe with seg_ok>=2
            robust_rows = [r for r in tp_summaries if r["robust_all_segments_positive"]]
            pick_kind: str
            if robust_rows:
                pick_kind = "robust"
                pick = max(robust_rows, key=lambda r: float(r["min_sharpe_segments"]))
                verdict = f"**Robust pick:** `atr_tp_mult={pick['atr_tp_mult']}` (min segment Sharpe > 0, min segment pnl_USD > 0, all segments meet min trades)."
            else:
                partial = [r for r in tp_summaries if int(r["segments_with_min_trades"] or 0) >= 2]
                partial_profitable = [
                    r
                    for r in partial
                    if float(r.get("full_pnl_usd") or 0) > 0 and float(r.get("full_sharpe") or 0) > 0
                ]
                pool = partial_profitable if partial_profitable else []
                if pool:
                    pick_kind = "best_effort_profitable"
                    pick = max(pool, key=lambda r: (float(r["full_sharpe"] or -999), float(r["full_pnl_usd"] or -999)))
                    verdict = (
                        f"**No fully robust tp.** Best effort (>=2 segments with min trades, full window profitable): "
                        f"`atr_tp_mult={pick['atr_tp_mult']}` "
                        f"(full pnl_USD={pick['full_pnl_usd']}, full Sharpe={pick['full_sharpe']}). "
                        "Still **not validated** on all early/mid/late segments."
                    )
                elif partial:
                    pick_kind = "no_profitable_full"
                    pick = max(partial, key=lambda r: (float(r["full_sharpe"] or -999), float(r["full_pnl_usd"] or -999)))
                    verdict = (
                        "**No robust tp and no profitable full-window candidate** in the grid "
                        f"(largest full Sharpe was `atr_tp_mult={pick['atr_tp_mult']}` "
                        f"with full pnl_USD={pick['full_pnl_usd']}). "
                        "Extend lookback, widen grid, or treat TP as unsettled."
                    )
                else:
                    pick_kind = "weak"
                    pick = max(tp_summaries, key=lambda r: (float(r["full_sharpe"] or -999), float(r["full_pnl_usd"] or -999)))
                    verdict = (
                        f"**Weak evidence:** `atr_tp_mult={pick['atr_tp_mult']}` by full-window only "
                        "(segments lack trades - shorten grid or extend lookback)."
                    )

            print()
            print(verdict)
            disk_note = ""
            if pick_kind == "no_profitable_full":
                disk_note = (
                    f"- **No** profitable full-window `atr_tp_mult` in this grid; do not treat diagnostics as a promotion "
                    f"(disk **atr_tp_mult={base.atr_tp_mult}**)."
                )
            elif pick_kind == "weak":
                disk_note = (
                    f"- **Sparse segments** (below min trades in some thirds); full-window reference only "
                    f"`atr_tp_mult={pick['atr_tp_mult']}` (disk **{base.atr_tp_mult}**)."
                )
            elif float(pick["atr_tp_mult"]) != float(base.atr_tp_mult):
                disk_note = (
                    f"- Disk champion uses **atr_tp_mult={base.atr_tp_mult}** - validation suggests **{pick['atr_tp_mult']}** on this tape."
                )
            else:
                disk_note = (
                    f"- Disk champion **atr_tp_mult={base.atr_tp_mult}** matches or is consistent with validation pick."
                )
            print(disk_note)
            print()

            lb_data["validation"] = {
                "disk_atr_tp_mult": float(base.atr_tp_mult),
                "pick_atr_tp_mult": float(pick["atr_tp_mult"]),
                "pick_kind": pick_kind,
                "robust_any": bool(robust_rows),
                "verdict_markdown": verdict + "\n" + disk_note,
                "pick_full_pnl_usd": pick.get("full_pnl_usd"),
                "pick_full_sharpe": pick.get("full_sharpe"),
            }

        print("---\n")

    if args.report_md:
        rp = Path(args.report_md)
        rp.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Champion atr_tp_mult validation summary",
            "",
            f"- Lookbacks: `{args.lookbacks}`",
            f"- Champion file: `{champ_path}`",
            "",
        ]
        for pk, pdata in all_results.get("pairs", {}).items():
            if not isinstance(pdata, dict):
                continue
            lines.append(f"## `{pk}` ({pdata.get('symbol', '')})")
            lines.append("")
            def _lb_key(name: str) -> tuple[int, str]:
                if name.endswith("_wfo"):
                    return (0, name)
                if name.endswith("_full"):
                    return (1, name)
                return (2, name)

            for k in sorted((x for x in pdata if x.startswith("lookback_")), key=_lb_key):
                v = pdata[k]
                if not isinstance(v, dict):
                    continue
                val = v.get("validation")
                if not isinstance(val, dict):
                    continue
                lb = k.replace("lookback_", "")
                lines.append(f"### Lookback `{lb}`")
                lines.append("")
                lines.append(val.get("verdict_markdown", "").replace("\n", "\n\n"))
                lines.append("")
            lines.append("---")
            lines.append("")
        rp.write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote report: `{rp}`")

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            json.dump(all_results, f, indent=2, default=str)
        print(f"Wrote JSON: `{out_path}`")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
