# Agent A — WFO coupling: min_windows × top-K × folds

## Summary

- **Per-fold pipeline:** For each rolling `(train, holdout)` pair, every grid index `pi` is evaluated on **train**; only rows passing **train** hard gates (`min_trades`, PF, win rate, max DD) get a finite objective score. Those scores are sorted and **only the top `wfo_top_k` indices** proceed to **holdout** evaluation for that fold.
- **Holdout rows per `pi`:** A holdout result is appended to `param_window_scores[pi]` only if `pi` was in the fold’s top‑K **and** holdout `trade_count >= holdout_trade_floor` (`wfo_min_holdout_trades == 0` maps to “same as train” via `WFOConfig.min_holdout_trades=None`). Otherwise that fold contributes **no** row for `pi`, so `len(param_window_scores[pi])` can stay below the global minimum even if train often ranked `pi` highly.
- **`wfo_min_window_fraction`:** Primary promotion requires each surviving `pi` to have holdout scores on at least `max(1, int(n_windows * fraction))` folds after aggregation filters (`_min_holdout_windows_from_fraction`). Lower fraction **lowers** that bar (easier to qualify, weaker cross-fold evidence).
- **Promotion tiers:** If `_aggregate_holdout_candidates` returns no one at the primary `min_windows`, the code retries with `min_windows = max(1, n_win // 4)` (`relaxed_quarter`), then as a last resort `min_windows = 1` (`any_window`). The chosen tier is written on the champion as `wfo_promotion_tier` / `wfo_min_windows_used`.
- **`wfo_max_roll_windows` + `wfo_step_hours`:** Together with train/holdout hours they define roll span (how much history is loaded/sliced). More roll depth and/or a **smaller** step yields **more** folds when the tape is long enough—more holdout samples per `pi` if it keeps re-entering top‑K, but **more** train+holdout backtests per pass.
- **`wfo_top_k`:** Scales **holdout** work per fold roughly linearly (`top_k` holdout evals vs `len(grid)` train evals). Raising K increases the set of `pi` that can accumulate holdout rows and can rescue “good but not train‑#1” modes, at a direct CPU cost; lowering K does the opposite.
- **Statistical vs promotion tradeoff:** Tighter fraction / fewer fallback tiers (by having enough candidates at primary) and adequate holdout trade floor push selection toward **broader OOS support**; looser fraction, `any_window`, very high K, or very small step without more history can favor **noisier** winners (lucky windows) even before live forward demotion.

## Code anchors

- `backend/server/scalp_bot/scalp_wfo.py` — `wfo_roll_span_hours` / `wfo_effective_roll_span_hours`, `rolling_windows`, `_aggregate_holdout_candidates`, `_min_holdout_windows_from_fraction`, `optimize_pair` (train_scores → top_k_indices → `param_window_scores`, holdout floor, promotion tiers, champion pick).
- `backend/server/scalp_bot/scalp_runtime.py` — `_wfo_config_from_scalp_cfg` (TOML → `WFOConfig`).
- `config.toml` — `[scalp]` `wfo_*` keys.

## Config-first recommendations (P&L, not “more champions”)

1. **Align `wfo_objective` with live edge** (e.g. `total_pnl` vs Sharpe-style); mismatched objectives invite good backtest scores that do not match realized P&L after fees.
2. **Keep holdout economically meaningful** before raising `wfo_top_k` or lowering `wfo_min_window_fraction`: hours, `wfo_min_trades`, optional explicit `wfo_min_holdout_trades`.
3. **Prefer staying in `primary` promotion:** raise `wfo_min_window_fraction` or add folds / coverage so aggregation rarely falls through to `relaxed_quarter` / `any_window`; treat tier downgrades as a **quality red flag**, not success.
4. **`wfo_step_hours` vs `wfo_max_roll_windows`:** favor partly independent holdouts over tiny steps on short span (high correlation + high CPU). `wfo_step_hours >= train+holdout` is deliberate single-window mode.
5. **`wfo_top_k`:** tune last as compute vs recall—raise only if strong modes die just outside top‑K on train.
6. **`wfo_interval_sec`:** operational refresh rate, not a substitute for fold design.

## Risks (loosening → live P&L)

- Lower `wfo_min_window_fraction` or reliance on `any_window`: champions from **one or few** holdout slices; regime shift → drawdowns.
- Very large `wfo_top_k`: weak train fits get holdout rows; overfit / lucky OOS paths if other gates are loose.
- Very small `wfo_step_hours` with bounded history: **overlapping** holdouts inflate window count without independent evidence.
- Raising `wfo_max_roll_windows` without stationarity care: old folds may **dilute** current-edge ranking.
