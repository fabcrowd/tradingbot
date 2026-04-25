# Plan vs repo: Nemesis-style audit + research corroboration

**Scope:** Compare [Freqtrade-style optimizations plan](file:///c:/Users/daroo/.cursor/plans/freqtrade-style_optimizations_18d8b0f0.plan.md) to the Fabcrowd Arceus codebase. Methods: code trace (verification gate), coupled-state mapping (Nemesis-inspired), light external research on WFO selection.

**Verdict:** The plan’s direction is **confirmed** by the repo. Several sections should be **narrowed or upgraded** with evidence below—especially §1 (warmup), where the dominant issue is not only `iv.ready` vs per-mode vec warmup but **truncated live history vs full WFO bars**.

---

## Nemesis map (coupled pairs)

| State A | State B | Risk if desync |
|--------|---------|----------------|
| `iv.ready` (hexital/numpy: `max(ema_slow, rsi_period, atr_period, min_candles_required)`) | Per-mode `warmup` in `scalp_vec_backtest.detect_signals_*` | Live blocks or allows entries on bars where vec already scores trades (or vice versa). |
| `IndicatorEngine._ohlc_hist` **maxlen=320** ([`indicators.py`](backend/server/scalp_bot/indicators.py) ~248) | WFO/vec slices using **full** roll-span bars from `bar_store` | Long-EMA / CHOP / MACD **state** on live is computed on a **short tail**; vec uses long history → champion scores may **not** reproduce live even with matching `min_bars`. |
| `param_tuner.TUNABLE_PARAMS` hull | `build_default_grid` in [`scalp_vec_backtest.py`](backend/server/scalp_bot/scalp_vec_backtest.py) | Tuner explores params **outside** the WFO combinatorial hull. |
| Holdout `mean_score` only ([`scalp_wfo.py`](backend/server/scalp_bot/scalp_wfo.py) ~937) | `stability` in same candidate tuple | Tie-break signal unused; noisy score ties pick arbitrary grid index. |
| Candle-close handler order in [`scalp_runtime.py`](backend/server/scalp_bot/scalp_runtime.py) ~2331–2367 | Per-mode vec `exit_reason` ordering in [`scalp_vec_backtest.py`](backend/server/scalp_bot/scalp_vec_backtest.py) | Live/sim exit attribution and edge cases can diverge if not documented. |

---

## Section-by-section: confirm / refute / upgrade

### §1 Indicator warmup and per-mode readiness

- **CONFIRMED** that live `iv.ready` is **generic** (`indicators.py` ~512–515) while vec uses **mode-specific** warmup (e.g. `ema_momentum`: `max(ema_slow_period, atr_period)` ~762–764; `sar_chop`: `max(ma_long_period, macd_slow + macd_signal, …) + 2` ~1976–1977).
- **REFINE plan wording:** Vec does **not** rely only on “implicit” validity; it **explicitly** masks `long_mask[:warmup] = False` (or loop `for i in range(warmup, n)`). The gap is **alignment** live ↔ vec, not absence of vec warmup.
- **UPGRADE (Nemesis):** `sar_chop_live_bundle` runs on `h_arr` built from `_ohlc_hist` with **maxlen=320** (`indicators.py` ~248). WFO uses much longer windows. For `ma_long_period` up to ~300, live EMA/MACD/CHOP are **not the same mathematical state** as vec on the full series. Implementing `min_bars_ready` alone **does not remove this skew**; the plan should add either: (a) raise cap / make cap config-driven per mode, (b) feed live evaluation from `bar_store` tail aligned to vec length, or (c) document that champions for long-MA modes are **tail-approximate** only.

### §2 Search spaces (tuner vs WFO grid)

- **CONFIRMED drift.** Example `sar_chop`: grid uses `sar_chop_ma_long_period ∈ {100, 200}`, `max_hold_bars ∈ {8, 16, 24}` (~3210–3228); tuner allows `50–300` step 25 and `max_hold_bars 5–40` (`param_tuner.py` ~185–195). Tuner can set points **never evaluated in WFO**.
- Plan’s “tuner outside hull” assertion is **validated**; optional assertion script is **proportionate**.

### §3 WFO tie-breakers

- **CONFIRMED:** `candidates.sort(key=lambda x: x[0], reverse=True)` uses **mean score only**; `stability` is unused for ordering (`scalp_wfo.py` ~936–938).
- **External corroboration (deep research):** Practitioner material emphasizes **parameter / performance stability** across OOS windows when scores are close (e.g. [StratBase walk-forward guide](https://stratbase.ai/en/blog/walk-forward-analysis-guide), [arXiv 2602.10785](https://arxiv.org/abs/2602.10785) on double OOS). Plan §3 is **aligned** with published WFO hygiene.

### §4 Staged / time-decaying exits

- **CONFIRMED** that multiple exit mechanisms exist (`exit_reason` in vec; `check_time_stop`, `check_paper_exits`, `check_rsi_exit`, trail/breakeven in `scalp_runtime.py`). Phase 4a/4b split remains **reasonable**; no refutation.

### §5 Exit evaluation order

- **CONFIRMED** live path on candle close (excerpt): `check_time_stop` → `check_trail_and_breakeven` → `check_paper_exits` → `check_rsi_exit` (rsi_reversion) → then entries/counter (`scalp_runtime.py` ~2331–2398). Vec modes interleave exits inside `simulate_*` loops—**documentation + spot-check** per plan is appropriate; full parity is mode-specific.

### §6 Parity fingerprint

- **CONFIRMED** as net-new value; no conflict found. Some fields already surface elsewhere (`lessons.md` fee tier); centralized startup fingerprint still helps.

### §7 Liquidity prefilter

- **CONFIRMED** feasibility: `bar_store` schema includes `volume` (`bar_store.py` ~80–87). Optional hook is consistent with repo.

---

## False positives / non-issues

- **“Vec has no warmup”** — **refuted**; vec is explicit per mode.
- **“Stability is computed but unused”** — **confirmed true**; not a false alarm.

---

## Recommended plan edits (before implementation)

1. **§1:** Add workstream for **`_ohlc_hist` window vs WFO history** (not only `min_bars_ready`).
2. **§1 tests:** Include **tail-320 vs full-series** expectation for at least one long-MA mode, or assert cap ≥ configured `ma_long_period` + margin.
3. Keep §2–§7 as written, with §1 expanded per above.

---

## Sources (external)

- [StratBase — Walk-Forward Analysis guide](https://stratbase.ai/en/blog/walk-forward-analysis-guide) — stability / OOS selection.
- [arXiv:2602.10785 — walk-forward + double OOS](https://arxiv.org/abs/2602.10785) — robustness framing for parameter choice.

---

## Files traced (verification)

- [`backend/server/scalp_bot/indicators.py`](backend/server/scalp_bot/indicators.py) — `ready`, `_ohlc_hist maxlen`, `sar_chop_live_bundle` call path.
- [`backend/server/scalp_bot/signal_engine.py`](backend/server/scalp_bot/signal_engine.py) — `optimized_ready` / `iv.ready` gating.
- [`backend/server/scalp_bot/scalp_vec_backtest.py`](backend/server/scalp_bot/scalp_vec_backtest.py) — per-mode warmup; `detect_signals_sar_chop`; `build_default_grid`.
- [`backend/server/scalp_bot/scalp_wfo.py`](backend/server/scalp_bot/scalp_wfo.py) — candidate sort.
- [`backend/server/scalp_bot/param_tuner.py`](backend/server/scalp_bot/param_tuner.py) — `TUNABLE_PARAMS`.
- [`backend/server/scalp_bot/scalp_runtime.py`](backend/server/scalp_bot/scalp_runtime.py) — exit/order of operations on candle close.
- [`backend/server/scalp_bot/bar_store.py`](backend/server/scalp_bot/bar_store.py) — `volume` column.
