# 05 Compare — skill run `skill_20260408_tape` (2026-04-08)

**Hypothesis:** H-LIVE-NEG-20260408  
**Lens B:** `research/H-LIVE-NEG-20260408/report.md` — **mixed**  
**Lens A:** `runs/skill_20260408_tape/lab.jsonl` + `lab.stderr.txt`

## Dual lens (iteration 1)

| Lens | Summary |
|------|---------|
| **B** | Three losing trades can be **variance**, **wrong assumed mode (WFO vs config)**, **late-window / small-n** weakness, or **execution gap**. |
| **A** | On **current** Parquet, **`daviddtech_scalp` is not steady across thirds on BTC**: early strong, mid microscopic (n=1), **late negative** with n=2. That **supports** “small-n + regime” and **supports** checking which mode actually traded. |

**Convergence:** **CORROBORATED (exploratory)** on the narrow claim *“do not treat current stack as steady PnL without per-window + mode audit.”*  
**NOT CONFIRMED** for RULE C / merge / “acceptable live performance.”

## Operating interval — BTC_USD @ 15m (`daviddtech_scalp`, vector metrics)

| Window | trades | total_pnl | profit_factor | win_rate | Notes |
|--------|--------|-----------|---------------|----------|--------|
| early | 3 | +1301.28 | 28.69 | 0.67 | High PnL but **G1 fails** (n&lt;5) for steady-PnL bundle |
| mid | 1 | +3.13 | — | 1.0 | **G1 fails** |
| late | 2 | **−22.70** | **0.98** | 0.50 | **Fails P1 / G3**; comparable order of magnitude to “three bad trades” narrative |

**RULE C pre-check:** **Fails** on registered gates (need ≥2/3 windows with n≥5, PnL&gt;0, PF≥1).

## Best mode by window (stderr SUMMARY — discovery)

- **late:** `ema_momentum` beats other modes on score for that slice.  
- **mid:** `rsi_reversion` wins on score with **n=1** in best row (unstable).  
- **early:** `daviddtech_scalp` wins.

Implication: **WFO’s job** should be aligned with **not** locking a single mode if the operator’s mental model is “always daviddtech” — but any auto-selected mode still needs **non-negative OOS** gates (see H-WFO-NEG-HOLD).

## Current `data/scalp_champion.json` on disk (spot check)

| Symbol | `mode` | `holdout_metrics.total_pnl` | Notes |
|--------|--------|-----------------------------|--------|
| BIP (BTC) | `macd_scalp` | **+2538** (logged row) | **Not** `daviddtech_scalp` from `config.toml`. |
| SLP (SOL) | `ema_momentum` | **−0.85** | Champion row stores **negative** OOS net; **PF 0.35**. |
| XPP (XRP) | `ema_momentum` | **+0.03** | Tiny positive. |

This **supports H-LIVE-NEG-20260408** and **H-WFO-NEG-HOLD**: live stack ≠ “daviddtech only,” and **SOL’s saved champion contradicts** naive “champion = safe.”

## Session evidence (Lens A supplement — not bar-exact)

`data/session_20260408_020005.jsonl` shows **`champion_saved`** for BTC with **`macd_scalp`**, **`holdout_metrics.total_pnl`** **negative** (~−311 and later ~−32 on subsequent pass lines). Logged **`score`** is positive because WFO aggregates **mean `expectancy_sqrt_n` across windows** and uses **`min_mean_score = -0.1`**, while **`holdout_metrics` in the event is not a full multi-window aggregate** (implementation uses latest-window metrics in the saved row — see `scalp_wfo.py` `avg_m = best_metrics[-1]`).

**Coupled readout:** Positive **score** + negative **displayed holdout PnL** ⇒ operator dashboard can look “ranked” while **latest slice is bleeding**.

## Interval vector (RULE B2)

5m / 60m: **skipped** (missing Parquet). Discovery on multi-timeframe is **blocked** until files exist.

## Operator actions (before next live session)

1. **Read `data/scalp_champion.json`** — confirm **mode per symbol** actually trading.  
2. **Do not** interpret three trades as proof the edge is dead; **do** treat as **mandatory** trigger for **mode + gate** review.  
3. **Consider:** `wfo_enabled = false` temporarily and fixed **`daviddtech_scalp`** *only if* you accept tape risk on late window; **or** tighten WFO so champions cannot save with **latest holdout net &lt; 0** (code change — RULE D later).  
4. **Next Lab Loop iteration:** map the **three** real fills to **pair + mode + bar time**; reconcile with `champion_period_start` lines in session JSONL.

## Artifacts

- Tape: `runs/skill_20260408_tape/lab.jsonl`  
- Stderr / SUMMARY: `runs/skill_20260408_tape/lab.stderr.txt`  
- Git: run `git rev-parse --short HEAD` when freezing verdict
