"""Polymarket crypto taker fee proxy from docs: fee = C * feeRate * p * (1-p)."""

from __future__ import annotations

CRYPTO_FEE_RATE = 0.072  # docs.polymarket.com trading/fees


def taker_fee_usdc(shares: float, price: float) -> float:
    """USDC fee for buying `shares` at probability price `p` in (0,1)."""
    p = min(max(price, 1e-6), 1.0 - 1e-6)
    return shares * CRYPTO_FEE_RATE * p * (1.0 - p)


def round_fee(x: float) -> float:
    return round(x + 1e-12, 5)
