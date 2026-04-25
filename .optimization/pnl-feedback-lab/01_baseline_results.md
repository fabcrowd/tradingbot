# Phase 1 — Baseline (multi-window)

**Run artifact:** `runs/lab_run_20260407.jsonl`  
**Method:** Three disjoint windows (`early` / `mid` / `late`) by bar index on each pair’s full Parquet series. All five modes evaluated per window.

## Primary metric (this cycle)

- **P2:** `score_exp_sqrt_n` = expectancy × √trade_count (same spirit as WFO objective).

## Guards referenced

- **G1:** trades ≥ 5 preferred for interpretation (many cells below → “thin”).
- **G2–G5:** see `05_compare_20260407.md`.

## BTC_USD (BIP-20DEC30-CDE, 15m) — best mode per window (by score)

| Window | Best mode | trades | total_pnl | score | Note |
|--------|-----------|--------|-----------|-------|------|
| early | daviddtech_scalp | 3 | +1186.28 | 684.90 | Very few trades |
| mid | rsi_reversion | 1 | +463.89 | 463.89 | Single trade — not steady |
| late | ema_momentum | 21 | +3824.93 | 834.67 | Strong sample |

**Baseline narrative:** No single mode wins all three windows on score or on PnL stability; `daviddtech_scalp` is competitive early/late but almost flat mid (1 trade).

## SOL_USD / XRP_USD

- Mostly **micro PnL** and/or **trades &lt; 5**; alt windows are **not** suitable for CONFIRMED steady-growth claims on this slice of history.

## Raw data

Full per-mode rows: `runs/lab_run_20260407.jsonl` (JSON lines after the contract object).
