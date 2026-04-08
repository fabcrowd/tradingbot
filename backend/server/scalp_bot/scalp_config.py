"""Config dataclass for the scalp bot — parsed from [scalp] section of config.toml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ScalpPairConfig:
    # Exchange symbol: Kraken spot "SOL/USD" or Coinbase perp product id "SOL-PERP-INTX"
    symbol: str
    interval: int = 1               # candle interval in minutes (1m default for scalping)
    # Strategy mode: "auto" = WFO picks, "daviddtech_scalp" (Optimized), "rsi_reversion", "ema_momentum", "ema_scalp", "macd_scalp"
    strategy_mode: str = "auto"
    # EMA momentum params
    ema_fast: int = 5               # fast EMA period (tuned for 1m)
    ema_slow: int = 13              # slow EMA period (tuned for 1m)
    rsi_period: int = 9             # RSI period
    atr_period: int = 14            # ATR period for stop sizing
    volume_ma_period: int = 20      # rolling average period for volume spike detection
    volume_mult: float = 1.5        # volume must be > this × rolling average to confirm
    atr_stop_mult: float = 1.0      # stop distance = ATR × this
    atr_tp_mult: float = 1.5        # take-profit distance = ATR × this
    risk_pct: float = 0.02          # fraction of scalp capital to risk per trade
    min_signals: int = 2            # minimum confluence signals required (ema_momentum mode)
    signal_cooldown_sec: float = 15.0   # min seconds between entries (fast for 1m)
    loss_cooldown_sec: float = 30.0     # shorter recovery for 1m scalping
    min_candles_required: int = 20  # wait for this many candles before trading
    max_hold_bars: int = 15         # time stop: 15 bars = 15min on 1m
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
    # Optimized Strategy (DaviddTech-style)
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
    # Coinbase INTX nano-style contracts: underlying size per 1 contract (for risk / PnL)
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
    # ── Session hours gate (UTC) ──────────────────────────────────────────────
    # Only emit signals when UTC hour is within [start, end).
    # None = disabled (24/7 trading). Supports wrap-around (e.g. start=22, end=4).
    trade_hours_start: int | None = None
    trade_hours_end: int | None = None
    # ── Regime filter ─────────────────────────────────────────────────────────
    # Block trend-following modes (ema_momentum, ema_scalp, daviddtech_scalp)
    # when ADX is below this value.  0.0 = disabled.
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
    # "kraken_spot" | "coinbase_perps" — drives candle feed, bar store path, and execution manager
    venue: str = "kraken_spot"
    pairs: dict[str, ScalpPairConfig] = field(default_factory=dict)
    # Max simultaneous open legs across all scalp pairs. ``<= 0`` = no cap (only capital /
    # ``max_notional_usd_per_pair`` / exchange margin gate entries).
    max_concurrent_positions: int = 0
    daily_loss_limit_pct: float = 5.0   # halt if daily loss exceeds this % of scalp capital
    allocated_capital_usd: float = 150.0  # USD reserved for scalp bot
    order_type: str = "limit"           # "limit" (maker) or "market" (taker)
    fee_bps_per_leg: float = 0.0       # Coinbase INTX promo: 0 maker/0 taker; standard: 2/4 bps
    slippage_bps: float = 1.0          # estimated slippage per fill in bps
    # Perps / Coinbase
    shorts_enabled: bool = False       # True when venue supports short execution (coinbase_perps)
    max_leverage: float = 1.0          # passed to exchange (1 = no amplification)
    margin_mode: str = "CROSS"         # CROSS | ISOLATED (Coinbase Advanced Trade)
    funding_warn_bps_per_hour: float = 5.0   # alert if |funding| exceeds this (rough, from polls)
    liquidation_warn_pct: float = 5.0        # warn if mark within this % of liquidation price
    max_notional_usd_per_pair: float | None = None  # cap estimated notional per open position
    rest_seed_candles: int = 100        # candles to fetch from REST on startup
    # Walk-forward optimizer (hours-based windows for scalp timeframes)
    wfo_enabled: bool = True
    wfo_interval_sec: float = 3600.0
    wfo_train_hours: float = 6.0     # training window (hours)
    wfo_holdout_hours: float = 2.0   # holdout/validation window (hours)
    wfo_step_hours: float = 2.0      # rolling step size (hours)
    wfo_min_trades: int = 20
    wfo_objective: str = "expectancy_sqrt_n"
    # Strategy lookback: how many hours of recent data to backtest for the UI dashboard
    strategy_lookback_hours: float = 2.0
    # ── Backtest fidelity ─────────────────────────────────────────────────────
    # "close_slip" = fill at signal-bar close + slippage (original, optimistic).
    # "next_open"  = fill at next bar open + slippage (realistic; recommended).
    backtest_fill_model: str = "close_slip"
    # When True, live indicator engine uses numpy-based EMA/RSI that matches the
    # backtest's SMA-seeded logic instead of the hexital library.  Only affects
    # ema_momentum, ema_scalp, rsi_reversion modes (daviddtech already shares numpy).
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
    # Mid-bar entries: price-action triggers on WS ticks using frozen last-bar indicators
    tick_entries_enabled: bool = False
    tick_signal_cooldown_sec: float = 300.0
    # ── Regime risk-on (volume / vol-adjusted moves → faster WFO, shorter bootstrap) ──
    regime_risk_on_enabled: bool = True
    regime_volume_spike_mult: float = 2.5       # bar volume ≥ this × volume MA
    regime_price_move_atr_mult: float = 1.75  # |close−prev_close| ≥ ATR × this
    regime_price_move_min_pct: float = 0.0    # optional min |Δ|% ; 0 = disabled
    # Live / tick-path regime (Coinbase ticker + candle WS; Kraken ohlc updates) — same risk-on window
    regime_live_vol_enabled: bool = True
    regime_live_use_volume: bool = True       # forming-bar vol ≥ volume_ma × regime_volume_spike_mult
    regime_live_range_atr_mult: float = 1.75  # (high−low) ≥ ATR × this on the open candle
    regime_live_velocity_window_sec: float = 45.0  # rolling window for velocity (0 = disable velocity leg)
    regime_live_velocity_min_bps: float = 20.0   # (max−min)/mid in window, bps; 0 = off
    risk_on_hold_sec: float = 900.0             # extend global risk-on window on each hit
    risk_on_wfo_interval_scale: float = 0.35    # multiply wfo_interval_sec while risk-on
    risk_on_wfo_min_interval_sec: float = 300.0 # floor between WFO passes (still ≥60 in loop)
    risk_on_bootstrap_hours: float = 1.0        # no-champion bootstrap window (capped vs 2h default)
    risk_on_nemesis_expectancy_slack: float = 0.0   # tuner wins if t_exp > b_exp − slack
    risk_on_nemesis_min_pf: float = 0.95      # min tuner PF while risk-on (default 1.0 off)

    def concurrent_open_cap(self) -> int | None:
        """Positive cap on open legs across pairs, or ``None`` when unlimited (``max_concurrent_positions`` <= 0)."""
        n = int(self.max_concurrent_positions)
        return None if n <= 0 else n


def load_scalp_config(raw: dict) -> ScalpBotConfig:
    """Parse [scalp] section from config.toml raw dict."""
    safe_startup = os.getenv("MITCH_SAFE_STARTUP", "").strip().lower() in {"1", "true", "yes", "on"}
    force_sim = os.getenv("MITCH_SCALP_FORCE_SIM", "").strip().lower() in {"1", "true", "yes", "on"}
    scalp_raw = raw.get("scalp", {})
    if not scalp_raw:
        return ScalpBotConfig(enabled=False)

    venue = str(scalp_raw.get("venue", "kraken_spot")).strip().lower()
    if venue not in ("kraken_spot", "coinbase_perps"):
        venue = "kraken_spot"
    shorts_default = venue == "coinbase_perps"

    pairs: dict[str, ScalpPairConfig] = {}
    for key, val in scalp_raw.get("pairs", {}).items():
        if not isinstance(val, dict) or "symbol" not in val:
            continue
        pairs[key] = ScalpPairConfig(
            symbol=val["symbol"],
            interval=int(val.get("interval", 1)),
            strategy_mode=str(val.get("strategy_mode", "auto")),
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
            contract_size=float(val.get("contract_size", 1.0)),
            breakeven_atr_trigger=float(val.get("breakeven_atr_trigger", 0.0)),
            breakeven_buffer_bps=float(val.get("breakeven_buffer_bps", 5.0)),
            trail_atr_trigger=float(val.get("trail_atr_trigger", 0.0)),
            trail_atr_distance=float(val.get("trail_atr_distance", 1.0)),
            trade_hours_start=(
                int(val["trade_hours_start"])
                if val.get("trade_hours_start") is not None else None
            ),
            trade_hours_end=(
                int(val["trade_hours_end"])
                if val.get("trade_hours_end") is not None else None
            ),
            regime_adx_min=float(val.get("regime_adx_min", 0.0)),
            partial_tp_enabled=bool(val.get("partial_tp_enabled", False)),
            partial_tp_pct=float(val.get("partial_tp_pct", 0.5)),
            partial_tp_runner_trail_atr=float(val.get("partial_tp_runner_trail_atr", 1.0)),
            correlation_group=str(val.get("correlation_group", "")),
        )

    return ScalpBotConfig(
        enabled=bool(scalp_raw.get("enabled", False)),
        venue=venue,
        pairs=pairs,
        max_concurrent_positions=int(scalp_raw.get("max_concurrent_positions", 0)),
        daily_loss_limit_pct=float(scalp_raw.get("daily_loss_limit_pct", 5.0)),
        allocated_capital_usd=float(scalp_raw.get("allocated_capital_usd", 150.0)),
        order_type=str(scalp_raw.get("order_type", "limit")),
        fee_bps_per_leg=float(scalp_raw.get("fee_bps_per_leg", 26.0)),
        slippage_bps=float(scalp_raw.get("slippage_bps", 1.0)),
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
        wfo_holdout_hours=float(scalp_raw.get("wfo_holdout_hours", 2.0)),
        wfo_step_hours=float(scalp_raw.get("wfo_step_hours", 2.0)),
        wfo_min_trades=int(scalp_raw.get("wfo_min_trades", 20)),
        wfo_objective=str(scalp_raw.get("wfo_objective", "expectancy_sqrt_n")),
        strategy_lookback_hours=float(scalp_raw.get("strategy_lookback_hours", 2.0)),
        backtest_fill_model=str(scalp_raw.get("backtest_fill_model", "close_slip")),
        use_numpy_indicators=bool(scalp_raw.get("use_numpy_indicators", False)),
        warmup_enabled=bool(scalp_raw.get("warmup_enabled", True)),
        warmup_min_bars=int(scalp_raw.get("warmup_min_bars", 500)),
        warmup_require_champion=bool(scalp_raw.get("warmup_require_champion", True)),
        warmup_max_hours=float(scalp_raw.get("warmup_max_hours", 0.0)),
        require_manual_go_live=bool(scalp_raw.get("require_manual_go_live", False)),
        tick_entries_enabled=bool(scalp_raw.get("tick_entries_enabled", False)),
        tick_signal_cooldown_sec=float(scalp_raw.get("tick_signal_cooldown_sec", 300.0)),
        regime_risk_on_enabled=bool(scalp_raw.get("regime_risk_on_enabled", True)),
        regime_volume_spike_mult=float(scalp_raw.get("regime_volume_spike_mult", 2.5)),
        regime_price_move_atr_mult=float(scalp_raw.get("regime_price_move_atr_mult", 1.75)),
        regime_price_move_min_pct=float(scalp_raw.get("regime_price_move_min_pct", 0.0)),
        regime_live_vol_enabled=bool(scalp_raw.get("regime_live_vol_enabled", True)),
        regime_live_use_volume=bool(scalp_raw.get("regime_live_use_volume", True)),
        regime_live_range_atr_mult=float(scalp_raw.get("regime_live_range_atr_mult", 1.75)),
        regime_live_velocity_window_sec=float(scalp_raw.get("regime_live_velocity_window_sec", 45.0)),
        regime_live_velocity_min_bps=float(scalp_raw.get("regime_live_velocity_min_bps", 20.0)),
        risk_on_hold_sec=float(scalp_raw.get("risk_on_hold_sec", 900.0)),
        risk_on_wfo_interval_scale=float(scalp_raw.get("risk_on_wfo_interval_scale", 0.35)),
        risk_on_wfo_min_interval_sec=float(scalp_raw.get("risk_on_wfo_min_interval_sec", 300.0)),
        risk_on_bootstrap_hours=float(scalp_raw.get("risk_on_bootstrap_hours", 1.0)),
        risk_on_nemesis_expectancy_slack=float(scalp_raw.get("risk_on_nemesis_expectancy_slack", 0.0)),
        risk_on_nemesis_min_pf=float(scalp_raw.get("risk_on_nemesis_min_pf", 0.95)),
    )
