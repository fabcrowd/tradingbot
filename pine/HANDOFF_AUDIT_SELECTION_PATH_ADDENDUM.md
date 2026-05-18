# Handoff: hull_suite Audit Addendum + Cross-Mode Selection-Path Findings

**Target reviewer:** LLM managing the trading bot Python codebase  
**Source modules:** `scalp_vec_backtest.py` (`build_default_grid`, `simulate_trades_bidir`), `param_tuner.py` (`TUNABLE_PARAMS`), `scalp_runtime.py`, `signal_engine.py`, `scalp_wfo.py`  
**Audit date:** 2026-05-16  
**Prerequisite:** [`HANDOFF_WFO_CHAMPION_AND_PARAM_TUNER.md`](HANDOFF_WFO_CHAMPION_AND_PARAM_TUNER.md)

---

## Maintainer code notes (2026-05-16 spot-check)

Use these to calibrate the findings below without re-auditing from scratch.

| Topic | Code reality |
|-------|----------------|
| **X1 state-classifier list** | **`hull_suite` confirmed** (dense HMA state mask). **`supertrend` / `utbot_alert` are flip detectors** (sparse masks) — `cooldown_bars` is low-sensitivity for them. Do not lump supertrend with hull for cooldown sweep priority. |
| **X2 champion refresh** | WFO is **periodic** (`wfo_interval_sec`, default 300s). Champions refresh when a new row passes promotion gates — **not** a one-shot static store. After a scoring-function change, expect **gradual** refresh per symbol on schedule; operator can force `ScalpWFO.run_once()` for full refresh. |
| **X3 entry bracket parity** | For `supertrend`, `hull_suite`, `macd_scalp`, `sar_chop`, `qqe_mod`, `squeeze_momentum`, `utbot_alert`, `daviddtech`, `ema_momentum`: `SignalEngine._eval_*` sets **initial** stop/TP as `entry ± atr × atr_stop_mult / atr_tp_mult` — same family as WFO bidir brackets. **`ema_scalp` is the known S/R-at-entry outlier** (`high_8` / `low_8`). |
| **X3 in-trade exits** | Live `scalp_trader` implements **breakeven + trailing** from pair `breakeven_atr_*` / `trail_atr_*`. WFO `run_once` passes those from `pair_cfg` into `evaluate_params` and sets **`counter_signal_exit=True`** for bidir modes. RSI path uses `simulate_trades_rsi` (RSI exit + ATR TP in bar loop after recent fix). Remaining gap: live counter-signal close logic in `check_counter_signal` is **stricter** than bidir’s unconditional counter exit — not fully modeled in WFO. |
| **X4 grid-omitted modes** | Confirmed in `build_default_grid()` docstring: `ema_scalp`, `squeeze_momentum`, `qqe_mod`, `utbot_alert`. |
| **Y1 `total_pnl`** | Sim PnL = `(exit−entry)×contract_size − fees` per trade (`_roundtrip_gross_fee_net`). **Per-symbol WFO** is self-consistent. **`wfo_mode_scoreboard`** exposes raw holdout USD — cross-symbol rows are **not** normalized (high-price / large-cs symbols dominate). `config.toml` sets `wfo_objective = "total_pnl"`; code default in `scalp_config.py` is `expectancy_sqrt_n`. |

---

## Final status (maintainer ack 2026-05-16)

| Finding | Final status |
|---------|----------------|
| **X1** `cooldown_bars` | ✅ Scoped — **hull_suite only** (flip modes + off-grid modes excluded) |
| **X2** Champion refresh | ✅ Strategy — periodic + on-demand **full** `run_once`; not RSI-champion-only |
| **X3** Exit parity | ✅ Recalibrated — entry OK except `ema_scalp`; see counter-exit gap below |
| **X4** Grid omissions | ✅ Restore all 11 — gradual rollout; see `HANDOFF_RESTORE_OFF_GRID_WFO_MODES.md` |
| **X5** Tuner vs grid | ⏸ Per-mode tables over time |
| **X6–X7** | ✅ Ledger updated |

---

## Audit framework (final)

**Path tags:** `[WFO]` `[LIVE]` `[BOOT]` `[TUNER]` `[DOC]` (multiple allowed).

| Tag combo | Severity floor |
|-----------|----------------|
| `[WFO][LIVE]` | 🔴 unless trivial |
| `[WFO]` only | 🔴 if in default grid; 🟡 if off-grid |
| `[LIVE]` only | 🔴 |
| `[BOOT]` | 🟡 |
| `[TUNER]` | 🟡 |
| `[DOC]` | 🟢 |

---

## X3 — Counter-exit strictness asymmetry (tracked)

WFO bidir (`counter_signal_exit=True` in `ScalpWFO.run_once`) closes on **any** opposite mask bar. Live `scalp_trader.check_counter_signal` also requires **breakeven hit** and **confidence** thresholds.

**Implication:** WFO is **more permissive** → simulated holds are **shorter** on average than live; more live exits via `max_hold_bars` / stop / TP. Shifts per-trade hold-time distribution; not the same class of bug as `ema_scalp` S/R **price** divergence.

**Tags:** `[WFO][LIVE]` — lower priority than `ema_scalp` S/R; magnitude bounded by how often counter signals fire (sparse for flip modes).

---

## Context

The prior per-mode audits implicitly assumed all 11 registered modes compete equally in WFO and that score-affecting bugs have uniform blast radius. The selection-path handoff revealed that's not how the system works:

1. **WFO grid is selective.** Only 7 of 11 modes are in `build_default_grid` by default.
2. **WFO sweeps mode-specific params**, not all `ParamSet` fields. Hidden parameters affect scores at a fixed point.
3. **Score function = `total_pnl` after fees on closed trades** (`wfo_objective`, default `total_pnl`).
4. **Live vs WFO exit parity is not systematically documented.** `ema_scalp` is the known entry-bracket divergence.

---

## 🔴 NEEDS DECISION — net-new findings

### FINDING X1 — Hidden parameter: `cooldown_bars` not swept by WFO grid

**Anchors to:** hull_suite FINDING A (continuous state vs edge detector)  
**Also see:** [`HANDOFF_DEFERRED_STATE_CLASSIFIER_COOLDOWN.md`](HANDOFF_DEFERRED_STATE_CLASSIFIER_COOLDOWN.md)

**Files:** `scalp_vec_backtest.py::build_default_grid`, `simulate_trades_bidir` (`cooldown_bars=1` default on `ParamSet`)

**Observations:**

- `next_allowed = exit_bar + cooldown_bars` throttles re-entry after each simulated trade.
- **Edge-detector modes** (EMA cross, MACD cross, supertrend flip, PSAR flip, etc.): sparse masks → `cooldown_bars` rarely binding.
- **State-classifier modes** (`hull_suite` confirmed): dense `long_mask` / `short_mask` → **`cooldown_bars` is load-bearing** alongside `max_hold_bars`.

WFO never sweeps `cooldown_bars`; every grid row uses default **1**.

**Decision tree:** Sweep for `hull_suite` only | document TV-validated `cooldown_bars=1` | small A/B on one symbol.

**Severity:** 🔴 for `hull_suite` (auto-grid). 🟢 for flip-based modes.

---

### FINDING X2 — RSI simulator fix blast radius beyond rsi_reversion champions

**Anchors to:** rsi_reversion FINDING A; daviddtech intrabar / RSI long-TP fixes

**Observations:**

- WFO ranks **all grid rows**; runner-up scores affect `wfo_require_holdout_beat_prior`, stability pools, scoreboard UI.
- Pre-fix `rsi_reversion` rows were systematically under-scored → other modes may have won champion slots they would not win post-fix.
- **Refresh strategy:** periodic WFO (`wfo_interval_sec`) eventually re-promotes per symbol when gates pass; for a clean break after scoring changes, operator should schedule a **full symbol-set WFO pass** and note `post-fix champion refresh` timestamp.

**Severity:** 🔴 operational / champion-history validity.

---

### FINDING X3 — Live vs WFO exit parity not systematically verified

**Anchors to:** ema_scalp FINDING A

**Entry brackets (spot-check):**

| Mode | Live initial stop/TP | WFO sim |
|------|----------------------|---------|
| Most bidir modes | `atr_stop_mult` / `atr_tp_mult` | Same in `simulate_trades_bidir` |
| `ema_scalp` | S/R (`high_8` / `low_8`) + ATR floor | Generic ATR bidir only |
| `rsi_reversion` | ATR brackets at signal | `simulate_trades_rsi` (+ RSI long exit, ATR TP in loop) |

**In-trade (partial alignment):**

- WFO bidir path can apply BE/trail from `pair_cfg` when `optimize_pair` is called with those kwargs; `ScalpWFO.run_once` enables **`counter_signal_exit=True`**.
- Live `scalp_trader` BE/trail ratchets are **not identical** to bidir’s close-based trail math but same intent.
- Live `check_counter_signal` adds breakeven-hit and confidence gates — **stricter** than WFO counter exit.

**Action:** Per-mode audit checklist item — document in `strategies.md` as “entry: ATR brackets; in-trade: scalp_trader BE/trail; WFO: bidir + optional counter” unless divergence found.

**Severity:** 🔴 if more entry-bracket divergences exist beyond `ema_scalp`; 🟡 for in-trade modeling gaps.

---

## 🟡 CODE SMELL — recalibrations

### FINDING X4 — Four modes unselectable by default WFO

**Resolution (2026-05):** Designer confirmed omissions were **unintentional**. Restore all 11 to `build_default_grid` gradually:

1. `utbot_alert` — on grid (cycle 1)  
2. `squeeze_momentum`, `qqe_mod` — after audits  
3. `ema_scalp` — after WFO/live S/R exit parity  

See [`HANDOFF_RESTORE_OFF_GRID_WFO_MODES.md`](HANDOFF_RESTORE_OFF_GRID_WFO_MODES.md).

---

### FINDING X5 — `TUNABLE_PARAMS` vs `build_default_grid` not unified

**Examples to verify per mode:**

- WFO sweeps `ema_fast` / `ema_slow`; tuner perturbs them — aligned for `ema_momentum`.
- `hull_period`: WFO sweeps; tuner **excludes** (TV-validated) — intentional drift.
- Risk knobs (`atr_stop_mult`, `max_hold_bars`): usually in both.

**Question:** Single source of truth per mode vs documented intentional divergence?

**Severity:** 🟡 maintenance hygiene.

---

## 🟢 ROBUSTNESS

### FINDING X6 — Champion keyed by symbol

Two pairs, same symbol → one champion row. Documented in selection handoff §7.3; remember when assessing “production impact.”

### FINDING X7 — Deferred bucket recalibration

| Item | Recalibrated priority |
|------|------------------------|
| `ema_scalp` WFO-vs-live | Lower — off default grid |
| `cooldown_bars` / hull_suite | Add — see X1 / deferred cooldown doc |
| RSI scoring refresh | Higher clarity — full symbol set, not RSI-only champions |
| Repo-wide shape guards | Higher confidence — adopt per-mode in audits |
| `hull_no_long_flip` rename | Unchanged — cosmetic |

---

## Suggested action sequence

| Priority | Item |
|----------|------|
| 1 | X2 — champion refresh policy after simulator fixes |
| 2 | X3 — exit parity matrix in `strategies.md` (entry + in-trade) |
| 3 | X1 — `cooldown_bars` for `hull_suite` only |
| 4 | X4 — grid omission triage |
| 5 | X5 — tuner vs grid alignment table |
| 6 | X7 — ledger updates (this file + `REVIEW_HANDOFF_FOR_LLM.txt`) |

---

## Updated audit checklist (remaining modes)

For each mode audit, tag findings with path:

| Tag | Meaning |
|-----|---------|
| `[WFO]` | Affects `build_default_grid` scoring / auto champions |
| `[LIVE]` | Affects `SignalEngine` / `scalp_trader` for all pairs using mode |
| `[BOOT]` | Affects `best_mode_bootstrap_no_champion` |
| `[TUNER]` | Affects `param_tuner` perturbation |
| `[MANUAL]` | Only if `strategy_mode` pinned to mode |

Checklist:

1. WFO grid coverage — in grid? which params?
2. State vs edge mask — if state, flag `cooldown_bars`
3. Live `_eval_<mode>` entry brackets vs bidir/rsi sim
4. In-trade: BE/trail/counter vs WFO kwargs
5. `TUNABLE_PARAMS[mode]` vs grid alignment
6. Bootstrap sanity on `risk_on_bootstrap_hours`

---

## Proactive findings (Y1–Y4)

### Y1 — `total_pnl` cross-symbol comparability

Per-symbol WFO ranks rows in **sim USD** for that symbol’s price level and `contract_size`. Holdout scoreboard / UI rows that compare symbols side-by-side are **not** percent-normalized unless a future objective says so.

**Tags:** `[WFO][DOC]` — clarify in UI/docs; not a per-symbol champion bug.

### Y2 — Champion `scoring_function_version`

No version field on promoted champions today. After simulator changes, champions can be **silently stale** until the next promotion. Suggest optional JSON field + WARN on load mismatch.

**Tags:** `[WFO]` — 🟡 robustness.

### Y3 — Scoreboard and grid-omitted modes

`wfo_mode_scoreboard` only includes modes present in the grid run. Omitted modes may appear absent / never-champion — indistinguishable from “underperforms.” Badge **“not in WFO grid”** or doc in UI.

**Tags:** `[DOC]` — 🟢 UX.

### Y4 — Tuner perturbation outside WFO grid bounds

`TUNABLE_PARAMS` can nudge e.g. `atr_stop_mult` beyond values WFO sampled. Either clamp to grid bounds or document deliberate exploration.

**Tags:** `[TUNER]` — 🟡 intent clarification (related to X5).

---

## Tracking ledger — audits complete

| Mode / doc | Status |
|------------|--------|
| daviddtech_scalp | ✅ |
| ema_momentum | ✅ |
| ema_scalp | ✅ |
| hull_suite | ✅ |
| rsi_reversion | ✅ |
| selection-path addendum | ✅ |
| **Next:** macd_scalp | ⏳ (apply path-tagged checklist) |

---

## Next audit target

**`macd_scalp`** — path-tagged checklist; begin when review package is ready.
