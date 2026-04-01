"""Kraken fee schedules — resolves maker/taker fee from 30-day volume."""

from __future__ import annotations

# (volume_threshold_usd, maker_bps, taker_bps)
# Sorted ascending by threshold. Last match wins.
SPOT_CRYPTO_FEE_TIERS: list[tuple[float, int, int]] = [
    (0,              25, 40),
    (10_000,         20, 35),
    (50_000,         14, 24),
    (100_000,        12, 22),
    (250_000,        10, 20),
    (500_000,         8, 18),
    (1_000_000,       6, 16),
    (2_500_000,       4, 14),
    (5_000_000,       2, 12),
    (10_000_000,      0, 10),
    (100_000_000,     0,  8),
    (500_000_000,     0,  5),
]

STABLECOIN_FX_FEE_TIERS: list[tuple[float, int, int]] = [
    (0,              20, 20),
    (50_000,         16, 16),
    (100_000,        12, 12),
    (250_000,         8,  8),
    (500_000,         4,  4),
    (1_000_000,       2,  2),
    (10_000_000,      0,  1),
    (100_000_000,     0,  0),
]

MAKER_REBATE_FEE_TIERS: list[tuple[float, int, int]] = [
    (0,              23, 40),
    (10_000,         18, 35),
    (50_000,         12, 24),
    (100_000,        10, 22),
    (250_000,         8, 20),
    (500_000,         6, 18),
    (1_000_000,       4, 16),
    (2_500_000,       2, 14),
    (5_000_000,       0, 12),
    (10_000_000,     -2, 10),
    (100_000_000,    -2,  8),
    (500_000_000,    -2,  5),
]

USDG_FEE_TIERS: list[tuple[float, int, int]] = [
    (0,              0, 1),
    (100_000_000,    0, 0),
]

USDE_PROMO_FEE_TIERS: list[tuple[float, int, int]] = [
    (0, 0, 0),
]

FEE_SCHEDULE_TIERS: dict[str, list[tuple[float, int, int]]] = {
    "spot_crypto": SPOT_CRYPTO_FEE_TIERS,
    "stablecoin_fx": STABLECOIN_FX_FEE_TIERS,
    "maker_rebate": MAKER_REBATE_FEE_TIERS,
    "usdg": USDG_FEE_TIERS,
    "usde_promo": USDE_PROMO_FEE_TIERS,
}


def _tiers(schedule: str) -> list[tuple[float, int, int]]:
    return FEE_SCHEDULE_TIERS.get(schedule, SPOT_CRYPTO_FEE_TIERS)


def infer_fee_schedule(symbol: str) -> str:
    """Best-effort Kraken fee schedule classification by symbol.

    Kraken rules (2026):
      - USDG as base  -> usdg schedule (0% maker)
      - USDe as base  -> usde_promo schedule (0%/0% campaign)
      - Stablecoin/fiat as base (USDT/USD, USDC/USDT, DAI/USDT) -> stablecoin_fx
      - Pegged token as base (TBTC/BTC, WBTC/BTC) -> stablecoin_fx
      - Crypto as base with any quote (BTC/USD, XRP/USDT) -> spot_crypto
    """
    sym = symbol.upper()

    if sym.startswith("USDG/"):
        return "usdg"
    if sym.startswith("USDE/"):
        return "usde_promo"

    base, _sep, quote = sym.partition("/")

    stablecoin = {"USDT", "USDC", "DAI", "EURC", "PYUSD", "USDD", "USDS", "TUSD"}
    fiat = {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF"}
    pegged = {"TBTC", "WBTC", "METH", "JITOSOL", "LSETH", "LSSOL", "CMETH", "MSOL"}

    if base in stablecoin or base in fiat or base in pegged:
        return "stablecoin_fx"

    return "spot_crypto"


def maker_fee_bps(volume_30d: float, schedule: str = "spot_crypto") -> int:
    """Return maker fee in bps for a given 30-day USD volume and schedule."""
    tiers = _tiers(schedule)
    fee = tiers[0][1]
    for threshold, maker, _taker in tiers:
        if volume_30d >= threshold:
            fee = maker
        else:
            break
    return fee


def taker_fee_bps(volume_30d: float, schedule: str = "spot_crypto") -> int:
    """Return taker fee in bps for a given 30-day USD volume and schedule."""
    tiers = _tiers(schedule)
    fee = tiers[0][2]
    for threshold, _maker, taker in tiers:
        if volume_30d >= threshold:
            fee = taker
        else:
            break
    return fee


def current_tier_info(volume_30d: float, schedule: str = "spot_crypto") -> dict:
    """Return a summary of current tier and progress to next."""
    tiers = _tiers(schedule)
    current_threshold = 0.0
    current_maker = tiers[0][1]
    current_taker = tiers[0][2]
    next_threshold: float | None = None
    next_maker: int | None = None

    for i, (threshold, maker, taker) in enumerate(tiers):
        if volume_30d >= threshold:
            current_threshold = threshold
            current_maker = maker
            current_taker = taker
            if i + 1 < len(tiers):
                next_threshold = tiers[i + 1][0]
                next_maker = tiers[i + 1][1]
            else:
                next_threshold = None
                next_maker = None
        else:
            break

    result: dict = {
        "schedule": schedule,
        "volume_30d": volume_30d,
        "tier_threshold": current_threshold,
        "maker_fee_bps": current_maker,
        "taker_fee_bps": current_taker,
    }
    if next_threshold is not None:
        result["next_tier_threshold"] = next_threshold
        result["next_tier_maker_bps"] = next_maker
        result["volume_to_next_tier"] = next_threshold - volume_30d
        result["progress_pct"] = round(
            (volume_30d - current_threshold)
            / (next_threshold - current_threshold)
            * 100,
            1,
        )
    return result
