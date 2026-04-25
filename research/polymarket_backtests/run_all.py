"""Download data (if missing), run all proxy backtests, write results JSON."""
from __future__ import annotations

import json
import time
from pathlib import Path

from fetch_binance_klines import download_range, klines_to_ohlc
from model_cross_asset import aligned_series, sweep_horizons
from model_dump_hedge import toy_dump_frequency
from model_oracle_lag import (
    random_entry_baseline,
    run_oracle_lag,
    run_oracle_lag_window_slice,
    sweep as sweep_oracle,
)
from synthetic_paths import write_synthetic

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_JSON = ROOT / "results.json"


def ensure_klines(symbol: str, days: int) -> Path:
    path = DATA_DIR / f"{symbol.lower()}_{days}d_1m.json"
    if path.exists() and path.stat().st_size > 1000:
        return path
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    download_range(symbol, "1m", start_ms, end_ms, path)
    return path


def main() -> None:
    days = 30
    data_source = "binance"
    btc_path = DATA_DIR / f"btcusdt_{days}d_1m.json"
    eth_path = DATA_DIR / f"ethusdt_{days}d_1m.json"
    try:
        need = (not btc_path.exists() or btc_path.stat().st_size < 1000) or (
            not eth_path.exists() or eth_path.stat().st_size < 1000
        )
        if need:
            ensure_klines("BTCUSDT", days)
            ensure_klines("ETHUSDT", days)
    except Exception as e:
        data_source = f"synthetic_fallback ({type(e).__name__}: {e})"
        write_synthetic(btc_path, eth_path, days=days, seed=11)

    ts_b, btc = klines_to_ohlc(btc_path)
    ts_e, eth = klines_to_ohlc(eth_path)
    btc_a, eth_a = aligned_series(ts_b, btc, ts_e, eth)

    bar_sec = 60
    oracle_rows = sweep_oracle(btc_a, bar_sec)
    # ETH-only oracle lag (second leg of user's pair idea)
    oracle_eth = sweep_oracle(eth_a, bar_sec)

    cross_rows = sweep_horizons(btc_a, eth_a, horizons=(5, 15, 60))

    # Fee stress: single representative oracle-lag config on BTC
    fee_curve = []
    for max_entry in (0.50, 0.55, 0.60, 0.62, 0.65, 0.70):
        r = run_oracle_lag(
            btc_a,
            bar_sec,
            lag_min=1,
            min_delta=0.0007,
            max_entry=max_entry,
        )
        fee_curve.append(
            {
                "max_entry": max_entry,
                "trades": r.trades,
                "win_rate": round(r.wins / r.trades, 4) if r.trades else None,
                "pnl_usdc": round(r.pnl_usdc, 2),
                "avg_fee_usdc": round(r.avg_fee_usdc, 5),
            }
        )

    bars_per_win = 15
    n_win = min(len(btc_a), len(eth_a)) // bars_per_win
    split = int(n_win * 0.7)
    cfg = dict(lag_min=1, min_delta=0.0007, max_entry=0.62)
    train = run_oracle_lag_window_slice(btc_a, bar_sec, 900, 0, split, **cfg)
    test = run_oracle_lag_window_slice(btc_a, bar_sec, 900, split, n_win, **cfg)
    random_runs = [
        random_entry_baseline(btc_a, bar_sec, seed=s) for s in (0, 1, 2, 3, 4)
    ]
    random_avg_pnl = sum(r.pnl_usdc for r in random_runs) / len(random_runs)
    wrs = [r.wins / r.trades for r in random_runs if r.trades]
    random_avg_wr = sum(wrs) / len(wrs) if wrs else 0.0

    multi_seed_rows = []
    for sd in (11, 21, 31, 41, 51):
        bp = DATA_DIR / f"btcusdt_{days}d_1m_seed{sd}.json"
        ep = DATA_DIR / f"ethusdt_{days}d_1m_seed{sd}.json"
        write_synthetic(bp, ep, days=days, seed=sd)
        _, btc_s = klines_to_ohlc(bp)
        r = run_oracle_lag(btc_s, bar_sec, **cfg)
        multi_seed_rows.append(
            {
                "seed": sd,
                "trades": r.trades,
                "win_rate": round(r.wins / r.trades, 4) if r.trades else None,
                "pnl_usdc": round(r.pnl_usdc, 2),
            }
        )

    payload = {
        "data": {
            "source": data_source,
            "days": days,
            "btc_bars": len(btc_a),
            "eth_bars": len(eth_a),
            "aligned_bars": len(btc_a),
        },
        "oracle_lag_proxy_btc_sweep": oracle_rows,
        "oracle_lag_proxy_eth_sweep": oracle_eth,
        "oracle_lag_fee_entry_curve_btc": fee_curve,
        "cross_asset_btc_eth": cross_rows,
        "dump_hedge_toy": toy_dump_frequency(),
        "oracle_lag_walk_forward_btc": {
            "config": cfg,
            "train_windows": [0, split],
            "test_windows": [split, n_win],
            "train": {
                "trades": train.trades,
                "win_rate": round(train.wins / train.trades, 4) if train.trades else None,
                "pnl_usdc": round(train.pnl_usdc, 2),
            },
            "test": {
                "trades": test.trades,
                "win_rate": round(test.wins / test.trades, 4) if test.trades else None,
                "pnl_usdc": round(test.pnl_usdc, 2),
            },
        },
        "random_entry_baseline_btc_5seeds": {
            "avg_pnl_usdc": round(random_avg_pnl, 2),
            "avg_win_rate": round(random_avg_wr, 4),
            "per_seed": [
                {
                    "seed": s,
                    "trades": r.trades,
                    "win_rate": round(r.wins / r.trades, 4) if r.trades else None,
                    "pnl_usdc": round(r.pnl_usdc, 2),
                }
                for s, r in zip((0, 1, 2, 3, 4), random_runs)
            ],
        },
        "oracle_lag_multi_synthetic_seed_btc": multi_seed_rows,
        "disclaimer": "Proxy simulations — synthetic GBM when Binance unavailable; not Polymarket L2/oracle replay.",
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2))
    print(f"wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
