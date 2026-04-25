# WFO operator playbook (quick reference)

Use this with `wfo_pair_result` / `wfo_pass_complete` in session JSONL and the dashboard **WFO pass log** + **last_wfo_pass** snapshot fields.

## Knob interaction (P&L-first order)

| Goal | Touch first | Then | Avoid |
|------|----------------|------|--------|
| More trades per train slice | **`wfo_train_hours`** ↑ or **`wfo_min_trades`** ↠ (careful) | **`wfo_top_k`** if train survivors look good but die before holdout | Lower **`wfo_min_trades`** before fixing hours/fees |
| Thicker holdout evidence | **`wfo_holdout_hours`** ↑ | **`wfo_min_holdout_trades`** < train only with intent | Tiny holdout + low min → lucky OOS |
| More independent folds | **`wfo_max_roll_windows`** ↑ and/or sensible **`wfo_step_hours`** | **`wfo_min_window_fraction`** only after folds exist | **`wfo_step_hours`** tiny on short span (correlated OOS) |
| Train gate pressure (PF/WR/DD) | **`wfo_min_profit_factor`**, **`wfo_min_win_rate`**, **`wfo_max_train_drawdown_pct`** in `config.toml` | Restart | Loosening all three + low **`wfo_min_trades`** at once |
| Cross-window persistence | **`wfo_min_window_fraction`** (primary tier) | **`wfo_top_k`** for recall | Relying on **`relaxed_quarter`** / **`any_window`** as “success” |

## Telemetry map

| Symptom | Check JSONL / UI |
|---------|-------------------|
| No bars | `skip_reason=no_bars_in_store`, readiness `span_hours` ≈ 0 |
| Tape too short | `insufficient_windows:…`, `wfo_diag.n_windows` |
| All train fails | `no_strategies_passed_train_gates`, **`wfo_diag.train_gate_diag`** (counts by reason) |
| Holdout aggregation fails | `no_candidates_after_stability_filters`, **`holdout_hit_rank`**, **`min_windows_*`** |
| Near-miss champion | `safety_gate:…`, `negative_latest_holdout_pnl`, `low_latest_holdout_pf` |
| Pass rollup | `wfo_pass_complete.by_skip_reason`, **`pairs[]`** |

## Config keys (train IS gates)

| TOML key | Default | Role |
|----------|---------|------|
| `wfo_min_profit_factor` | 0.8 | Train PF floor |
| `wfo_min_win_rate` | 0.20 | Train win-rate floor (fraction) |
| `wfo_max_train_drawdown_pct` | 30.0 | Train max DD % cap |

Restart required; not in Settings runtime patch.
