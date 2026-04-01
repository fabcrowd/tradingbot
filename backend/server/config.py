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
PROFITABILITY_MARGIN_BPS = 4


@dataclass
class PairConfig:
    symbol: str
    spread_bps: int = 8
    order_size: float = 30.0
    max_inventory: float = 300.0
    fee_bps: int = 25  # static fallback; overridden at runtime by volume tier
    fee_override: bool = False  # True = ignore volume tier, use fee_bps as-is
    cycle_ms: int | None = None  # falls back to global default
    spread_floor_bps: int | None = None  # per-pair floor overrides global
    peg_price: float | None = None  # for stablecoin depeg detection (e.g. 1.0)
    # Warmup: quote tighter half-spread until this many sells on the pair, then use spread_bps.
    bootstrap_half_spread_bps: int | None = None
    bootstrap_until_sell_trades: int = 0


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
        return maker_fee_bps(volume_30d)

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
        depeg_threshold_bps=int(bot_raw.get("depeg_threshold_bps", 50)),
        per_trade_profitability=bool(bot_raw.get("per_trade_profitability", True)),
        min_quote_half_spread_bps=int(bot_raw.get("min_quote_half_spread_bps", 2)),
        min_total_pnl_usd=bot_raw.get("min_total_pnl_usd"),
        daily_profit_target_usd=bot_raw.get("daily_profit_target_usd"),
        daily_loss_limit_usd=bot_raw.get("daily_loss_limit_usd"),
        max_drawdown_pct=bot_raw.get("max_drawdown_pct"),
    )

    pairs: dict[str, PairConfig] = {}
    for key, val in raw.get("pairs", {}).items():
        pairs[key] = PairConfig(
            symbol=val["symbol"],
            spread_bps=val.get("spread_bps", 8),
            order_size=val.get("order_size", 30.0),
            max_inventory=val.get("max_inventory", 300.0),
            fee_bps=val.get("fee_bps", 25),
            fee_override=bool(val.get("fee_override", False)),
            cycle_ms=val.get("cycle_ms"),
            spread_floor_bps=val.get("spread_floor_bps"),
            peg_price=val.get("peg_price"),
            bootstrap_half_spread_bps=val.get("bootstrap_half_spread_bps"),
            bootstrap_until_sell_trades=int(val.get("bootstrap_until_sell_trades", 0)),
        )

    return AppConfig(
        server=server,
        bot=bot,
        pairs=pairs,
        api_key=os.getenv("KRAKEN_API_KEY", ""),
        api_secret=os.getenv("KRAKEN_API_SECRET", ""),
    )
