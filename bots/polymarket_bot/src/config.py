from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class BotConfig:
    bot_id: str
    display_name: str
    version: str
    env: str
    host: str
    port: int
    mode: str
    cycle_ms: int
    use_public_polymarket_feed: bool
    gamma_api_url: str
    market_query: str
    preferred_symbols: tuple[str, ...]
    live_dry_run: bool
    polymarket_clob_host: str
    polymarket_chain_id: int
    polymarket_signature_type: int
    daily_loss_limit_usd: float
    max_position_pct: float
    max_portfolio_exposure_usd: float
    polymarket_private_key: str
    polymarket_funder: str
    polymarket_api_key: str
    polymarket_api_secret: str
    polymarket_api_passphrase: str
    # position / sizing
    quote_size_usd: float
    max_concurrent_positions: int
    per_market_max_positions: int
    # signal thresholds
    taker_min_momentum_pct: float
    taker_min_net_edge_bps: float
    # ws endpoints (frozen defaults, overridable for testing)
    rtds_url: str
    market_ws_url: str
    # strategy router
    sports_alloc_pct: float
    maker_alloc_pct: float
    crypto_alloc_pct: float
    # sports arb
    odds_api_key: str
    the_odds_api_keys: str
    sports_min_edge_bps: float
    sports_poll_sec: float
    sports_cooldown_sec: float
    # maker spread capture
    maker_min_spread_pct: float
    maker_min_volume_usd: float
    maker_scan_all_categories: bool
    # crypto taker schedule
    crypto_taker_always_active: bool
    binance_futures_ws: str

    @property
    def has_live_trading_credentials(self) -> bool:
        return bool(self.polymarket_private_key and self.polymarket_funder)


def _bool_env(key: str, default: str = "true") -> bool:
    return os.getenv(key, default).lower() in {"1", "true", "yes", "on"}


def load_config() -> BotConfig:
    load_dotenv()
    symbols_raw = os.getenv("PMBOT_PREFERRED_SYMBOLS", "BTC,ETH")
    return BotConfig(
        bot_id=os.getenv("PMBOT_BOT_ID", "polymarket_bot"),
        display_name=os.getenv("PMBOT_DISPLAY_NAME", "Polymarket Bot"),
        version=os.getenv("PMBOT_VERSION", "0.1.0"),
        env=os.getenv("PMBOT_ENV", "dev"),
        host=os.getenv("PMBOT_HOST", "0.0.0.0"),
        port=int(os.getenv("PMBOT_PORT", "8091")),
        mode=os.getenv("PMBOT_MODE", "paper"),
        cycle_ms=int(os.getenv("PMBOT_CYCLE_MS", "1000")),
        use_public_polymarket_feed=_bool_env("PMBOT_USE_PUBLIC_POLYMARKET_FEED"),
        gamma_api_url=os.getenv("PMBOT_GAMMA_API_URL", "https://gamma-api.polymarket.com"),
        market_query=os.getenv("PMBOT_MARKET_QUERY", "5-minute 15-minute"),
        preferred_symbols=tuple(s.strip().upper() for s in symbols_raw.split(",") if s.strip()),
        live_dry_run=_bool_env("PMBOT_LIVE_DRY_RUN"),
        polymarket_clob_host=os.getenv("PMBOT_POLY_CLOB_HOST", "https://clob.polymarket.com"),
        polymarket_chain_id=int(os.getenv("PMBOT_POLY_CHAIN_ID", "137")),
        polymarket_signature_type=int(os.getenv("PMBOT_POLY_SIGNATURE_TYPE", "1")),
        daily_loss_limit_usd=float(os.getenv("PMBOT_DAILY_LOSS_LIMIT_USD", "20")),
        max_position_pct=float(os.getenv("PMBOT_MAX_POSITION_PCT", "0.5")),
        max_portfolio_exposure_usd=float(os.getenv("PMBOT_MAX_PORTFOLIO_EXPOSURE_USD", "200")),
        polymarket_private_key=os.getenv("PMBOT_POLY_PRIVATE_KEY", ""),
        polymarket_funder=os.getenv("PMBOT_POLY_FUNDER", ""),
        polymarket_api_key=os.getenv("PMBOT_POLY_API_KEY", ""),
        polymarket_api_secret=os.getenv("PMBOT_POLY_API_SECRET", ""),
        polymarket_api_passphrase=os.getenv("PMBOT_POLY_API_PASSPHRASE", ""),
        quote_size_usd=float(os.getenv("PMBOT_QUOTE_SIZE_USD", "5.0")),
        max_concurrent_positions=int(os.getenv("PMBOT_MAX_CONCURRENT_POSITIONS", "10")),
        per_market_max_positions=int(os.getenv("PMBOT_PER_MARKET_MAX_POSITIONS", "1")),
        taker_min_momentum_pct=float(os.getenv("PMBOT_TAKER_MIN_MOMENTUM_PCT", "0.3")),
        taker_min_net_edge_bps=float(os.getenv("PMBOT_TAKER_MIN_NET_EDGE_BPS", "20")),
        rtds_url=os.getenv("PMBOT_RTDS_URL", "wss://ws-live-data.polymarket.com"),
        market_ws_url=os.getenv("PMBOT_MARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"),
        sports_alloc_pct=float(os.getenv("PMBOT_SPORTS_ALLOC_PCT", "50")),
        maker_alloc_pct=float(os.getenv("PMBOT_MAKER_ALLOC_PCT", "30")),
        crypto_alloc_pct=float(os.getenv("PMBOT_CRYPTO_ALLOC_PCT", "20")),
        odds_api_key=os.getenv("PMBOT_ODDS_API_KEY", ""),
        the_odds_api_keys=os.getenv("PMBOT_THE_ODDS_API_KEYS", ""),
        sports_min_edge_bps=float(os.getenv("PMBOT_SPORTS_MIN_EDGE_BPS", "50")),
        sports_poll_sec=float(os.getenv("PMBOT_SPORTS_POLL_SEC", "30")),
        sports_cooldown_sec=float(os.getenv("PMBOT_SPORTS_COOLDOWN_SEC", "30")),
        maker_min_spread_pct=float(os.getenv("PMBOT_MAKER_MIN_SPREAD_PCT", "2.0")),
        maker_min_volume_usd=float(os.getenv("PMBOT_MAKER_MIN_VOLUME_USD", "10000")),
        maker_scan_all_categories=_bool_env("PMBOT_MAKER_SCAN_ALL_CATEGORIES"),
        crypto_taker_always_active=_bool_env("PMBOT_CRYPTO_TAKER_ALWAYS_ACTIVE", "false"),
        binance_futures_ws=os.getenv("PMBOT_BINANCE_FUTURES_WS", "wss://fstream.binance.com/ws"),
    )
