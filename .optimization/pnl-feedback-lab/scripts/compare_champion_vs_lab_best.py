#!/usr/bin/env python3
"""Equal-window backtest: saved WFO champion vs lab-style 'best' strategy on 15m (or pair interval).

Uses the **same** OHLCV arrays, fill model, fee/slip, and ``bars_per_year`` for both runs.
Default lookback matches ``scalp_wfo.optimize_pair`` bar load: train+holdout+step*3 hours (+0.5d margin).

Example (repo root):

  python .optimization/pnl-feedback-lab/scripts/compare_champion_vs_lab_best.py
  python .../compare_champion_vs_lab_best.py --lookback full --lab-strategy ema_momentum
"""

from __future__ import annotations

import argparse
import json
import math
import sys
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
from scalp_bot.scalp_wfo import CHAMPION_PATH, WFOConfig, load_champion_for_symbol
from scalp_bot.scalp_wfo import _params_from_config


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


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _pnl_usd(total_pnl_backtest: float, contract_size: float) -> float:
    return float(total_pnl_backtest)


def _fmt_usd(x: float) -> str:
    ax = abs(x)
    if ax >= 1_000_000:
        return f"${x:,.0f}"
    if ax >= 1000:
        return f"${x:,.2f}"
    return f"${x:,.4f}"


def _segment_slice(bars: dict[str, np.ndarray], segment: str) -> dict[str, np.ndarray]:
    """Same bar-index thirds as ``run_multiwindow_lab._window_thirds`` (needs n >= 90)."""
    n = len(bars["close"])
    if n < 90:
        return bars
    t = n // 3
    ranges = {"early": (0, t), "mid": (t, 2 * t), "late": (2 * t, n)}
    a, b = ranges[segment]
    return {k: np.asarray(v)[a:b].copy() for k, v in bars.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare champion vs lab-best on identical bars.")
    ap.add_argument("--config", type=str, default=None, help="config.toml path (default: repo root)")
    ap.add_argument("--pair-key", type=str, default="BTC_USD", help="[scalp.pairs.*] key")
    ap.add_argument(
        "--champion-path",
        type=str,
        default=str(CHAMPION_PATH),
        help="scalp_champion.json path",
    )
    ap.add_argument(
        "--lab-strategy",
        type=str,
        default="ema_momentum",
        help="Strategy label to compare (lab headline default for BTC 15m)",
    )
    ap.add_argument(
        "--lookback",
        type=str,
        default="wfo",
        help='Bar window: "wfo" (same load_days as optimize_pair), "full" (entire parquet), or "days:FLOAT"',
    )
    ap.add_argument(
        "--recency-half-life-bars",
        type=float,
        default=0.0,
        help="0 = same as multiwindow lab; >0 matches WFO inner-window weighting (e.g. n_bars/3)",
    )
    ap.add_argument(
        "--segment",
        type=str,
        default=None,
        choices=("early", "mid", "late"),
        help="After loading bars, keep only this bar-index third (same geometry as pnl lab). Ignored if n<90.",
    )
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve() if args.config else _REPO / "config.toml"
    if not cfg_path.exists():
        print("config not found:", cfg_path, file=sys.stderr)
        return 2

    with cfg_path.open("rb") as f:
        raw = tomllib.load(f)
    bot = load_scalp_config(raw)
    pk = args.pair_key
    if pk not in bot.pairs:
        print("pair_key not in [scalp.pairs]:", pk, file=sys.stderr)
        return 2
    pc = bot.pairs[pk]

    venue = (bot.venue or "coinbase_perps").strip().lower()
    bar_store.set_bar_store_venue("coinbase_perps")

    interval = int(pc.interval)
    symbol = pc.symbol

    if args.lookback == "full":
        last_n_days: float | None = None
        lookback_label = "full Parquet"
    elif args.lookback == "wfo":
        scalp_raw = raw.get("scalp", {})
        train_h = float(scalp_raw.get("wfo_train_hours", 24.0))
        hold_h = float(scalp_raw.get("wfo_holdout_hours", 8.0))
        step_h = float(scalp_raw.get("wfo_step_hours", 4.0))
        last_n_days = _wfo_load_days(train_h, hold_h, step_h)
        lookback_label = f"wfo load_days={last_n_days:.3f} (train={train_h}h holdout={hold_h}h step={step_h}h)"
    elif args.lookback.startswith("days:"):
        last_n_days = float(args.lookback.split(":", 1)[1])
        lookback_label = f"last_n_days={last_n_days}"
    else:
        print('--lookback must be "wfo", "full", or "days:FLOAT"', file=sys.stderr)
        return 2

    bars = bar_store.load_bars(symbol, interval, last_n_days=last_n_days)
    if bars is None or len(bars["close"]) < 40:
        print("insufficient bars:", symbol, interval, file=sys.stderr)
        return 2

    segment_note = ""
    if args.segment:
        n_before = len(bars["close"])
        bars = _segment_slice(bars, args.segment)
        segment_note = f"  segment=`{args.segment}` (bar-index third of loaded series; n {n_before}->{len(bars['close'])})"
        if len(bars["close"]) < 40:
            print("segment too short for evaluation (need >=40 bars)", file=sys.stderr)
            return 2

    champ_path = Path(args.champion_path)
    champ_row = load_champion_for_symbol(symbol, path=champ_path)
    if champ_row is None:
        print("no champion row for symbol", symbol, "in", champ_path, file=sys.stderr)
        return 2
    params_blob = champ_row.get("params")
    if not isinstance(params_blob, dict):
        print("champion row missing params dict", file=sys.stderr)
        return 2

    fill_model = getattr(bot, "backtest_fill_model", "next_open") or "next_open"
    fee = effective_scalp_fee_bps_per_leg(bot) / 10_000.0
    slip = bot.slippage_bps / 10_000.0
    cs = float(getattr(pc, "contract_size", 1.0) or 1.0)

    champion_ps = _param_set_from_champion_blob(params_blob, fill_model=fill_model)
    champion_ps = replace(
        champion_ps,
        fee_pct=fee,
        slippage_pct=slip,
        fill_model=fill_model,
        contract_size=cs,
        fee_usd_per_contract_per_leg=float(
            getattr(bot, "fee_usd_per_contract_per_leg", 0.0) or 0.0
        ),
    )

    base = _params_from_config(pc, bot)
    lab_ps = replace(base, mode=str(args.lab_strategy), slippage_pct=slip, fill_model=fill_model)

    bpy = _bars_per_year(interval)
    hl = float(args.recency_half_life_bars)
    if hl < 0:
        hl = 0.0

    m_ch = evaluate_params(bars, champion_ps, recency_half_life_bars=hl, bars_per_year=bpy)
    m_lb = evaluate_params(bars, lab_ps, recency_half_life_bars=hl, bars_per_year=bpy)

    def score_exp(m) -> float:
        return float(m.expectancy) * math.sqrt(max(1, int(m.trade_count)))

    ts0 = int(bars["timestamp"][0])
    ts1 = int(bars["timestamp"][-1])
    n = len(bars["close"])

    print("## Equal-window comparison")
    print()
    print(f"- **Pair:** `{pk}`  symbol `{symbol}`  interval **{interval}m**")
    print(f"- **Lookback:** {lookback_label}{segment_note}")
    print(f"- **Bars:** {n}  from `{_fmt_ts(ts0)}` -> `{_fmt_ts(ts1)}`")
    print(f"- **Fill / costs:** `{fill_model}`  fee_leg={fee:.6f}  slip={slip:.6f}")
    print(f"- **recency_half_life_bars:** {hl}")
    print(
        f"- **USD PnL:** `total_pnl` is already per 1 contract in USD "
        f"(underlying multiplier **{cs}** + fees from config); one contract per trade."
    )
    print()
    print(
        "| Side | mode | trades | win_% | pnl_USD | pnl_pts | expectancy | PF | Sharpe | score_exp_sqrt_n |"
    )
    print(
        "|------|------|--------|-------|---------|---------|------------|----|--------|------------------|"
    )
    pf_c = m_ch.profit_factor
    pf_c_s = "inf" if pf_c == float("inf") else f"{pf_c:.3f}"
    pf_l = m_lb.profit_factor
    pf_l_s = "inf" if pf_l == float("inf") else f"{pf_l:.3f}"
    print(
        f"| **Champion** | `{champion_ps.mode}` | {m_ch.trade_count} | {100 * m_ch.win_rate:.1f}% | "
        f"{_fmt_usd(_pnl_usd(m_ch.total_pnl, cs))} | {m_ch.total_pnl:.4f} | {m_ch.expectancy:.6f} | "
        f"{pf_c_s} | {m_ch.sharpe:.3f} | {score_exp(m_ch):.4f} |"
    )
    print(
        f"| **Lab preset** | `{lab_ps.mode}` | {m_lb.trade_count} | {100 * m_lb.win_rate:.1f}% | "
        f"{_fmt_usd(_pnl_usd(m_lb.total_pnl, cs))} | {m_lb.total_pnl:.4f} | {m_lb.expectancy:.6f} | "
        f"{pf_l_s} | {m_lb.sharpe:.3f} | {score_exp(m_lb):.4f} |"
    )
    print()
    print("Champion JSON snippet (mode + key knobs):")
    print(json.dumps({"mode": champion_ps.mode, "max_hold_bars": champion_ps.max_hold_bars, "macd_fast_len": champion_ps.macd_fast_len, "macd_slow_len": champion_ps.macd_slow_len, "atr_stop_mult": champion_ps.atr_stop_mult, "atr_tp_mult": champion_ps.atr_tp_mult}, indent=2))
    print()
    print("Lab preset key knobs (from config for this pair):")
    print(json.dumps({"mode": lab_ps.mode, "ema_fast": lab_ps.ema_fast, "ema_slow": lab_ps.ema_slow, "max_hold_bars": lab_ps.max_hold_bars, "atr_stop_mult": lab_ps.atr_stop_mult, "atr_tp_mult": lab_ps.atr_tp_mult}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
