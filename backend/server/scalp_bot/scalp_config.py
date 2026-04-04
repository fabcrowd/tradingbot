"""Config dataclass for the scalp bot — parsed from [scalp] section of config.toml."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScalpPairConfig:
    symbol: str                     # Kraken symbol e.g. "XBT/USD"
    interval: int = 5               # candle interval in minutes (1, 5, 15)
    ema_fast: int = 9               # fast EMA period
    ema_slow: int = 21              # slow EMA period
    rsi_period: int = 9             # RSI period
    atr_period: int = 14            # ATR period for stop sizing
    volume_ma_period: int = 20      # rolling average period for volume spike detection
    volume_mult: float = 1.5        # volume must be > this × rolling average to confirm
    atr_stop_mult: float = 1.0      # stop distance = ATR × this
    atr_tp_mult: float = 2.0        # take-profit distance = ATR × this (2:1 R:R default)
    risk_pct: float = 0.01          # fraction of scalp capital to risk per trade (1%)
    min_signals: int = 3            # minimum confluence signals required (out of 4)
    signal_cooldown_sec: float = 60.0   # min seconds between entries
    loss_cooldown_sec: float = 120.0    # extra cooldown after a stopped-out trade
    min_candles_required: int = 30  # wait for this many candles before trading


@dataclass
class ScalpBotConfig:
    enabled: bool = False
    pairs: dict[str, ScalpPairConfig] = field(default_factory=dict)
    max_concurrent_positions: int = 2
    daily_loss_limit_pct: float = 5.0   # halt if daily loss exceeds this % of scalp capital
    allocated_capital_usd: float = 150.0  # USD reserved for scalp bot
    order_type: str = "limit"           # "limit" (maker, no fee) or "market" (immediate)
    rest_seed_candles: int = 100        # candles to fetch from REST on startup


def load_scalp_config(raw: dict) -> ScalpBotConfig:
    """Parse [scalp] section from config.toml raw dict."""
    scalp_raw = raw.get("scalp", {})
    if not scalp_raw:
        return ScalpBotConfig(enabled=False)

    pairs: dict[str, ScalpPairConfig] = {}
    for key, val in scalp_raw.get("pairs", {}).items():
        if not isinstance(val, dict) or "symbol" not in val:
            continue
        pairs[key] = ScalpPairConfig(
            symbol=val["symbol"],
            interval=int(val.get("interval", 5)),
            ema_fast=int(val.get("ema_fast", 9)),
            ema_slow=int(val.get("ema_slow", 21)),
            rsi_period=int(val.get("rsi_period", 9)),
            atr_period=int(val.get("atr_period", 14)),
            volume_ma_period=int(val.get("volume_ma_period", 20)),
            volume_mult=float(val.get("volume_mult", 1.5)),
            atr_stop_mult=float(val.get("atr_stop_mult", 1.0)),
            atr_tp_mult=float(val.get("atr_tp_mult", 2.0)),
            risk_pct=float(val.get("risk_pct", 0.01)),
            min_signals=int(val.get("min_signals", 3)),
            signal_cooldown_sec=float(val.get("signal_cooldown_sec", 60.0)),
            loss_cooldown_sec=float(val.get("loss_cooldown_sec", 120.0)),
            min_candles_required=int(val.get("min_candles_required", 30)),
        )

    return ScalpBotConfig(
        enabled=bool(scalp_raw.get("enabled", False)),
        pairs=pairs,
        max_concurrent_positions=int(scalp_raw.get("max_concurrent_positions", 2)),
        daily_loss_limit_pct=float(scalp_raw.get("daily_loss_limit_pct", 5.0)),
        allocated_capital_usd=float(scalp_raw.get("allocated_capital_usd", 150.0)),
        order_type=str(scalp_raw.get("order_type", "limit")),
        rest_seed_candles=int(scalp_raw.get("rest_seed_candles", 100)),
    )
