"""Kraken fee schedule — resolves maker/taker fee from 30-day volume."""

from __future__ import annotations

# (volume_threshold_usd, maker_bps, taker_bps)
# Sorted ascending by threshold. Last match wins.
KRAKEN_FEE_TIERS: list[tuple[float, int, int]] = [
    (0,           20, 20),
    (50_000,      16, 16),
    (100_000,     12, 12),
    (250_000,      8,  8),
    (500_000,      4,  4),
    (1_000_000,    2,  2),
    (10_000_000,   0,  1),
    (100_000_000,  0,  0),
]


def maker_fee_bps(volume_30d: float) -> int:
    """Return the maker fee in bps for a given 30-day USD volume."""
    fee = KRAKEN_FEE_TIERS[0][1]
    for threshold, maker, _taker in KRAKEN_FEE_TIERS:
        if volume_30d >= threshold:
            fee = maker
        else:
            break
    return fee


def taker_fee_bps(volume_30d: float) -> int:
    """Return the taker fee in bps for a given 30-day USD volume."""
    fee = KRAKEN_FEE_TIERS[0][2]
    for threshold, _maker, taker in KRAKEN_FEE_TIERS:
        if volume_30d >= threshold:
            fee = taker
        else:
            break
    return fee


def current_tier_info(volume_30d: float) -> dict:
    """Return a summary of the current tier and progress to the next."""
    current_threshold = 0.0
    current_maker = KRAKEN_FEE_TIERS[0][1]
    next_threshold: float | None = None
    next_maker: int | None = None

    for i, (threshold, maker, _taker) in enumerate(KRAKEN_FEE_TIERS):
        if volume_30d >= threshold:
            current_threshold = threshold
            current_maker = maker
            if i + 1 < len(KRAKEN_FEE_TIERS):
                next_threshold = KRAKEN_FEE_TIERS[i + 1][0]
                next_maker = KRAKEN_FEE_TIERS[i + 1][1]
            else:
                next_threshold = None
                next_maker = None
        else:
            break

    result: dict = {
        "volume_30d": volume_30d,
        "tier_threshold": current_threshold,
        "maker_fee_bps": current_maker,
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
