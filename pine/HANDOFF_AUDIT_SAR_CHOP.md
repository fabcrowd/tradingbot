# Handoff: sar_chop Bot Logic Audit

**Target reviewer:** LLM managing the trading bot Python codebase  
**Source module:** `backend/server/scalp_bot/scalp_vec_backtest.py` (`detect_signals_sar_chop` + helpers)  
**Audit date:** 2026-05-16  
**Implementation follow-up:** 2026-05-17

---

## Maintainer resolutions (2026-05-17)

| Finding | Resolution |
|---------|------------|
| **A** — PSAR init price-derived | **Canonical.** Wilder seed on bars 0–1; not the supertrend/utbot `direction=1` pattern. Documented in `_parabolic_sar` docstring. |
| **B** — Lucid `_parabolic_sar(close, close, …)` | **By design.** Docstring notes `high is low` for close-based PSAR; no HL validation (intentional). |
| **C** — `chop_threshold` 68 vs 38.2 | **Consistent by layer.** Repo default **68** (`scalp_config`, detector, Pine input, WFO grid includes 38.2/50/61.8/**68**). Champions may use **38.2** from WFO/tuner — per-symbol, not a code bug. `param_tuner` sweeps 30–70. |
| **D** — Short MA asymmetry vs spec | **Not a bug.** Current `strategies.md` §11, Pine (`close < ms` + `< ml`), Python (`c < ma_short` + `< ma_long`), and `test_sar_chop_short_condition.py` all match. Long uses **MA stack** (`ma_short >= ma_long`); short uses **price below MAs** (easier shorts under golden-cross). Old audit quoted outdated spec text (`MA(50) <= MA(200)`). |
| **E** — TR duplicated in `_chop_index` | **Deferred.** Cross-mode `true_range` extraction (batched with `atr()` refactor). |
| **F** — `_utbot_trail_flips` vs `detect_signals_utbot` | **Deferred.** Single shared UT state machine when utbot init work is revisited. |
| **G** — Helper split rationale | **Documented.** `_sar_chop_common_mats` consumed by `detect_signals_sar_chop`, `sar_chop_diagnostic_frame`, `sar_chop_signal_dump`. |
| **H** — PSAR seed gap edge case | **Canonical Wilder** — document only. |
| **I** — Silent zero-signal | **Fixed.** `_scalp_vec_bt_diag_warn` when `n < 3` or `warmup >= n`. |
| **J** — Function naming | Direct map `detect_signals_sar_chop` ↔ `sar_chop`. |
| **K** — Shape validation | **Fixed** in `detect_signals_sar_chop`. |

**Review package:** `MODE_RANGES` extended to lines **1978–2551** (includes `sar_chop_diagnostic_frame` + `sar_chop_live_bundle`). Regenerate with `python pine/package_review_folders.py`.

---

## Original audit summary (2026-05-16)

- Most complex mode: PSAR flip + CHOP + MA stack + MACD + optional Lucid/UT gates.
- **Highest blast radius:** on WFO grid + `auto_mode_fallback`.
- Synthetic `test_sar_chop_short_condition` brittleness: **data property** (needs PSAR flips + CHOP pass), not detector bug.

---

## Open questions (answered)

1. **Short MA stack vs price?** → **Intentional asymmetry**; spec and code agree.  
2. **CHOP 68 everywhere?** → Default 68; grid/tuner/champions may use stricter values.  
3. **Other `_sar_chop_common_mats` consumers?** → `sar_chop_diagnostic_frame`, `sar_chop_signal_dump`.  
4. **Validate `high >= low` in PSAR?** → Skip when Lucid passes same series twice.

---

## Audit program status

**All 11 registered modes audited.** Next phase: batched cross-mode fixes (shape validation sweep, `true_range`, `_touch_crossover` adoption, live-bundle perf) per `REVIEW_HANDOFF_FOR_LLM.txt`.
