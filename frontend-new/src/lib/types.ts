export type BookLevel = { price: number; volume: number };

export type PairSnapshot = {
  symbol: string;
  best_bid: number;
  best_ask: number;
  mid_price: number;
  spread: number;
  microprice: number;
  bid_levels: BookLevel[];
  ask_levels: BookLevel[];
  inventory_base: number;
  inventory_quote: number;
  position_cost_quote: number;
  threat_level: string | null;
  book_imbalance: number;
  mid_velocity_bps: number;
  tick_volatility: number;
  spread_blow_out_ratio: number;
  trailing_stop_active: boolean;
  trailing_high_pnl: number;
  pair_realized_pnl: number;
  btd_active: boolean;
  realized_vol: number;
  last_book_update_ts: number;
  [key: string]: unknown;
};

export type ActiveOrder = {
  cl_ord_id: string;
  pair_key: string;
  side: string;
  price: number;
  qty: number;
  filled_qty: number;
};

export type ExchangeErrorEvent = {
  id: string;
  ts: number;
  level: "error" | "warning";
  title: string;
  detail: string;
  source: string;
  acknowledged: boolean;
};

/** Server ring-buffer row: alerts, actions, etc. (`backend/server/ui_event_log.py`). */
export type UiLogEntry = {
  id: string;
  ts: number;
  kind: string;
  level: string;
  title: string;
  detail: string;
  source: string;
  persistent?: boolean;
  exchange_error_id?: string;
  meta?: Record<string, unknown>;
};

export type Fill = {
  timestamp: number;
  pair_key: string;
  side: string;
  price: number;
  qty: number;
  fee: number;
  pnl_delta: number;
  [key: string]: unknown;
};

export type Snapshot = {
  pairs: Record<string, PairSnapshot>;
  active_orders: ActiveOrder[];
  recent_fills: Fill[];
  total_pnl: number;
  total_trades: number;
  fill_event_count: number;
  win_rate: number;
  spread_captured: number;
  pnl_curve: [number, number][];
  running: boolean;
  mode: string;
  spread_bot_enabled?: boolean;
  active_pair_key: string;
  last_cancel_reason: Record<string, string>;
  learner_info: Record<string, unknown>;
  optimizer_info: Record<string, unknown>;
  last_fill_ts: Record<string, number>;
  volume_30d: number;
  session_start_pnl: number;
  session_start_ts: number;
  peak_pnl: number;
  risk_halted: boolean;
  risk_halt_reason: string;
  oco_pairs: Record<string, unknown>;
  twap_orders: Record<string, unknown>;
  last_order_reject_reason: string;
  order_reject_count: number;
  exchange_errors?: ExchangeErrorEvent[];
  exchange_errors_unacked?: number;
  /** Last K UI log rows for Logs tab hydration (matches server ring buffer tail). */
  ui_log_tail?: UiLogEntry[];
  scalp?: ScalpSnapshot;
  [key: string]: unknown;
};

export type PairConfig = {
  symbol: string;
  spread_bps: number;
  order_size: number;
  max_inventory: number;
  fee_bps: number;
  fee_schedule: string;
  cycle_ms: number;
  inventory_skew_scale: number;
  spread_floor_bps: number;
  bootstrap_half_spread_bps: number | null;
  bootstrap_until_sell_trades: number;
  [key: string]: unknown;
};

export type Alert = {
  id: string;
  level: "error" | "warning" | "info" | "success";
  title: string;
  detail: string;
  source: string;
  ts: number;
  persistent?: boolean;
  exchange_error_id?: string;
  /** When set, matches ``UiLogEntry.id`` from server ``broadcast_alert``. */
  server_id?: string;
};

export type ScalpPosition = {
  symbol: string;
  /** Config pair key (multiple legs can share the same symbol). */
  pair_key?: string;
  /** Unique client order id for this entry leg. */
  entry_cl_ord_id?: string;
  /** "long" | "short" — shorts require venue=coinbase_perps */
  direction?: string;
  /** Strategy that opened the position (live attribution). */
  strategy_mode?: string;
  /** Unix seconds when the leg was opened (aligns to candle `t` for chart markers). */
  entry_ts?: number;
  entry: number;
  stop: number;
  tp: number;
  qty: number;
  contract_size?: number;
  status: string;
  age_sec: number;
  unrealized_pnl?: number;
  mark_price?: number;
  leverage?: number;
  liquidation_price?: number;
  funding_rate?: number | null;
};

export type ScalpIndicators = {
  candles: number;
  ready: boolean;
  ema_fast: number;
  ema_slow: number;
  rsi: number;
  prev_rsi: number;
  atr: number;
  vwap: number;
  ema_bullish: boolean;
  rsi_bullish: boolean;
  rsi_oversold: boolean;
  rsi_sell_trigger: boolean;
  vwap_bullish: boolean;
  volume_confirmed: boolean;
  ema_scalp: number;
  ema_scalp_cross_bull: boolean;
  high_8: number;
  low_8: number;
  macd_line: number;
  macd_signal: number;
  macd_cross_bull: boolean;
  t3?: number;
  hlc_green?: number;
  hlc_red?: number;
  wae_up?: number;
  wae_down?: number;
  adx?: number;
  optimized_ready?: boolean;
  optimized_long_setup?: boolean;
  optimized_short_setup?: boolean;
  /** Global WFO regime: faster optimizer cadence + shorter bootstrap while true. */
  wfo_risk_on_active?: boolean;
  /** Human-readable label when ``wfo_risk_on_active`` (e.g. "WFO risk on"). */
  wfo_risk_on_label?: string | null;
};

export type ScalpCandle = {
  t: number; o: number; h: number; l: number; c: number; v: number;
};

export type IndicatorOverlayPoint = {
  t: number;
  ema_fast: number;
  ema_slow: number;
  t3: number;
  vwap: number;
  /** MACD line − signal (same bar); rendered as lower-pane histogram, not full-width price rules. */
  macd_hist?: number;
};

export type ScalpTrade = {
  pair_key: string;
  direction?: string;
  strategy_mode?: string;
  entry_ts: number;
  exit_ts: number;
  entry_price: number;
  exit_price: number;
  qty: number;
  pnl: number;
  reason: string;
  simulated: boolean;
};

export type WarmupStepData = {
  key: string;
  label: string;
  status: "pending" | "running" | "done" | "failed";
  pct: number;
  detail: string;
  retry_count: number;
  error: string;
  /** Server-incremented tick while the step runs (proves snapshots are still updating). */
  heartbeat?: number;
};

export type ScalpWarmup = {
  phase: string;
  enabled: boolean;
  bars_collected?: Record<string, number>;
  bars_required?: number;
  progress_pct?: number;
  champion_found?: boolean;
  wfo_triggered?: boolean;
  elapsed_sec?: number;
  startup_steps?: WarmupStepData[];
};

/** Progress rows for Settings: standby / configuration / go live. */
export type ScalpOperatorFlowStep = {
  key: string;
  label: string;
  pct: number;
  state: "pending" | "running" | "done";
};

export type ScalpOperatorFlow = {
  visible: boolean;
  title?: string;
  overall_pct?: number;
  steps: ScalpOperatorFlowStep[];
};

/** One-shot UI event for fullscreen modal (dedupe by ``seq``). */
export type ScalpOperatorFlowEvent = {
  seq: number;
  kind: string;
  detail?: string;
};

/** Operator standby / go-live gate (Settings tab). */
export type ScalpOperator = {
  standby: boolean;
  prep_busy?: boolean;
  require_manual_go_live?: boolean;
  flow?: ScalpOperatorFlow | null;
  flow_seq?: number;
  flow_event?: ScalpOperatorFlowEvent | null;
  /** High-level startup phase: standby | warming_up | primed | live */
  startup_phase?: string;
  can_begin_warmup?: boolean;
  can_go_live?: boolean;
  warmup_steps?: WarmupStepData[];
};

/** Read-only policy from server ``[scalp]`` for Settings explanations. */
export type ScalpSessionPolicy = {
  warmup_enabled: boolean;
  /** First configured pair's bar size; for warm-up hour estimate before candles load. */
  default_candle_interval_minutes?: number;
  warmup_min_bars: number;
  warmup_require_champion: boolean;
  warmup_max_hours: number;
  wfo_enabled: boolean;
  /** Seconds between scheduled WFO passes (server floor 60s; risk-on can shorten further). */
  wfo_interval_sec?: number;
  wfo_train_hours: number;
  wfo_holdout_hours: number;
  wfo_step_hours: number;
  /** Holdout phase: how many top train-scoring grid combos to validate per rolling window. */
  wfo_top_k?: number;
  wfo_objective?: string;
  /** When true, server forces mean holdout ``total_pnl`` and relaxes strict WFO promotion gates. */
  wfo_pnl_first_promotion?: boolean;
  /** Seconds between param-tuner cycles (server floor 30s). */
  param_tuner_interval_sec?: number;
  /** Rolling WFO: number of train→holdout folds in the sliding band. */
  wfo_max_roll_windows?: number;
  /** Extra train-score weight for trades on the UTC day of the train window end (0 = off). */
  wfo_train_same_calendar_day_boost?: number;
  /** Hours of bar history used for rolling WFO (derived). */
  wfo_roll_span_hours?: number;
  /** Minimum closed trades on each training slice for a grid point to score. */
  wfo_min_trades?: number;
  /** Holdout-only minimum trades (0 = use wfo_min_trades). */
  wfo_min_holdout_trades?: number;
  /** Minimum train-slice profit factor to score a grid point (WFO IS hard gate). */
  wfo_min_profit_factor?: number;
  /** Minimum train-slice win rate (0–1) to score. */
  wfo_min_win_rate?: number;
  /** Maximum train-slice drawdown % to score. */
  wfo_max_train_drawdown_pct?: number;
  /** Include constant perps funding in bar backtests / WFO / tuner. */
  backtest_funding_enabled?: boolean;
  /** Signed bps per hour on notional while flat; >0 means longs pay. */
  backtest_funding_bps_per_hour?: number;
  /** Bump when fee tier assumptions change; tracked vs data/scalp_fee_assumption_state.json. */
  scalp_fee_assumption_revision?: number;
  /** Rolling fee-tier volume baseline when source is manual (USD). When source is exchange, Coinbase poll fills display. */
  fee_tier_30d_volume_usd?: number | null;
  /** ``exchange`` = poll Coinbase transaction_summary; ``manual`` = baseline (+ optional session bot notional). */
  fee_tier_volume_source?: string;
  /** Minimum seconds between automatic exchange polls (60–86400). */
  fee_tier_poll_interval_sec?: number;
  /** Manual path only: add abs(bot fill USD) this session to baseline for display. */
  fee_tier_add_bot_fill_notional?: boolean;
  /** When true with exchange volume source, Coinbase poll updates maker/taker bps in memory for WFO/sim. */
  fee_tier_auto_apply_exchange_fee_rates?: boolean;
  /** On startup, remove champions if persisted fee snapshot differs from config. */
  scalp_auto_invalidate_champion_on_fee_change?: boolean;
  /** Do not run param tuner until a WFO champion exists for the symbol. */
  param_tuner_require_wfo_champion?: boolean;
  /** Allow tuner to switch active mode away from WFO champion when it disagrees. */
  param_tuner_allow_mode_override_champion?: boolean;
  /** WFO/tuner vec sim uses taker bps per leg (stress vs live hybrid market bursts). */
  wfo_assume_taker_fee?: boolean;
  /** Resolved per-leg bps for WFO grid after ``wfo_assume_taker_fee``. */
  wfo_fee_bps_sim_per_leg?: number;
  wfo_forward_min_trades?: number;
  wfo_forward_demotion_threshold?: number;
  wfo_forward_outperform_factor?: number;
  volatility_armed_param_tuner_interval_mult?: number;
  funding_warn_bps_per_hour?: number;
  empirical_market_promotion_enabled?: boolean;
  /** When true, each entry TTL cancel immediately arms N market entries (bypasses missed-move pattern + arm cooldown). */
  empirical_market_ttl_cancel_arms_promotion?: boolean;
  empirical_market_missed_move_bps?: number;
  empirical_market_miss_eval_window_sec?: number;
  empirical_market_min_pattern_in_window?: number;
  empirical_market_pattern_window_sec?: number;
  empirical_market_promotion_entries?: number;
  empirical_market_promotion_cooldown_sec?: number;
  /** First daily loss breach also sets scalp portfolio halt (snapshot + JSONL). */
  daily_loss_set_scalp_halt?: boolean;
  slip_calibration_enabled?: boolean;
  slip_calibration_ema_alpha?: number;
  slip_calibration_min_samples?: number;
  slip_calibration_floor_bps?: number;
  slip_calibration_cap_bps?: number;
  slip_calibration_mode?: string;
};

/** Scalp-native portfolio halt (independent of MM risk_halted). */
export type ScalpPortfolioRisk = {
  scalp_risk_halted: boolean;
  scalp_risk_halt_reason: string;
  scalp_risk_halted_ts: number;
  scalp_entries_blocked: boolean;
  mm_spread_bot_enabled?: boolean;
  mm_risk_halted?: boolean;
};

export type ChampionSummary = {
  mode: string;
  score: number;
  stability: number;
  sharpe: number;
  sortino: number;
  calmar: number;
  recovery_factor: number;
  profit_factor: number;
  max_drawdown_pct: number;
  win_rate: number;
  trade_count: number;
  buy_hold_return: number;
  expectancy: number;
};

/** Vector backtest per strategy mode over bar_store lookback (train+holdout hours). */
export type StrategyLookbackModeRow = {
  win_rate: number;
  trades: number;
  pnl: number;
  /** PnL as % of window start price (flat / unweighted backtest). */
  return_pct?: number;
  weighted_win_rate?: number;
  weighted_pnl?: number;
  /** PnL as % of window start price (recency-weighted backtest; pairs with weighted win %). */
  weighted_return_pct?: number;
  profit_factor?: number;
};

export type StrategyLookbackSnapshot = {
  lookback_hours: number;
  updated_ts: number;
  pairs: Record<string, Record<string, StrategyLookbackModeRow>>;
};

export type WfoPairReadiness = {
  span_hours: number;
  bar_count: number;
  windows: number;
  progress_pct: number;
};

/** Per-strategy mode row from the last WFO holdout aggregation (best grid row per ``mode``). */
export type WfoModeScoreboardRow = {
  mode: string;
  pi?: number;
  holdout_windows?: number;
  mean_holdout_score?: number;
  stability?: number | null;
  mean_holdout_total_pnl?: number;
  sum_holdout_total_pnl?: number;
  mean_max_drawdown_pct?: number;
  mean_holdout_trades?: number;
  qualified_champion_pool?: boolean;
  is_wfo_champion_row?: boolean;
  is_wfo_champion_mode?: boolean;
  objective?: string;
};

/** One row from the last WFO pass (dashboard + JSONL parity). */
export type WfoLastPassPairSummary = {
  pair_key: string;
  outcome: string;
  skip_reason?: string | null;
  gate_reason?: string | null;
  n_windows?: number | null;
  span_hours?: number | null;
  bar_count?: number | null;
  /** Holdout scoreboard: one best row per strategy mode (``optimize_pair`` diagnostics). */
  wfo_mode_scoreboard?: WfoModeScoreboardRow[];
};

/** Result of the most recent ``run_once`` (per-pair outcomes + skip histogram). */
export type WfoLastPass = {
  ts: number;
  /** WFO objective name for the pass (e.g. ``total_pnl``, ``sharpe``). */
  objective?: string;
  champion_pairs: string[];
  n_pairs: number;
  champion_count: number;
  by_skip_reason: Record<string, number>;
  pairs: WfoLastPassPairSummary[];
};

/** Walk-forward optimizer UI: data buildup + next scheduled grid pass */
export type WfoUi = {
  enabled: boolean;
  interval_sec?: number;
  seconds_until_next?: number;
  last_run_ts?: number;
  loop_started_at?: number;
  overall_progress_pct: number;
  data_progress_pct: number;
  ui_progress_pct: number;
  champion_active: boolean;
  required_span_hours: number;
  total_load_hours: number;
  train_hours: number;
  holdout_hours: number;
  step_hours: number;
  pairs: Record<string, WfoPairReadiness>;
  max_roll_windows?: number;
  /** Latest ``run_once`` summary (null until first pass completes). */
  last_wfo_pass?: WfoLastPass | null;
  /** Newest-last lines from the in-process WFO deque (read-only). */
  wfo_action_log?: string;
};

/** Self-tuner state per mode within a pair. */
export type TunerModeInfo = {
  win_rate: number;
  pnl: number;
  trades: number;
  profit_factor: number;
  aggressiveness: string;
  adjustments: string[];
  params_changed?: Record<string, number>;
};

/** Self-tuner state per pair. */
export type TunerPairState = {
  best_mode: string;
  best_win_rate: number;
  best_pnl: number;
  best_trades: number;
  frozen: boolean;
  aggressiveness: string;
  adjustments: string[];
  all_modes: Record<string, TunerModeInfo>;
  timestamp: number;
};

export type ScalpSpotAccount = { currency: string; available: number };
export type ScalpFuturesBalance = {
  /** Coinbase futures / perps wallet equity in USD (balance_summary.total_usd_balance). */
  total_usd_balance?: number;
  buying_power: number;
  unrealized_pnl: number;
  daily_realized_pnl: number;
  /** Margin locked in open perp positions. */
  initial_margin: number;
  /** Free margin not tied to positions (may differ from buying_power). */
  available_margin: number;
  /** Collateral held for resting futures orders (balance_summary.total_open_orders_hold_amount). */
  open_orders_hold_usd?: number;
};
export type ScalpBalances = {
  spot_accounts?: ScalpSpotAccount[];
  /** Sum of available USDC + USD in linked spot accounts (for transfers / spot leg). */
  spot_usd_available?: number;
  futures?: ScalpFuturesBalance;
};

/** Slim resting-order row from Coinbase reconcile (perps + dated futures + brackets). */
export type ScalpVenueOpenOrder = {
  product_id: string;
  side: string;
  status: string;
  order_type: string;
  client_order_id: string;
  order_id: string;
  filled_base: number;
  /** Resting limit (0 if unknown or not a limit leg). */
  limit_price?: number;
  /** Stop / trigger price when applicable. */
  trigger_price?: number;
  /** Order size in base (contracts) when provided by list_orders. */
  base_size?: number;
};

/** Fee-tier / 30d volume telemetry (Coinbase perps + manual baseline). */
export type ScalpFeeTierSnapshot = {
  volume_source: string;
  display_volume_usd: number | null;
  manual_baseline_usd: number | null;
  bot_fill_usd_session: number;
  exchange: Record<string, unknown> | null;
  last_poll_ts: number;
  poll_error: string | null;
  poll_interval_sec: number;
  auto_apply_exchange_fee_rates?: boolean;
  effective_maker_bps?: number;
  effective_taker_bps?: number;
  /** Flat NFA/clearing USD per contract per leg from config (not in Coinbase summary API). */
  fee_usd_per_contract_per_leg?: number;
};

export type ScalpSnapshot = {
  /** False while `ScalpRuntime` is not wired yet (dashboard starts before exchange init). */
  runtime_attached?: boolean;
  /** High-level startup phase: standby | warming_up | primed | live */
  startup_phase?: string;
  /** Server fell back to placeholder after snapshot() raised. */
  snapshot_error?: boolean;
  enabled: boolean;
  /** Coinbase CDE: ``coinbase_perps`` */
  venue?: string;
  /** Resolved fee-tier volume + last Coinbase poll metadata. */
  fee_tier?: ScalpFeeTierSnapshot;
  sim_mode: boolean;
  /** Max open legs across all scalp pairs; ``<= 0`` in config means unlimited. */
  max_concurrent_positions?: number;
  operator?: ScalpOperator;
  /** Portfolio halt / MM risk mirror for operator controls. */
  portfolio_risk?: ScalpPortfolioRisk;
  session_policy?: ScalpSessionPolicy;
  balances?: ScalpBalances;
  /** Resting Coinbase Advanced Trade orders on configured scalp products (from reconcile). */
  exchange_open_orders?: ScalpVenueOpenOrder[];
  /** Every OPEN order returned by Coinbase for this key (includes dated futures, brackets, manual). */
  exchange_open_orders_all?: ScalpVenueOpenOrder[];
  /** Subset of ``exchange_open_orders_all`` whose product_id is not in ``[scalp.pairs.*].symbol``. */
  exchange_open_orders_outside_pairs?: ScalpVenueOpenOrder[];
  warmup: ScalpWarmup;
  /** pair_key -> exchange symbol (e.g. CDE product id); used with ``champions``. */
  pair_symbols?: Record<string, string>;
  /** [scalp] default when ``strategy_mode`` is auto and no champion exists. */
  auto_mode_fallback?: string;
  active_modes?: Record<string, string>;
  mode_sources?: Record<string, string>;
  champion?: ChampionSummary | null;
  /** WFO champion metrics keyed by exchange symbol (see ``pair_symbols``). */
  champions?: Record<string, ChampionSummary> | null;
  /** Per-mode backtest win % for current pair params (refreshed ~60s). */
  strategy_lookback?: StrategyLookbackSnapshot | null;
  /** Self-tuning optimizer state (per-pair param adjustments). */
  tuner?: Record<string, TunerPairState> | null;
  wfo?: WfoUi | null;
  trader: {
    open_positions: Record<string, ScalpPosition>;
    open_count: number;
    daily_pnl: number;
    reserved_capital: number;
    trade_history: ScalpTrade[];
    sim_mode: boolean;
    /** Live empirical limit→market promotion state (optional on older servers). */
    empirical_market?: {
      enabled: boolean;
      promotion_remaining: Record<string, number>;
      active_watch_count: number;
      pattern_buffer_len: number;
    };
  };
  indicators: Record<string, ScalpIndicators>;
  candles?: Record<string, {
    closed?: ScalpCandle[];
    live: ScalpCandle | null;
    interval: number;
    indicator_overlay?: IndicatorOverlayPoint[];
  }>;
  orderbooks?: Record<string, {
    bids: [number, number][];
    asks: [number, number][];
  }>;
  /** Volume/ATR-triggered WFO + bootstrap aggressiveness; ``mode_label`` when active. */
  regime_risk_on?: {
    enabled: boolean;
    /** When true, WS tick/ohlc path can extend risk-on before bar close. */
    live_enabled?: boolean;
    active: boolean;
    mode_label: string | null;
    until_ts: number;
    pair_reasons: Record<string, string[]>;
    effective_bootstrap_hours: number;
    effective_wfo_sleep_sec: number | null;
  };
  /** Bootstrap vs champion / tuner reconciliation hints from runtime. */
  nemesis?: {
    champion_bootstrap_advisory?: Record<string, unknown>;
    no_champion_last_resolution?: Record<string, unknown>;
  };
  /** Operator-facing config vs live-behavior mismatches (e.g. partial TP on live). */
  config_warnings?: string[];
  /** Live slip calibration telemetry (WFO/tuner use ``effective_bps`` when enabled). */
  slip_calibration?: {
    enabled: boolean;
    samples: number;
    ema_bps: number | null;
    effective_bps: number;
    config_bps: number;
    mode: string;
  };
};

export type ConfigSnapshot = {
  mode: string;
  /** When false, spread-MM stack is off; spread-MM dashboard actions are disabled. */
  spread_bot_enabled?: boolean;
  pair_keys_for_trading: string[];
  pairs: Record<string, PairConfig>;
  per_trade_profitability: boolean;
  min_total_pnl_usd: number | null;
  daily_profit_target_usd: number | null;
  daily_loss_limit_usd: number | null;
  max_drawdown_pct: number | null;
  learner_enabled: boolean;
  learner_interval_sec: number;
  learner_min_samples: number;
  learner_max_daily_adjustments: number;
  learner_lookback_max_age_sec: number;
  learner_loss_lookback_sells: number;
  learner_widen_on_avg_loss: boolean;
  optimizer_enabled: boolean;
  optimizer_interval_sec: number;
  optimizer_train_hours: number;
  optimizer_holdout_pct: number;
  optimizer_min_fills: number;
  optimizer_max_delta_spread_bps: number;
  optimizer_max_delta_size_pct: number;
  optimizer_objective: string;
  adaptive_tuning: boolean;
  adaptive_target_win_pct: number;
  adaptive_win_band_pct: number;
  adaptive_spread_step_bps: number;
  adaptive_spread_floor_bps: number;
  adaptive_spread_ceiling_bps: number;
  adaptive_min_sample_sells: number;
  adaptive_lookback_sells: number;
  adaptive_interval_sec: number;
  momentum_hold_sells: number;
  momentum_hold_sec: number;
  btd_enabled: boolean;
  btd_step_bps: number;
  btd_size_multiplier: number;
  btd_sma_short: number;
  btd_sma_long: number;
  btd_levels: number;
  trailing_stop_enabled: boolean;
  trailing_stop_pct: number;
  take_profit_usd: number | null;
  oco_enabled: boolean;
  oco_stop_bps: number;
  oco_tp_bps: number;
  twap_enabled: boolean;
  twap_slice_count: number;
  twap_duration_sec: number;
  threat_quoting_pause: boolean;
  threat_velocity_bps: number;
  threat_critical_velocity_bps: number;
  threat_spread_multiplier: number;
  threat_imbalance_threshold: number;
  threat_spread_blowout_ratio: number;
  depeg_threshold_bps: number;
  min_quote_half_spread_bps: number;
  pain_floor_decay_hours: number;
  decay_start_sec: number;
  decay_interval_sec: number;
  decay_step_bps: number;
  presets?: { name: string; label: string; description: string; recommended_for: string[] }[];
  pair_archetypes?: Record<string, string>;
  [key: string]: unknown;
};
