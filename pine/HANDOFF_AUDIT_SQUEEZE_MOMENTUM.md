# Handoff: squeeze_momentum Bot Logic Audit

**Target reviewer:** LLM managing the trading bot Python codebase  
**Source module:** `backend/server/scalp_bot/scalp_vec_backtest.py` (`detect_signals_squeeze`)  
**Audit date:** 2026-05-16  
**Implementation follow-up:** 2026-05-17 (warmup fix, rolling helpers, diag, doc resolution)

---

## Maintainer resolutions (2026-05-17)

| Finding | Resolution |
|---------|------------|
| **A** — `squeeze_on[i-1]` only | **Deliberate.** Documented in `detect_signals_squeeze` docstring, `strategies.md`, and Pine (`squeezeOn[1]`). No `not squeeze_on[i]` gate. |
| **B** — Strict BB/KC inequalities | No change (zero-measure boundaries). |
| **C** — Inline rolling loops | **Fixed.** Uses `rolling_mean_arr`, `rolling_std_arr`, `rolling_max_arr`, `rolling_min_arr`. |
| **D** — Warmup formula | **Fixed.** `indicator_warmup.py`: `w = max(bb + mom - 1, atr); return w + 2`. Test: `test_squeeze_momentum_prefix_matches_indicator_chain`. |
| **E** — LR projection | Verified correct; no change. |
| **F** — Touch-and-cross | Valid; no change. |
| **G** — Naming shorthand | Cosmetic; convention confirmed. |
| **H** — Shape validation | **Fixed** in `detect_signals_squeeze` (OHLC length check). |
| **I** — `xdot == 0` | **Fixed.** Early return when `mom_period < 2`; `xdot` only used when `mom_period >= 2`. |
| **J** — Silent zero-signal | **Fixed.** `_scalp_vec_bt_diag_warn` when `warmup >= n` (behind `SCALP_VEC_BT_DIAG=1`). |

**Grid restoration:** Prerequisites (A documented, D, J) satisfied. Safe to add `squeeze_momentum` to `build_default_grid()` per `HANDOFF_RESTORE_OFF_GRID_WFO_MODES.md` after one monitor cycle if desired.

---

## Original audit (findings A–J)

*(Submitted 2026-05-16 — preserved for traceability.)*

### FINDING A — Squeeze gate uses `squeeze_on[i - 1]` `[WFO][LIVE]` — **RESOLVED: deliberate**

Prior-bar squeeze + momentum cross; does not require release on bar `i`. Matches LazyBear-style “fire while recent compression context” used in this repo’s Pine port.

### FINDING B — Strict inequality at BB/KC — **No action**

### FINDING C — Three scalar loops — **Fixed** (shared rolling helpers)

### FINDING D — Warmup too small — **Fixed** (`bb + mom - 1` chain)

### FINDING E — LR formula — **Verified**

### FINDING F — Touch-and-cross — **Valid**

### FINDING G — Function naming — **DOC only**

### FINDING H — Shape validation — **Fixed** (this mode; batch elsewhere deferred)

### FINDING I — `xdot == 0` — **Fixed** (`mom_period < 2` guard)

### FINDING J — Silent zero-signal — **Fixed** (diag pattern)

---

## Open questions (answered)

1. **Release bar required?** → **No** (design choice; documented).  
2. **Warmup formula empirical?** → Was imprecise; now derived from `bb + mom - 1`.  
3. **Live uses squeeze state separately?** → **No**; `squeeze_live_bundle` / `IndicatorValues` expose only `squeeze_long` / `squeeze_short` masks from `detect_signals_squeeze`.

---

## Next audit

Per `REVIEW_HANDOFF_FOR_LLM.txt`: **`qqe_mod`**, then **`sar_chop`**.
