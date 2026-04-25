# Compare — run `skills_verify_20260409`

**Generated:** 2026-04-09 16:20Z (auto)
**Git:** `813c83e`
**Lens B:** *not linked — add `--lens-b` on lab run or edit this file*

---

## 1. What we tested

| Field | Value |
|-------|-------|
| Command | `.optimization/pnl-feedback-lab/scripts/run_multiwindow_lab.py --intervals 5,15,60 --jsonl-out .optimization/pnl-feedback-lab/runs/lab_skills_verify_20260409.jsonl --export-pnl-details --compare-md .optimization/pnl-feedback-lab/runs/05_compare_skills_verify_20260409.md --run-id skills_verify_20260409` |
| Artifacts | `.optimization\pnl-feedback-lab\runs\lab_skills_verify_20260409.jsonl` (+ stderr from same run) |
| Strategies compared | daviddtech_scalp, ema_momentum, ema_scalp, hull_suite, qqe_mod, rsi_reversion, squeeze_momentum, supertrend, utbot_alert |
| Pairs | BTC_USD, SOL_USD, XRP_USD |
| Time windows | early / mid / late (or `full` if series < 90 bars) — thirds of bar index |
| Intervals swept | `[5, 15, 60]` |
| Skipped (stderr) | `# skip BTC_USD BTC-PERP-INTX 60m: insufficient or missing parquet`<br>`# skip SOL_USD SOL-PERP-INTX 60m: insufficient or missing parquet`<br>`# skip XRP_USD XRP-PERP-INTX 60m: insufficient or missing parquet` |
| Simulation contract | see table below |

| Contract key | Value |
|--------------|-------|
| fee_bps_per_leg | `0.0` |
| fee_bps_source | `config` |
| fill_model | `next_open` |
| intervals_swept | `[5, 15, 60]` |
| min_trades_per_window | `None` |
| slippage_bps | `1.0` |
| venue | `coinbase_perps` |
| windows | `thirds_of_series_bar_index` |

**Units:** `total_pnl` is the **vector backtester’s internal PnL** for the sim — not guaranteed live USD.

---

## 2. PnL impact during the test windows

### BTC_USD — best mode by window (highest `score_exp_sqrt_n`)

| Interval | Window | Best mode | total_pnl | trades | PF |
|----------|--------|-----------|-----------|--------|-----|
| 5m | early | ema_scalp | 773.817145 | 12 | 3.0452 |
| 5m | mid | rsi_reversion | 312.923984 | 5 | — |
| 5m | late | ema_momentum | 763.51202 | 23 | 1.4417 |
| 15m | early | rsi_reversion | 85.96431 | 1 | — |
| 15m | mid | supertrend | 208.540388 | 2 | 2.5303 |
| 15m | late | supertrend | 345.736229 | 2 | — |

**Focus mode (`config.strategy_mode`): `daviddtech_scalp`** (operating interval = `config_interval_m` rows only)

| Window | total_pnl | trades | PF |
|--------|-----------|--------|-----|
| early | 0.0 | 0 | 0.0000 |
| mid | -160.881292 | 2 | 0.2850 |
| late | -30.59526 | 1 | 0.0000 |

**Weakest window (focus):** `mid` (total_pnl=-160.881292). **Thin sample (trades<5):** early, mid, late.

### SOL_USD — best mode by window (highest `score_exp_sqrt_n`)

| Interval | Window | Best mode | total_pnl | trades | PF |
|----------|--------|-----------|-----------|--------|-----|
| 5m | early | daviddtech_scalp | 0.369 | 1 | — |
| 5m | mid | rsi_reversion | 0.796713 | 3 | — |
| 5m | late | daviddtech_scalp | 1.933151 | 4 | 5.3346 |
| 15m | early | supertrend | 0.444574 | 2 | 4.4464 |
| 15m | mid | squeeze_momentum | 1.227777 | 5 | 3.0134 |
| 15m | late | hull_suite | 0.606122 | 1 | — |

**Focus mode (`config.strategy_mode`): `daviddtech_scalp`** (operating interval = `config_interval_m` rows only)

| Window | total_pnl | trades | PF |
|--------|-----------|--------|-----|
| early | 0.0 | 0 | 0.0000 |
| mid | 0.0 | 0 | 0.0000 |
| late | -0.011281 | 1 | 0.0000 |

**Weakest window (focus):** `late` (total_pnl=-0.011281). **Thin sample (trades<5):** early, mid, late.

### XRP_USD — best mode by window (highest `score_exp_sqrt_n`)

| Interval | Window | Best mode | total_pnl | trades | PF |
|----------|--------|-----------|-----------|--------|-----|
| 5m | early | ema_scalp | 0.007423 | 16 | 1.4806 |
| 5m | mid | rsi_reversion | 0.002368 | 1 | — |
| 5m | late | daviddtech_scalp | 0.024956 | 3 | — |
| 15m | early | qqe_mod | 0.003437 | 2 | 4.0384 |
| 15m | mid | utbot_alert | 0.005644 | 9 | 1.4273 |
| 15m | late | qqe_mod | 0.018747 | 5 | 5.6470 |

**Focus mode (`config.strategy_mode`): `daviddtech_scalp`** (operating interval = `config_interval_m` rows only)

| Window | total_pnl | trades | PF |
|--------|-----------|--------|-----|
| early | 0.0 | 0 | 0.0000 |
| mid | 0.0 | 0 | 0.0000 |
| late | -0.00157 | 2 | 0.4827 |

**Weakest window (focus):** `late` (total_pnl=-0.00157). **Thin sample (trades<5):** early, mid, late.

---

## 3. How we validated

| Scope | Note |
|-------|------|
| Multi-window tape | ✓ Thirds (or full) per pair/interval present in JSONL |
| Dual lens | *Manual:* link `report.md` and set CORROBORATED/REFUTED/DEFERRED after Lab Loop |
| Live / funding | ✗ Not covered by this JSONL |

### Automatic gates (focus mode, operating interval only)

#### BTC_USD — `daviddtech_scalp`

| Check | Result |
|-------|--------|
| G1 (≥5 trades per window) | **FAIL** — 0/3 windows |
| G3 (profit factor ≥ 1 where finite) | **FAIL** — 0/3 windows with finite PF |
| RULE C-style (≥2/3 windows with total_pnl > 0) | **FAIL** — 0/3 positive |

#### SOL_USD — `daviddtech_scalp`

| Check | Result |
|-------|--------|
| G1 (≥5 trades per window) | **FAIL** — 0/3 windows |
| G3 (profit factor ≥ 1 where finite) | **FAIL** — 0/3 windows with finite PF |
| RULE C-style (≥2/3 windows with total_pnl > 0) | **FAIL** — 0/3 positive |

#### XRP_USD — `daviddtech_scalp`

| Check | Result |
|-------|--------|
| G1 (≥5 trades per window) | **FAIL** — 0/3 windows |
| G3 (profit factor ≥ 1 where finite) | **FAIL** — 0/3 windows with finite PF |
| RULE C-style (≥2/3 windows with total_pnl > 0) | **FAIL** — 0/3 positive |

**What this run does not prove:** live fills, funding, WFO train/holdout alignment with these thirds, or profitability if fees/slippage change.

---

## 4. Recommended optimizations

1. Raise sample bar (e.g. `wfo_min_trades` / longer history) — thin windows: early, mid, late.
2. Windows with PF < 1: early, mid, late — review mode or gates before scaling live.
3. Negative or flat windows on focus mode: early, mid, late.
4. Add or backfill Parquet for skipped interval×symbol combinations, or stop passing unused `--intervals`.
5. Tighten WFO promotion rules if `scalp_champion.json` modes disagree with per-window winners above.
6. Re-run lab after bar file updates; append a new `run_id` rather than repeating identical tape for 'more data'.
