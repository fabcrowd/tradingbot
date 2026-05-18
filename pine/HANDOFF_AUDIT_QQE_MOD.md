# Handoff: qqe_mod Bot Logic Audit

**Target reviewer:** LLM managing the trading bot Python codebase  
**Source module:** `backend/server/scalp_bot/scalp_vec_backtest.py` (`detect_signals_qqe`, `qqe_live_bundle`)  
**Related:** `indicator_warmup.py`, `signal_engine.py`, `strategies.md` mode #8  
**Audit date:** 2026-05-16  
**Implementation follow-up:** 2026-05-17

---

## Maintainer resolutions (2026-05-17)

| Finding | Resolution |
|---------|------------|
| **A** — Inline cross vs `_touch_crossover` | **Fixed.** Uses `_touch_crossover` / `_touch_crossunder` + 50-line threshold. Behavior unchanged. |
| **B** — Warmup understates QQE convergence | **Fixed.** `w = max(rsi + smooth + wilders + 5, atr); return w + 2` (~52-bar prefix with defaults). Test: `test_qqe_mod_prefix_matches_indicator_chain`. |
| **C** — `qqe_live_bundle` full recompute | **Deferred.** Documented trade-off (correctness vs O(n) per tick). Revisit if mode becomes live champion. |
| **D** — Trail seed `smooth_rsi[start]` | **Canonical.** Matches Pine `qqe_mod` block (`trail := sm` at `wildLen`). Documented in detector docstring. |
| **E** — `prepend=smooth_rsi[0]` zero-bias | **Fixed.** `prepend=np.nan` for unbiased Wilder seed (`nanmean` skips bar 0). Pine still uses `bar_index==0 ? 0` — minor export drift; bot is reference for WFO. |
| **F** — No `warmup >= n` diag | **Fixed.** `_scalp_vec_bt_diag_warn` when `warmup >= n` (`SCALP_VEC_BT_DIAG=1`). |
| **G** — `detect_signals_qqe` vs `qqe_mod` | Cosmetic; cross-mode convention. |
| **H** — Shape validation | **Fixed** in `detect_signals_qqe` (OHLC length check). |
| **I** — Live bundle lacks `qqe_bull` | **Deferred.** No consumer today; `qqe_live_bundle` returns flip masks only (same as audit X3). |

**Grid restoration:** Prerequisites **B, D (confirmed), E, F** satisfied. Safe to add `qqe_mod` to `build_default_grid()` per `HANDOFF_RESTORE_OFF_GRID_WFO_MODES.md` after monitor cycle if desired.

---

## Original audit summary (2026-05-16)

- **Math / spec:** QQE chain (RSI → EMA → Wilder |diff| → trail) and cross + 50 threshold match `strategies.md` mode #8.
- **Blocker was B:** prior warmup (~29 bars) allowed signals while trail/atr_rsi still converging from partial-window seed.
- **Conservative warmup:** `rsi_period + qqe_smoothing + wilders_period + 5` margin after trail seed bar (`wilders_period = 2*rsi - 1`).

---

## Open questions (answered)

1. **Biased-seed convergence characterized?** → Not empirically in-repo; addressed by conservative warmup bump (Finding B).  
2. **Trail seed canonical?** → **Yes** — `smooth_rsi[wilders_period]` matches Pine port.  
3. **`prepend` zero deliberate?** → **No** — fixed to `np.nan` for unbiased seed (Finding E).  
4. **Need `qqe_bull` in live bundle?** → **Not currently**; add when UI/stop logic needs position state.

---

## Next audit

Per `REVIEW_HANDOFF_FOR_LLM.txt`: **`sar_chop`** (final mode in queue).
