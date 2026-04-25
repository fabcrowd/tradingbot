#!/usr/bin/env python3
"""Compare TP validation / selection methods vs bar-thirds baseline on the same tape.

Methods (hypotheses from improvement notes):
  BASE   - Bar-index early/mid/late (PnL lab geometry); robust = all 3 segments ok.
  BLK5   - 5 equal contiguous time blocks; robust = all 5 blocks pass (trades, pnl>0, sharpe>0).
  RMIN   - Restrict tp grid to R = atr_tp_mult / atr_stop_mult in [r_lo, r_hi].
  BLK+R  - BLK5 robust pick restricted to R-filtered grid.
  BAR+R  - BASE robust pick restricted to R-filtered grid.

Also reports:
  HOLD   - Top-1 tp by Sharpe on first 80%% bars vs top-1 on last 20%% (stability diagnostic).
  SOLDDT - SOL only: config ``daviddtech_scalp`` params vs champion mode, same TP sweep on full bars.

Outputs markdown + optional JSON. Run from repo root::

  python .optimization/pnl-feedback-lab/scripts/champion_tp_ablation_compare.py
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
from scalp_bot.scalp_wfo import CHAMPION_PATH, _params_from_config, load_champion_for_symbol


def _wfo_load_days(train_h: float, holdout_h: float, step_h: float) -> float:
    return (train_h + holdout_h + step_h * 3.0) / 24.0 + 0.5


def _bars_per_year(interval_minutes: int) -> float:
    return 365.25 * 24.0 * 60.0 / float(max(1, interval_minutes))


def _param_set_from_champion_blob(raw: dict, *, fill_model: str) -> ParamSet:
    names = {f.name for f in fields(ParamSet)}
    kwargs = {k: raw[k] for k in raw if k in names}
    kwargs.setdefault("fill_model", fill_model)
    return ParamSet(**kwargs)


def _iter_lab_segments(bars: dict[str, np.ndarray]) -> list[tuple[str, dict[str, np.ndarray]]]:
    n = len(bars["close"])
    if n < 90:
        return []
    t = n // 3
    out = []
    for name, a, b in (("early", 0, t), ("mid", t, 2 * t), ("late", 2 * t, n)):
        out.append((name, {k: np.asarray(v)[a:b].copy() for k, v in bars.items()}))
    return out


def _iter_equal_blocks(bars: dict[str, np.ndarray], n_blocks: int, min_bars: int):
    n = len(bars["close"])
    for i in range(n_blocks):
        a = int(i * n / n_blocks)
        b = int((i + 1) * n / n_blocks)
        if b - a < min_bars:
            continue
        yield i, {k: np.asarray(v)[a:b].copy() for k, v in bars.items()}


def _block_pass(m, cs: float, min_trades: int) -> bool:
    return (
        m.trade_count >= min_trades
        and float(m.sharpe) > 0
        and float(m.total_pnl) > 0
    )


def _segment_pass(m, cs: float, min_trades: int) -> bool:
    return _block_pass(m, cs, min_trades)


def _metrics_for_tp(
    bars: dict[str, np.ndarray],
    ps: ParamSet,
    *,
    bpy: float,
    cs: float,
    min_trades: int,
    min_bars: int,
    n_blocks: int,
) -> dict[str, float | bool | int]:
    m_full = evaluate_params(bars, ps, bars_per_year=bpy)
    segs = _iter_lab_segments(bars)
    seg_ok = 0
    seg_sharpes: list[float] = []
    seg_pnls: list[float] = []
    thirds_robust = False
    if len(segs) == 3:
        ok_all = True
        for _name, wb in segs:
            if len(wb["close"]) < min_bars:
                ok_all = False
                break
            m = evaluate_params(wb, ps, bars_per_year=bpy)
            seg_sharpes.append(float(m.sharpe))
            seg_pnls.append(float(m.total_pnl))
            if _segment_pass(m, cs, min_trades):
                seg_ok += 1
            else:
                ok_all = False
        thirds_robust = ok_all

    blk_pass = 0
    blk_sharpes: list[float] = []
    blk_pnls: list[float] = []
    for _i, wb in _iter_equal_blocks(bars, n_blocks, min_bars):
        m = evaluate_params(wb, ps, bars_per_year=bpy)
        blk_sharpes.append(float(m.sharpe))
        blk_pnls.append(float(m.total_pnl))
        if _block_pass(m, cs, min_trades):
            blk_pass += 1
    n_blk = len(blk_sharpes)
    blk_robust = n_blk == n_blocks and blk_pass == n_blocks

    return {
        "full_pnl_usd": float(m_full.total_pnl),
        "full_sharpe": float(m_full.sharpe),
        "full_trades": int(m_full.trade_count),
        "thirds_robust": thirds_robust,
        "thirds_seg_ok": seg_ok,
        "thirds_min_sharpe": min(seg_sharpes) if seg_sharpes else float("nan"),
        "thirds_min_pnl_usd": min(seg_pnls) if seg_pnls else float("nan"),
        "blk_pass": blk_pass,
        "blk_n": n_blk,
        "blk_robust": blk_robust,
        "blk_min_sharpe": min(blk_sharpes) if blk_sharpes else float("nan"),
        "blk_min_pnl_usd": min(blk_pnls) if blk_pnls else float("nan"),
    }


def _tp_grid_filtered(
    base: ParamSet,
    grid: list[float],
    *,
    r_lo: float,
    r_hi: float,
) -> list[float]:
    stop = float(base.atr_stop_mult)
    if stop <= 0:
        return list(grid)
    out = []
    for tp in grid:
        r = float(tp) / stop
        if r_lo <= r <= r_hi:
            out.append(tp)
    return out if out else list(grid)


def _pick_best_effort_profitable(rows: list[tuple[float, dict]]) -> tuple[float, dict] | None:
    """Among rows with >=2 segment ok (if segments exist) or always, require full pnl>0 sharpe>0."""
    pool = []
    for tp, d in rows:
        if d["full_pnl_usd"] <= 0 or d["full_sharpe"] <= 0:
            continue
        if int(d["thirds_seg_ok"]) >= 2 or not math.isfinite(d["thirds_min_sharpe"]):
            pool.append((tp, d))
    if not pool:
        return None
    tp, d = max(pool, key=lambda x: (x[1]["full_sharpe"], x[1]["full_pnl_usd"]))
    return tp, d


def _pick_robust_thirds(rows: list[tuple[float, dict]]) -> tuple[float, dict] | None:
    cand = [(tp, d) for tp, d in rows if d["thirds_robust"]]
    if not cand:
        return None
    tp, d = max(cand, key=lambda x: x[1]["thirds_min_sharpe"])
    return tp, d


def _pick_robust_blk(rows: list[tuple[float, dict]]) -> tuple[float, dict] | None:
    cand = [(tp, d) for tp, d in rows if d["blk_robust"]]
    if not cand:
        return None
    tp, d = max(cand, key=lambda x: x[1]["blk_min_sharpe"])
    return tp, d


def _pick_soft_blk(
    rows: list[tuple[float, dict]],
    *,
    min_pass: int,
    n_blocks: int,
) -> tuple[float, dict] | None:
    """Pick tp with blk_pass >= min_pass, maximize (blk_min_sharpe, full_pnl_usd)."""
    cand = [
        (tp, d)
        for tp, d in rows
        if int(d["blk_n"]) == n_blocks and int(d["blk_pass"]) >= min_pass
    ]
    if not cand:
        return None
    tp, d = max(cand, key=lambda x: (x[1]["blk_min_sharpe"], x[1]["full_pnl_usd"]))
    return tp, d


def _holdout_top1(
    bars: dict[str, np.ndarray],
    base: ParamSet,
    tp_grid: list[float],
    *,
    bpy: float,
    min_trades: int,
    min_bars: int,
) -> tuple[float | None, float | None, bool]:
    n = len(bars["close"])
    cut = int(0.8 * n)
    if cut < min_bars or n - cut < min_bars:
        return None, None, False
    train = {k: np.asarray(v)[:cut].copy() for k, v in bars.items()}
    test = {k: np.asarray(v)[cut:].copy() for k, v in bars.items()}

    def best_tp(sub: dict[str, np.ndarray]) -> float | None:
        best: tuple[float, float] | None = None
        for tp in tp_grid:
            ps = replace(base, atr_tp_mult=float(tp))
            m = evaluate_params(sub, ps, bars_per_year=bpy)
            if m.trade_count < min_trades:
                continue
            sc = float(m.sharpe)
            if best is None or sc > best[0]:
                best = (sc, float(tp))
        return best[1] if best else None

    t_tr = best_tp(train)
    t_te = best_tp(test)
    match = t_tr is not None and t_te is not None and abs(t_tr - t_te) < 1e-9
    return t_tr, t_te, match


def _spearman(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3 or len(xs) != len(ys):
        return float("nan")
    rx = np.argsort(np.argsort(xs)).astype(float)
    ry = np.argsort(np.argsort(ys)).astype(float)
    rx -= rx.mean()
    ry -= ry.mean()
    denom = float(np.sqrt((rx**2).sum() * (ry**2).sum()))
    if denom <= 0:
        return float("nan")
    return float((rx * ry).sum() / denom)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=str, default=None)
    ap.add_argument("--lookbacks", type=str, default="wfo,full")
    ap.add_argument("--tp-grid", type=str, default="1.0,1.5,2.0,2.5,3.0,3.5,4.0,5.0,6.0")
    ap.add_argument("--r-lo", type=float, default=1.0, help="Min atr_tp_mult / atr_stop_mult")
    ap.add_argument("--r-hi", type=float, default=5.0, help="Max atr_tp_mult / atr_stop_mult")
    ap.add_argument("--n-blocks", type=int, default=5)
    ap.add_argument("--min-bars", type=int, default=40)
    ap.add_argument("--min-trades", type=int, default=3)
    ap.add_argument("--json-out", type=str, default=None)
    ap.add_argument("--report-md", type=str, default=None)
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve() if args.config else _REPO / "config.toml"
    with cfg_path.open("rb") as f:
        raw = tomllib.load(f)
    bot = load_scalp_config(raw)
    venue = (bot.venue or "coinbase_perps").strip().lower()
    bar_store.set_bar_store_venue("coinbase_perps")

    scalp_raw = raw.get("scalp", {}) or {}
    train_h = float(scalp_raw.get("wfo_train_hours", 24.0))
    hold_h = float(scalp_raw.get("wfo_holdout_hours", 8.0))
    step_h = float(scalp_raw.get("wfo_step_hours", 4.0))

    def parse_lb(s: str) -> tuple[str, float | None]:
        s = s.strip().lower()
        if s == "wfo":
            return ("wfo", _wfo_load_days(train_h, hold_h, step_h))
        if s == "full":
            return ("full", None)
        if s.startswith("days:"):
            return (s, float(s.split(":", 1)[1]))
        raise ValueError(s)

    lookbacks = [parse_lb(x) for x in args.lookbacks.split(",") if x.strip()]
    tp_grid = [float(x.strip()) for x in args.tp_grid.split(",") if x.strip()]
    fill_model = getattr(bot, "backtest_fill_model", "next_open") or "next_open"
    fee = effective_scalp_fee_bps_per_leg(bot) / 10_000.0
    slip = bot.slippage_bps / 10_000.0

    champ_path = Path(CHAMPION_PATH)
    all_out: dict = {"lookbacks": args.lookbacks, "pairs": {}}

    print("# Champion TP method ablation (vs bar-thirds baseline)")
    print()
    print(f"- R filter: tp/stop in [{args.r_lo}, {args.r_hi}]")
    print(f"- Blocks: {args.n_blocks} equal slices; segment pass = trades>={args.min_trades}, pnl_USD>0, Sharpe>0")
    print()

    pair_keys = list(bot.pairs.keys())

    for pk in pair_keys:
        pc = bot.pairs[pk]
        sym = pc.symbol
        iv = int(pc.interval)
        cs = float(getattr(pc, "contract_size", 1.0) or 1.0)
        bpy = _bars_per_year(iv)
        row = load_champion_for_symbol(sym, path=champ_path)
        if not row or not isinstance(row.get("params"), dict):
            print(f"## {pk} - no champion, skip\n")
            continue

        base = _param_set_from_champion_blob(row["params"], fill_model=fill_model)
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
        disk_tp = float(base.atr_tp_mult)

        print(f"## `{pk}` `{sym}` mode=`{base.mode}` stop={base.atr_stop_mult} disk_tp={disk_tp}")
        print()

        pair_out: dict = {"symbol": sym, "disk_tp": disk_tp, "lookbacks": {}}

        for lb_label, last_n in lookbacks:
            bars = bar_store.load_bars(sym, iv, last_n_days=last_n)
            if bars is None or len(bars["close"]) < args.min_bars:
                print(f"### {lb_label} - insufficient bars\n")
                continue

            rows: list[tuple[float, dict]] = []
            sharpes_full: list[float] = []
            tps_list: list[float] = []
            for tp in tp_grid:
                ps = replace(base, atr_tp_mult=float(tp))
                d = _metrics_for_tp(
                    bars,
                    ps,
                    bpy=bpy,
                    cs=cs,
                    min_trades=args.min_trades,
                    min_bars=args.min_bars,
                    n_blocks=args.n_blocks,
                )
                d["tp"] = tp
                rows.append((tp, d))
                sharpes_full.append(d["full_sharpe"])
                tps_list.append(tp)

            tp_r = _tp_grid_filtered(base, tp_grid, r_lo=args.r_lo, r_hi=args.r_hi)
            rows_r = [(tp, d) for tp, d in rows if tp in tp_r]

            try:
                disk_d = next(d for tp, d in rows if abs(tp - disk_tp) < 1e-9)
            except StopIteration:
                ps_d = replace(base, atr_tp_mult=disk_tp)
                disk_d = _metrics_for_tp(
                    bars,
                    ps_d,
                    bpy=bpy,
                    cs=cs,
                    min_trades=args.min_trades,
                    min_bars=args.min_bars,
                    n_blocks=args.n_blocks,
                )

            def fmt_pick(label: str, picked: tuple[float, dict] | None) -> str:
                if picked is None:
                    return f"| {label} | (none) | | | | | |"
                tp, d = picked
                return (
                    f"| {label} | {tp} | {d['full_pnl_usd']:.2f} | {d['full_sharpe']:.2f} | "
                    f"{'Y' if d['thirds_robust'] else 'N'} | {int(d['blk_pass'])}/{int(d['blk_n'])} | "
                    f"{d['thirds_min_sharpe']:.2f} | {d['blk_min_sharpe']:.2f} |"
                )

            bar_rob = _pick_robust_thirds(rows)
            blk_rob = _pick_robust_blk(rows)
            blk_soft4 = _pick_soft_blk(rows, min_pass=4, n_blocks=args.n_blocks)
            blk_soft3 = _pick_soft_blk(rows, min_pass=3, n_blocks=args.n_blocks)
            bar_r_rob = _pick_robust_thirds(rows_r)
            blk_r_rob = _pick_robust_blk(rows_r)
            blk_r_soft4 = _pick_soft_blk(rows_r, min_pass=4, n_blocks=args.n_blocks)
            be = _pick_best_effort_profitable(rows)

            h_tr, h_te, h_match = _holdout_top1(
                bars, base, tp_grid, bpy=bpy, min_trades=args.min_trades, min_bars=args.min_bars
            )
            rho = _spearman(tps_list, sharpes_full)

            print(f"### Lookback `{lb_label}` (n={len(bars['close'])})")
            print()
            print(
                "| method | pick_tp | full_pnl_USD | full_Sharpe | thirds_robust | blk_pass | min_seg_Sh | min_blk_Sh |"
            )
            print("|---|---|---:|---:|:---:|---:|---:|---:|")
            print(
                f"| DISK | {disk_tp} | {disk_d['full_pnl_usd']:.2f} | {disk_d['full_sharpe']:.2f} | "
                f"{'Y' if disk_d['thirds_robust'] else 'N'} | {int(disk_d['blk_pass'])}/{int(disk_d['blk_n'])} | "
                f"{disk_d['thirds_min_sharpe']:.2f} | {disk_d['blk_min_sharpe']:.2f} |"
            )
            print(fmt_pick("BASE_BAR_ROB", bar_rob))
            print(fmt_pick("BASE_BLK_ROB", blk_rob))
            print(fmt_pick("BASE_BLK_GE4", blk_soft4))
            print(fmt_pick("BASE_BLK_GE3", blk_soft3))
            print(fmt_pick("RFLT_BAR_ROB", bar_r_rob))
            print(fmt_pick("RFLT_BLK_ROB", blk_r_rob))
            print(fmt_pick("RFLT_BLK_GE4", blk_r_soft4))
            print(fmt_pick("BASE_BEST_EFF", be))

            ht_tr = f"{h_tr}" if h_tr is not None else "-"
            ht_te = f"{h_te}" if h_te is not None else "-"
            print(
                f"| HOLD_TOP1 | train={ht_tr} test={ht_te} | | | | | match={h_match} | rho(S,full)={rho:.2f} |"
            )
            print()

            lb_entry = {
                "n_bars": len(bars["close"]),
                "disk": {k: disk_d[k] for k in disk_d if k != "tp"},
                "picks": {
                    "BASE_BAR_ROB": {"tp": bar_rob[0], **bar_rob[1]} if bar_rob else None,
                    "BASE_BLK_ROB": {"tp": blk_rob[0], **blk_rob[1]} if blk_rob else None,
                    "BASE_BLK_GE4": {"tp": blk_soft4[0], **blk_soft4[1]} if blk_soft4 else None,
                    "BASE_BLK_GE3": {"tp": blk_soft3[0], **blk_soft3[1]} if blk_soft3 else None,
                    "RFLT_BAR_ROB": {"tp": bar_r_rob[0], **bar_r_rob[1]} if bar_r_rob else None,
                    "RFLT_BLK_ROB": {"tp": blk_r_rob[0], **blk_r_rob[1]} if blk_r_rob else None,
                    "RFLT_BLK_GE4": {"tp": blk_r_soft4[0], **blk_r_soft4[1]} if blk_r_soft4 else None,
                    "BASE_BEST_EFF": {"tp": be[0], **be[1]} if be else None,
                },
                "holdout_train_tp": h_tr,
                "holdout_test_tp": h_te,
                "holdout_top1_match": h_match,
                "spearman_tp_vs_full_sharpe": rho,
            }
            pair_out["lookbacks"][lb_label] = lb_entry

        # SOL: daviddtech from config vs champion mode on FULL bars only
        if pk == "SOL_USD":
            bars_f = bar_store.load_bars(sym, iv, last_n_days=None)
            if bars_f is not None and len(bars_f["close"]) >= args.min_bars:
                ddt = _params_from_config(pc, bot)
                ddt = replace(ddt, fee_pct=fee, slippage_pct=slip, fill_model=fill_model)
                rows_ch: list[tuple[float, dict]] = []
                rows_dd: list[tuple[float, dict]] = []
                for tp in tp_grid:
                    ps_c = replace(base, atr_tp_mult=float(tp))
                    ps_d = replace(ddt, atr_tp_mult=float(tp))
                    rows_ch.append(
                        (
                            tp,
                            _metrics_for_tp(
                                bars_f,
                                ps_c,
                                bpy=bpy,
                                cs=cs,
                                min_trades=args.min_trades,
                                min_bars=args.min_bars,
                                n_blocks=args.n_blocks,
                            ),
                        )
                    )
                    rows_dd.append(
                        (
                            tp,
                            _metrics_for_tp(
                                bars_f,
                                ps_d,
                                bpy=bpy,
                                cs=cs,
                                min_trades=args.min_trades,
                                min_bars=args.min_bars,
                                n_blocks=args.n_blocks,
                            ),
                        )
                    )
                be_ch = _pick_best_effort_profitable(rows_ch)
                be_dd = _pick_best_effort_profitable(rows_dd)
                br_ch = _pick_robust_thirds(rows_ch)
                br_dd = _pick_robust_thirds(rows_dd)
                bk_ch = _pick_robust_blk(rows_ch)
                bk_dd = _pick_robust_blk(rows_dd)

                def one_line(name: str, picked: tuple[float, dict] | None) -> str:
                    if not picked:
                        return f"{name}: (none)"
                    tp, d = picked
                    return (
                        f"{name}: tp={tp} full_pnl={d['full_pnl_usd']:.2f} sharpe={d['full_sharpe']:.2f} "
                        f"thirds_robust={d['thirds_robust']} blk={d['blk_pass']}/{d['blk_n']}"
                    )

                print("### SOL_USD extra: champion mode vs config daviddtech (full parquet)")
                print()
                print(f"- Champion `{base.mode}`: {one_line('BAR_ROB', br_ch)} | {one_line('BLK_ROB', bk_ch)} | {one_line('BEST_EFF', be_ch)}")
                print(f"- Config `daviddtech_scalp`: {one_line('BAR_ROB', br_dd)} | {one_line('BLK_ROB', bk_dd)} | {one_line('BEST_EFF', be_dd)}")
                print()

                narrow = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
                rows_narrow: list[tuple[float, dict]] = []
                for tp in narrow:
                    ps_c = replace(base, atr_tp_mult=float(tp))
                    rows_narrow.append(
                        (
                            tp,
                            _metrics_for_tp(
                                bars_f,
                                ps_c,
                                bpy=bpy,
                                cs=cs,
                                min_trades=args.min_trades,
                                min_bars=args.min_bars,
                                n_blocks=args.n_blocks,
                            ),
                        )
                    )
                be_narrow = _pick_best_effort_profitable(rows_narrow)
                bk_narrow = _pick_robust_blk(rows_narrow)

                print(
                    f"- Champion **narrow TP grid** {narrow}: "
                    f"{one_line('BLK_ROB', bk_narrow)} | {one_line('BEST_EFF', be_narrow)}"
                )
                print()

                pair_out["sol_mode_compare_full"] = {
                    "champion_mode": base.mode,
                    "champion": {k: ({"tp": v[0], **v[1]} if v else None) for k, v in [("BAR_ROB", br_ch), ("BLK_ROB", bk_ch), ("BEST_EFF", be_ch)]},
                    "daviddtech": {k: ({"tp": v[0], **v[1]} if v else None) for k, v in [("BAR_ROB", br_dd), ("BLK_ROB", bk_dd), ("BEST_EFF", be_dd)]},
                    "champion_narrow_grid": {
                        "grid": narrow,
                        "BLK_ROB": ({"tp": bk_narrow[0], **bk_narrow[1]} if bk_narrow else None),
                        "BEST_EFF": ({"tp": be_narrow[0], **be_narrow[1]} if be_narrow else None),
                    },
                }

        all_out["pairs"][pk] = pair_out
        print("---\n")

    # Verdict block: which method beats DISK on full_pnl for same robustness tier
    print("## Aggregate verdict (full lookback only, when present)")
    print()
    print(
        "| pair | DISK pnl | BLK_GE4 pnl/tp | BLK_GE3 pnl/tp | BAR_ROB pnl/tp | best beats DISK? |"
    )
    print("|---|---:|---|---|---|---|")
    for pk, po in all_out["pairs"].items():
        lb = po.get("lookbacks", {}).get("full")
        if not lb:
            continue
        disk = lb["disk"]
        picks = lb["picks"]
        dpnl = float(disk["full_pnl_usd"])
        b4 = picks.get("BASE_BLK_GE4")
        b3 = picks.get("BASE_BLK_GE3")
        bar = picks.get("BASE_BAR_ROB")
        best_delta = 0.0
        best_label = "DISK"
        for label, p in (
            ("BASE_BLK_GE4", b4),
            ("BASE_BLK_GE3", b3),
            ("BASE_BAR_ROB", bar),
            ("RFLT_BLK_GE4", picks.get("RFLT_BLK_GE4")),
            ("BASE_BEST_EFF", picks.get("BASE_BEST_EFF")),
        ):
            if p and float(p["full_pnl_usd"]) - dpnl > best_delta:
                best_delta = float(p["full_pnl_usd"]) - dpnl
                best_label = f"{label} (+{best_delta:.2f})"
        if best_delta <= 1e-9:
            best_label = "DISK or tie"
        b4s = f"{b4['full_pnl_usd']:.1f}/{b4['tp']}" if b4 else "-"
        b3s = f"{b3['full_pnl_usd']:.1f}/{b3['tp']}" if b3 else "-"
        bars = f"{bar['full_pnl_usd']:.1f}/{bar['tp']}" if bar else "-"
        print(f"| {pk} | {dpnl:.2f} | {b4s} | {b3s} | {bars} | {best_label} |")
    print()

    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w") as f:
            json.dump(all_out, f, indent=2, default=str)
        print(f"Wrote JSON `{p}`")
    if args.report_md:
        p = Path(args.report_md)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Champion TP ablation summary", ""]
        for pk, po in all_out["pairs"].items():
            lines.append(f"## {pk}")
            lines.append("")
            for lb, le in po.get("lookbacks", {}).items():
                lines.append(f"### {lb}")
                lines.append("")
                lines.append(f"- disk full_pnl_USD: {le['disk']['full_pnl_usd']}")
                for mn, pv in le.get("picks", {}).items():
                    if pv:
                        lines.append(
                            f"- {mn}: tp={pv['tp']} full_pnl={pv['full_pnl_usd']:.2f} "
                            f"thirds_robust={pv['thirds_robust']} blk={pv['blk_pass']}/{pv['blk_n']}"
                        )
                lines.append("")
        p.write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote report `{p}`")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
