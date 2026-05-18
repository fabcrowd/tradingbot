"""Load configuration from config.toml + .env — scalp bot only."""

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


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_flag_any(*names: str) -> bool:
    return any(_env_flag(n) for n in names)


def _normalize_coinbase_pem(secret: str) -> str:
    """CDP EC keys are often pasted as one line with literal \\n sequences; PEM parsers need real newlines."""
    s = (secret or "").strip().lstrip("\ufeff")
    s = s.strip().strip('"').strip("'")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    if "-----BEGIN" in s and "\\n" in s:
        s = s.replace("\\n", "\n")
    return s.strip()


def _sanitize_coinbase_api_key(key: str) -> str:
    """Strip BOM / smart quotes / whitespace — invisible chars break JWT ``kid`` and cause 401."""
    s = (key or "").strip().lstrip("\ufeff")
    return s.strip().strip('"').strip("'").strip()


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    mode: str = "paper"
    #: Active CDP pair from ``.env``: ``\"1\"`` = COINBASE_API_KEY / SECRET; ``\"2\"`` = …_KEY2 / …_SECRET2.
    coinbase_credential_slot: str = "1"
    coinbase_api_key: str = ""
    coinbase_api_secret: str = ""
    # Coinbase REST rate limits (order pacing).
    rate_limit_order_per_sec: float = 10.0
    rate_limit_burst: int = 20

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    # Stub so session_logger and other callers that iterate pairs don't crash.
    @property
    def pairs(self) -> dict:
        return {}

    def pair_keys_for_trading(self) -> list[str]:
        return []

    def symbols(self) -> list[str]:
        return []


def read_coinbase_creds_from_env() -> tuple[str, str, str]:
    """Choose CDP API keypair from ``os.environ`` (after ``load_dotenv``).

    Returns ``(credential_slot, api_key, api_secret)`` with slot ``\"1\"`` or ``\"2\"``.
    Use ``COINBASE_CDP_CREDENTIAL_SLOT=2`` (or ``COINBASE_API_KEY_SLOT``) for the secondary pair.
    """
    slot_raw = (
        os.getenv("COINBASE_CDP_CREDENTIAL_SLOT") or os.getenv("COINBASE_API_KEY_SLOT") or "1"
    ).strip().lower()
    credential_slot = "2" if slot_raw in {"2", "secondary", "alt", "b", "key2"} else "1"

    if credential_slot == "2":
        return (
            credential_slot,
            _sanitize_coinbase_api_key(os.getenv("COINBASE_API_KEY2", "")),
            _normalize_coinbase_pem(os.getenv("COINBASE_API_SECRET2", "")),
        )
    return (
        credential_slot,
        _sanitize_coinbase_api_key(os.getenv("COINBASE_API_KEY", "")),
        _normalize_coinbase_pem(os.getenv("COINBASE_API_SECRET", "")),
    )


def load_raw_toml(config_path: Path | None = None) -> dict:
    """Return the raw parsed TOML dict without constructing AppConfig."""
    if config_path is None:
        config_path = PROJECT_ROOT / "config.toml"
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def load_config(config_path: Path | None = None) -> AppConfig:
    if config_path is None:
        config_path = PROJECT_ROOT / "config.toml"

    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    load_dotenv(PROJECT_ROOT / ".env")
    safe_startup = _env_flag_any("ARCEUS_SAFE_STARTUP", "MITCH_SAFE_STARTUP")
    forced_mode = (
        os.getenv("ARCEUS_FORCE_MODE") or os.getenv("MITCH_FORCE_MODE") or ""
    ).strip().lower()
    if safe_startup:
        forced_mode = "paper"

    srv = raw.get("server", {})
    server = ServerConfig(
        host=os.getenv("ARCEUS_SERVER_HOST")
        or os.getenv("MITCH_SERVER_HOST")
        or str(srv.get("host", "0.0.0.0")),
        port=int(
            os.getenv("ARCEUS_SERVER_PORT")
            or os.getenv("MITCH_SERVER_PORT")
            or str(srv.get("port", 8080))
        ),
        log_level=srv.get("log_level", "INFO"),
    )

    bot = raw.get("bot", {}) or {}
    mode = forced_mode or bot.get("mode", "paper")
    if mode not in ("paper", "live"):
        raise ValueError(f"Invalid mode: {mode!r} (must be 'paper' or 'live')")

    credential_slot, coinbase_api_key, coinbase_api_secret = read_coinbase_creds_from_env()
    if safe_startup or _env_flag_any("ARCEUS_DISABLE_LIVE_KEYS", "MITCH_DISABLE_LIVE_KEYS"):
        coinbase_api_key = ""
        coinbase_api_secret = ""

    return AppConfig(
        server=server,
        mode=mode,
        coinbase_credential_slot=credential_slot,
        coinbase_api_key=coinbase_api_key,
        coinbase_api_secret=coinbase_api_secret,
        rate_limit_order_per_sec=float(bot.get("rate_limit_order_per_sec", 10)),
        rate_limit_burst=int(bot.get("rate_limit_burst", 20)),
    )
