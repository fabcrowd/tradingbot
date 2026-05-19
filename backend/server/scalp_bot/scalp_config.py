"""Config dataclass for the scalp bot — parsed from [scalp] section of config.toml."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

LOG = logging.getLogger(__name__)


@dataclass
class ScalpPairConfig:
    # Exchange symbol: Coinbase CDE product id e.g. "SLP-20DEC30-CDE"
    symbol: str
    interval: int = 5               # candle interval in minutes (live default 5m)
    # Strategy mode: "auto" = champion mode for symbol, else auto_mode_fallback.
    # Manual: "daviddtech_scalp", "ema_momentum", "ema_scalp", "macd_scalp", "rsi_reversion",
    # "supertrend", "squeeze_momentum", "qqe_mod", "utbot_alert", "hull_suite", "sar_chop"
    strategy_mode: str = "auto"
    # When strategy_mode is "auto" and no WFO champion row exists yet — default to sar_chop.
    # WFO will replace this with its champion mode if/when one scores better for the symbol.
    auto_mode_fallback: str = "sar_chop"
    # EMA momentum params
    ema_fast: int = 5               # fast EMA period (tuned for 5m charts)
    ema_slow: int = 13              # slow EMA period (tuned for 5m charts)
    rsi_period: int = 9             # RSI period
    atr_period: int = 14            # ATR period for stop sizing
    volume_ma_period: int = 20      # rolling average period for volume spike detection
    volume_mult: float = 1.5        # volume must be > this × rolling average to confirm
    atr_stop_mult: float = 1.0      # stop distance = ATR × this
    # TP distance = ATR × this (single stage here; staged TP is vec/live policy, not extra knobs).
    atr_tp_mult: float = 1.5        # take-profit distance = ATR × this
    risk_pct: float = 0.02          # fraction of scalp capital to risk per trade
    min_signals: int = 2            # minimum confluence signals required (ema_momentum mode)
    signal_cooldown_sec: float = 15.0   # min seconds between entries
    loss_cooldown_sec: float = 30.0     # recovery window before new entries after a loss
    min_candles_required: int = 20  # wait for this many candles before trading
    # Rolling OHLC history cap for live bundles (0 = auto ``max(320, sar_chop_ma_long+50)``).
    ohlc_hist_max_bars: int = 0
    # Time stop: exit after N bars if stop/TP not hit (bar count, not wall clock).
    max_hold_bars: int = 15         # bar count (15 × 5m = 75m wall time at default interval); vec/live parity
    # RSI reversion params
    rsi_buy_threshold: float = 10.0     # buy when RSI <= this
    rsi_sell_threshold: float = 50.0    # sell when RSI >= this (long exit)
    rsi_short_threshold: float = 70.0   # short entry when RSI >= this (perps)
    # EMA scalp params (Tony's EMA Scalper)
    ema_scalp_period: int = 20          # single EMA period for cross detection
    ema_scalp_sr_bars: int = 8          # lookback for support/resistance levels
    # MACD scalp params (Scalp Pro — Ehlers super-smoother MACD)
    macd_fast_len: int = 8              # fast super-smoother period
    macd_slow_len: int = 10             # slow super-smoother period
    macd_signal_len: int = 8            # signal line smoothing period
    # Optimized Strategy (daviddtech_scalp params)
    t3_length: int = 7
    t3_vfactor: float = 0.7
    hlc_close_period: int = 5
    hlc_low_period: int = 13
    hlc_high_period: int = 34
    adx_period: int = 14
    adx_threshold: float = 20.0
    wae_sensitivity: float = 150.0
    wae_fast_len: int = 20
    wae_slow_len: int = 40
    wae_bb_len: int = 20
    wae_bb_mult: float = 2.0
    # Supertrend
    supertrend_period: int = 10
    supertrend_factor: float = 3.0
    # Squeeze Momentum
    squeeze_bb_period: int = 20
    squeeze_bb_mult: float = 2.0
    squeeze_kc_mult: float = 1.5
    squeeze_mom_period: int = 12
    # QQE Mod
    qqe_rsi_period: int = 14
    qqe_factor: float = 4.238
    qqe_smoothing: int = 5
    # UT Bot Alert
    utbot_atr_period: int = 10
    utbot_atr_mult: float = 1.0
    # Hull Suite (TV Hull Suite Strategy default length)
    hull_period: int = 38
    # SAR + CHOP (TV "5 min bot scalper" decode)
    sar_start: float = 0.02
    sar_increment: float = 0.02
    sar_max: float = 0.2
    sar_chop_ma_fast_period: int = 7
    sar_chop_ma_long_period: int = 200
    sar_chop_ma_short_period: int = 50
    sar_chop_chop_period: int = 14
    sar_chop_chop_threshold: float = 68.0
    sar_chop_macd_fast: int = 12
    sar_chop_macd_slow: int = 26
    sar_chop_macd_signal: int = 9
    sar_chop_use_lucid: bool = True
    sar_chop_use_utbot_trail: bool = True
    sar_chop_utbot_atr_period: int = 10
    sar_chop_utbot_mult: float = 2.0
    # Coinbase CDE perps: underlying (BTC, SOL, …) per 1 contract (for risk / PnL)
    contract_size: float = 1.0
    # ── Break-even & trailing stop ────────────────────────────────────────────
    # Move stop to entry (+ fee buffer) once unrealised profit reaches N × ATR.
    # 0.0 = disabled.
    breakeven_atr_trigger: float = 0.0
    # How far past entry the break-even stop sits, in bps (covers round-trip fees).
    breakeven_buffer_bps: float = 5.0
    # Start trailing once unrealised profit reaches N × ATR (must be > breakeven_atr_trigger).
    # 0.0 = disabled.
    trail_atr_trigger: float = 0.0
    # Trail stop distance expressed as N × ATR from current price.
    trail_atr_distance: float = 1.0
    # ── Regime filter (legacy; ignored by signal engine as of 2026-04) ─────────
    # An outer ADX floor duplicated ``daviddtech_scalp``'s ``adx > adx_threshold`` inside
    # ``detect_signals_daviddtech`` and caused live vs WFO skew. Fields remain for TOML
    # compatibility — tune ``adx_threshold`` / champion params instead.
    regime_adx_filter: bool = False
    regime_adx_min: float = 0.0
    # ── Partial take-profit (paper/sim only) ──────────────────────────────────
    # Close partial_tp_pct of position at TP1, move stop to breakeven, let rest run.
    # False = disabled (full position exits at TP).  Live mode logs a warning.
    partial_tp_enabled: bool = False
    partial_tp_pct: float = 0.5               # fraction to close at first TP (0.5 = 50%)
    partial_tp_runner_trail_atr: float = 1.0  # trail distance for the runner, in ATR units
    # ── Correlation-aware sizing ──────────────────────────────────────────────
    # Pairs sharing the same non-empty group string are treated as correlated.
    # dollar_risk scales down by 1 / (1 + same-direction open count in group).
    # "" = disabled (each pair sized independently).
    correlation_group: str = ""


@dataclass
class ScalpBotConfig:
    enabled: bool = False
    # Coinbase Derivatives Exchange (CDE) perps only — candle feed, bar store, execution.
    venue: str = "coinbase_perps"
    pairs: dict[str, ScalpPairConfig] = field(default_factory=dict)
    # Default for pair auto_mode_fallback when not overridden per pair block (see [scalp]).
    auto_mode_fallback: str = "sar_chop"
    # Max simultaneous open legs across all scalp pairs. ``<= 0`` = no cap (only capital /
    # ``max_notional_usd_per_pair`` / exchange margin gate entries).
    max_concurrent_positions: int = 0
    daily_loss_limit_pct: float = 5.0   # halt if daily loss exceeds this % of scalp capital
    # When daily loss limit is breached: optionally enter operator standby and/or cancel resting orders.
    daily_loss_enter_standby: bool = False
    daily_loss_cancel_open_orders: bool = False
    # When True, first daily loss limit breach also sets BotState.scalp_risk_halted (explicit halt + snapshot).
    daily_loss_set_scalp_halt: bool = True
    # False = PnL counter and breach state persist for the entire bot session (no UTC midnight reset).
    # True = reset daily PnL at midnight UTC (legacy default). Set false so the loss limit is sticky
    # until operator explicitly restarts the bot.
    daily_auto_reset: bool = True
    allocated_capital_usd: float = 150.0  # USD reserved for scalp bot
    # "limit" | "market" | "hybrid" (limit + empirical market promotion when enabled)
    order_type: str = "limit"
    # Coinbase Advanced derivatives (per leg): % of notional + flat NFA/clearing per contract.
    # ``fee_bps_per_leg`` = maker (limit); ``fee_bps_taker_per_leg`` = market. WFO uses
    # ``effective_scalp_fee_bps_per_leg(self)`` from ``order_type``. 0 = fee-free promo / sim.
    fee_bps_per_leg: float = 0.0
    fee_bps_taker_per_leg: float = 7.0
    fee_usd_per_contract_per_leg: float = 0.0
    slippage_bps: float = 1.0          # estimated slippage per fill in bps
    # EMA of live entry slip from ``scalp_fill_execution`` → WFO / param tuner sim (when enabled).
    slip_calibration_enabled: bool = False
    slip_calibration_ema_alpha: float = 0.2
    slip_calibration_min_samples: int = 8
    slip_calibration_floor_bps: float = 0.0
    slip_calibration_cap_bps: float = 80.0
    # max_with_config: effective = max(slippage_bps, calibrated); replace: use calibrated only (clamped).
    slip_calibration_mode: str = "max_with_config"
    # Live Coinbase limit entries: cancel if still working after TTL (unblocks ``pending`` slots).
    entry_limit_ttl_sec: float = 0.0   # 0 = disabled
    # Live limit only: push limit through the book — long +bps, short −bps (0 = signal price only).
    entry_limit_offset_bps: float = 0.0
    # Hybrid execution: after repeated TTL cancel + favorable missed-move, arm N market entries.
    empirical_market_promotion_enabled: bool = False
    empirical_market_missed_move_bps: float = 12.0
    empirical_market_miss_eval_window_sec: float = 600.0
    empirical_market_min_pattern_in_window: int = 3
    empirical_market_pattern_window_sec: float = 86400.0
    empirical_market_promotion_entries: int = 2
    empirical_market_promotion_cooldown_sec: float = 3600.0
    # When promotion is enabled: each entry TTL cancel immediately arms N market entries for that
    # pair (next signal(s) use market via resolve_order_type). Independent of missed-move pattern.
    empirical_market_ttl_cancel_arms_promotion: bool = False
    empirical_market_ttl_cancel_promotion_entries: int = 1
    # When True, WFO / param tuner / ParamSet use taker bps for simulated round-trip fees (stress vs
    # live hybrid limit+promoted market). Does not change live order_type.
    wfo_assume_taker_fee: bool = False
    # Perps / Coinbase
    shorts_enabled: bool = False       # True when venue supports short execution (coinbase_perps)
    max_leverage: float = 1.0          # passed to exchange (1 = no amplification)
    margin_mode: str = "CROSS"         # CROSS | ISOLATED (Coinbase Advanced Trade)
    funding_warn_bps_per_hour: float = 5.0   # alert if |funding| exceeds this (rough, from polls)
    liquidation_warn_pct: float = 5.0        # warn if mark within this % of liquidation price
    max_notional_usd_per_pair: float | None = None  # cap estimated notional per open position
    rest_seed_candles: int = 100        # candles to fetch from REST on startup
    # Walk-forward optimizer (continuous full-grid evaluation)
    wfo_enabled: bool = True
    wfo_interval_sec: float = 3600.0
    # Param tuner / dashboard backtest lookback (hours); not the WFO eval window.
    wfo_train_hours: float = 6.0
    wfo_min_trades: int = 20
    wfo_objective: str = "expectancy_sqrt_n"
    # Strategy lookback: how many hours of recent data to backtest for the UI dashboard
    strategy_lookback_hours: float = 24.0
    # ── Backtest fidelity ─────────────────────────────────────────────────────
    # "close_slip" = fill at signal-bar close + slippage (original, optimistic).
    # "next_open"  = fill at next bar open + slippage (realistic; recommended).
    backtest_fill_model: str = "close_slip"
    # Constant perps funding in bar sim (signed bps/hour on notional; >0 = longs pay).
    backtest_funding_enabled: bool = False
    backtest_funding_bps_per_hour: float = 0.0
    # When True, live indicator engine uses numpy-based EMA/RSI that matches the
    # backtest's SMA-seeded logic instead of the hexital library.  Only affects
    # ema_momentum, ema_scalp, rsi_reversion modes (daviddtech_scalp already shares numpy).
    use_numpy_indicators: bool = False
    # Warmup: collect data + find a champion strategy before trading
    sim_mode: bool = False
    warmup_enabled: bool = True
    warmup_min_bars: int = 500
    warmup_require_champion: bool = True
    warmup_max_hours: float = 0.0
    # When True, startup leaves scalp in operator standby: no new entries until WS
    # ``scalp_operator_go_live`` (Settings tab). Warmup/WFO still run unless disabled.
    require_manual_go_live: bool = False
    # When True, entries are blocked until mode_source is WFO-backed (not bootstrap).
    # The pair only trades after WFO crowns a champion (or forward demotion / approved overrides).
    require_champion_to_trade: bool = True
    # Mid-bar entries: price-action triggers on WS ticks using frozen last-bar indicators
    tick_entries_enabled: bool = False
    tick_signal_cooldown_sec: float = 300.0
    # ── Regime risk-on (volume / vol-adjusted moves → faster WFO, shorter bootstrap) ──
    regime_risk_on_enabled: bool = True
    regime_volume_spike_mult: float = 3.0       # bar volume ≥ this × volume MA (3× = high-volatility event)
    regime_price_move_atr_mult: float = 1.75  # |close−prev_close| ≥ ATR × this
    regime_price_move_min_pct: float = 0.0    # optional min |Δ|% ; 0 = disabled
    regime_rsi_oversold: float = 20.0         # RSI ≤ this triggers risk-on (extreme oversold)
    regime_rsi_overbought: float = 80.0       # RSI ≥ this triggers risk-on (extreme overbought)
    # Live / tick-path regime (Coinbase ticker + candle WS) — same risk-on window
    regime_live_vol_enabled: bool = True
    regime_live_use_volume: bool = True       # forming-bar vol ≥ volume_ma × regime_volume_spike_mult
    regime_live_range_atr_mult: float = 1.75  # (high−low) ≥ ATR × this on the open candle
    regime_live_velocity_window_sec: float = 45.0  # rolling window for velocity (0 = disable velocity leg)
    regime_live_velocity_min_bps: float = 20.0   # (max−min)/mid in window, bps; 0 = off
    # Max time risk-on stays on if calm detection never runs (e.g. no ticks); refreshed per trigger.
    risk_on_hold_sec: float = 3600.0
    # Once all triggers clear (RSI back inside band, vol/ATR calm), wait this long before releasing (0 = disabled).
    risk_on_relax_after_calm_sec: float = 60.0
    risk_on_wfo_interval_scale: float = 0.25    # multiply wfo_interval_sec while risk-on
    risk_on_wfo_min_interval_sec: float = 60.0   # floor between WFO passes (still ≥60 in loop)
    risk_on_bootstrap_hours: float = 1.0        # no-champion bootstrap window (capped vs 2h default)
    risk_on_nemesis_expectancy_slack: float = 0.0   # tuner wins if t_exp > b_exp − slack
    risk_on_nemesis_min_pf: float = 0.95      # min tuner PF while risk-on (default 1.0 off)
    risk_on_size_mult: float = 1.5            # position size multiplier while regime risk-on is active
    risk_on_signal_cooldown_scale: float = 0.5  # shrink signal + tick cooldowns by this factor during risk-on
    # News-event regime risk-on (Forex Factory calendar)
    news_risk_on_enabled: bool = True
    news_pre_event_minutes: float = 15.0     # trigger risk-on this many minutes before a qualifying event
    news_post_event_minutes: float = 30.0    # minimum hold after the event time (often < risk_on_hold_sec)
    news_min_impact: str = "High"            # minimum impact level: "High", "Medium", "Low"
    news_currencies: str = "USD"             # comma-separated currency codes; empty = all
    news_calendar_refresh_sec: float = 3600.0  # how often to re-fetch the calendar
    # News front-run trade (DDG keyword scorer — no API key required)
    news_front_run_enabled: bool = True
    news_watch_window_min: float = 60.0      # start watching when event is within N minutes
    news_front_run_entry_min: float = 10.0   # enter position when event is within N minutes
    news_front_run_cutoff_min: float = 2.0   # abort if < N minutes to event (slippage risk)
    news_ai_confidence_threshold: int = 65   # minimum keyword confidence score (0-100) to trade
    news_advisor_refresh_min: float = 20.0   # re-query DDG every N minutes per event
    news_front_run_sl_atr_mult: float = 0.4  # stop-loss distance: ATR × this (tight pre-event)
    news_front_run_tp_atr_mult: float = 1.5  # take-profit distance: ATR × this
    # ── Volatility filter (execution risk-on — separate from regime / WFO risk-on) ──
    # Two-step: prime on a high-threshold volume spike (must pass volume_confirmed), then
    # confirm on the next closed bar. Arms temporary larger position risk (see volatility_exec_*).
    volatility_filter_enabled: bool = False
    volatility_spike_volume_mult: float = 4.0       # bar vol ≥ this × vol MA (strict; also needs pair volume_confirmed)
    volatility_confirm_min_volume_mult: float = 1.15  # confirm bar: vol ≥ this × vol MA
    volatility_confirm_follow_atr_mult: float = 0.35  # or |Δclose| vs spike ≥ ATR × this (0 = volume-only confirm)
    volatility_reject_bearish_climax: bool = True     # skip prime on bearish close pinned low (single big sell look)
    volatility_climax_bearish_range_frac: float = 0.22  # close in bottom N of bar range → reject
    volatility_reject_bullish_exhaust: bool = False   # skip prime on bullish close pinned high
    volatility_climax_bullish_exhaust_frac: float = 0.88  # close in top N of range (if reject enabled)
    volatility_exec_risk_mult: float = 1.25           # multiply dollar risk on new entries while armed
    volatility_exec_risk_cap: float = 2.0           # hard cap on execution_risk_mult applied in trader
    volatility_exec_hold_sec: float = 1800.0        # wall-clock seconds to stay armed after confirm
    # While volatility filter is armed: scale down entry cooldowns (faster re-entries on confirmed spikes).
    volatility_armed_tick_cooldown_scale: float = 0.5   # × tick_signal_cooldown_sec
    volatility_armed_signal_cooldown_scale: float = 0.5  # × pair signal_cooldown_sec (bar + tick path)
    volatility_armed_cooldown_floor_sec: float = 1.0    # floor after scaling (avoid zero)
    # While vol filter armed on any pair: multiply param tuner sleep (1.0 = no change; 0 = skip tuner pass).
    volatility_armed_param_tuner_interval_mult: float = 1.0
    # CDE expiry guard — blocks entries near contract expiry (liquidity thins + spreads widen)
    expiry_guard_warning_days: int = 7        # log WARNING when ≤N days to symbol expiry
    expiry_guard_block_days: int = 3          # block new entries when ≤N days to symbol expiry
    # Forward validation — auto-demote champion if live performance diverges from holdout expectation
    wfo_forward_min_trades: int = 10          # minimum live trades before demotion check activates
    wfo_forward_demotion_threshold: float = -0.5  # demote if forward_pnl / expected_pnl < this ratio
    wfo_forward_outperform_factor: float = 1.5  # Gate 2: replacement expectancy vs champion live expectancy
    wfo_forward_reconciliation_alert_pct: float = 0.30  # |1 - forward_ratio| above this → alert + log
    # Live circuit breaker (default off): demote champion to bootstrap/fallback on large forward loss.
    wfo_live_circuit_breaker_enabled: bool = False
    wfo_live_circuit_breaker_dd_mult: float = 2.0  # trip when forward_pnl < -mult * holdout max_drawdown
    wfo_live_circuit_breaker_hours: float = 24.0   # rolling window for forward PnL (via period_start)
    wfo_no_candidates_demotion_passes: int = 0  # consecutive no_candidates passes before wfo_champion demoted to bootstrap (0 = disabled)
    # WFO holdout quality gates (configurable; loose defaults let "best available" through)
    wfo_min_mean_score: float = -999.0        # minimum mean holdout score; -999 = no gate (best-available)
    wfo_min_stability_ratio: float = -999.0   # mean/std across windows; -999 = no stability gate
    wfo_require_positive_holdout: bool = False # if True, reject champion with negative holdout PnL
    wfo_min_holdout_pf: float = 0.5           # minimum holdout profit factor (0.5 = very loose)
    wfo_max_avg_dd_pct: float = 999.0         # max average drawdown % across holdout windows; 999 = no gate
    wfo_min_profit_factor: float = 0.8
    wfo_min_win_rate: float = 0.20
    wfo_max_train_drawdown_pct: float = 30.0
    # REST backfill requests eval+warmup span + this many extra hours (pagination / gap slack).
    wfo_backfill_buffer_hours: float = 24.0
    # Min seconds between successful champion disk writes per symbol (0 = off).
    wfo_champion_cooldown_sec: float = 0.0
    # Require new champion holdout score >= prior score + epsilon before overwriting champion.json.
    wfo_require_holdout_beat_prior: bool = False
    wfo_prior_beat_epsilon: float = 1e-6
    # Additional margin vs prior champion holdout score (same units as ``wfo_objective``); 0 = off.
    wfo_min_champion_score_delta: float = 0.0
    # Primary sort key for continuous champion pool: "total_pnl" | "calmar" | "sharpe_like"
    wfo_period_rank_metric: str = "total_pnl"
    wfo_pick_best_per_mode: bool = True
    wfo_continuous_eval_hours:   float = 672.0   # 28-day evaluation window
    wfo_continuous_warmup_hours: float = 168.0   # 7-day indicator warmup prefix
    wfo_continuous_min_trades:   int   = 20      # min closed trades in eval window
    # While regime risk-on: WFO sleep is at least ``wfo_interval_sec`` × this fraction (0 = off).
    risk_on_wfo_min_base_interval_frac: float = 0.5
    # Vol-armed WFO overlay is a no-op in continuous mode (fields kept for config compat).
    wfo_vol_armed_min_latest_holdout_pf: float = 0.0
    wfo_vol_armed_disallow_promotion_relaxation: bool = True
    # Pessimistic re-score on the continuous eval window before champion save.
    wfo_adverse_check_enabled: bool = False
    wfo_adverse_fill_model: str = "next_open"
    wfo_adverse_assume_taker_fee: bool = True
    wfo_adverse_min_mean_holdout_pnl: float = 0.0
    # If > 0: require adverse mean objective >= primary champion score × this ratio (primary ≤ 0 skips).
    wfo_adverse_min_objective_ratio_vs_primary: float = 0.0
    # Safety gate: max allowed delta on ATR stop/TP multipliers between consecutive champions (same mode).
    # Default WFOConfig values (1.0/1.5) are too tight for grids that explore 1.5–6.0 TP ranges.
    wfo_max_param_delta_stop: float = 1.0
    wfo_max_param_delta_tp: float = 1.5
    # Holdout champion sort within ``wfo_holdout_score_epsilon`` of top mean score (see ``scalp_wfo``).
    # Default: prefer more aggregate holdout $, then shallower DD, then activity, then stability.
    wfo_holdout_tiebreakers: tuple[str, ...] = (
        "sum_holdout_total_pnl",
        "neg_mean_max_dd_pct",
        "min_holdout_trade_count",
        "stability",
    )
    # Bucket mean score for sorting when >0 (ties within epsilon use tie-breakers).
    wfo_holdout_score_epsilon: float = 0.0
    # When True and a WFO champion row exists for the symbol, still set active mode to the
    # param tuner's grid ``best_mode`` when it differs so ``apply_tuner_result`` can run.
    # Default False: WFO champion keeps mode; tuner may skip apply on mode mismatch (see AGENTS.md).
    param_tuner_allow_mode_override_champion: bool = False
    # When True, skip param tuner entirely until a WFO champion exists for the pair's symbol.
    param_tuner_require_wfo_champion: bool = True
    # How often the fine param tuner runs per pair (seconds). Lower = more CPU, faster knob drift.
    param_tuner_interval_sec: float = 900.0
    # Require this many new closed candles per pair between tuner runs (0 = off).
    param_tuner_min_bars_between_runs: int = 0
    # Suppress tuner apply after a successful param apply (seconds; 0 = off).
    param_tuner_cooldown_sec_after_apply: float = 0.0
    # If >0, log WARNING when ``param_tuner_interval_sec`` < this multiple of bar duration (0 = off).
    param_tuner_warn_interval_below_bar_mult: float = 5.0
    # Bump when fee tier / assumptions change; persisted in data/scalp_fee_assumption_state.json
    scalp_fee_assumption_revision: int = 0
    # Operator-maintained 30d volume (USD) baseline when ``fee_tier_volume_source`` is ``manual``.
    fee_tier_30d_volume_usd: float | None = None
    # ``exchange`` = poll Coinbase Advanced ``transaction_summary`` (FUTURE/PERP); ``manual`` = baseline only.
    fee_tier_volume_source: str = "exchange"
    # Minimum seconds between automatic exchange polls (manual refresh ignores this).
    fee_tier_poll_interval_sec: float = 900.0
    # When source is ``manual``, add abs(fill USD) from this bot's session fills to the baseline (rough).
    fee_tier_add_bot_fill_notional: bool = False
    # When True (and venue=coinbase_perps, volume_source=exchange), poll updates ``fee_bps_*`` from
    # Coinbase ``transaction_summary.fee_tier`` so WFO / bar sim / tuner track live tier rates.
    fee_tier_auto_apply_exchange_fee_rates: bool = True
    # If True, startup removes champion rows when persisted fee snapshot != config.
    scalp_auto_invalidate_champion_on_fee_change: bool = False
    # Cap sizing by exchange futures buying_power (min with allocated_capital_usd). Off by default.
    use_exchange_buying_power_cap: bool = False
    buying_power_buffer_usd: float = 0.0
    # If >0 and no successful futures summary poll within this many seconds, ignore buying_power.
    balance_stale_sec: float = 120.0
    # Extra reserved margin (× entry margin) after stop+TP rest — reduces stacked INSUFFICIENT_FUNDS. 0 = off.
    protective_margin_reserve_mult: float = 0.0
    # After this many consecutive venue order rejects, pause new entries for order_reject_cooldown_sec
    # (only when exchange_entry_cooldown_enabled).
    order_reject_max_consecutive: int = 3
    order_reject_cooldown_sec: float = 120.0
    insufficient_funds_cooldown_sec: float = 300.0
    # When True, venue rejects extend order_reject_pause_until / insufficient_funds_until (blocks entries).
    # When False, entries rely on sizing / buying power only; exits are unaffected.
    exchange_entry_cooldown_enabled: bool = False
    # Persist closed legs to data/scalp_trade_history.jsonl and reload on startup (chart markers / UI).
    persist_trade_history: bool = True
    # In-memory deque maxlen and max rows reloaded from disk (most recent first).
    trade_history_max_entries: int = 500
    # Limiter penalty (seconds) on 403 / connection-style REST failures.
    exchange_penalize_base_sec: float = 15.0

    def concurrent_open_cap(self) -> int | None:
        """Positive cap on open legs across pairs, or ``None`` when unlimited (``max_concurrent_positions`` <= 0)."""
        n = int(self.max_concurrent_positions)
        return None if n <= 0 else n


def effective_scalp_fee_bps_per_leg(cfg: ScalpBotConfig) -> float:
    """Return maker or taker fee bps per leg from ``order_type`` (WFO / backtests / tuner)."""
    ot = str(getattr(cfg, "order_type", "limit") or "limit").lower().strip()
    if ot == "market":
        return float(getattr(cfg, "fee_bps_taker_per_leg", 7.0))
    if ot == "hybrid":
        return float(getattr(cfg, "fee_bps_per_leg", 0.0))
    return float(getattr(cfg, "fee_bps_per_leg", 0.0))


def wfo_fee_bps_per_leg(cfg: ScalpBotConfig) -> float:
    """Per-leg fee bps for WFO grid, param tuner, and ParamSet backtests.

    Uses taker when ``wfo_assume_taker_fee`` is set (conservative vs empirical market bursts);
    otherwise same as ``effective_scalp_fee_bps_per_leg``.
    """
    if bool(getattr(cfg, "wfo_assume_taker_fee", False)):
        return float(getattr(cfg, "fee_bps_taker_per_leg", 7.0) or 7.0)
    return effective_scalp_fee_bps_per_leg(cfg)


def wfo_continuous_span_hours(cfg: ScalpBotConfig) -> float:
    """Bar-history span for WFO backfill and offline backtests (eval + warmup hours)."""
    eval_h = float(getattr(cfg, "wfo_continuous_eval_hours", 672.0) or 672.0)
    warm_h = float(getattr(cfg, "wfo_continuous_warmup_hours", 168.0) or 168.0)
    return eval_h + warm_h


def wfo_tuner_lookback_hours(cfg: ScalpBotConfig) -> float:
    """Param-tuner / dashboard lookback — not the WFO eval window."""
    return float(getattr(cfg, "wfo_train_hours", 6.0) or 6.0)


def load_scalp_config(raw: dict) -> ScalpBotConfig:
    """Parse [scalp] section from config.toml raw dict."""
    _on = {"1", "true", "yes", "on"}
    safe_startup = (
        os.getenv("ARCEUS_SAFE_STARTUP", "").strip().lower() in _on
        or os.getenv("MITCH_SAFE_STARTUP", "").strip().lower() in _on
    )
    force_sim = (
        os.getenv("ARCEUS_SCALP_FORCE_SIM", "").strip().lower() in _on
        or os.getenv("MITCH_SCALP_FORCE_SIM", "").strip().lower() in _on
    )
    scalp_raw = raw.get("scalp", {})
    if not scalp_raw:
        return ScalpBotConfig(enabled=False)

    venue = str(scalp_raw.get("venue", "coinbase_perps")).strip().lower()
    if venue in ("kraken_spot", "kraken", ""):
        LOG.warning(
            "scalp: venue %r is no longer supported — using coinbase_perps",
            venue or "(empty)",
        )
        venue = "coinbase_perps"
    elif venue != "coinbase_perps":
        LOG.warning("scalp: unknown venue %r — using coinbase_perps", venue)
        venue = "coinbase_perps"
    shorts_default = True
    _ft_src = str(scalp_raw.get("fee_tier_volume_source", "exchange" if venue == "coinbase_perps" else "manual")).lower()
    if _ft_src not in ("exchange", "manual"):
        _ft_src = "manual"

    pairs: dict[str, ScalpPairConfig] = {}
    for key, val in scalp_raw.get("pairs", {}).items():
        if not isinstance(val, dict) or "symbol" not in val:
            continue
        _afb = str(scalp_raw.get("auto_mode_fallback", "sar_chop"))
        pairs[key] = ScalpPairConfig(
            symbol=val["symbol"],
            interval=int(val.get("interval", 5)),
            strategy_mode=str(val.get("strategy_mode", "auto")),
            auto_mode_fallback=str(val.get("auto_mode_fallback", _afb)),
            ema_fast=int(val.get("ema_fast", 5)),
            ema_slow=int(val.get("ema_slow", 13)),
            rsi_period=int(val.get("rsi_period", 9)),
            atr_period=int(val.get("atr_period", 14)),
            volume_ma_period=int(val.get("volume_ma_period", 20)),
            volume_mult=float(val.get("volume_mult", 1.5)),
            atr_stop_mult=float(val.get("atr_stop_mult", 1.0)),
            atr_tp_mult=float(val.get("atr_tp_mult", 1.5)),
            risk_pct=float(val.get("risk_pct", 0.02)),
            min_signals=int(val.get("min_signals", 2)),
            signal_cooldown_sec=float(val.get("signal_cooldown_sec", 15.0)),
            loss_cooldown_sec=float(val.get("loss_cooldown_sec", 30.0)),
            min_candles_required=int(val.get("min_candles_required", 20)),
            ohlc_hist_max_bars=int(val.get("ohlc_hist_max_bars", 0)),
            max_hold_bars=int(val.get("max_hold_bars", 15)),
            rsi_buy_threshold=float(val.get("rsi_buy_threshold", 10.0)),
            rsi_sell_threshold=float(val.get("rsi_sell_threshold", 50.0)),
            rsi_short_threshold=float(val.get("rsi_short_threshold", 70.0)),
            ema_scalp_period=int(val.get("ema_scalp_period", 20)),
            ema_scalp_sr_bars=int(val.get("ema_scalp_sr_bars", 8)),
            macd_fast_len=int(val.get("macd_fast_len", 8)),
            macd_slow_len=int(val.get("macd_slow_len", 10)),
            macd_signal_len=int(val.get("macd_signal_len", 8)),
            t3_length=int(val.get("t3_length", 7)),
            t3_vfactor=float(val.get("t3_vfactor", 0.7)),
            hlc_close_period=int(val.get("hlc_close_period", 5)),
            hlc_low_period=int(val.get("hlc_low_period", 13)),
            hlc_high_period=int(val.get("hlc_high_period", 34)),
            adx_period=int(val.get("adx_period", 14)),
            adx_threshold=float(val.get("adx_threshold", 20.0)),
            wae_sensitivity=float(val.get("wae_sensitivity", 150.0)),
            wae_fast_len=int(val.get("wae_fast_len", 20)),
            wae_slow_len=int(val.get("wae_slow_len", 40)),
            wae_bb_len=int(val.get("wae_bb_len", 20)),
            wae_bb_mult=float(val.get("wae_bb_mult", 2.0)),
            supertrend_period=int(val.get("supertrend_period", 10)),
            supertrend_factor=float(val.get("supertrend_factor", 3.0)),
            squeeze_bb_period=int(val.get("squeeze_bb_period", 20)),
            squeeze_bb_mult=float(val.get("squeeze_bb_mult", 2.0)),
            squeeze_kc_mult=float(val.get("squeeze_kc_mult", 1.5)),
            squeeze_mom_period=int(val.get("squeeze_mom_period", 12)),
            qqe_rsi_period=int(val.get("qqe_rsi_period", 14)),
            qqe_factor=float(val.get("qqe_factor", 4.238)),
            qqe_smoothing=int(val.get("qqe_smoothing", 5)),
            utbot_atr_period=int(val.get("utbot_atr_period", 10)),
            utbot_atr_mult=float(val.get("utbot_atr_mult", 1.0)),
            hull_period=int(val.get("hull_period", 38)),
            sar_start=float(val.get("sar_start", 0.02)),
            sar_increment=float(val.get("sar_increment", 0.02)),
            sar_max=float(val.get("sar_max", 0.2)),
            sar_chop_ma_fast_period=int(val.get("sar_chop_ma_fast_period", 7)),
            sar_chop_ma_long_period=int(val.get("sar_chop_ma_long_period", 200)),
            sar_chop_ma_short_period=int(val.get("sar_chop_ma_short_period", 50)),
            sar_chop_chop_period=int(val.get("sar_chop_chop_period", 14)),
            sar_chop_chop_threshold=float(val.get("sar_chop_chop_threshold", 68.0)),
            sar_chop_macd_fast=int(val.get("sar_chop_macd_fast", 12)),
            sar_chop_macd_slow=int(val.get("sar_chop_macd_slow", 26)),
            sar_chop_macd_signal=int(val.get("sar_chop_macd_signal", 9)),
            sar_chop_use_lucid=bool(val.get("sar_chop_use_lucid", True)),
            sar_chop_use_utbot_trail=bool(val.get("sar_chop_use_utbot_trail", True)),
            sar_chop_utbot_atr_period=int(val.get("sar_chop_utbot_atr_period", 10)),
            sar_chop_utbot_mult=float(val.get("sar_chop_utbot_mult", 2.0)),
            contract_size=float(val.get("contract_size", 1.0)),
            breakeven_atr_trigger=float(val.get("breakeven_atr_trigger", 0.0)),
            breakeven_buffer_bps=float(val.get("breakeven_buffer_bps", 5.0)),
            trail_atr_trigger=float(val.get("trail_atr_trigger", 0.0)),
            trail_atr_distance=float(val.get("trail_atr_distance", 1.0)),
            regime_adx_filter=bool(val.get("regime_adx_filter", False)),
            regime_adx_min=float(val.get("regime_adx_min", 0.0)),
            partial_tp_enabled=bool(val.get("partial_tp_enabled", False)),
            partial_tp_pct=float(val.get("partial_tp_pct", 0.5)),
            partial_tp_runner_trail_atr=float(val.get("partial_tp_runner_trail_atr", 1.0)),
            correlation_group=str(val.get("correlation_group", "")),
        )

    # Fee defaults: Coinbase CDE Advanced 1-style (~$50k–$500k/mo derivatives tier) unless overridden.
    _def_maker = 6.5 if venue == "coinbase_perps" else 26.0
    _def_taker = 7.0 if venue == "coinbase_perps" else 26.0
    _def_usd = 0.15 if venue == "coinbase_perps" else 0.0
    _maker = float(scalp_raw.get("fee_bps_per_leg", _def_maker))
    _taker = float(scalp_raw.get("fee_bps_taker_per_leg", _def_taker))
    _usd = float(scalp_raw.get("fee_usd_per_contract_per_leg", _def_usd))

    _tb_raw = scalp_raw.get("wfo_holdout_tiebreakers")
    if isinstance(_tb_raw, (list, tuple)) and len(_tb_raw) > 0:
        _holdout_tb = tuple(str(x).strip() for x in _tb_raw if str(x).strip())
    else:
        _holdout_tb = (
            "sum_holdout_total_pnl",
            "neg_mean_max_dd_pct",
            "min_holdout_trade_count",
            "stability",
        )

    return ScalpBotConfig(
        enabled=bool(scalp_raw.get("enabled", False)),
        venue=venue,
        pairs=pairs,
        auto_mode_fallback=str(scalp_raw.get("auto_mode_fallback", "sar_chop")),
        max_concurrent_positions=int(scalp_raw.get("max_concurrent_positions", 0)),
        daily_loss_limit_pct=float(scalp_raw.get("daily_loss_limit_pct", 5.0)),
        daily_loss_enter_standby=bool(scalp_raw.get("daily_loss_enter_standby", False)),
        daily_loss_cancel_open_orders=bool(scalp_raw.get("daily_loss_cancel_open_orders", False)),
        daily_loss_set_scalp_halt=bool(scalp_raw.get("daily_loss_set_scalp_halt", True)),
        daily_auto_reset=bool(scalp_raw.get("daily_auto_reset", True)),
        allocated_capital_usd=float(scalp_raw.get("allocated_capital_usd", 150.0)),
        order_type=str(scalp_raw.get("order_type", "limit")),
        fee_bps_per_leg=_maker,
        fee_bps_taker_per_leg=_taker,
        fee_usd_per_contract_per_leg=_usd,
        slippage_bps=float(scalp_raw.get("slippage_bps", 1.0)),
        slip_calibration_enabled=bool(scalp_raw.get("slip_calibration_enabled", False)),
        slip_calibration_ema_alpha=float(scalp_raw.get("slip_calibration_ema_alpha", 0.2)),
        slip_calibration_min_samples=int(scalp_raw.get("slip_calibration_min_samples", 8)),
        slip_calibration_floor_bps=float(scalp_raw.get("slip_calibration_floor_bps", 0.0)),
        slip_calibration_cap_bps=float(scalp_raw.get("slip_calibration_cap_bps", 80.0)),
        slip_calibration_mode=str(scalp_raw.get("slip_calibration_mode", "max_with_config")),
        entry_limit_ttl_sec=float(scalp_raw.get("entry_limit_ttl_sec", 0.0)),
        entry_limit_offset_bps=float(scalp_raw.get("entry_limit_offset_bps", 0.0)),
        empirical_market_promotion_enabled=bool(scalp_raw.get("empirical_market_promotion_enabled", False)),
        empirical_market_missed_move_bps=float(scalp_raw.get("empirical_market_missed_move_bps", 12.0)),
        empirical_market_miss_eval_window_sec=float(
            scalp_raw.get("empirical_market_miss_eval_window_sec", 600.0)
        ),
        empirical_market_min_pattern_in_window=int(
            scalp_raw.get("empirical_market_min_pattern_in_window", 3)
        ),
        empirical_market_pattern_window_sec=float(
            scalp_raw.get("empirical_market_pattern_window_sec", 86400.0)
        ),
        empirical_market_promotion_entries=int(scalp_raw.get("empirical_market_promotion_entries", 2)),
        empirical_market_promotion_cooldown_sec=float(
            scalp_raw.get("empirical_market_promotion_cooldown_sec", 3600.0)
        ),
        empirical_market_ttl_cancel_arms_promotion=bool(
            scalp_raw.get("empirical_market_ttl_cancel_arms_promotion", False)
        ),
        empirical_market_ttl_cancel_promotion_entries=int(
            scalp_raw.get("empirical_market_ttl_cancel_promotion_entries", 1)
        ),
        wfo_assume_taker_fee=bool(scalp_raw.get("wfo_assume_taker_fee", False)),
        shorts_enabled=bool(scalp_raw.get("shorts_enabled", shorts_default)),
        max_leverage=float(scalp_raw.get("max_leverage", 1.0)),
        margin_mode=str(scalp_raw.get("margin_mode", "CROSS")).upper(),
        funding_warn_bps_per_hour=float(scalp_raw.get("funding_warn_bps_per_hour", 5.0)),
        liquidation_warn_pct=float(scalp_raw.get("liquidation_warn_pct", 5.0)),
        max_notional_usd_per_pair=(
            float(scalp_raw["max_notional_usd_per_pair"])
            if scalp_raw.get("max_notional_usd_per_pair") is not None
            else None
        ),
        rest_seed_candles=int(scalp_raw.get("rest_seed_candles", 100)),
        sim_mode=bool(scalp_raw.get("sim_mode", False)) or force_sim or safe_startup,
        wfo_enabled=bool(scalp_raw.get("wfo_enabled", True)),
        wfo_interval_sec=float(scalp_raw.get("wfo_interval_sec", 3600.0)),
        wfo_train_hours=float(scalp_raw.get("wfo_train_hours", 6.0)),
        wfo_min_trades=int(scalp_raw.get("wfo_min_trades", 20)),
        wfo_objective=str(scalp_raw.get("wfo_objective", "expectancy_sqrt_n")),
        strategy_lookback_hours=float(scalp_raw.get("strategy_lookback_hours", 24.0)),
        backtest_fill_model=str(scalp_raw.get("backtest_fill_model", "close_slip")),
        backtest_funding_enabled=bool(scalp_raw.get("backtest_funding_enabled", False)),
        backtest_funding_bps_per_hour=float(scalp_raw.get("backtest_funding_bps_per_hour", 0.0)),
        use_numpy_indicators=bool(scalp_raw.get("use_numpy_indicators", False)),
        warmup_enabled=bool(scalp_raw.get("warmup_enabled", True)),
        warmup_min_bars=int(scalp_raw.get("warmup_min_bars", 500)),
        warmup_require_champion=bool(scalp_raw.get("warmup_require_champion", True)),
        warmup_max_hours=float(scalp_raw.get("warmup_max_hours", 0.0)),
        require_manual_go_live=bool(scalp_raw.get("require_manual_go_live", False)),
        require_champion_to_trade=bool(scalp_raw.get("require_champion_to_trade", True)),
        tick_entries_enabled=bool(scalp_raw.get("tick_entries_enabled", False)),
        tick_signal_cooldown_sec=float(scalp_raw.get("tick_signal_cooldown_sec", 300.0)),
        regime_risk_on_enabled=bool(scalp_raw.get("regime_risk_on_enabled", True)),
        regime_volume_spike_mult=float(scalp_raw.get("regime_volume_spike_mult", 3.0)),
        regime_price_move_atr_mult=float(scalp_raw.get("regime_price_move_atr_mult", 1.75)),
        regime_price_move_min_pct=float(scalp_raw.get("regime_price_move_min_pct", 0.0)),
        regime_rsi_oversold=float(scalp_raw.get("regime_rsi_oversold", 20.0)),
        regime_rsi_overbought=float(scalp_raw.get("regime_rsi_overbought", 80.0)),
        regime_live_vol_enabled=bool(scalp_raw.get("regime_live_vol_enabled", True)),
        regime_live_use_volume=bool(scalp_raw.get("regime_live_use_volume", True)),
        regime_live_range_atr_mult=float(scalp_raw.get("regime_live_range_atr_mult", 1.75)),
        regime_live_velocity_window_sec=float(scalp_raw.get("regime_live_velocity_window_sec", 45.0)),
        regime_live_velocity_min_bps=float(scalp_raw.get("regime_live_velocity_min_bps", 20.0)),
        risk_on_hold_sec=float(scalp_raw.get("risk_on_hold_sec", 3600.0)),
        risk_on_relax_after_calm_sec=float(scalp_raw.get("risk_on_relax_after_calm_sec", 60.0)),
        risk_on_wfo_interval_scale=float(scalp_raw.get("risk_on_wfo_interval_scale", 0.25)),
        risk_on_wfo_min_interval_sec=float(scalp_raw.get("risk_on_wfo_min_interval_sec", 60.0)),
        risk_on_bootstrap_hours=float(scalp_raw.get("risk_on_bootstrap_hours", 1.0)),
        risk_on_nemesis_expectancy_slack=float(scalp_raw.get("risk_on_nemesis_expectancy_slack", 0.0)),
        risk_on_nemesis_min_pf=float(scalp_raw.get("risk_on_nemesis_min_pf", 0.95)),
        risk_on_size_mult=float(scalp_raw.get("risk_on_size_mult", 1.5)),
        risk_on_signal_cooldown_scale=float(scalp_raw.get("risk_on_signal_cooldown_scale", 0.5)),
        news_risk_on_enabled=bool(scalp_raw.get("news_risk_on_enabled", True)),
        news_pre_event_minutes=float(scalp_raw.get("news_pre_event_minutes", 15.0)),
        news_post_event_minutes=float(scalp_raw.get("news_post_event_minutes", 30.0)),
        news_min_impact=str(scalp_raw.get("news_min_impact", "High")),
        news_currencies=str(scalp_raw.get("news_currencies", "USD")),
        news_calendar_refresh_sec=float(scalp_raw.get("news_calendar_refresh_sec", 3600.0)),
        news_front_run_enabled=bool(scalp_raw.get("news_front_run_enabled", True)),
        news_watch_window_min=float(scalp_raw.get("news_watch_window_min", 60.0)),
        news_front_run_entry_min=float(scalp_raw.get("news_front_run_entry_min", 10.0)),
        news_front_run_cutoff_min=float(scalp_raw.get("news_front_run_cutoff_min", 2.0)),
        news_ai_confidence_threshold=int(scalp_raw.get("news_ai_confidence_threshold", 65)),
        news_advisor_refresh_min=float(scalp_raw.get("news_advisor_refresh_min", 20.0)),
        news_front_run_sl_atr_mult=float(scalp_raw.get("news_front_run_sl_atr_mult", 0.4)),
        news_front_run_tp_atr_mult=float(scalp_raw.get("news_front_run_tp_atr_mult", 1.5)),
        volatility_filter_enabled=bool(scalp_raw.get("volatility_filter_enabled", False)),
        volatility_spike_volume_mult=float(scalp_raw.get("volatility_spike_volume_mult", 4.0)),
        volatility_confirm_min_volume_mult=float(
            scalp_raw.get("volatility_confirm_min_volume_mult", 1.15)
        ),
        volatility_confirm_follow_atr_mult=float(
            scalp_raw.get("volatility_confirm_follow_atr_mult", 0.35)
        ),
        volatility_reject_bearish_climax=bool(scalp_raw.get("volatility_reject_bearish_climax", True)),
        volatility_climax_bearish_range_frac=float(
            scalp_raw.get("volatility_climax_bearish_range_frac", 0.22)
        ),
        volatility_reject_bullish_exhaust=bool(
            scalp_raw.get("volatility_reject_bullish_exhaust", False)
        ),
        volatility_climax_bullish_exhaust_frac=float(
            scalp_raw.get("volatility_climax_bullish_exhaust_frac", 0.88)
        ),
        volatility_exec_risk_mult=float(scalp_raw.get("volatility_exec_risk_mult", 1.25)),
        volatility_exec_risk_cap=float(scalp_raw.get("volatility_exec_risk_cap", 2.0)),
        volatility_exec_hold_sec=float(scalp_raw.get("volatility_exec_hold_sec", 1800.0)),
        volatility_armed_tick_cooldown_scale=float(
            scalp_raw.get("volatility_armed_tick_cooldown_scale", 0.5)
        ),
        volatility_armed_signal_cooldown_scale=float(
            scalp_raw.get("volatility_armed_signal_cooldown_scale", 0.5)
        ),
        volatility_armed_cooldown_floor_sec=float(
            scalp_raw.get("volatility_armed_cooldown_floor_sec", 1.0)
        ),
        volatility_armed_param_tuner_interval_mult=float(
            scalp_raw.get("volatility_armed_param_tuner_interval_mult", 1.0)
        ),
        expiry_guard_warning_days=int(scalp_raw.get("expiry_guard_warning_days", 7)),
        expiry_guard_block_days=int(scalp_raw.get("expiry_guard_block_days", 3)),
        wfo_forward_min_trades=int(scalp_raw.get("wfo_forward_min_trades", 10)),
        wfo_forward_demotion_threshold=float(scalp_raw.get("wfo_forward_demotion_threshold", -0.5)),
        wfo_forward_outperform_factor=float(scalp_raw.get("wfo_forward_outperform_factor", 1.5)),
        wfo_forward_reconciliation_alert_pct=float(
            scalp_raw.get("wfo_forward_reconciliation_alert_pct", 0.30),
        ),
        wfo_live_circuit_breaker_enabled=bool(
            scalp_raw.get("wfo_live_circuit_breaker_enabled", False),
        ),
        wfo_live_circuit_breaker_dd_mult=float(
            scalp_raw.get("wfo_live_circuit_breaker_dd_mult", 2.0),
        ),
        wfo_live_circuit_breaker_hours=float(
            scalp_raw.get("wfo_live_circuit_breaker_hours", 24.0),
        ),
        wfo_no_candidates_demotion_passes=int(scalp_raw.get("wfo_no_candidates_demotion_passes", 0)),
        param_tuner_allow_mode_override_champion=bool(
            scalp_raw.get("param_tuner_allow_mode_override_champion", False)
        ),
        param_tuner_require_wfo_champion=bool(scalp_raw.get("param_tuner_require_wfo_champion", True)),
        param_tuner_interval_sec=float(scalp_raw.get("param_tuner_interval_sec", 900.0)),
        param_tuner_min_bars_between_runs=int(
            scalp_raw.get("param_tuner_min_bars_between_runs", 0) or 0
        ),
        param_tuner_cooldown_sec_after_apply=float(
            scalp_raw.get("param_tuner_cooldown_sec_after_apply", 0.0) or 0.0
        ),
        param_tuner_warn_interval_below_bar_mult=float(
            scalp_raw.get("param_tuner_warn_interval_below_bar_mult", 5.0) or 0.0
        ),
        scalp_fee_assumption_revision=int(scalp_raw.get("scalp_fee_assumption_revision", 0)),
        fee_tier_30d_volume_usd=(
            float(scalp_raw["fee_tier_30d_volume_usd"])
            if scalp_raw.get("fee_tier_30d_volume_usd") is not None
            else None
        ),
        fee_tier_volume_source=_ft_src,
        fee_tier_poll_interval_sec=float(scalp_raw.get("fee_tier_poll_interval_sec", 900.0)),
        fee_tier_add_bot_fill_notional=bool(scalp_raw.get("fee_tier_add_bot_fill_notional", False)),
        fee_tier_auto_apply_exchange_fee_rates=bool(
            scalp_raw.get("fee_tier_auto_apply_exchange_fee_rates", True)
        ),
        scalp_auto_invalidate_champion_on_fee_change=bool(
            scalp_raw.get("scalp_auto_invalidate_champion_on_fee_change", False)
        ),
        use_exchange_buying_power_cap=bool(scalp_raw.get("use_exchange_buying_power_cap", False)),
        buying_power_buffer_usd=float(scalp_raw.get("buying_power_buffer_usd", 0.0)),
        balance_stale_sec=float(scalp_raw.get("balance_stale_sec", 120.0)),
        protective_margin_reserve_mult=float(scalp_raw.get("protective_margin_reserve_mult", 0.0)),
        order_reject_max_consecutive=int(scalp_raw.get("order_reject_max_consecutive", 3)),
        order_reject_cooldown_sec=float(scalp_raw.get("order_reject_cooldown_sec", 120.0)),
        insufficient_funds_cooldown_sec=float(scalp_raw.get("insufficient_funds_cooldown_sec", 300.0)),
        exchange_entry_cooldown_enabled=bool(scalp_raw.get("exchange_entry_cooldown_enabled", False)),
        persist_trade_history=bool(scalp_raw.get("persist_trade_history", True)),
        trade_history_max_entries=max(1, int(scalp_raw.get("trade_history_max_entries", 500) or 500)),
        exchange_penalize_base_sec=float(scalp_raw.get("exchange_penalize_base_sec", 15.0)),
        wfo_min_mean_score=float(scalp_raw.get("wfo_min_mean_score", -999.0)),
        wfo_min_stability_ratio=float(scalp_raw.get("wfo_min_stability_ratio", -999.0)),
        wfo_require_positive_holdout=bool(scalp_raw.get("wfo_require_positive_holdout", False)),
        wfo_min_holdout_pf=float(scalp_raw.get("wfo_min_holdout_pf", 0.5)),
        wfo_max_avg_dd_pct=float(scalp_raw.get("wfo_max_avg_dd_pct", 999.0)),
        wfo_min_profit_factor=float(scalp_raw.get("wfo_min_profit_factor", 0.8)),
        wfo_min_win_rate=float(scalp_raw.get("wfo_min_win_rate", 0.20)),
        wfo_max_train_drawdown_pct=float(scalp_raw.get("wfo_max_train_drawdown_pct", 30.0)),
        wfo_backfill_buffer_hours=float(scalp_raw.get("wfo_backfill_buffer_hours", 24.0)),
        wfo_champion_cooldown_sec=float(scalp_raw.get("wfo_champion_cooldown_sec", 0.0)),
        wfo_require_holdout_beat_prior=bool(scalp_raw.get("wfo_require_holdout_beat_prior", False)),
        wfo_prior_beat_epsilon=float(scalp_raw.get("wfo_prior_beat_epsilon", 1e-6)),
        wfo_min_champion_score_delta=float(scalp_raw.get("wfo_min_champion_score_delta", 0.0)),
        wfo_period_rank_metric=str(scalp_raw.get("wfo_period_rank_metric", "total_pnl") or "total_pnl"),
        wfo_pick_best_per_mode=bool(scalp_raw.get("wfo_pick_best_per_mode", True)),
        risk_on_wfo_min_base_interval_frac=float(
            scalp_raw.get("risk_on_wfo_min_base_interval_frac", 0.5),
        ),
        wfo_vol_armed_min_latest_holdout_pf=float(
            scalp_raw.get("wfo_vol_armed_min_latest_holdout_pf", 0.0),
        ),
        wfo_vol_armed_disallow_promotion_relaxation=bool(
            scalp_raw.get("wfo_vol_armed_disallow_promotion_relaxation", True),
        ),
        wfo_adverse_check_enabled=bool(scalp_raw.get("wfo_adverse_check_enabled", False)),
        wfo_adverse_fill_model=str(scalp_raw.get("wfo_adverse_fill_model", "next_open")),
        wfo_adverse_assume_taker_fee=bool(scalp_raw.get("wfo_adverse_assume_taker_fee", True)),
        wfo_adverse_min_mean_holdout_pnl=float(
            scalp_raw.get("wfo_adverse_min_mean_holdout_pnl", 0.0),
        ),
        wfo_adverse_min_objective_ratio_vs_primary=float(
            scalp_raw.get("wfo_adverse_min_objective_ratio_vs_primary", 0.0),
        ),
        wfo_max_param_delta_stop=float(scalp_raw.get("wfo_max_param_delta_stop", 1.0)),
        wfo_max_param_delta_tp=float(scalp_raw.get("wfo_max_param_delta_tp", 1.5)),
        wfo_holdout_tiebreakers=_holdout_tb,
        wfo_holdout_score_epsilon=float(scalp_raw.get("wfo_holdout_score_epsilon", 0.0) or 0.0),
        wfo_continuous_eval_hours=float(scalp_raw.get("wfo_continuous_eval_hours", 672.0) or 672.0),
        wfo_continuous_warmup_hours=float(scalp_raw.get("wfo_continuous_warmup_hours", 168.0) or 168.0),
        wfo_continuous_min_trades=int(scalp_raw.get("wfo_continuous_min_trades", 20) or 20),
    )
