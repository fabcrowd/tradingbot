"""Offline WFO parity (stage 2) + analytical knob sweeps.

**Stage 2** uses production ``optimize_pair``: full ``build_default_grid`` on one
continuous eval window (``wfo_continuous_eval_hours`` + warmup prefix), per-mode
champion pick, safety gate, and optional positive-PnL gate.

**P&L metrics (every JSON row):**

- When ``optimize_pair`` saves a champion: ``latest_holdout_total_pnl``,
  ``mean_holdout_total_pnl`` (from champion dict).
- Always (optimize ok or not): ``wf_diagnostic_total_pnl`` = stage-1-style sum of
  forward-segment simulated ``total_pnl`` so you can **rank knob settings by P&L**
  even when WFO returns ``no_strategies_passed_train_gates``.
- Bootstrap suite: ``bootstrap_window_total_pnl`` on the lookback window for the
  bootstrap-picked mode.

``optimize_pair`` is called with **stock** ``WFOConfig`` (no production patches).
Longer-history **P&L** comparisons use ``wf_diagnostic_*`` only (``--load-days``
feeds ``bar_store.load_bars`` there, not the WFO bar-load formula).

Examples::

  python .optimization/wfo_knob_suite.py --suite stage2_train_hours --load-days 35
  python .optimization/wfo_knob_suite.py --suite all --load-days 35 --output-json .optimization/runs/wfo_knob_last.json

Requires Parquet for each pair symbol (see ``[scalp]`` venue -> ``bar_store``).
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import tomllib

logging.basicConfig(level=logging.WARNING)
for _name in ("scalp_wfo", "backend.server.scalp_bot.scalp_wfo"):
    logging.getLogger(_name).setLevel(logging.WARNING)

import numpy as np

from backend.server.scalp_bot import bar_store
from backend.server.scalp_bot.param_tuner import STRATEGY_MODES, _params_from_pair_config
from backend.server.scalp_bot.scalp_config import effective_scalp_fee_bps_per_leg, load_scalp_config
from backend.server.scalp_bot.scalp_runtime import _wfo_config_from_scalp_cfg
from backend.server.scalp_bot.scalp_vec_backtest import evaluate_params
from backend.server.scalp_bot.scalp_wfo import WFOConfig, optimize_pair, _params_from_config, score_strategy
from backend.server.scalp_bot.strategy_lookback import (
    best_mode_bootstrap_no_champion,
    per_strategy_lookback_metrics,
)

CONFIG_PATH = _root / "config.toml"

# Stage-1-style OOS sum (same spirit as ``wfo_window_ab_test.py``): comparable **P&L** across knobs.
WF_DIAG_WARMUP_BARS = 192
WF_DIAG_SEGMENT_BARS = 96
WF_DIAG_MIN_FWD_BARS = 20


def ensure_synthetic_bars_if_missing(
    bot_cfg: object,
    *,
    days: float,
    pair_keys: list[str] | None = None,
    seed: int = 42,
) -> None:
    """Write deterministic OHLCV Parquet for any configured pair missing bars.

    **Only for offline harness / CI** -- not representative of live markets.
    """
    rng = np.random.default_rng(seed)
    now = int(time.time())
    pairs_iter = _pairs_subset(bot_cfg, pair_keys) if pair_keys else list(bot_cfg.pairs.items())
    for _pk, pc in pairs_iter:
        sym = pc.symbol
        iv = int(pc.interval)
        if bar_store.load_bars(sym, iv, last_n_days=min(days + 5.0, 90.0)) is not None:
            continue
        step = iv * 60
        n = min(int(days * 86400 / max(step, 60)), 2200)
        if n < 200:
            n = 200
        start = now - n * step
        close = 50_000.0 + np.cumsum(rng.normal(0.0, 12.0, n))
        wick = np.abs(rng.normal(0.0, 25.0, n))
        high = close + wick
        low = close - wick
        open_ = np.empty(n)
        open_[0] = close[0]
        open_[1:] = close[:-1]
        vol = rng.uniform(100.0, 5000.0, n)
        batch: list[dict] = []
        for i in range(n):
            ts = int(start + i * step)
            o = float(open_[i])
            h = float(high[i])
            l = float(low[i])
            c = float(close[i])
            batch.append(
                {
                    "timestamp": ts,
                    "open": o,
                    "high": max(h, o, c),
                    "low": min(l, o, c),
                    "close": c,
                    "volume": float(vol[i]),
                    "vwap": c,
                    "trades": int(rng.integers(50, 500)),
                }
            )
        for j in range(0, len(batch), 400):
            bar_store.append_candles(sym, iv, batch[j : j + 400])
        print(f"[synth] wrote ~{n} bars for {sym} @{iv}m", flush=True)


def _load_bot_cfg():
    with open(CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)
    cfg = load_scalp_config(raw)
    bar_store.set_bar_store_venue(cfg.venue)
    return cfg


def wfo_from_bot_cfg(bot_cfg, *, fast: bool = False) -> WFOConfig:
    w = _wfo_config_from_scalp_cfg(bot_cfg)
    if fast:
        w = replace(
            w,
            continuous_eval_hours=min(float(w.continuous_eval_hours), 48.0),
            continuous_warmup_hours=min(float(w.continuous_warmup_hours), 24.0),
            continuous_min_trades=min(int(w.continuous_min_trades), 5),
        )
    return w


def _run_optimize(
    pair_key: str, pair_cfg, bot_cfg: object, wfo: WFOConfig,
) -> tuple[dict | None, str | None, dict]:
    current = _params_from_config(pair_cfg, bot_cfg)
    fee_pct = effective_scalp_fee_bps_per_leg(bot_cfg) / 10_000.0
    slippage_pct = bot_cfg.slippage_bps / 10_000.0
    fill_model = getattr(bot_cfg, "backtest_fill_model", "close_slip")
    return optimize_pair(
        pair_cfg.symbol,
        pair_cfg.interval,
        fee_pct,
        slippage_pct,
        wfo,
        current,
        fill_model,
        contract_size=float(getattr(pair_cfg, "contract_size", 1.0) or 1.0),
        fee_usd_per_contract_per_leg=float(
            getattr(bot_cfg, "fee_usd_per_contract_per_leg", 0.0) or 0.0
        ),
    )


def _summarize(result: dict | None, skip: str | None) -> dict:
    if result:
        hm = result.get("holdout_metrics") or {}
        hmm = result.get("holdout_metrics_mean") or {}
        return {
            "ok": True,
            "mode": result.get("mode"),
            "score": result.get("score"),
            "stability": result.get("stability"),
            "windows_evaluated": result.get("windows_evaluated"),
            "holdout_metrics": result.get("holdout_metrics"),
            "holdout_metrics_mean": result.get("holdout_metrics_mean"),
            # Flatten for cross-row P&L comparison (primary production outcome when ok)
            "latest_holdout_total_pnl": hm.get("total_pnl"),
            "mean_holdout_total_pnl": hmm.get("total_pnl"),
        }
    return {"ok": False, "skip_reason": skip}


def _slice_bars(bars: dict, start: int, end: int) -> dict:
    return {k: v[start:end] for k, v in bars.items()}


def _pick_best_mode_train(
    pc,
    bot_cfg: object,
    bars: dict,
    *,
    objective: str,
    recency_div: float,
) -> str:
    """Best STRATEGY_MODES on train slice (same family as stage-1 window harness)."""
    pc = copy.deepcopy(pc)
    n = len(bars["close"])
    if n < 4:
        return pc.strategy_mode
    hl = max(10.0, float(n) / max(1e-9, float(recency_div)))
    best_mode = pc.strategy_mode
    best_s = -float("inf")
    for mode in STRATEGY_MODES:
        params = _params_from_pair_config(pc, bot_cfg, mode)
        m = evaluate_params(bars, params, recency_half_life_bars=hl)
        s = score_strategy(m, objective)
        if s > best_s:
            best_s = s
            best_mode = mode
    return best_mode


def _walk_forward_diagnostic_pnl(
    pc,
    bot_cfg: object,
    *,
    load_days: float,
    train_hours: float,
    recency_div: float,
    objective: str,
) -> dict:
    """Sum of **forward-segment** ``total_pnl`` (OOS-style); headline when ``optimize_pair`` skips.

    Uses fixed warmup/segment bar counts aligned with ``wfo_window_ab_test.py`` so
    **deltas across knob values** are on the same path length. Not identical to full
    ``optimize_pair`` (no param grid on forward).
    """
    bars = bar_store.load_bars(pc.symbol, pc.interval, last_n_days=load_days)
    if bars is None:
        return {
            "wf_diagnostic_total_pnl": None,
            "wf_diagnostic_trades": None,
            "wf_diagnostic_segments": 0,
            "wf_diagnostic_train_bars": None,
            "wf_diagnostic_note": "no_bars",
        }
    n = int(len(bars["timestamp"]))
    iv = max(1, int(pc.interval))
    train_bars = max(20, int(train_hours * 60.0 / float(iv)))
    if n < WF_DIAG_WARMUP_BARS + WF_DIAG_SEGMENT_BARS + WF_DIAG_MIN_FWD_BARS:
        return {
            "wf_diagnostic_total_pnl": None,
            "wf_diagnostic_trades": None,
            "wf_diagnostic_segments": 0,
            "wf_diagnostic_train_bars": train_bars,
            "wf_diagnostic_note": "insufficient_series_length",
        }
    pc = copy.deepcopy(pc)
    total = 0.0
    trades = 0
    segs = 0
    cursor = WF_DIAG_WARMUP_BARS
    while cursor < n:
        fwd_end = min(cursor + WF_DIAG_SEGMENT_BARS, n)
        if fwd_end - cursor < WF_DIAG_MIN_FWD_BARS:
            break
        lb_start = max(0, cursor - train_bars)
        lb = _slice_bars(bars, lb_start, cursor)
        if len(lb["close"]) < 10:
            break
        mode = _pick_best_mode_train(pc, bot_cfg, lb, objective=objective, recency_div=recency_div)
        fwd = _slice_bars(bars, cursor, fwd_end)
        params = _params_from_pair_config(pc, bot_cfg, mode)
        m = evaluate_params(fwd, params)
        total += float(m.total_pnl)
        trades += int(m.trade_count)
        segs += 1
        cursor = fwd_end
    return {
        "wf_diagnostic_total_pnl": round(total, 6),
        "wf_diagnostic_trades": int(trades),
        "wf_diagnostic_segments": int(segs),
        "wf_diagnostic_train_bars": int(train_bars),
        "wf_diagnostic_note": "stage1_style_oos_sum",
    }


def _bootstrap_window_pnl(pc, bot_cfg: object, lookback_hours: float) -> dict:
    """Flat P&L for the bootstrap-picked mode on ``lookback_hours`` (return % driver)."""
    mode = best_mode_bootstrap_no_champion(pc, bot_cfg, lookback_hours=lookback_hours)
    mets = per_strategy_lookback_metrics(pc, bot_cfg, lookback_hours=lookback_hours)
    pnl = None
    trades = None
    if mets and mode in mets:
        pnl = mets[mode].get("pnl")
        trades = mets[mode].get("trades")
    return {
        "bootstrap_best_mode": mode,
        "bootstrap_window_total_pnl": pnl,
        "bootstrap_window_trades": trades,
    }


def _pairs_subset(bot_cfg, only: list[str] | None):
    if not only:
        return list(bot_cfg.pairs.items())
    out = []
    for k in only:
        if k in bot_cfg.pairs:
            out.append((k, bot_cfg.pairs[k]))
        else:
            print(f"[warn] unknown pair_key {k!r} -- skip", file=sys.stderr)
    return out


def suite_stage2_train_hours(
    bot_cfg,
    load_days: float,
    pair_keys: list[str] | None,
    *,
    fast: bool,
) -> list[dict]:
    base = wfo_from_bot_cfg(bot_cfg, fast=fast)
    rows: list[dict] = []
    for th in (24.0, 48.0):
        wfo = replace(base, train_hours=th)
        for pk, pc in _pairs_subset(bot_cfg, pair_keys):
            t0 = time.perf_counter()
            res, skip, _diag = _run_optimize(pk, pc, bot_cfg, wfo)
            dt = time.perf_counter() - t0
            pnl_diag = _walk_forward_diagnostic_pnl(
                pc,
                bot_cfg,
                load_days=load_days,
                train_hours=float(wfo.train_hours),
                recency_div=3.0,
                objective=str(wfo.objective),
            )
            rows.append(
                {
                    "suite": "stage2_train_hours",
                    "train_hours": th,
                    "pair_key": pk,
                    "symbol": pc.symbol,
                    "elapsed_sec": round(dt, 2),
                    **_summarize(res, skip),
                    **pnl_diag,
                }
            )
    return rows


def suite_recency_divisor(
    bot_cfg,
    load_days: float,
    pair_keys: list[str] | None,
    *,
    fast: bool,
) -> list[dict]:
    """``optimize_pair`` is identical across divisors (production WFO uses n/3 only).

    Rows differ in ``wf_diagnostic_*``, which sweeps train-slice recency **divisor**
    for P&L ranking without touching ``scalp_wfo.py``.
    """
    base = replace(wfo_from_bot_cfg(bot_cfg, fast=fast), train_hours=48.0)
    rows: list[dict] = []
    for pk, pc in _pairs_subset(bot_cfg, pair_keys):
        t0 = time.perf_counter()
        res, skip, _diag = _run_optimize(pk, pc, bot_cfg, base)
        dt = time.perf_counter() - t0
        for div in (2.0, 3.0, 4.0):
            pnl_diag = _walk_forward_diagnostic_pnl(
                pc,
                bot_cfg,
                load_days=load_days,
                train_hours=float(base.train_hours),
                recency_div=float(div),
                objective=str(base.objective),
            )
            rows.append(
                {
                    "suite": "recency_divisor",
                    "recency_half_life_divisor": div,
                    "pair_key": pk,
                    "symbol": pc.symbol,
                    "elapsed_sec": round(dt, 2),
                    "optimize_pair_elapsed_sec": round(dt, 2),
                    **_summarize(res, skip),
                    **pnl_diag,
                }
            )
    return rows


def suite_wfo_min_trades(
    bot_cfg,
    load_days: float,
    pair_keys: list[str] | None,
    *,
    fast: bool,
) -> list[dict]:
    base = wfo_from_bot_cfg(bot_cfg, fast=fast)
    rows: list[dict] = []
    for mt in (4, 6, 10):
        wfo = replace(base, min_trades=mt)
        for pk, pc in _pairs_subset(bot_cfg, pair_keys):
            t0 = time.perf_counter()
            res, skip, _diag = _run_optimize(pk, pc, bot_cfg, wfo)
            dt = time.perf_counter() - t0
            pnl_diag = _walk_forward_diagnostic_pnl(
                pc,
                bot_cfg,
                load_days=load_days,
                train_hours=float(wfo.train_hours),
                recency_div=3.0,
                objective=str(wfo.objective),
            )
            rows.append(
                {
                    "suite": "wfo_min_trades",
                    "min_trades": mt,
                    "pair_key": pk,
                    "symbol": pc.symbol,
                    "elapsed_sec": round(dt, 2),
                    **_summarize(res, skip),
                    **pnl_diag,
                }
            )
    return rows


def suite_holdout_pf(
    bot_cfg,
    load_days: float,
    pair_keys: list[str] | None,
    *,
    fast: bool,
) -> list[dict]:
    base = wfo_from_bot_cfg(bot_cfg, fast=fast)
    rows: list[dict] = []
    for pf in (0.95, 1.0, 1.05):
        wfo = replace(base, min_latest_holdout_pf=pf)
        for pk, pc in _pairs_subset(bot_cfg, pair_keys):
            t0 = time.perf_counter()
            res, skip, _diag = _run_optimize(pk, pc, bot_cfg, wfo)
            dt = time.perf_counter() - t0
            pnl_diag = _walk_forward_diagnostic_pnl(
                pc,
                bot_cfg,
                load_days=load_days,
                train_hours=float(wfo.train_hours),
                recency_div=3.0,
                objective=str(wfo.objective),
            )
            rows.append(
                {
                    "suite": "min_latest_holdout_pf",
                    "min_latest_holdout_pf": pf,
                    "pair_key": pk,
                    "symbol": pc.symbol,
                    "elapsed_sec": round(dt, 2),
                    **_summarize(res, skip),
                    **pnl_diag,
                }
            )
    return rows


def suite_bootstrap_lookback(
    bot_cfg,
    load_days: float,
    pair_keys: list[str] | None,
    *,
    fast: bool,
) -> list[dict]:
    rows: list[dict] = []
    for hrs in (1.0, 2.0, 4.0):
        for pk, pc in _pairs_subset(bot_cfg, pair_keys):
            t0 = time.perf_counter()
            boot = _bootstrap_window_pnl(pc, bot_cfg, hrs)
            dt = time.perf_counter() - t0
            rows.append(
                {
                    "suite": "bootstrap_lookback_hours",
                    "lookback_hours": hrs,
                    "pair_key": pk,
                    "symbol": pc.symbol,
                    "ok": True,
                    "elapsed_sec": round(dt, 3),
                    **boot,
                }
            )
    return rows


SUITES: dict[str, Callable[..., list[dict]]] = {
    "stage2_train_hours": lambda b, ld, pk, fast: suite_stage2_train_hours(b, ld, pk, fast=fast),
    "recency_divisor": lambda b, ld, pk, fast: suite_recency_divisor(b, ld, pk, fast=fast),
    "wfo_min_trades": lambda b, ld, pk, fast: suite_wfo_min_trades(b, ld, pk, fast=fast),
    "holdout_pf": lambda b, ld, pk, fast: suite_holdout_pf(b, ld, pk, fast=fast),
    "bootstrap": lambda b, ld, pk, fast: suite_bootstrap_lookback(b, ld, pk, fast=fast),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="WFO stage-2 parity and knob sweeps.")
    ap.add_argument(
        "--suite",
        choices=list(SUITES.keys()) + ["all"],
        default="all",
        help="Which sweep to run (default: all).",
    )
    ap.add_argument(
        "--load-days",
        type=float,
        default=35.0,
        help="Days of Parquet for wf_diagnostic_* walk-forward only (not WFO bar load). Default 35.",
    )
    ap.add_argument(
        "--pairs",
        type=str,
        default="",
        help="Comma-separated pair_keys (e.g. BTC_USD). Empty = all configured pairs.",
    )
    ap.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Write full result document to this path.",
    )
    ap.add_argument(
        "--synth-if-missing",
        action="store_true",
        help="If Parquet is missing for a pair, write deterministic synthetic bars "
        "(offline only; not market-realistic).",
    )
    ap.add_argument(
        "--fast",
        action="store_true",
        help="Shorthand: coarser WFO step_hours floor + wf_diagnostic load_days=28.",
    )
    args = ap.parse_args()

    bot_cfg = _load_bot_cfg()
    pair_keys_pre = [x.strip() for x in args.pairs.split(",") if x.strip()] or None
    load_days = 28.0 if args.fast else float(args.load_days)
    if args.fast:
        print("[fast] wf_diagnostic load_days=28 + coarser WFO step_hours", flush=True)

    if args.synth_if_missing:
        ensure_synthetic_bars_if_missing(
            bot_cfg,
            days=max(load_days, 35.0),
            pair_keys=pair_keys_pre,
        )
    pair_keys = pair_keys_pre

    meta = {
        "config": str(CONFIG_PATH),
        "venue": bot_cfg.venue,
        "wf_diagnostic_load_days": load_days,
        "fast": bool(args.fast),
        "suite": args.suite,
        "ts": time.time(),
    }

    rows: list[dict] = []
    if args.suite == "all":
        order = [
            "stage2_train_hours",
            "recency_divisor",
            "wfo_min_trades",
            "holdout_pf",
            "bootstrap",
        ]
        for name in order:
            print(f"\n### Running suite: {name}", flush=True)
            part = SUITES[name](bot_cfg, load_days, pair_keys, args.fast)
            rows.extend(part)
    else:
        rows = SUITES[args.suite](bot_cfg, load_days, pair_keys, args.fast)

    doc = {"meta": meta, "rows": rows}

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, default=str)

    # Human-readable summary
    print("\n" + "=" * 72)
    print("SUMMARY (ok / mode / skip_reason)")
    print("=" * 72)
    for r in rows:
        tag = "OK " if r.get("ok") else "NO "
        mode = r.get("mode") or r.get("bootstrap_best_mode") or "-"
        skip = r.get("skip_reason", "")
        wf_p = r.get("wf_diagnostic_total_pnl")
        bs_p = r.get("bootstrap_window_total_pnl")
        pnl_s = ""
        if wf_p is not None:
            pnl_s += f" wf_pnl={wf_p}"
        if bs_p is not None:
            pnl_s += f" boot_pnl={bs_p}"
        if r.get("latest_holdout_total_pnl") is not None:
            pnl_s += f" hold_pnl={r.get('latest_holdout_total_pnl')}"
        keys = " ".join(
            f"{k}={r[k]}"
            for k in sorted(r.keys())
            if k
            in (
                "suite",
                "train_hours",
                "recency_half_life_divisor",
                "min_trades",
                "min_latest_holdout_pf",
                "lookback_hours",
            )
            and r[k] is not None
        )
        extra = f" | {skip}" if skip else ""
        print(f"{tag} {r.get('pair_key','?'):10} {keys:55} mode={mode}{pnl_s}{extra}")

    print("\nDone.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
