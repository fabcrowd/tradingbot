/**
 * Mouseover (title) copy for Settings → WFO & param tuner. Keep under ~1.2k chars for browser tooltips.
 * Each starts with RECOMMENDED so operators can scan quickly.
 */

export const SCALP_WFO_TT = {
  wfo_interval:
    "RECOMMENDED: 900–3600s between WFO passes (wall-clock, not candle size); 60s floor always applies.\n\nBenefit: fresher champions after new bars without hammering CPU.\nRisk: very low values burn CPU and can churn champions on noise.",

  param_tuner_interval:
    "RECOMMENDED: 120–300s.\n\nBenefit: periodic knob refinement on the active mode.\nRisk: too low wastes CPU; overrides can fight WFO if enabled.",

  wfo_max_roll_windows:
    "RECOMMENDED: 8–12 on volatile perps.\n\nBenefit: more folds = stabler mean score across regimes.\nRisk: more history + longer backfill; marginal gains past ~12.",

  wfo_top_k:
    "RECOMMENDED: 30–60.\n\nBenefit: fewer good modes dropped before holdout.\nRisk: high K multiplies holdout CPU per window.",

  wfo_train_same_calendar_day_boost:
    "RECOMMENDED: 0 (off) until you trust recency bias; try 0.15–0.5 if you want “today’s tape” to dominate train ranking.\n\nBenefit: emphasizes same-UTC-day trades near the train end.\nRisk: overfits to one session; pairs badly with Sharpe-style objectives when weights are on.",

  wfo_train_hours:
    "RECOMMENDED: 4–12h on 1–5m bars (scale with interval).\n\nBenefit: enough trades for train gates.\nRisk: too short fails min_trades; too long dilutes recent regime.",

  wfo_holdout_hours:
    "RECOMMENDED: 1–3h (often < train).\n\nBenefit: OOS slice that still gets trades with a looser holdout min.\nRisk: too short = thin OOS evidence.",

  wfo_step_hours:
    "RECOMMENDED: match holdout or ~½ train for rolling folds; huge value = single fold.\n\nBenefit: controls fold overlap.\nRisk: tiny step + many windows = heavy CPU.",

  wfo_min_trades:
    "RECOMMENDED: 15–30 on active 5m tape; raise if you see flaky grid winners.\n\nBenefit: statistical gate on train.\nRisk: too high → no champion in quiet markets.",

  wfo_min_holdout_trades:
    "RECOMMENDED: 0 (=same as train) or 50–70% of train min (e.g. train 20 → holdout 10–14) when holdout hours are tight.\n\nBenefit: fewer holdout skips from clock-window pressure.\nRisk: lower OOS sample → use PF / PnL gates to compensate.",

  backtest_funding_enabled:
    "RECOMMENDED: off until you have a stable signed bps/hour estimate from venue funding polls.\n\nBenefit: closer perp sim.\nRisk: wrong sign/magnitude mis-ranks champions.",

  backtest_funding_bps:
    "RECOMMENDED: 0 unless you measured; small ±0.5–3 bps/h for stress.\n\nBenefit: stress carry.\nRisk: unrealistic carry dominates tiny edge strategies.",

  fee_assumption_revision:
    "RECOMMENDED: bump +1 whenever you change fee_bps*, fee_usd*, or order_type in config.\n\nBenefit: audit trail + optional auto-champion clear.\nRisk: forgetting bump leaves stale mental model (file still updates after WFO).",

  fee_tier_volume_source:
    "RECOMMENDED: “exchange” for Coinbase perps live; “manual” if API blocked or you want a frozen baseline.\n\nexchange: polls Coinbase Advanced GET transaction_summary (FUTURE/PERP variants).\nmanual: uses the USD baseline field; optional session bot-fill add-on.",

  fee_tier_poll_interval:
    "RECOMMENDED: 900s default; min 60s.\n\nBenefit: fresh trailing volume without rate limits.\nRisk: too aggressive may hit REST limits on shared keys.",

  fee_tier_30d_volume_usd:
    "RECOMMENDED: leave empty when source=exchange; when manual, enter the ~30d USD volume shown in Coinbase Advanced today.\n\nBenefit: honest baseline when API is unavailable.\nRisk: drifts vs reality if you never refresh.",

  fee_tier_add_bot_fill:
    "RECOMMENDED: off (exchange path already includes your venue volume). On manual only: adds abs(fill USD) from this bot since process start to your baseline (rough session delta, not full 30d).\n\nBenefit: see momentum toward next tier during a session.\nRisk: not exchange-accurate; resets on restart.",

  fee_tier_auto_apply_rates:
    "RECOMMENDED: on for Coinbase perps with volume_source=exchange.\n\nBenefit: maker/taker bps in memory track Coinbase **derivatives** transaction_summary fee_tier — startup poll + periodic poll; WFO, vec sim, and param tuner read live config so they follow tier moves.\nFlat $/contract/leg (NFA/clearing on the fee page) is NOT in the API — keep fee_usd_per_contract_per_leg in config.toml in sync with Coinbase.\nRisk: config.toml can drift from live bps until you edit the file; turn off to freeze maker/taker to TOML only.",

  fee_auto_invalidate:
    "RECOMMENDED: off until you automate fee edits; then on for safety.\n\nBenefit: clears champions when on-disk fee snapshot ≠ config.\nRisk: surprise cold start until WFO reruns.",

  param_tuner_require_champion:
    "RECOMMENDED: on.\n\nBenefit: tuner refines only after WFO anchors mode.\nRisk: no Nemesis tuner path before champion.",

  param_tuner_override:
    "RECOMMENDED: off unless you explicitly want tuner to override WFO mode.\n\nBenefit: escape hatch after regime break.\nRisk: splits authority; champion JSON may not match live mode.",

  wfo_objective:
    "RECOMMENDED: leave as configured; pick objective in config.toml + restart.\n\nBenefit: stable champion selection metric across sessions.\nRisk: changing mid-session without restart leaves UI label out of sync with the engine until restart.",

  wfo_pnl_first_promotion:
    "When on (config.toml + restart): WFO always ranks by mean holdout simulated USD and skips heavy promotion gates (stability, latest-window PF/PnL, beat-prior, min score delta, vol-armed WFO tighten, wide param-delta safety).\n\nBenefit: champion tracks best backtest $ more directly.\nRisk: choppier promotions; keep daily_loss / optional require_champion_to_trade as live guardrails.",

  wfo_roll_span:
    "RECOMMENDED: treat as telemetry — widen train/step/windows if this exceeds your bar-store comfort.\n\nBenefit: shows how many hours of tape rolling WFO expects.\nRisk: very large span can slow backfills and each WFO pass.",

  wfo_assume_taker_fee:
    "RECOMMENDED: off unless you want conservative WFO/tuner sim vs live hybrid (empirical market bursts).\n\nBenefit: grid scores use taker bps per leg while order_type stays limit.\nRisk: champions look worse than maker-only sim; turn off for default maker alignment.",

  wfo_forward_min_trades:
    "RECOMMENDED: 8–15 live closed trades before forward demotion ratio is trusted.\n\nBenefit: fewer noisy demotions.\nRisk: slow to demote a bad champion.",

  wfo_forward_demotion_threshold:
    "RECOMMENDED: -0.5 (default): demote when live PnL vs holdout expectation ratio falls below this.\n\nBenefit: auto safety valve.\nRisk: too tight churns modes; too loose keeps a broken champion.",

  funding_warn_bps_per_hour:
    "RECOMMENDED: 5–20 bps/h for alert threshold on parsed get_product funding (best-effort).\n\nBenefit: heads-up when carry is large.\nRisk: API field scaling may differ; confirm on Coinbase UI; alerts throttle ~30m per product.",

  empirical_market_promotion:
    "RECOMMENDED: on with defaults while tuning limits; off if you never want automatic market entries.\n\nBenefit: arms short market bursts after TTL + missed favorable move pattern.\nRisk: pays taker on those entries; WFO still uses order_type fees unless wfo_assume_taker_fee is on.",

  empirical_market_ttl_cancel_arms_promotion:
    "RECOMMENDED: usually off — use the missed-move pattern path only.\n\nWhat it does: on every entry limit TTL cancel, immediately grants market-entry slots for the next signal(s), without waiting for favorable drift vs your cancelled limit and without the pattern arm’s re-arm cooldown.\n\nBenefit: faster aggression when you believe any non-fill means you must cross.\nRisk: more taker fees and slippage when the limit simply didn’t get hit for benign reasons (queue, chop). Independent of config.toml until you Apply; restart reloads file.",

  apply_runtime:
    "RECOMMENDED: Apply after edits; values stay in memory until restart reloads config.toml.\n\nBenefit: instant tuning without file edits.\nRisk: drift vs on-disk config if you forget to persist changes.",

  fee_tier_refresh:
    "RECOMMENDED: after fee-tier moves or when poll_error appears (Coinbase perps only).\n\nBenefit: one forced GET transaction_summary without waiting for the poll interval.\nRisk: extra REST call; shares rate limits with other Advanced Trade calls.",

  fee_tier_live_snapshot:
    "Live telemetry from the scalp runtime: resolved display volume, last poll time, Coinbase raw summary (when available), and bot session add-on (manual path only).",

  wfo_action_log:
    "RECOMMENDED: skim after each WFO pass or when warmup stalls on “no champion”.\n\nBenefit: newest-first lines show per-pair outcome + skip_reason tail without opening session JSONL.\nRisk: log is in-memory only — cleared on restart; full detail stays in session JSONL (`wfo_pair_result.wfo_diag`).",
} as const;
