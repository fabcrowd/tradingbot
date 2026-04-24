"""Compare 5m vs 15m vector backtest win rates (same params, same lookback hours).

Run from repo root:
  python tools/compare_scalp_intervals.py

Requires data/coinbase_bars Parquet for configured product ids (REST backfill or live).
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "backend" / "server"
sys.path.insert(0, str(SERVER))

import tomllib  # py3.11+

from scalp_bot import bar_store
from scalp_bot.scalp_config import load_scalp_config
from scalp_bot.scalp_vec_backtest import evaluate_params
from scalp_bot.scalp_wfo import _params_from_config
from scalp_bot.strategy_lookback import STRATEGY_MODES, _slice_bars_to_hours


def main() -> None:
    raw = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    bot_cfg = load_scalp_config(raw)
    look_h = float(bot_cfg.wfo_train_hours) + float(bot_cfg.wfo_holdout_hours)
    load_days = look_h / 24.0 + 0.25

    print(f"lookback_hours={look_h} (train+holdout) fee_bps_per_leg={bot_cfg.fee_bps_per_leg}")
    print()

    for pk, pc in bot_cfg.pairs.items():
        sym = pc.symbol
        base = _params_from_config(pc, bot_cfg)

        print(f"=== {pk} ({sym}) ===")
        for interval in (5, 15):
            bars = bar_store.load_bars(sym, interval, last_n_days=load_days)
            if bars is None or len(bars.get("timestamp", [])) < 30:
                print(f"  {interval}m: no data or <30 bars")
                continue
            sl = _slice_bars_to_hours(bars, look_h)
            n = len(sl["close"])
            hl = max(10.0, n / 3.0)
            best_w = -1.0
            best_f = -1.0
            best_mode_w = ""
            rows = []
            for mode in STRATEGY_MODES:
                p = replace(base, mode=mode)
                m_flat = evaluate_params(sl, p, recency_half_life_bars=0.0)
                m_w = evaluate_params(sl, p, recency_half_life_bars=hl)
                wf = float(m_flat.win_rate)
                ww = float(m_w.win_rate)
                rows.append((mode, wf, ww, int(m_flat.trade_count), float(m_w.total_pnl)))
                if ww > best_w:
                    best_w = ww
                    best_mode_w = mode
                if wf > best_f:
                    best_f = wf
            print(
                f"  {interval}m: bars={n}  "
                f"BEST(recency)={best_mode_w} wr_w={best_w:.1%}  best_flat={best_f:.1%}",
            )
            for mode, wf, ww, tc, pnl in rows:
                print(
                    f"    {mode:14} flat={wf:6.1%}  wtd={ww:6.1%}  "
                    f"trades={tc:4}  pnl(wtd)={pnl:+.6f}",
                )
        print(
            "  -> verdict: compare BEST(recency) wr_w above; "
            "higher interval wins if its best_w beats the other.",
        )
        print()


if __name__ == "__main__":
    main()
