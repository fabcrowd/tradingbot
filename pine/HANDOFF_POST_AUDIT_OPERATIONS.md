# Post-audit operations plan

**Context:** Per-mode detector audits are complete (11/11). Default WFO grid includes all registered modes (2026-05-17). This document plans the shift from **code archaeology** to **production validation** and **high-frequency config mutators**.

**Related:** `HANDOFF_WFO_CHAMPION_AND_PARAM_TUNER.md`, `HANDOFF_RESTORE_OFF_GRID_WFO_MODES.md`, `HANDOFF_AUDIT_SELECTION_PATH_ADDENDUM.md`, `AGENTS.md` (sim vs live PnL).

---

## What already exists (don't rebuild blindly)

| Concern | In repo today | Gap |
|---------|----------------|-----|
| Live vs holdout demotion | `_check_champion_forward_validation()` in `scalp_runtime.py` — ratio `forward_pnl / (holdout_expectancy × trades)` vs `wfo_forward_demotion_threshold` | **Gate 2:** demotion only if a **better mode** exists in `strategy_lookback` snapshot; **not** a pure circuit breaker to `auto_mode_fallback` / bootstrap on bad live PnL alone |
| No-candidate demotion | `wfo_no_candidates_demotion_passes` → demote `wfo_champion` to bootstrap | Triggered by WFO failure, not live drawdown |
| Portfolio halt | `BotState.scalp_risk_halted` / `scalp_entries_blocked()` | Halts **entries**; not the same as champion rollback |
| Correlated sizing (live) | `ScalpPairConfig.correlation_group` + `dollar_risk / (1 + correlated_open)` in `scalp_trader.py` | WFO still **per-symbol**; `portfolio_correlation_backtest.py` post-processes multi-symbol sims only |
| Leg cap | `max_concurrent_positions` | Count-based, not beta-/notional-correlation aware |
| Live fill telemetry | `scalp_fill_execution` JSONL + optional EMA slip → WFO/tuner (`config.toml` comment) | No standing **dashboard** for live vs holdout reconciliation |
| Slippage in WFO | `slippage_pct` / `slippage_bps` in config; `backtest_fill_model = "next_open"` | Calibration vs CDE fills is **operational**, not automated |

---

## Priority order (recommended)

### P0 — Production validation loop (4 weeks post-restoration)

**Goal:** Close the loop: *does audit-validated WFO translate to live PnL?*

| Step | Deliverable | Owner / effort |
|------|-------------|----------------|
| 0 | **Baseline window start** — note restoration date + `wfo_top_k=80` + grid size ~5019 | Ops |
| 1 | **Per-symbol metrics** (daily roll): live realized PnL, live trade count, champion `mode`, `mode_source` | Script or dashboard |
| 2 | **Holdout reference** from champion JSON: `holdout_metrics.expectancy`, `total_pnl`, window count at promotion | Read `data/scalp_champion.json` |
| 3 | **Ratio:** `live_forward_pnl / (holdout_expectancy × live_trades)` — same spirit as forward validation, but **logged continuously** not only at demotion | Extend session log or snapshot |
| 4 | **Alert threshold:** flag if \|divergence\| > **30%** over rolling 4 weeks (tune after first month) | Config + log event |
| 5 | **Funding / fees:** include funding and fee tier in live side of comparison (`AGENTS.md`) | Reconciliation doc row |

**Success criteria:** For each active symbol, you can answer “is live within tolerance of holdout expectation?” without reading code.

**Out of scope:** Proving profitability (that requires capital and time); this proves **model fidelity**.

---

### P1 — `param_tuner.py` audit pass `[TUNER]`

**Why:** ~96 tuner cycles/day/pair vs ~1 WFO cycle/hour → **highest-frequency production mutator**. Detectors and WFO path audited; tuner is not.

**Scope (single handoff, same rigor as mode audits):**

| Area | Questions |
|------|-----------|
| Perturbation bounds | Do `TUNABLE_PARAMS` ranges stay inside WFO grid envelope? (X5 / Y4) |
| Mode override | `param_tuner_allow_mode_override_champion` — can tuner bypass WFO mode lock? |
| Objective alignment | Tuner scores `evaluate_params` on stored bars — same `fill_model` / slippage / fees as WFO? |
| Champion lock | `champion_tuner_mode_resolution`, `param_tuner_require_wfo_champion` |
| State file | `scalp_tuner_state.json` drift, partial applies, freeze thresholds |
| Blast radius | One bad perturbation on `atr_stop_mult` between WFO runs |

**Deliverable:** `HANDOFF_AUDIT_PARAM_TUNER.md` with findings table + resolutions.

**Tests to add:** bounds never exceed grid extrema; mode lock honored when override false.

---

### P2 — Slippage / fill calibration (1–2 days analysis)

**Goal:** WFO scores use defensible friction vs Coinbase CDE reality.

| Step | Action |
|------|--------|
| 1 | Pull N≥50 `scalp_fill_execution` rows per symbol (entry + exit) |
| 2 | Compute realized slip bps vs signal-bar close / next open reference |
| 3 | Compare to `slippage_bps` / `ParamSet.slippage_pct` used in grid |
| 4 | If median live **>** sim by ≥2 bps, bump default sim slip or enable live EMA feed into WFO |

**Deliverable:** one-page calibration note in this file or `lessons.md` with recommended `config.toml` values.

---

### P3 — Champion rollback / circuit breaker (architecture)

**Current:** Forward demotion needs a **replacement mode** with positive lookback expectancy — good for avoiding panic switches, bad for “stop bleeding” when all modes fail.

**Options (pick one for v1):**

| Option | Behavior | When |
|--------|----------|------|
| A — **Harden existing** | Document Gate 1-only path: operator sets `wfo_forward_demotion_threshold` aggressive + ensure lookback runs all modes | Low code |
| B — **Pure circuit breaker** | If rolling 24h live PnL < −X × holdout daily DD → `mode_source=bootstrap`, `auto_mode_fallback` mode, block entries until WFO | New flag in `scalp_runtime.py` |
| C — **Portfolio halt tie-in** | Existing `scalp_risk_halted` on drawdown; does not change champion | Already partial |

**Recommendation:** Spec **Option B** behind `wfo_live_circuit_breaker_enabled` (default false). Reuse `forward_pnl_since` + holdout DD from champion metrics.

**Not in v1:** Automatic re-promotion without WFO.

---

### P4 — Cross-symbol / portfolio risk (if scaling capital)

**Live today:** `correlation_group` sizing + optional `max_concurrent_positions`.

**Gaps:**

- WFO does not score portfolio-level drawdown when BIP+ETP+XPP align.
- `portfolio_correlation_backtest.py` exists for **offline** A/B — run after restoration on aligned bars.

| Step | Action |
|------|--------|
| 1 | Set `correlation_group` consistently in pair TOML (e.g. `crypto_nano`) |
| 2 | Run `portfolio_correlation_backtest` baseline vs `sizing_live_mirror` on last 90d aligned data |
| 3 | Decide if `max_concurrent_positions` or group-level notional cap needed beyond `1/(1+k)` sizing |

**Defer** until live PnL loop shows correlated loss clusters.

---

### P5 — Operator dial sensitivity (low urgency)

Meta-parameters in `config.toml` are hand-tuned, not optimized.

| Dial | Experiment | Metric |
|------|------------|--------|
| `wfo_top_k` | 50 vs 80 vs 110 on one symbol, frozen data | Champion mode/params delta, holdout score |
| `wfo_require_holdout_beat_prior` | Count promotions blocked vs accepted | False negative rate |
| `param_tuner_interval_sec` | 900 vs 1800 vs 3600 | Param drift magnitude between WFO |

**Procedure:** See `HANDOFF_RESTORE_OFF_GRID_WFO_MODES.md` § `wfo_top_k` — **watch first at 80**, then decide from `wfo_mode_scoreboard` + per-mode holdout counts.

---

### P6 — External out-of-sample reproduction (optional, high value)

**Goal:** Validate specs, not just internal consistency.

| Step | Action |
|------|--------|
| 1 | Pick one mode (e.g. `daviddtech_scalp`) + one symbol + fixed 3-week window |
| 2 | External builder gets `strategies.md` § only + OHLC export — **no repo code** |
| 3 | Compare signal timestamps to `detect_signals_*` output / `sar_chop_diagnostic_frame` style dump |
| 4 | Mismatch → spec or code drift audits missed |

**Cost:** contractor/friend time; **cheapest** falsification test for the whole audit program.

---

## Hygiene backlog (small, batch when convenient)

| Item | Effort | Notes |
|------|--------|-------|
| `hull_no_long_flip` log rename | ~5 min | `signal_engine.py` — misleading skip reason |
| `SCALP_VEC_BT_DIAG_MAX_KEYS = 64` | ~15 min | Document in docstring; optional env override |
| `wfo_objective = total_pnl` cross-symbol bias (Y1) | doc | Scoreboard favors high-notional symbols; use per-symbol views or normalize |
| Batched shape validation (11/11 audits) | 1 PR | Deferred from audit pass |
| `true_range` helper + UT dedup | 1 PR | Deferred E/F from sar_chop audit |

---

## Explicitly deferred (do not start yet)

- More per-mode detector audits
- More Pine parity work as primary track
- New strategy modes beyond 11
- Expanding WFO grid dimensions (already ~5k rows)
- Preemptive `wfo_top_k` bump (watch-first per restore handoff)

---

## Suggested timeline

| Week | Focus |
|------|--------|
| 0 | Grid restoration live; start P0 metric collection |
| 1–4 | P0 dashboard + weekly review; run first full WFO with 11 modes; review `wfo_mode_scoreboard` for top_k |
| 2 | P1 tuner audit (parallel) |
| 3 | P2 slippage calibration |
| 4 | P0 retrospective — 30% divergence rule; decide top_k / circuit breaker |
| 5+ | P3–P6 as capital / operator bandwidth allows |

---

## Tracking

| ID | Track | Status |
|----|-------|--------|
| P0 | Live vs holdout reconciliation | **Done** — `forward_reconciliation.py`, runtime snapshot/log, `tools/forward_reconciliation_report.py`, tests |
| P1 | `param_tuner.py` audit | **Done** — `HANDOFF_AUDIT_PARAM_TUNER.md`, `test_param_tuner_grid_bounds.py` |
| P2 | Slippage calibration | **Done** — `tools/analyze_scalp_slippage.py`, tests (run on session with `scalp_fill_execution` + `slip_bps`) |
| P3 | Circuit breaker | **Done** — `wfo_live_circuit_breaker_enabled=false` default, `_check_live_circuit_breaker`, tests |
| P4 | Portfolio correlation | **Done** — `correlation_group=l1_crypto` in config; `test_portfolio_correlation_smoke.py` |
| P5 | Operator dial sensitivity | Watch `wfo_top_k` at 80 (see restore handoff) |
| P6 | External reproduction | Optional / manual |
| H1 | Hygiene batch | **Done** — hull log rename, `SCALP_VEC_BT_DIAG_MAX_KEYS` env, daviddtech/rsi shape checks |

### Calibration results (P2)

- Latest session in repo has **no** `scalp_fill_execution` rows with `slip_bps` — run `analyze_scalp_slippage.py` after live trading accumulates fills.
- Config default: `slippage_bps = 1.0`, `backtest_fill_model = next_open`.

### Forward validation tuning (P3 / Option A)

| Knob | Default | Role |
|------|---------|------|
| `wfo_forward_demotion_threshold` | -0.5 | Gate 1 ratio floor |
| `wfo_forward_outperform_factor` | 1.5 | Gate 2 replacement must beat champion |
| `wfo_forward_min_trades` | 10 | Min trades before forward checks |
| `wfo_live_circuit_breaker_enabled` | **false** | Pure loss breaker (no replacement mode required) |
| `wfo_live_circuit_breaker_dd_mult` | 2.0 | Trip when `forward_pnl < -mult * holdout_max_drawdown` |

---

## Ops (your machine)

1. Restart scalp / run WFO with 11-mode grid (~5019 rows).
2. `python tools/forward_reconciliation_report.py` after live trades.
3. Enable `wfo_live_circuit_breaker_enabled` only after reviewing forward ratios.
