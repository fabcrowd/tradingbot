# PnL Feedback Lab — Phase 0 Recon (tradingbot-1)

## PnL surfaces

- **Live / sim:** `ScalpTrader` PnL, session logs, `data/trades_*.jsonl`
- **Historical lab:** `scalp_vec_backtest.evaluate_params` → `BacktestMetrics.total_pnl`, expectancy, drawdown, trade list

## Bar truth (historical charts)

- Parquet under `data/coinbase_bars/` (venue `coinbase_perps`) and `data/kraken_bars/`
- Loader: `backend/server/scalp_bot/bar_store.py`

## Harnesses

| Harness | Path / command |
|---------|----------------|
| Multi-window lab (this run) | `python .optimization/pnl-feedback-lab/scripts/run_multiwindow_lab.py` |
| Interval compare (may hit REST) | `python backend/server/scalp_bot/compare_intervals.py` |
| WFO (runtime) | `ScalpWalkForwardOptimizer` in `scalp_wfo.py` |
| Session replay | `backend/server/backtest.py` (MM sessions) |

## Knobs (hypothesis queue seeds)

- `[scalp]` in `config.toml`: modes, ATR mults, WFO hours, fill model, fees, per-pair params
- Regime / WFO risk-on: `scalp_runtime` + `regime_risk.py`

## Leakage / caveats

- Disjoint **bar slices** reset indicator warmup per window — regimes are independent; short windows → few trades → unstable metrics.
- `next_open` fill model matches current WFO default; good for lab alignment.
- Massive `max_dd_pct` in some rows usually means tiny denominator equity — treat as **noise** unless trade count is healthy.

## Baseline for this repo (frozen for run `20260407`)

- Contract emitted in `runs/lab_run_20260407.jsonl` first JSON object: `next_open`, fee 0, slippage 1 bps, windows = thirds of bar index per series.
