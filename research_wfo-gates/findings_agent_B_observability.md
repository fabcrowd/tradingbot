# Agent B — WFO observability: JSONL vs snapshot

## Current telemetry

- **Session JSONL:** `SessionLogger.log_scalp(subtype, **payload)` → `{"event":"scalp","subtype":...}`. Periodic dashboard-shaped state: `scalp_snapshot` (lean `snapshot()`, drops candles).
- **`wfo_pass_start`:** From `ScalpWalkForwardOptimizer.run_once` — `pairs`, `train_hours`, `holdout_hours`, `step_hours`, `interval_sec`, `objective`.
- **`wfo_pair_result`:** Per pair — `outcome` is `champion_saved` or `no_champion`, optional `skip_reason`, and **`wfo_diag`** (bars, windows, merged **`train_gate_diag`**, promotion/min_windows on failure paths). Champion-only: `mode`, `score`, `windows_evaluated`, `holdout_metrics`, `objective`.
- **`wfo_pass_complete`:** `champion_pairs`, `n_pairs` — no per-pair skip rollup.
- **`optimize_pair` skips:** Reasons include `no_bars_in_store`, `insufficient_windows`, `no_strategies_passed_train_gates`, `no_candidates_after_stability_filters`, safety gates, `negative_latest_holdout_pnl`, `low_latest_holdout_pf`. Per-window **train** `gate_diag` is **LOG.info** only, not JSONL.
- **Champion dict on disk:** `wfo_promotion_tier`, `wfo_min_windows_used`, `windows_evaluated`, etc. — **not** on failed passes.
- **Dashboard `snapshot()["wfo"]`:** When WFO enabled — data readiness (`progress_pct`, spans, `windows`, etc.) + scheduler (`interval_sec`, `last_run_ts`, …). **No** last-pass skip reason, tier, or gate histogram.

## Gaps vs ideal P&L diagnosis

- `outcome="no_change"` conflates “no champion passed” with “unchanged champion”; `skip_reason` not always obvious to operators.
- Snapshot lacks **last WFO diagnostic** per pair (why promotion failed).
- `wfo_pass_complete` lacks structured rollup (pair → skip_reason, histogram).
- Train `gate_diag` not in JSONL — hard to distinguish “grid never trades” vs “holdout sparse.”
- Promotion path invisible on **failure** (only successful champions carry tier fields on disk).
- Cross-source friction: correlate JSONL + `scalp_champion.json` + server logs.

## Minimal additions (ranked)

1. **`wfo_pair_result`:** explicit outcomes (`no_champion` / `champion_saved` / `unchanged`), always attach `skip_reason` when no write, add `n_windows` / `span_hours` / `bar_count` from the same pass.
2. **`snapshot["wfo"].last_wfo_pass`:** timestamp, per-pair `{ skip_reason, n_windows, bar_span_h }`, optional champion list mirror.
3. **`wfo_pass_complete` rollup:** `champion_count`, `by_skip_reason`, optional `pairs[]` summary.
4. **JSONL `wfo_train_gate_summary` (sampled or final):** aggregate `gate_diag` counts — unlocks train vs holdout failure modes without log tailing.
5. **Failed-pass near-miss:** `promotion_tier`, `min_windows_effective`, `candidates_after_filter` when a candidate existed but final checks failed.
6. **Lower priority:** pass-scoped WFO config fingerprint on `wfo_pass_start` / complete for config drift across sessions.
