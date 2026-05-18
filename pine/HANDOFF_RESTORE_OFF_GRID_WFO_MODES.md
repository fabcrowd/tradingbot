# Handoff: Restore Four Off-Grid Modes to Default WFO Grid

**Status:** Complete (2026-05-17)  
**Designer decision:** Omissions of `ema_scalp`, `squeeze_momentum`, `qqe_mod`, `utbot_alert` were **not intentional** — all 11 registered modes should compete in WFO.

---

## Rollout state

| Mode | Grid | Notes |
|------|------|-------|
| `utbot_alert` | ✅ | Restored 2026-05 (cycle 1); 243 rows |
| `squeeze_momentum` | ✅ | Restored 2026-05-17; 243 rows (`bb_period` × `mom_period` × exits) |
| `qqe_mod` | ✅ | Restored 2026-05-17; 243 rows (`rsi_period` × `smoothing` × exits) |
| `ema_scalp` | ✅ | Restored 2026-05-17; 243 rows (`ema_period` × `sr_bars` × exits) |

**Invariant test:** `test_build_default_grid_covers_all_registered_modes`

---

## Grid shapes (per mode)

Each restored mode uses a **3×3×3×3×3 = 243** compact sub-grid (same exit sweep as `utbot_alert` / `supertrend`):

- **Shared exits:** `max_hold_bars` ∈ {5, 10, 20}, `atr_stop_mult` ∈ {0.75, 1.0, 1.5}, `atr_tp_mult` ∈ {1.5, 2.5, 4.0}
- **ema_scalp:** `ema_scalp_period` ∈ {15, 20, 26}, `ema_scalp_sr_bars` ∈ {6, 8, 12}
- **squeeze_momentum:** `squeeze_bb_period` ∈ {15, 20, 25}, `squeeze_mom_period` ∈ {8, 12, 16}; `bb_mult=2.0`, `kc_mult=1.5` fixed
- **qqe_mod:** `qqe_rsi_period` ∈ {12, 14, 18}, `qqe_smoothing` ∈ {4, 5, 7}; `qqe_factor=4.238` fixed

---

## Known caveats

- **ema_scalp:** WFO scores `simulate_trades_bidir` (ATR stop/TP); live uses S/R-based stops — documented in `strategies.md` §3. Path A (align live to bidir) still optional hygiene.

---

## `wfo_top_k` — watch first, decide later (2026-05-17)

**Current:** `wfo_top_k = 80` in `config.toml` (unchanged after grid restoration).

| Metric | Value |
|--------|--------|
| Old grid | ~4,300 rows |
| New grid | ~5,019 rows (+17%) |
| 80 / old field | ~1.9% selection rate |
| 80 / new field | ~1.6% selection rate |

**Why not bump preemptively:** Top-K is **global** across all 11 modes. With 80 slots, average ~7 per mode; with 95, ~8.6. A single mode whose grid rows dominate train scores can crowd out others regardless of a small K bump.

**If bumping later:**

| Goal | Suggested `wfo_top_k` |
|------|------------------------|
| Restore old global selection rate (~1.9%) | **95–100** |
| Target ~10 holdout-retest slots per mode on average | **~110** |

**Decision procedure (after first post-restoration WFO at K=80):**

1. Run WFO refresh with all 11 modes on-grid; keep `wfo_top_k = 80`.
2. Inspect holdout representation per mode:
   - **Train:** log `scalp_wfo: … window[N] — X/Y scored (mode:count, …)` — train-gate survival per mode.
   - **Holdout:** map `param_window_scores` keys (`pi`) → `grid[pi].mode`; count grid points per mode that reach `min_windows` and pass `_aggregate_holdout_candidates` stability/mean/DD gates.
   - **Dashboard:** `wfo_mode_scoreboard` in WFO diag (`_wfo_mode_holdout_scoreboard_rows`) — best qualified row per mode.
3. **Bump** only if **3+ modes** consistently have **&lt;3** holdout-eligible candidates. **Stay at 80** if distribution is roughly even. **Do not bump** if new modes show **0** train passes — fix gates/grid/data first.

---

## References

- `HANDOFF_AUDIT_SQUEEZE_MOMENTUM.md`, `HANDOFF_AUDIT_QQE_MOD.md`
- `build_default_grid()` in `scalp_vec_backtest.py`
