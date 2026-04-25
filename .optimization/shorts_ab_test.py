"""Walk-forward A/B test: shorts_enabled=False (baseline) vs shorts_enabled=True (variant).

The current live bot is long-only (shorts_enabled=False in config). The vec backtest
in evaluate_params already computes and passes short_mask to simulators. To faithfully
mirror live behavior, this test:

  Baseline:  Zeroes out short_mask before simulation (long-only, mirrors live)
  Variant:   Full short_mask passed to simulator (long+short, proposed change)

Walk-forward structure: same as tuner_ab_test.py
  - First LOOKBACK_BARS bars = initial mode selection (tuner-based WFO proxy)
  - Every SEGMENT_BARS, evaluate forward performance under each arm
  - Sum realized PnL across all segments

Usage:
    python .optimization/shorts_ab_test.py
"""

from __future__ import annotations

import copy
import sys
from dataclasses import replace
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np

from backend.server.scalp_bot import bar_store
from backend.server.scalp_bot.scalp_vec_backtest import (
    BacktestMetrics,
    ParamSet,
    compute_metrics,
    simulate_trades_bidir,
    simulate_trades_rsi,
    detect_signals_daviddtech,
    detect_signals_ema,
    detect_signals_ema_scalp,
    detect_signals_rsi,
    detect_signals_supertrend,
    detect_signals_squeeze,
    detect_signals_qqe,
    detect_signals_utbot,
    detect_signals_hull,
    detect_signals_sar_chop,
    evaluate_params,
    build_default_grid,
)
from backend.server.scalp_bot.scalp_config import load_scalp_config
from backend.server.scalp_bot.param_tuner import (
    STRATEGY_MODES,
    _params_from_pair_config,
)

import tomllib

CONFIG_PATH = _root / "config.toml"
with open(CONFIG_PATH, "rb") as f:
    _raw = tomllib.load(f)

scalp_cfg = load_scalp_config(_raw)

PAIRS = {
    "BTC_USD": scalp_cfg.pairs["BTC_USD"],
    "SOL_USD": scalp_cfg.pairs["SOL_USD"],
    "XRP_USD": scalp_cfg.pairs["XRP_USD"],
}

LOOKBACK_BARS = 128   # 32h at 15m — initial mode selection window
SEGMENT_BARS  = 96    # 24h forward segments
MIN_FWD_BARS  = 20


def _slice(bars: dict, start: int, end: int) -> dict:
    return {k: v[start:end] for k, v in bars.items()}


def _detect(bars: dict, params: ParamSet):
    """Dispatch signal detection for a mode. Returns (long_mask, short_mask, atr_vals)."""
    close = bars["close"]; high = bars["high"]; low = bars["low"]
    p = params
    m = p.mode

    if m == "daviddtech_scalp":
        return detect_signals_daviddtech(
            close=close, high=high, low=low,
            atr_period=p.atr_period, adx_period=p.adx_period,
            t3_length=p.t3_length, t3_vfactor=p.t3_vfactor,
            hlc_close_period=p.hlc_close_period, hlc_low_period=p.hlc_low_period,
            hlc_high_period=p.hlc_high_period, adx_threshold=p.adx_threshold,
            wae_sensitivity=p.wae_sensitivity, wae_fast_len=p.wae_fast_len,
            wae_slow_len=p.wae_slow_len, wae_bb_len=p.wae_bb_len,
            wae_bb_mult=p.wae_bb_mult,
        )
    elif m == "ema_scalp":
        lm, sm, atr, _, _ = detect_signals_ema_scalp(
            close=close, high=high, low=low,
            ema_period=p.ema_scalp_period, atr_period=p.atr_period,
            sr_bars=p.ema_scalp_sr_bars,
        )
        return lm, sm, atr
    elif m == "rsi_reversion":
        lm, sm, atr, _ = detect_signals_rsi(
            close=close, high=high, low=low,
            rsi_period=p.rsi_period, atr_period=p.atr_period,
            rsi_buy_threshold=p.rsi_buy_threshold,
            rsi_sell_threshold=p.rsi_sell_threshold,
            rsi_short_threshold=p.rsi_short_threshold,
        )
        return lm, sm, atr
    elif m == "supertrend":
        return detect_signals_supertrend(
            close=close, high=high, low=low,
            period=p.supertrend_period, factor=p.supertrend_factor,
            atr_period=p.atr_period,
        )
    elif m == "squeeze_momentum":
        return detect_signals_squeeze(
            close=close, high=high, low=low,
            bb_period=p.squeeze_bb_period, bb_mult=p.squeeze_bb_mult,
            kc_mult=p.squeeze_kc_mult, mom_period=p.squeeze_mom_period,
            atr_period=p.atr_period,
        )
    elif m == "qqe_mod":
        return detect_signals_qqe(
            close=close, high=high, low=low,
            rsi_period=p.qqe_rsi_period, qqe_factor=p.qqe_factor,
            qqe_smoothing=p.qqe_smoothing, atr_period=p.atr_period,
        )
    elif m == "utbot_alert":
        return detect_signals_utbot(
            close=close, high=high, low=low,
            atr_period=p.utbot_atr_period, atr_mult=p.utbot_atr_mult,
        )
    elif m == "hull_suite":
        return detect_signals_hull(
            close=close, high=high, low=low,
            hull_period=p.hull_period, atr_period=p.atr_period,
        )
    elif m == "sar_chop":
        return detect_signals_sar_chop(
            close=close, high=high, low=low,
            sar_start=p.sar_start, sar_increment=p.sar_increment, sar_max=p.sar_max,
            ma_long_period=p.sar_chop_ma_long_period,
            ma_short_period=p.sar_chop_ma_short_period,
            chop_period=p.sar_chop_chop_period,
            chop_threshold=p.sar_chop_chop_threshold,
            macd_fast=p.sar_chop_macd_fast,
            macd_slow=p.sar_chop_macd_slow,
            macd_signal=p.sar_chop_macd_signal,
            use_lucid_sar=p.sar_chop_use_lucid,
            use_utbot_trail=p.sar_chop_use_utbot_trail,
            utbot_atr_period=p.sar_chop_utbot_atr_period,
            utbot_atr_mult=p.sar_chop_utbot_mult,
            atr_period=p.atr_period,
        )
    else:  # ema_momentum
        lm, sm, atr = detect_signals_ema(
            close=close, high=high, low=low,
            volume=bars["volume"], timestamp=bars["timestamp"],
            ema_fast_period=p.ema_fast, ema_slow_period=p.ema_slow,
            atr_period=p.atr_period, vol_ma_period=p.vol_ma_period,
            vol_mult=p.vol_mult, min_signals=p.min_signals,
        )
        return lm, sm, atr


def _evaluate_with_shorts(
    bars: dict,
    params: ParamSet,
    allow_shorts: bool,
) -> BacktestMetrics:
    """Like evaluate_params, but with explicit short suppression for baseline."""
    close = bars["close"]; high = bars["high"]; low = bars["low"]
    open_prices = bars.get("open")
    fee = params.fee_pct
    slip = params.slippage_pct
    fm = params.fill_model
    cs = float(getattr(params, "contract_size", 1.0) or 1.0)
    fee_u = float(getattr(params, "fee_usd_per_contract_per_leg", 0.0) or 0.0)

    if params.mode == "rsi_reversion":
        # rsi uses its own simulator
        from backend.server.scalp_bot.scalp_vec_backtest import detect_signals_rsi, simulate_trades_rsi
        lm, sm, atr, rsi_vals = detect_signals_rsi(
            close=close, high=high, low=low,
            rsi_period=params.rsi_period, atr_period=params.atr_period,
            rsi_buy_threshold=params.rsi_buy_threshold,
            rsi_sell_threshold=params.rsi_sell_threshold,
            rsi_short_threshold=params.rsi_short_threshold,
        )
        if not allow_shorts:
            sm = np.zeros_like(sm)
        trades = simulate_trades_rsi(
            close=close, high=high, low=low,
            long_mask=lm, short_mask=sm,
            atr_vals=atr, rsi_vals=rsi_vals,
            rsi_sell_threshold=params.rsi_sell_threshold,
            rsi_short_cover_threshold=params.rsi_buy_threshold,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee,
            contract_size=cs,
            fee_usd_per_contract_per_leg=fee_u,
            slippage_pct=slip,
            fill_model=fm,
        )
    else:
        lm, sm, atr = _detect(bars, params)
        if not allow_shorts:
            sm = np.zeros_like(sm)
        trades = simulate_trades_bidir(
            close=close, high=high, low=low,
            long_mask=lm, short_mask=sm, atr_vals=atr,
            open_prices=open_prices,
            atr_stop_mult=params.atr_stop_mult,
            atr_tp_mult=params.atr_tp_mult,
            max_hold_bars=params.max_hold_bars,
            fee_pct=fee,
            contract_size=cs,
            fee_usd_per_contract_per_leg=fee_u,
            slippage_pct=slip,
            fill_model=fm,
        )

    return compute_metrics(trades, close, contract_size=cs)


def _pick_initial_mode(pair_key: str, pair_cfg, bot_cfg, bars: dict) -> str:
    """Pick initial active mode from the first lookback window (WFO proxy)."""
    best_mode = pair_cfg.strategy_mode
    best_score = -float("inf")
    n = len(bars["close"])
    half_life = max(10.0, n / 3.0)
    for mode in STRATEGY_MODES:
        params = _params_from_pair_config(pair_cfg, bot_cfg, mode)
        m = evaluate_params(bars, params, recency_half_life_bars=half_life)
        score = float(m.expectancy) if m.trade_count >= 5 else float(m.total_pnl)
        if score > best_score:
            best_score = score
            best_mode = mode
    return best_mode


def walk_forward(
    pair_key: str,
    pair_cfg,
    bot_cfg,
    bars: dict,
) -> dict:
    """Two-arm walk-forward: baseline (long-only) vs variant (long+short)."""
    n = len(bars["timestamp"])

    # Pick initial mode (same for both arms)
    init_bars = _slice(bars, 0, LOOKBACK_BARS)
    champion_mode = _pick_initial_mode(pair_key, pair_cfg, bot_cfg, init_bars)
    print(f"  Champion mode: {champion_mode}")

    results = {}

    for arm, allow_shorts in [("baseline (long-only)", False), ("variant (long+short)", True)]:
        pc = copy.deepcopy(pair_cfg)
        params = _params_from_pair_config(pc, bot_cfg, champion_mode)

        total_pnl = 0.0
        total_trades = 0
        short_trades = 0
        long_trades = 0
        segments = 0
        details = []

        cursor = LOOKBACK_BARS
        while cursor < n:
            fwd_end = min(cursor + SEGMENT_BARS, n)
            if fwd_end - cursor < MIN_FWD_BARS:
                break

            fwd_bars = _slice(bars, cursor, fwd_end)
            m = _evaluate_with_shorts(fwd_bars, params, allow_shorts=allow_shorts)

            # Count long vs short trades separately
            seg_longs = seg_shorts = 0
            if allow_shorts:
                close = fwd_bars["close"]; high = fwd_bars["high"]; low = fwd_bars["low"]
                lm, sm, _ = _detect(fwd_bars, params) if params.mode != "rsi_reversion" else (
                    detect_signals_rsi(
                        close=close, high=high, low=low,
                        rsi_period=params.rsi_period, atr_period=params.atr_period,
                        rsi_buy_threshold=params.rsi_buy_threshold,
                        rsi_sell_threshold=params.rsi_sell_threshold,
                        rsi_short_threshold=params.rsi_short_threshold,
                    )[:2] + (None,)
                )
                seg_longs = int(lm.sum())
                seg_shorts = int(sm.sum())

            total_pnl += float(m.total_pnl)
            total_trades += int(m.trade_count)
            long_trades += seg_longs
            short_trades += seg_shorts
            segments += 1
            details.append({
                "start": cursor, "end": fwd_end,
                "pnl": round(float(m.total_pnl), 6),
                "trades": int(m.trade_count),
                "pf": round(float(m.profit_factor), 3) if m.profit_factor != float("inf") else 999.0,
            })
            cursor = fwd_end

        results[arm] = {
            "pnl": round(total_pnl, 6),
            "trades": total_trades,
            "long_signals": long_trades,
            "short_signals": short_trades,
            "segments": segments,
            "details": details,
        }

    return results, champion_mode


def main():
    print("=" * 80)
    print("SHORTS A/B TEST — Long-only vs Long+Short")
    print("=" * 80)
    print(f"Lookback:   {LOOKBACK_BARS} bars ({LOOKBACK_BARS * 15 / 60:.0f}h at 15m)")
    print(f"Segments:   {SEGMENT_BARS} bars ({SEGMENT_BARS * 15 / 60:.0f}h forward each)")
    print(f"Fee:        {scalp_cfg.fee_bps_per_leg} bps/leg | Fill: {scalp_cfg.backtest_fill_model}")
    print()

    all_results = {}

    for pair_key, pair_cfg in PAIRS.items():
        bars = bar_store.load_bars(pair_cfg.symbol, pair_cfg.interval, last_n_days=90)
        if bars is None:
            print(f"[{pair_key}] No bars — skipping")
            continue
        n = len(bars["timestamp"])
        span_d = (float(bars["timestamp"][-1]) - float(bars["timestamp"][0])) / 86400
        print(f"[{pair_key}] {pair_cfg.symbol} @{pair_cfg.interval}m: {n} bars, {span_d:.1f}d")

        results, champ = walk_forward(pair_key, pair_cfg, scalp_cfg, bars)
        all_results[pair_key] = (results, champ)

        print(f"\n  {'Arm':<25} {'PnL':>12} {'Trades':>8} {'D vs Base':>12}")
        print(f"  {'-'*25} {'-'*12} {'-'*8} {'-'*12}")
        base_pnl = results["baseline (long-only)"]["pnl"]
        for arm, r in results.items():
            delta = r["pnl"] - base_pnl if arm != "baseline (long-only)" else 0.0
            d_str = f"{delta:>+12.4f}" if arm != "baseline (long-only)" else f"{'--':>12}"
            print(f"  {arm:<25} {r['pnl']:>12.4f} {r['trades']:>8} {d_str}")

        # Segment detail (variant only — shows where shorts added/detracted)
        vr = results["variant (long+short)"]
        br = results["baseline (long-only)"]
        print(f"\n  Segment comparison (baseline vs variant):")
        print(f"  {'Bars':<14} {'Base PnL':>10} {'Var PnL':>10} {'D':>10} {'Trades(b)':>10} {'Trades(v)':>10}")
        print(f"  {'-'*14} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
        for b_seg, v_seg in zip(br["details"], vr["details"]):
            d = v_seg["pnl"] - b_seg["pnl"]
            print(f"  {b_seg['start']:>5}-{b_seg['end']:>5}    {b_seg['pnl']:>10.4f} {v_seg['pnl']:>10.4f} {d:>+10.4f} {b_seg['trades']:>10} {v_seg['trades']:>10}")
        print()

    # Grand summary
    print("=" * 80)
    print("GRAND SUMMARY")
    print("=" * 80)
    print(f"\n{'Pair':<10} {'Champion':<22} {'Arm':<25} {'PnL':>12} {'Trades':>8} {'D vs Base':>12}")
    print(f"{'-'*10} {'-'*22} {'-'*25} {'-'*12} {'-'*8} {'-'*12}")

    total_base = 0.0
    total_var = 0.0
    total_base_t = 0
    total_var_t = 0

    for pair_key, (results, champ) in all_results.items():
        base = results["baseline (long-only)"]
        var = results["variant (long+short)"]
        delta = var["pnl"] - base["pnl"]
        total_base += base["pnl"]
        total_var += var["pnl"]
        total_base_t += base["trades"]
        total_var_t += var["trades"]
        print(f"{pair_key:<10} {champ:<22} {'baseline (long-only)':<25} {base['pnl']:>12.4f} {base['trades']:>8} {'--':>12}")
        print(f"{pair_key:<10} {champ:<22} {'variant (long+short)':<25} {var['pnl']:>12.4f} {var['trades']:>8} {delta:>+12.4f}")
        print()

    total_delta = total_var - total_base
    pct = (total_delta / abs(total_base) * 100) if total_base != 0 else float("inf")

    print(f"{'TOTAL':<10} {'':22} {'baseline (long-only)':<25} {total_base:>12.4f} {total_base_t:>8} {'--':>12}")
    print(f"{'TOTAL':<10} {'':22} {'variant (long+short)':<25} {total_var:>12.4f} {total_var_t:>8} {total_delta:>+12.4f}")

    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)
    if abs(total_delta) < 0.001 and abs(pct) < 1.0:
        verdict = "NEUTRAL vs deployed"
    elif total_delta > 0:
        verdict = "NET POSITIVE vs deployed"
    else:
        verdict = "NET NEGATIVE vs deployed"
    print(f"  shorts_enabled=True: {verdict} (D={total_delta:+.4f}, {pct:+.1f}%)")
    print()
    print("  Limits:")
    print("  - Deterministic backtest on 30d of 15m bars; no live market confound.")
    print("  - Fee=0 bps (INTX promo). Real short funding cost not modeled.")
    print("  - Short execution (margin, fill quality) may differ from backtest.")
    print("  - Champion mode held fixed per pair; WFO may pick different modes with shorts live.")


if __name__ == "__main__":
    main()
