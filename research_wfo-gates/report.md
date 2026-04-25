# WFO gates: internet norms vs Fabcrowd Arceus `scalp_wfo` — are they too strong?

## Bottom line

- **Train/holdout trade floors at `wfo_min_trades = 3` are *not* too strong by mainstream vendor guidance**; they are **much weaker** than TradeStation’s FAQ recommendation for credible WFA (**~30 trades per OOS run** in their example framing).  
- **Default profit-factor / win-rate / train drawdown gates** in `WFOConfig` (**0.8 PF, 20% WR, 30% max DD on train**) are **moderate floors**, not aggressive “elite only” filters relative to informal practitioner articles.  
- **The most demanding rule relative to what web sources *explicitly* encode** is the **custom aggregation rule**: after **per-window top-K train selection**, a single grid index `pi` must accumulate holdout scores on at least **`max(1, int(n_windows * wfo_min_window_fraction))` folds** (primary tier), with **fallback tiers** `relaxed_quarter` then `any_window` (min_windows=1) if no candidates — see `backend/server/scalp_bot/scalp_wfo.py` (`_min_holdout_windows_from_fraction`, `_aggregate_holdout_candidates`, `optimize_pair`). That still asks for **cross-regime persistence of the same parameter tuple**, stricter than typical “WFE ~50%” narratives that compare **aggregate** IS vs OOS.

**Verdict:** If you see frequent `no_candidates_after_stability_filters` or sparse `param_window_scores` despite bars/windows being healthy, **raise `wfo_min_trades` for statistical honesty** when holdouts are thick enough; to **reduce false negatives**, the **first knobs are fold design and data span** (`wfo_max_roll_windows`, `wfo_step_hours`, meaningful holdout hours/trades), then **`wfo_top_k`** as a recall-vs-CPU tradeoff. Treat **`relaxed_quarter` / `any_window`** on the champion as a **quality warning** (see `findings_agent_A_wfo_coupling.md`), not an ops win.

## Repo gate inventory (effective path)

| Gate | Typical effective value | Notes |
|------|-------------------------|--------|
| `min_trades` (train) | `config.toml`: **3** | `_wfo_config_from_scalp_cfg` passes this through |
| `min_holdout_trades` | Falls back to **3** when `wfo_min_holdout_trades = 0` | ```452:456:c:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_wfo.py``` |
| Train PF / WR / DD | **Not** from TOML — **WFOConfig dataclass defaults** unless extended | `min_profit_factor=0.8`, `min_win_rate=0.2`, `max_drawdown_pct=30` in ```83:96:c:\Users\daroo\Desktop\Repos\tradingbot-1\backend\server\scalp_bot\scalp_wfo.py``` |
| `min_mean_score` | **-999** in repo `scalp_config` / TOML | Effectively **off** |
| `min_stability_ratio` | **-999** | Effectively **off** unless changed |
| `max_avg_dd_pct` | **999** in TOML | Effectively **off** |
| Cross-window | **`wfo_min_window_fraction`** → `min_windows` per `pi`; fallbacks **quarter** / **1** | Strong structural filter; tier on champion: `wfo_promotion_tier` |

## External guidance (cited)

1. [TradeStation WFO FAQ — minimum trades](https://help.tradestation.com/10_00/eng/tswfo/topics/frequently_asked_questions.htm) — recommends **≥ ~30 trades per OOS run** (ideal) and large cumulative counts across multi-run WFAs.  
2. [Interactive Brokers Campus — walk forward analysis](https://www.interactivebrokers.com/campus/ibkr-quant-news/the-future-of-backtesting-a-deep-dive-into-walk-forward-analysis/) — stresses multi-segment testing and **consistency**.  
3. [QuantStart — successful backtesting I](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-I/) — methodology / overfitting awareness.  
4. [TradeZella — profit factor](https://www.tradezella.com/blog/profit-factor) — PF interpretation (edge vs breakeven framing).  
5. [r/algotrading — how I use walk-forward optimization](https://www.reddit.com/r/algotrading/comments/qjrj7b/this_is_how_i_use_walkforward_optimization/) — practitioner diversity; no single standard for “half of folds same parameters”.

## Gaps / low confidence

- **Crypto 15m scalp + CDE fee model** may invalidate apples-to-apples comparison to daily-equity WFO examples.  
- Academic papers (Bailey et al. on backtest overfitting) were **not** deeply fetched in this pass; conclusions lean on **vendor + quant education + forum** layers.  
- **Structured `skip_reason` in snapshot** is still a gap; see `findings_agent_B_observability.md` for a ranked minimal-telemetry plan (JSONL + `snapshot["wfo"]`).

## Suggested next step (ops)

Mine `wfo_pair_result` in session JSONL for `skip_reason`. Mapping:

- `no_strategies_passed_train_gates` → train micro-sample / fees / grid too sparse for **3** trades.  
- `no_candidates_after_stability_filters` → **cross-window + optional stability/mean** path.  
- `insufficient_windows` → history span vs `wfo_roll_span_hours`.

---

## Deep-research agent synthesis (repo + framework)

Three readonly explore passes produced:

| File | Focus |
|------|--------|
| `findings_agent_A_wfo_coupling.md` | `wfo_min_window_fraction`, top-K, folds, promotion tiers, P&L-first config order |
| `findings_agent_B_observability.md` | JSONL vs `snapshot()["wfo"]` gaps; ranked telemetry additions |
| `findings_agent_C_settings_ui.md` | Where to add `wfo_action_log` in Settings + types/CSS patterns |

**P&L-first recommendations (consolidated):**

1. **Objective and holdout thickness first** — align `wfo_objective` with what you optimize live; ensure holdout windows produce enough trades that scores are low-variance before loosening cross-window rules or raising `wfo_top_k`.
2. **Prefer primary-tier champions** — aim for enough folds and sensible `wfo_min_window_fraction` so promotion rarely needs `relaxed_quarter` / `any_window`; if logs show frequent last-resort promotion, expect **higher live variance**, not “fixed no champion.”
3. **Fold independence vs CPU** — avoid tiny `wfo_step_hours` on short span (overlapping holdouts inflate evidence without reducing live risk); use `wfo_max_roll_windows` and step deliberately.
4. **Observability before more tuning** — implement Agent B’s top items (`wfo_pair_result` clarity, `last_wfo_pass` in snapshot, pass rollup) so every WFO pass is **auditable**; that shortens the loop from “no champion” to **which gate** (train vs holdout vs stability vs latest-holdout safety).
5. **Dashboard** — implemented: `snapshot["wfo"].wfo_action_log` + `last_wfo_pass` in `WfoTunerRuntimeSection` (Settings); full diagnostics on `wfo_pair_result.wfo_diag` in session JSONL.

*Generated as part of deep-research workflow: `plan.md` + `findings_*.md` in this folder.*
