# Deferred follow-up: `cooldown_bars` and state-classifier modes (WFO scoring)

**Audience:** Bot maintainer / LLM implementing WFO grid changes  
**Priority:** After current per-mode audit queue; not a detector bug  
**Related:** `HANDOFF_WFO_CHAMPION_AND_PARAM_TUNER.md`, `hull_suite` audit (continuous HMA state)

---

## Issue (one paragraph)

`hull_suite` (and any mode whose `long_mask` / `short_mask` stay **True for many consecutive bars**) does **not** fire one entry per bar in production or Pine: both only call `strategy.entry` when **flat**, and `simulate_trades_bidir` enforces the same via `next_allowed = exit_bar + cooldown_bars`. WFO’s `build_default_grid()` sweeps `hull_period`, `max_hold_bars`, and ATR stop/TP for `hull_suite`, but **`cooldown_bars` is never swept** — every grid row uses `ParamSet`’s default **`cooldown_bars = 1`**. So holdout scores for `hull_suite` champions encode an implicit, fixed re-entry throttle that is as load-bearing as `max_hold_bars` for trade frequency, yet is invisible in the mode’s documented knobs. **Edge detectors** (`supertrend`, `utbot_alert`, `macd_scalp`, `ema_momentum`, etc.) are less sensitive because masks are sparse on flips; **`hull_suite` is the confirmed case.**

---

## Maintainer decision (pick one)

1. **Expand WFO grid** — add `cooldown_bars` (e.g. `{0, 1, 2}`) to `build_default_grid()` for `hull_suite` only, or for all modes in the default grid (heavier CPU).
2. **Document convention** — state in `strategies.md` + `build_default_grid` comment that `cooldown_bars=1` is fixed for WFO ranking of `hull_suite` (TV/Pine parity: flat-only entries).
3. **Change detector semantics** — optional future: emit entries only on HMA state **edges** (`_rising_edge` on trend state); would align mask sparsity with flip modes but **breaks current Pine parity** (`hma > hma[2]` every bar while flat).

---

## Cross-check during remaining audits

| Mode | Mask style (expected) | `cooldown_bars` sensitivity |
|------|------------------------|---------------------------|
| `hull_suite` | Continuous state | **High** — confirmed |
| `supertrend` | Direction flip | Low |
| `utbot_alert` | Direction flip | Low (sparse; on WFO grid 2026-05) |
| `qqe_mod` | TBD in audit | TBD |
| `sar_chop` | PSAR flip + gates | Low–medium |

---

## Severity axis (post-handoff)

**🟡 for auto-grid `hull_suite` only** — affects which `hull_suite` ParamSet wins holdout, not whether code crashes. Not 🔴 unless a specific symbol’s live `cooldown` differs materially from `1` and you care about champion fidelity for `hull_suite`.
