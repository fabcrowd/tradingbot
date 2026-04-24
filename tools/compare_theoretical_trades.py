"""Compare theoretical (vector backtest) trades on the same bar slice as ``run_tuner_cycle``.

Run from repo root:
  python tools/compare_theoretical_trades.py --pair-key SOL_USD
  python tools/compare_theoretical_trades.py --pair-key SOL_USD --scenario both --recency-weighted

**Scenario A (deployed / “real world” for WFO):** If ``data/scalp_champion.json`` has this
symbol, use that ParamSet; else pair ``strategy_mode`` + current TOML params.

**Scenario B — two interpretations:**

* ``same_mode`` — **Fine-tune only:** same mode as A, with the tuner's ``params_changed``
  for that mode (what you'd get if the tuner kept applying nudges while WFO still owns mode).
* ``tuner_best`` — **Structural proposed:** ``param_tuner_allow_mode_override_champion``
  style path — tuner's grid ``best_mode`` + that mode's tuned params.

``--scenario both`` prints both B variants so you can separate "nudge PnL" from "mode switch PnL".

**Note:** The tuner only evaluates ``param_tuner.STRATEGY_MODES``. If A's mode is outside
that set, ``same_mode`` B may equal A (no ``params_changed`` bucket).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "backend" / "server"
sys.path.insert(0, str(SERVER))

import tomllib  # py3.11+

from scalp_bot import bar_store
from scalp_bot.param_tuner import (
    param_set_for_tuned_mode,
    run_tuner_cycle,
    _params_from_pair_config,
)
from scalp_bot.scalp_config import load_scalp_config
from scalp_bot.scalp_vec_backtest import (
    BacktestMetrics,
    ParamSet,
    TradeResult,
    evaluate_params,
    min_entry_bar_for_last_hours,
)
from scalp_bot.scalp_mode_resolution import resolve_auto_mode
from scalp_bot.scalp_wfo import load_champion_for_symbol, param_set_from_champion_row


def _trade_fingerprint(t: TradeResult) -> tuple[int, int, float]:
    return (int(t.entry_bar), int(t.exit_bar), round(float(t.pnl), 8))


def _summarize_diff(a: list[TradeResult], b: list[TradeResult]) -> dict:
    fa = {_trade_fingerprint(t) for t in a}
    fb = {_trade_fingerprint(t) for t in b}
    return {
        "only_a": len(fa - fb),
        "only_b": len(fb - fa),
        "both": len(fa & fb),
    }


def _active_mode_from_pair(pair_cfg, bot_cfg, champion_row: dict | None) -> str:
    """Match live semantics: auto → champion mode if row exists, else ``auto_mode_fallback``."""
    return resolve_auto_mode(
        str(getattr(pair_cfg, "strategy_mode", "auto") or "auto"),
        champion_row=champion_row if isinstance(champion_row, dict) else None,
        auto_mode_fallback=(
            getattr(pair_cfg, "auto_mode_fallback", None)
            or getattr(bot_cfg, "auto_mode_fallback", "ema_momentum")
        ),
    )


def _eval_pair(
    bars: dict,
    ps_a: ParamSet,
    ps_b: ParamSet,
    *,
    recency: float,
    min_entry: int,
) -> tuple[BacktestMetrics, BacktestMetrics, dict]:
    m_a = evaluate_params(bars, ps_a, recency_half_life_bars=recency, min_entry_bar=min_entry)
    m_b = evaluate_params(bars, ps_b, recency_half_life_bars=recency, min_entry_bar=min_entry)
    diff = _summarize_diff(list(m_a.trades), list(m_b.trades))
    return m_a, m_b, diff


def _print_block(
    *,
    title: str,
    label_a: str,
    label_b: str,
    ps_a: ParamSet,
    ps_b: ParamSet,
    m_a: BacktestMetrics,
    m_b: BacktestMetrics,
    diff: dict,
) -> None:
    print(title)
    print(f"  A ({label_a}): mode={ps_a.mode}  trades={m_a.trade_count} "
          f"win_rate={m_a.win_rate:.2%} total_pnl={m_a.total_pnl:.6f} expectancy={m_a.expectancy:.6f}")
    print(f"  B ({label_b}): mode={ps_b.mode}  trades={m_b.trade_count} "
          f"win_rate={m_b.win_rate:.2%} total_pnl={m_b.total_pnl:.6f} expectancy={m_b.expectancy:.6f}")
    print(
        f"  Trade diff (entry_bar, exit_bar, pnl): "
        f"only_A={diff['only_a']} only_B={diff['only_b']} both={diff['both']}",
    )
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config.toml",
        help="Path to config.toml",
    )
    ap.add_argument(
        "--pair-key",
        required=True,
        help="Scalp pair key as in [scalp].pairs (e.g. SOL_USD)",
    )
    ap.add_argument(
        "--lookback-hours",
        type=float,
        default=None,
        help="Hours of history (default: wfo_train_hours + wfo_holdout_hours — same window as run_tuner_cycle in scalp_runtime)",
    )
    ap.add_argument(
        "--window-trades-only",
        action="store_true",
        help="Count only trades whose entry bar falls inside the lookback window "
        "(indicators still use full loaded series in evaluate_params)",
    )
    ap.add_argument(
        "--recency-weighted",
        action="store_true",
        help="Use recency half-life n_bars/3 like the tuner (default: flat metrics)",
    )
    ap.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write full comparison + trade lists as JSON",
    )
    ap.add_argument(
        "--scenario",
        choices=("tuner_best", "same_mode", "both"),
        default="both",
        help="tuner_best = grid best_mode (mode-override proposal); "
        "same_mode = tuner nudges on A's mode only; both = print both (default)",
    )
    args = ap.parse_args()

    raw_cfg = tomllib.loads(args.config.read_text(encoding="utf-8"))
    bot_cfg = load_scalp_config(raw_cfg)
    bar_store.set_bar_store_venue(bot_cfg.venue)
    pairs = bot_cfg.pairs
    if args.pair_key not in pairs:
        keys = ", ".join(sorted(pairs.keys()))
        print(f"Unknown pair-key {args.pair_key!r}. Known: {keys}", file=sys.stderr)
        sys.exit(1)
    pair_cfg = pairs[args.pair_key]
    look_h = (
        float(args.lookback_hours)
        if args.lookback_hours is not None
        else float(bot_cfg.wfo_train_hours) + float(bot_cfg.wfo_holdout_hours)
    )

    load_days = look_h / 24.0 + 0.5
    bars_full = bar_store.load_bars(pair_cfg.symbol, pair_cfg.interval, last_n_days=load_days)
    if bars_full is None or len(bars_full.get("timestamp", [])) < 50:
        print("No bars or <50 bars in store for this pair/interval.", file=sys.stderr)
        sys.exit(2)

    ts = bars_full["timestamp"]
    cutoff = float(ts[-1]) - look_h * 3600.0
    mask = ts >= cutoff
    if int(mask.sum()) < 50:
        print("Lookback slice has <50 bars.", file=sys.stderr)
        sys.exit(2)
    bars = {k: v[mask] for k, v in bars_full.items()}
    n_bars = len(bars["close"])
    half_life = max(10.0, n_bars / 3.0)
    recency = float(half_life) if args.recency_weighted else 0.0

    min_entry = 0
    if args.window_trades_only:
        min_entry = min_entry_bar_for_last_hours(bars, look_h)

    champ = load_champion_for_symbol(pair_cfg.symbol)
    label_a = "pair_config"
    ps_a = None
    if champ is not None:
        ps_a = param_set_from_champion_row(champ, pair_cfg, bot_cfg)
        if ps_a is not None:
            label_a = "wfo_champion"
    if ps_a is None:
        mode_a = _active_mode_from_pair(pair_cfg, bot_cfg, champ)
        ps_a = _params_from_pair_config(pair_cfg, bot_cfg, mode_a)
        label_a = "pair_config"

    tuner = run_tuner_cycle(args.pair_key, pair_cfg, bot_cfg, look_h)
    if tuner is None:
        print("Tuner cycle returned None (insufficient data).", file=sys.stderr)
        sys.exit(3)

    best = str(tuner.best_mode or "").strip()
    if not best:
        print("Tuner returned empty best_mode.", file=sys.stderr)
        sys.exit(3)

    mode_a = str(ps_a.mode).strip()
    ps_b_same = param_set_for_tuned_mode(pair_cfg, bot_cfg, mode_a, tuner.all_modes)
    ps_b_best = param_set_for_tuned_mode(pair_cfg, bot_cfg, best, tuner.all_modes)

    same_note = ""
    if mode_a not in tuner.all_modes:
        same_note = f" (warning: mode {mode_a!r} not in tuner.all_modes — B may equal A)"

    print(f"pair={args.pair_key} symbol={pair_cfg.symbol} interval={pair_cfg.interval}m")
    print(f"lookback_hours={look_h} bars_in_slice={n_bars}")
    print(f"recency_half_life_bars={recency} min_entry_bar={min_entry}")
    print(f"tuner.best_mode={best!r}  deployed.mode={mode_a!r}  label_A={label_a!r}")
    if champ is None:
        print("note: no champion row for symbol in data/scalp_champion.json — A uses pair TOML (not WFO snapshot).")
    print()

    json_payload: dict = {
        "pair_key": args.pair_key,
        "symbol": pair_cfg.symbol,
        "interval": pair_cfg.interval,
        "lookback_hours": look_h,
        "bars_in_slice": n_bars,
        "recency_half_life_bars": recency,
        "min_entry_bar": min_entry,
        "tuner_best_mode": best,
        "deployed": {
            "label": label_a,
            "param_set": asdict(ps_a),
        },
    }

    if args.scenario in ("same_mode", "both"):
        m_a1, m_b1, d1 = _eval_pair(bars, ps_a, ps_b_same, recency=recency, min_entry=min_entry)
        changed = (tuner.all_modes.get(mode_a) or {}).get("params_changed") or {}
        _print_block(
            title=f"--- Fine-tune (same mode as deployed){same_note} ---",
            label_a=label_a,
            label_b="tuner_nudges_same_mode",
            ps_a=ps_a,
            ps_b=ps_b_same,
            m_a=m_a1,
            m_b=m_b1,
            diff=d1,
        )
        if args.json_out is not None:
            da = asdict(m_a1)
            da.pop("trades", None)
            db = asdict(m_b1)
            db.pop("trades", None)
            json_payload["fine_tune_same_mode"] = {
                "params_changed_for_mode": changed,
                "b_param_set": asdict(ps_b_same),
                "metrics_a": da,
                "metrics_b": db,
                "trades_a": [asdict(t) for t in m_a1.trades],
                "trades_b": [asdict(t) for t in m_b1.trades],
                "fingerprint_diff": d1,
            }

    if args.scenario in ("tuner_best", "both"):
        m_a2, m_b2, d2 = _eval_pair(bars, ps_a, ps_b_best, recency=recency, min_entry=min_entry)
        _print_block(
            title="--- Structural proposed (tuner grid best_mode + tuned params) ---",
            label_a=label_a,
            label_b="tuner_best_mode",
            ps_a=ps_a,
            ps_b=ps_b_best,
            m_a=m_a2,
            m_b=m_b2,
            diff=d2,
        )
        if args.json_out is not None:
            da = asdict(m_a2)
            da.pop("trades", None)
            db = asdict(m_b2)
            db.pop("trades", None)
            json_payload["structural_tuner_best"] = {
                "b_param_set": asdict(ps_b_best),
                "metrics_a": da,
                "metrics_b": db,
                "trades_a": [asdict(t) for t in m_a2.trades],
                "trades_b": [asdict(t) for t in m_b2.trades],
                "fingerprint_diff": d2,
            }

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(json_payload, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {args.json_out}")


if __name__ == "__main__":
    main()
