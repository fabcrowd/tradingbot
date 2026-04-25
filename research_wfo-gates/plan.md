# Research plan: Are Fabcrowd Arceus scalp WFO gates too strong?

## Question

Compared to published WFO / OOS practice and practitioner guidance, are the **hard gates and aggregation rules** in `backend/server/scalp_bot/scalp_wfo.py` (plus `[scalp]` config defaults) **overly strict**, explaining frequent “no champion” outcomes?

## Subtopics (non-overlapping)

1. **Trade-count / sample size** — minimum trades per IS/OOS segment; statistical credibility.
2. **Profit factor, win rate, drawdown floors** — typical thresholds; risk of rejecting all vs overfitting.
3. **Cross-window / robustness rules** — how many folds must “pass”; relation to Walk-Forward Efficiency (WFE) and consistency narratives.

## Repo anchors (fixed for comparison)

| Mechanism | Code / config |
|-----------|----------------|
| Train gates | `min_trades` (config `wfo_min_trades`), `_gate_fail_reason`: PF ≥ `min_profit_factor` (default **0.8**), WR ≥ **20%**, max DD ≤ **30%** unless `WFOConfig` overridden — `_wfo_config_from_scalp_cfg` does **not** pass PF/WR/DD from TOML; dataclass defaults apply |
| Holdout trade floor | `min_holdout_trades` or same as `min_trades` when 0 |
| Post-train aggregation | Same `pi` must score holdout on ≥ **`max(1, int(n_windows * wfo_min_window_fraction))`** folds (primary); retries **`relaxed_quarter`** then **`any_window`** (min_windows=1); optional `min_stability_ratio`, `min_mean_score`, `max_avg_dd_pct` from config |
| Latest holdout | `require_positive_latest_holdout`, `min_latest_holdout_pf` from TOML |
| Live `config.toml` (user tree) | `wfo_min_trades = 3`, `wfo_min_holdout_trades = 0`, `wfo_min_mean_score = -999`, stability off by default in `scalp_config` |

## Output

- `findings_trades.md`, `findings_pf_wr_dd.md`, `findings_cross_window.md`
- `report.md` — conclusions, cited links, recommendation on which gate is “strongest” vs literature
