# Handoff: param_tuner.py audit

**Target:** `backend/server/scalp_bot/param_tuner.py`, `run_tuner_cycle`  
**Audit date:** 2026-05-17  
**Tags:** `[TUNER]`

---

## Maintainer resolutions (2026-05-17)

| ID | Finding | Resolution |
|----|---------|------------|
| **A** | High frequency vs WFO (~96:1) | **Documented.** Tuner refines params; WFO selects mode/champion. `param_tuner_require_wfo_champion=true` by default. |
| **B** | Mode override vs champion lock | **By design.** `param_tuner_allow_mode_override_champion=false` (default) → `champion_tuner_mode_resolution` keeps WFO mode. Tests: `test_param_tuner_champion_override.py`. |
| **C** | TUNABLE_PARAMS vs grid bounds | **Partial overlap.** Compact WFO grid does not sweep every tuner knob (e.g. daviddtech WAE stack). Test `test_param_tuner_grid_bounds.py` checks **shared exit + mode-specific knobs present in grid** only; daviddtech non-grid knobs documented as wider. |
| **D** | fill_model / slippage alignment | Tuner uses `_params_from_pair_config` + `effective_slippage_bps_for_sim` path in runtime when `slippage_bps` passed; WFO uses `build_default_grid(fill_model=...)`. **Aligned when runtime passes slip resolver** (same as WFO loop). |
| **E** | `scalp_tuner_state.json` drift | Operational — freeze at PF≥threshold via `_aggressiveness_from_pf`. No code change. |
| **F** | Best-mode pick across all modes when no champion lock | Only when override enabled or no champion; default cycle scores all modes but runtime applies champion lock before `apply_tuner_result`. |

**Tests added:** `test_param_tuner_grid_bounds.py`, existing `test_param_tuner_champion_override.py`.

---

## Forward validation vs tuner (related)

- **Forward demotion** (`_check_champion_forward_validation`): Gate 2 requires better lookback mode.  
- **Circuit breaker** (`_check_live_circuit_breaker`): optional; `wfo_live_circuit_breaker_enabled=false` default.  
- **Forward reconciliation** (P0): telemetry only; `forward_reconciliation` session event.

---

## Operator knobs

| Config | Default | Note |
|--------|---------|------|
| `param_tuner_interval_sec` | 900 | 15 min |
| `param_tuner_allow_mode_override_champion` | false | Keep false unless intentional |
| `param_tuner_require_wfo_champion` | true | Blocks tuner until champion exists |
