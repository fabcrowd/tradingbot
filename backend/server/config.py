"""Load and validate configuration from config.toml + .env."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROFITABILITY_MARGIN_BPS = 2


@dataclass
class PairConfig:
    symbol: str
    spread_bps: int = 8
    order_size: float = 30.0
    max_inventory: float = 300.0
    fee_bps: int = 25  # static fallback; overridden at runtime by volume tier
    fee_override: bool = False  # True = ignore volume tier, use fee_bps as-is
    fee_schedule: str = "spot_crypto"
    cycle_ms: int | None = None  # falls back to global default
    spread_floor_bps: int | None = None  # per-pair floor overrides global
    peg_price: float | None = None  # for stablecoin depeg detection (e.g. 1.0)
    # Warmup: quote tighter half-spread until this many sells on the pair, then use spread_bps.
    bootstrap_half_spread_bps: int | None = None
    bootstrap_until_sell_trades: int = 0
    twap_threshold_qty: float | None = None
    inventory_skew_scale: float = 0.4
    sell_order_size: float | None = None  # if set, sell orders use this instead of order_size
    sell_floor_base: float | None = None  # bot won't sell wallet below this quantity
    order_levels: int = 1           # number of resting orders per side (multi-level quoting)
    level_step_bps: int = 40        # bps between each level (from closest to deepest)
    min_order_qty: float = 75.0     # exchange minimum order volume for this pair


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"


@dataclass
class BotConfig:
    mode: str = "paper"
    default_cycle_ms: int = 3000
    # If set, only these pair keys get quotes from the spread engine (book still shows all).
    # Omit or set to null to trade every [pairs.*] block.
    enabled_pairs: list[str] | None = None
    # Adaptive spread: nudge spread_bps from recent sell win% (toggle in UI or here).
    adaptive_tuning: bool = False
    adaptive_interval_sec: float = 90.0
    adaptive_min_sample_sells: int = 10
    adaptive_lookback_sells: int = 30
    adaptive_target_win_pct: float = 48.0
    adaptive_win_band_pct: float = 8.0
    adaptive_spread_step_bps: int = 2
    adaptive_spread_floor_bps: int = 4
    adaptive_spread_ceiling_bps: int = 100
    # Market intelligence / threat detection.
    threat_imbalance_threshold: float = 0.5
    threat_spread_blowout_ratio: float = 2.0
    threat_velocity_bps: float = 15.0
    threat_critical_velocity_bps: float = 50.0
    threat_spread_multiplier: float = 1.5
    # Strategy learner: nudges spread_bps toward historically best avg P&L bucket.
    learner_enabled: bool = False
    learner_interval_sec: float = 120.0
    learner_min_samples: int = 15
    learner_max_daily_adjustments: int = 12
    # If recent sells avg negative P&L, bias toward widening (learn from losing trades).
    learner_loss_lookback_sells: int = 5
    learner_widen_on_avg_loss: bool = True
    learner_lookback_max_age_sec: float = 3600.0  # ignore fills older than this for P&L
    # No-fill decay: tighten spread when idle to attract fills.
    decay_start_sec: float = 90.0      # seconds of no fills before decay starts
    decay_interval_sec: float = 60.0   # tighten 1 step every N seconds after that
    decay_step_bps: int = 1            # bps per decay step (half the learner step)
    pain_floor_decay_hours: float = 4.0  # pain floor lowers 1 bps every N hours
    momentum_hold_sells: int = 2
    momentum_hold_sec: float = 60.0
    depeg_threshold_bps: int = 50
    # When True (default): half-spread is clamped to fee_bps + margin — every round-trip
    # can be net-positive after fees. When False ("survival" / volume-building): floor is
    # only min_quote_half_spread_bps + pair spread_floor_bps — you may lose on individual
    # fills to get volume; use min_total_pnl_usd to stop before the account bleeds out.
    per_trade_profitability: bool = True
    min_quote_half_spread_bps: int = 2
    min_total_pnl_usd: float | None = None  # e.g. -50.0 = halt quotes if P&L below -$50
    # Risk management auto-stops (friend's feature)
    daily_profit_target_usd: float | None = None  # auto-stop when daily P&L hits this
    daily_loss_limit_usd: float | None = None      # auto-stop when daily loss hits this
    max_drawdown_pct: float | None = None          # auto-stop when P&L drops X% from peak
    rate_limit_order_per_sec: float = 10.0
    rate_limit_burst: int = 20
    threat_quoting_pause: bool = True
    abort_on_withdraw_permission: bool = False
    trailing_stop_enabled: bool = False
    trailing_stop_pct: float = 50.0
    take_profit_usd: float | None = None
    oco_enabled: bool = False
    oco_stop_bps: int = 30
    oco_tp_bps: int = 30
    twap_enabled: bool = False
    twap_slice_count: int = 5
    twap_duration_sec: float = 30.0
    btd_enabled: bool = False
    btd_sma_short: int = 20
    btd_sma_long: int = 60
    btd_levels: int = 3
    btd_step_bps: int = 20
    btd_size_multiplier: float = 1.5
    optimizer_enabled: bool = False
    optimizer_interval_sec: float = 900.0
    optimizer_train_hours: float = 4.0
    optimizer_holdout_pct: float = 0.25
    optimizer_max_delta_spread_bps: int = 6
    optimizer_max_delta_size_pct: float = 50.0
    optimizer_min_fills: int = 20
    optimizer_objective: str = "total_dollar_wins"
    # Ping-pong: after a buy fill, suppress new buys; after sell, suppress new sells
    ping_pong_enabled: bool = False
    # Hanging orders: preserve near-fill opposite-side orders when other side refreshes
    hanging_orders_enabled: bool = False
    hanging_orders_cancel_pct: float = 3.0  # % from mid to keep hanging order
    # Triple barrier: per-fill stop-loss / take-profit / time-limit exits
    triple_barrier_enabled: bool = False
    tb_stop_pct: float = 2.0        # stop-loss: sell if price drops this % below buy
    tb_tp_pct: float = 1.5          # take-profit: sell if price rises this % above buy
    tb_max_hold_sec: float = 3600.0  # time-limit: sell after this many seconds
    # Consecutive losing sell pause
    consecutive_loss_halt_count: int = 3
    consecutive_loss_pause_sec: float = 300.0
    # Fill cascade protection: after a fill, cancel same-side resting orders
    # and pause new ones for this many seconds so the book can settle.
    fill_cooldown_sec: float = 5.0
    # Time-based quoting (Phase 5 — leave disabled until bot is stable)
    time_quoting_enabled: bool = False
    time_peak_start_utc: int = 13    # peak activity window start (UTC hour)
    time_peak_end_utc: int = 21      # peak activity window end (UTC hour)
    time_normal_multiplier: float = 1.15   # spread multiplier outside peak
    time_offpeak_multiplier: float = 1.35  # spread multiplier during 00-08 UTC
    time_offpeak_size_pct: float = 50.0    # reduce order qty to this % during off-peak
    # Warmup: observe market before placing any orders
    warmup_sec: float = 30.0              # seconds to observe before first order placement
    warmup_buy_percentile: float = 25.0   # target buy near this percentile of observed range (0=low, 100=high)
    # MEV / bot detection
    mev_detection_enabled: bool = False
    mev_chain_map: dict[str, str] = field(default_factory=dict)
    mev_bot_widen_scale: float = 0.15
    mev_arb_widen_scale: float = 0.25
    mev_clean_tighten_scale: float = 0.10
    mev_bot_score_threshold: float = 0.5
    mev_detector_window_sec: float = 60.0
    mev_eigenphi_enabled: bool = False
    mev_mempool_feed_enabled: bool = False
    # Auto-reseed: if min_profitable_sell_price > mid * (1 + this%) at START, reseed to mid.
    # 0 disables. Default 5% = reseed if sell floor is >5% above current market price.
    barrier_auto_reseed_pct: float = 5.0

    def validate(self) -> None:
        """Assert critical fields have safe values."""
        for name in ("decay_interval_sec", "adaptive_interval_sec", "learner_interval_sec", "pain_floor_decay_hours"):
            val = getattr(self, name, 0)
            if val is not None and val <= 0:
                raise ValueError(f"bot.{name} must be > 0 (got {val})")
        if self.twap_slice_count < 1:
            raise ValueError(f"bot.twap_slice_count must be >= 1 (got {self.twap_slice_count})")
        if self.twap_duration_sec <= 0:
            raise ValueError(f"bot.twap_duration_sec must be > 0 (got {self.twap_duration_sec})")
        if self.rate_limit_order_per_sec <= 0:
            raise ValueError(f"bot.rate_limit_order_per_sec must be > 0 (got {self.rate_limit_order_per_sec})")
        if self.adaptive_spread_floor_bps > self.adaptive_spread_ceiling_bps:
            raise ValueError(
                f"bot.adaptive_spread_floor_bps ({self.adaptive_spread_floor_bps}) "
                f"> adaptive_spread_ceiling_bps ({self.adaptive_spread_ceiling_bps})"
            )
        if self.optimizer_interval_sec <= 0:
            raise ValueError(f"bot.optimizer_interval_sec must be > 0 (got {self.optimizer_interval_sec})")
        if self.optimizer_train_hours <= 0:
            raise ValueError(f"bot.optimizer_train_hours must be > 0 (got {self.optimizer_train_hours})")
        if not (0.05 <= self.optimizer_holdout_pct <= 0.5):
            raise ValueError(
                f"bot.optimizer_holdout_pct must be between 0.05 and 0.5 "
                f"(got {self.optimizer_holdout_pct})"
            )
        if self.optimizer_max_delta_spread_bps < 1:
            raise ValueError(
                "bot.optimizer_max_delta_spread_bps must be >= 1 "
                f"(got {self.optimizer_max_delta_spread_bps})"
            )
        if self.optimizer_max_delta_size_pct <= 0:
            raise ValueError(
                f"bot.optimizer_max_delta_size_pct must be > 0 (got {self.optimizer_max_delta_size_pct})"
            )
        if self.optimizer_min_fills < 5:
            raise ValueError(f"bot.optimizer_min_fills must be >= 5 (got {self.optimizer_min_fills})")
        if self.optimizer_objective not in {"total_dollar_wins", "net_pnl", "risk_adjusted", "sharpe"}:
            raise ValueError(
                "bot.optimizer_objective must be one of: "
                "total_dollar_wins, net_pnl, risk_adjusted, sharpe "
                f"(got {self.optimizer_objective!r})"
            )


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    bot: BotConfig = field(default_factory=BotConfig)
    pairs: dict[str, PairConfig] = field(default_factory=dict)
    api_key: str = ""
    api_secret: str = ""

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def mode(self) -> str:
        return self.bot.mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in ("paper", "live"):
            raise ValueError(f"Invalid mode: {value}")
        self.bot.mode = value

    def pair_cycle_ms(self, pair_key: str) -> int:
        pc = self.pairs.get(pair_key)
        if pc and pc.cycle_ms is not None:
            return pc.cycle_ms
        return self.bot.default_cycle_ms

    def symbols(self) -> list[str]:
        return [pc.symbol for pc in self.pairs.values()]

    def effective_fee_bps(self, pair_key: str, volume_30d: float) -> int:
        """Resolve maker fee: volume-tier rate unless pair has fee_override."""
        from .fee_schedule import maker_fee_bps

        pc = self.pairs.get(pair_key)
        if pc is None:
            return maker_fee_bps(volume_30d)
        if pc.fee_override:
            return pc.fee_bps
        return maker_fee_bps(volume_30d, pc.fee_schedule)

    def pair_keys_for_trading(self) -> list[str]:
        """Pair keys that receive engine ticks (orders)."""
        if self.bot.enabled_pairs is None:
            return list(self.pairs.keys())
        seen: set[str] = set()
        out: list[str] = []
        for k in self.bot.enabled_pairs:
            if k not in self.pairs:
                continue
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out


def load_config(config_path: Path | None = None) -> AppConfig:
    if config_path is None:
        config_path = PROJECT_ROOT / "config.toml"

    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    load_dotenv(PROJECT_ROOT / ".env")

    srv = raw.get("server", {})
    server = ServerConfig(
        host=srv.get("host", "0.0.0.0"),
        port=srv.get("port", 8080),
        log_level=srv.get("log_level", "INFO"),
    )

    bot_raw = raw.get("bot", {})
    enabled_pairs = bot_raw.get("enabled_pairs")
    if enabled_pairs is not None and not isinstance(enabled_pairs, list):
        raise ValueError("bot.enabled_pairs must be a list of pair keys or omitted")
    if enabled_pairs:
        for k in enabled_pairs:
            if k not in raw.get("pairs", {}):
                raise ValueError(f"bot.enabled_pairs: unknown pair key {k!r}")
    bot = BotConfig(
        mode=bot_raw.get("mode", "paper"),
        default_cycle_ms=bot_raw.get("default_cycle_ms", 3000),
        enabled_pairs=enabled_pairs,
        adaptive_tuning=bool(bot_raw.get("adaptive_tuning", False)),
        adaptive_interval_sec=float(bot_raw.get("adaptive_interval_sec", 90.0)),
        adaptive_min_sample_sells=int(bot_raw.get("adaptive_min_sample_sells", 10)),
        adaptive_lookback_sells=int(bot_raw.get("adaptive_lookback_sells", 30)),
        adaptive_target_win_pct=float(bot_raw.get("adaptive_target_win_pct", 48.0)),
        adaptive_win_band_pct=float(bot_raw.get("adaptive_win_band_pct", 8.0)),
        adaptive_spread_step_bps=int(bot_raw.get("adaptive_spread_step_bps", 2)),
        adaptive_spread_floor_bps=int(bot_raw.get("adaptive_spread_floor_bps", 4)),
        adaptive_spread_ceiling_bps=int(bot_raw.get("adaptive_spread_ceiling_bps", 100)),
        threat_imbalance_threshold=float(
            bot_raw.get("threat_imbalance_threshold", 0.5)
        ),
        threat_spread_blowout_ratio=float(
            bot_raw.get("threat_spread_blowout_ratio", 2.0)
        ),
        threat_velocity_bps=float(bot_raw.get("threat_velocity_bps", 15.0)),
        threat_critical_velocity_bps=float(
            bot_raw.get("threat_critical_velocity_bps", 50.0)
        ),
        threat_spread_multiplier=float(bot_raw.get("threat_spread_multiplier", 1.5)),
        learner_enabled=bool(bot_raw.get("learner_enabled", False)),
        learner_interval_sec=float(bot_raw.get("learner_interval_sec", 120.0)),
        learner_min_samples=int(bot_raw.get("learner_min_samples", 15)),
        learner_max_daily_adjustments=int(bot_raw.get("learner_max_daily_adjustments", 12)),
        learner_loss_lookback_sells=int(bot_raw.get("learner_loss_lookback_sells", 5)),
        learner_widen_on_avg_loss=bool(bot_raw.get("learner_widen_on_avg_loss", True)),
        learner_lookback_max_age_sec=float(bot_raw.get("learner_lookback_max_age_sec", 3600.0)),
        decay_start_sec=float(bot_raw.get("decay_start_sec", 90.0)),
        decay_interval_sec=float(bot_raw.get("decay_interval_sec", 60.0)),
        decay_step_bps=int(bot_raw.get("decay_step_bps", 1)),
        pain_floor_decay_hours=float(bot_raw.get("pain_floor_decay_hours", 4.0)),
        momentum_hold_sells=int(bot_raw.get("momentum_hold_sells", 2)),
        momentum_hold_sec=float(bot_raw.get("momentum_hold_sec", 60.0)),
        depeg_threshold_bps=int(bot_raw.get("depeg_threshold_bps", 50)),
        per_trade_profitability=bool(bot_raw.get("per_trade_profitability", True)),
        min_quote_half_spread_bps=int(bot_raw.get("min_quote_half_spread_bps", 2)),
        min_total_pnl_usd=float(bot_raw["min_total_pnl_usd"]) if bot_raw.get("min_total_pnl_usd") is not None else None,
        daily_profit_target_usd=float(bot_raw["daily_profit_target_usd"]) if bot_raw.get("daily_profit_target_usd") is not None else None,
        daily_loss_limit_usd=float(bot_raw["daily_loss_limit_usd"]) if bot_raw.get("daily_loss_limit_usd") is not None else None,
        max_drawdown_pct=float(bot_raw["max_drawdown_pct"]) if bot_raw.get("max_drawdown_pct") is not None else None,
        rate_limit_order_per_sec=float(bot_raw.get("rate_limit_order_per_sec", 10.0)),
        rate_limit_burst=int(bot_raw.get("rate_limit_burst", 20)),
        threat_quoting_pause=bool(bot_raw.get("threat_quoting_pause", True)),
        abort_on_withdraw_permission=bool(bot_raw.get("abort_on_withdraw_permission", False)),
        trailing_stop_enabled=bool(bot_raw.get("trailing_stop_enabled", False)),
        trailing_stop_pct=float(bot_raw.get("trailing_stop_pct", 50.0)),
        take_profit_usd=float(bot_raw["take_profit_usd"]) if bot_raw.get("take_profit_usd") is not None else None,
        oco_enabled=bool(bot_raw.get("oco_enabled", False)),
        oco_stop_bps=int(bot_raw.get("oco_stop_bps", 30)),
        oco_tp_bps=int(bot_raw.get("oco_tp_bps", 30)),
        twap_enabled=bool(bot_raw.get("twap_enabled", False)),
        twap_slice_count=int(bot_raw.get("twap_slice_count", 5)),
        twap_duration_sec=float(bot_raw.get("twap_duration_sec", 30.0)),
        btd_enabled=bool(bot_raw.get("btd_enabled", False)),
        btd_sma_short=int(bot_raw.get("btd_sma_short", 20)),
        btd_sma_long=int(bot_raw.get("btd_sma_long", 60)),
        btd_levels=int(bot_raw.get("btd_levels", 3)),
        btd_step_bps=int(bot_raw.get("btd_step_bps", 20)),
        btd_size_multiplier=float(bot_raw.get("btd_size_multiplier", 1.5)),
        optimizer_enabled=bool(bot_raw.get("optimizer_enabled", False)),
        optimizer_interval_sec=float(bot_raw.get("optimizer_interval_sec", 900.0)),
        optimizer_train_hours=float(bot_raw.get("optimizer_train_hours", 4.0)),
        optimizer_holdout_pct=float(bot_raw.get("optimizer_holdout_pct", 0.25)),
        optimizer_max_delta_spread_bps=int(bot_raw.get("optimizer_max_delta_spread_bps", 6)),
        optimizer_max_delta_size_pct=float(bot_raw.get("optimizer_max_delta_size_pct", 50.0)),
        optimizer_min_fills=int(bot_raw.get("optimizer_min_fills", 20)),
        optimizer_objective=str(bot_raw.get("optimizer_objective", "total_dollar_wins")),
        ping_pong_enabled=bool(bot_raw.get("ping_pong_enabled", False)),
        hanging_orders_enabled=bool(bot_raw.get("hanging_orders_enabled", False)),
        hanging_orders_cancel_pct=float(bot_raw.get("hanging_orders_cancel_pct", 3.0)),
        triple_barrier_enabled=bool(bot_raw.get("triple_barrier_enabled", False)),
        tb_stop_pct=float(bot_raw.get("tb_stop_pct", 2.0)),
        tb_tp_pct=float(bot_raw.get("tb_tp_pct", 1.5)),
        tb_max_hold_sec=float(bot_raw.get("tb_max_hold_sec", 3600.0)),
        consecutive_loss_halt_count=int(bot_raw.get("consecutive_loss_halt_count", 3)),
        consecutive_loss_pause_sec=float(bot_raw.get("consecutive_loss_pause_sec", 300.0)),
        fill_cooldown_sec=float(bot_raw.get("fill_cooldown_sec", 5.0)),
        time_quoting_enabled=bool(bot_raw.get("time_quoting_enabled", False)),
        time_peak_start_utc=int(bot_raw.get("time_peak_start_utc", 13)),
        time_peak_end_utc=int(bot_raw.get("time_peak_end_utc", 21)),
        time_normal_multiplier=float(bot_raw.get("time_normal_multiplier", 1.15)),
        time_offpeak_multiplier=float(bot_raw.get("time_offpeak_multiplier", 1.35)),
        time_offpeak_size_pct=float(bot_raw.get("time_offpeak_size_pct", 50.0)),
        # MEV / bot detection
        mev_detection_enabled=bool(bot_raw.get("mev_detection_enabled", False)),
        mev_chain_map=dict(bot_raw.get("mev_chain_map", {})),
        mev_bot_widen_scale=float(bot_raw.get("mev_bot_widen_scale", 0.3)),
        mev_arb_widen_scale=float(bot_raw.get("mev_arb_widen_scale", 0.5)),
        mev_clean_tighten_scale=float(bot_raw.get("mev_clean_tighten_scale", 0.15)),
        mev_bot_score_threshold=float(bot_raw.get("mev_bot_score_threshold", 0.3)),
        mev_detector_window_sec=float(bot_raw.get("mev_detector_window_sec", 60.0)),
        mev_eigenphi_enabled=bool(bot_raw.get("mev_eigenphi_enabled", False)),
        mev_mempool_feed_enabled=bool(bot_raw.get("mev_mempool_feed_enabled", False)),
        barrier_auto_reseed_pct=float(bot_raw.get("barrier_auto_reseed_pct", 5.0)),
    )

    bot.validate()

    from .fee_schedule import infer_fee_schedule

    pairs: dict[str, PairConfig] = {}
    for key, val in raw.get("pairs", {}).items():
        symbol = val["symbol"]
        fee_schedule = val.get("fee_schedule", infer_fee_schedule(symbol))
        pairs[key] = PairConfig(
            symbol=symbol,
            spread_bps=val.get("spread_bps", 8),
            order_size=val.get("order_size", 30.0),
            max_inventory=val.get("max_inventory", 300.0),
            fee_bps=val.get("fee_bps", 25),
            fee_override=bool(val.get("fee_override", False)),
            fee_schedule=fee_schedule,
            cycle_ms=val.get("cycle_ms"),
            spread_floor_bps=val.get("spread_floor_bps"),
            peg_price=val.get("peg_price"),
            bootstrap_half_spread_bps=val.get("bootstrap_half_spread_bps"),
            bootstrap_until_sell_trades=int(val.get("bootstrap_until_sell_trades", 0)),
            twap_threshold_qty=val.get("twap_threshold_qty"),
            inventory_skew_scale=float(val.get("inventory_skew_scale", 0.4)),
            sell_order_size=val.get("sell_order_size"),
            sell_floor_base=val.get("sell_floor_base"),
            order_levels=int(val.get("order_levels", 1)),
            level_step_bps=int(val.get("level_step_bps", 40)),
            min_order_qty=float(val.get("min_order_qty", 75.0)),
        )

    return AppConfig(
        server=server,
        bot=bot,
        pairs=pairs,
        api_key=os.getenv("KRAKEN_API_KEY", ""),
        api_secret=os.getenv("KRAKEN_API_SECRET", ""),
    )
