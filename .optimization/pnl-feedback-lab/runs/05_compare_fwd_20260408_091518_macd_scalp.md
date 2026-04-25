# Compare — run `agent_fwd_20260408_091518_macd_scalp`

**Generated:** 2026-04-08 13:15Z (auto)
**Git:** `203c2d1`
**Lens B:** *not linked — add `--lens-b` on lab run or edit this file*

---

## 1. What we tested

| Field | Value |
|-------|-------|
| Command | `post-backfill --intervals 5,15,60; focus=macd_scalp` |
| Artifacts | `.optimization\pnl-feedback-lab\runs\lab_run_fwd_20260408_091518.jsonl` (+ stderr from same run) |
| Strategies compared | daviddtech_scalp, ema_momentum, ema_scalp, macd_scalp, rsi_reversion |
| Pairs | BTC_USD, SOL_USD, XRP_USD |
| Time windows | early / mid / late (or `full` if series < 90 bars) — thirds of bar index |
| Intervals swept | `[5, 15, 60]` |
| Simulation contract | see table below |

| Contract key | Value |
|--------------|-------|
| fee_bps_per_leg | `0.0` |
| fill_model | `next_open` |
| intervals_swept | `[5, 15, 60]` |
| slippage_bps | `1.0` |
| venue | `coinbase_perps` |
| windows | `thirds_of_series_bar_index` |

**Units:** `total_pnl` is the **vector backtester’s internal PnL** for the sim — not guaranteed live USD.

---

## 2. PnL impact during the test windows

### BTC_USD — best mode by window (highest `score_exp_sqrt_n`)

| Interval | Window | Best mode | total_pnl | trades | PF |
|----------|--------|-----------|-----------|--------|-----|
| 5m | early | rsi_reversion | 129.8385 | 3 | — |
| 5m | mid | daviddtech_scalp | 667.668489 | 8 | 2.1869 |
| 5m | late | ema_momentum | 1545.333156 | 45 | 1.2521 |
| 15m | early | daviddtech_scalp | 1301.28079 | 3 | 28.6895 |
| 15m | mid | rsi_reversion | 726.661622 | 2 | — |
| 15m | late | ema_momentum | 3802.627136 | 24 | 2.4328 |
| 60m | early | ema_momentum | 431.6015 | 2 | — |
| 60m | mid | ema_scalp | 702.683404 | 1 | — |
| 60m | late | ema_scalp | 1529.279522 | 1 | — |

**Focus mode (`--focus-strategy` override): `macd_scalp`** (operating interval = `config_interval_m` rows only)

| Window | total_pnl | trades | PF |
|--------|-----------|--------|-----|
| early | -1713.112745 | 17 | 0.4873 |
| mid | 1768.790448 | 19 | 2.0527 |
| late | -1047.388932 | 17 | 0.7262 |

**Weakest window (focus):** `early` (total_pnl=-1713.112745). **Thin sample (trades<5):** none.

### SOL_USD — best mode by window (highest `score_exp_sqrt_n`)

| Interval | Window | Best mode | total_pnl | trades | PF |
|----------|--------|-----------|-----------|--------|-----|
| 5m | early | daviddtech_scalp | 0.710898 | 3 | — |
| 5m | mid | daviddtech_scalp | 1.856614 | 4 | 39.4551 |
| 5m | late | daviddtech_scalp | 2.354779 | 6 | 3.8767 |
| 15m | early | daviddtech_scalp | 0.917542 | 2 | 20.0046 |
| 15m | mid | macd_scalp | 2.220261 | 19 | 2.2042 |
| 15m | late | ema_momentum | 3.84071 | 21 | 1.9673 |
| 60m | early | ema_momentum | 1.474997 | 3 | — |
| 60m | mid | ema_scalp | 1.304894 | 1 | — |
| 60m | late | ema_scalp | 2.084797 | 1 | — |

**Focus mode (`--focus-strategy` override): `macd_scalp`** (operating interval = `config_interval_m` rows only)

| Window | total_pnl | trades | PF |
|--------|-----------|--------|-----|
| early | -2.207402 | 20 | 0.6186 |
| mid | 2.220261 | 19 | 2.2042 |
| late | -5.233802 | 19 | 0.3613 |

**Weakest window (focus):** `late` (total_pnl=-5.233802). **Thin sample (trades<5):** none.

### XRP_USD — best mode by window (highest `score_exp_sqrt_n`)

| Interval | Window | Best mode | total_pnl | trades | PF |
|----------|--------|-----------|-----------|--------|-----|
| 5m | early | rsi_reversion | -0.000845 | 3 | 0.8400 |
| 5m | mid | daviddtech_scalp | 0.01509 | 5 | 2.9523 |
| 5m | late | daviddtech_scalp | 0.014445 | 5 | 2.1445 |
| 15m | early | daviddtech_scalp | 0.016181 | 2 | — |
| 15m | mid | rsi_reversion | 0.006016 | 1 | — |
| 15m | late | daviddtech_scalp | 0.021822 | 1 | — |
| 60m | early | ema_scalp | 0.018364 | 2 | 4.2039 |
| 60m | mid | ema_scalp | 0.018975 | 1 | — |
| 60m | late | ema_scalp | 0.027144 | 1 | — |

**Focus mode (`--focus-strategy` override): `macd_scalp`** (operating interval = `config_interval_m` rows only)

| Window | total_pnl | trades | PF |
|--------|-----------|--------|-----|
| early | 0.011317 | 21 | 1.2285 |
| mid | -0.014113 | 21 | 0.7364 |
| late | -0.037059 | 21 | 0.6103 |

**Weakest window (focus):** `late` (total_pnl=-0.037059). **Thin sample (trades<5):** none.

---

## 3. How we validated

| Scope | Note |
|-------|------|
| Multi-window tape | ✓ Thirds (or full) per pair/interval present in JSONL |
| Dual lens | *Manual:* link `report.md` and set CORROBORATED/REFUTED/DEFERRED after Lab Loop |
| Live / funding | ✗ Not covered by this JSONL |

### Automatic gates (focus mode, operating interval only)

#### BTC_USD — `macd_scalp`

| Check | Result |
|-------|--------|
| G1 (≥5 trades per window) | **PASS** — 3/3 windows |
| G3 (profit factor ≥ 1 where finite) | **FAIL** — 1/3 windows with finite PF |
| RULE C-style (≥2/3 windows with total_pnl > 0) | **FAIL** — 1/3 positive |

#### SOL_USD — `macd_scalp`

| Check | Result |
|-------|--------|
| G1 (≥5 trades per window) | **PASS** — 3/3 windows |
| G3 (profit factor ≥ 1 where finite) | **FAIL** — 1/3 windows with finite PF |
| RULE C-style (≥2/3 windows with total_pnl > 0) | **FAIL** — 1/3 positive |

#### XRP_USD — `macd_scalp`

| Check | Result |
|-------|--------|
| G1 (≥5 trades per window) | **PASS** — 3/3 windows |
| G3 (profit factor ≥ 1 where finite) | **FAIL** — 1/3 windows with finite PF |
| RULE C-style (≥2/3 windows with total_pnl > 0) | **FAIL** — 1/3 positive |

**What this run does not prove:** live fills, funding, WFO train/holdout alignment with these thirds, or profitability if fees/slippage change.

---

## 4. Recommended optimizations

1. Windows with PF < 1: early, late — review mode or gates before scaling live.
2. Negative or flat windows on focus mode: early, late.
3. Windows with PF < 1: mid, late — review mode or gates before scaling live.
4. Negative or flat windows on focus mode: mid, late.
5. Tighten WFO promotion rules if `scalp_champion.json` modes disagree with per-window winners above.
6. Re-run lab after bar file updates; append a new `run_id` rather than repeating identical tape for 'more data'.
