"""Probe which Coinbase endpoints the current CDP key can access.

Distinguishes spot/Advanced Trade vs INTX/CDE derivatives permissions —
the bot needs derivatives perms for CDE perps; spot-only keys return 401
on get_fills, get_product (for CDE products), get_transaction_summary
(futures variants), and create_order with margin_type=CROSS.
"""

from __future__ import annotations

import os
import sys
import traceback

from dotenv import load_dotenv

from backend.server.config import (
    PROJECT_ROOT,
    _normalize_coinbase_pem,
    _sanitize_coinbase_api_key,
)


def _try(label: str, fn):
    try:
        out = fn()
        n = "?"
        if isinstance(out, dict):
            for k in ("accounts", "fills", "orders", "products"):
                if k in out:
                    n = f"{len(out[k])} {k}"
                    break
        print(f"  OK   {label}  ({n})")
        return True
    except Exception as e:
        msg = str(e).splitlines()[0][:140]
        print(f"  FAIL {label}  -> {type(e).__name__}: {msg}")
        return False


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    key = _sanitize_coinbase_api_key(os.getenv("COINBASE_API_KEY", ""))
    sec = _normalize_coinbase_pem(os.getenv("COINBASE_API_SECRET", ""))
    if not key or not sec:
        print("Missing COINBASE_API_KEY / COINBASE_API_SECRET in .env")
        return 1

    from coinbase.rest import RESTClient

    c = RESTClient(api_key=key, api_secret=sec)
    print(f"Probing Coinbase CDP key: ...{key[-12:]}")
    print("-" * 60)

    print("Spot / Advanced Trade:")
    _try("get_accounts(limit=1)", lambda: c.get_accounts(limit=1))
    _try("get_products(limit=1)", lambda: c.get_products(limit=1))
    _try("get_transaction_summary()", lambda: c.get_transaction_summary())

    print("\nDerivatives / INTX / CDE (required for scalp bot):")
    _try("get_fills(limit=1)", lambda: c.get_fills(limit=1))
    _try("list_orders(limit=1)", lambda: c.list_orders(limit=1))
    _try(
        "get_product('BIP-20DEC30-CDE')",
        lambda: c.get_product(product_id="BIP-20DEC30-CDE"),
    )
    _try("list_portfolios()", lambda: c.get_portfolios())
    _try(
        "get_futures_balance_summary()",
        lambda: c.get_futures_balance_summary(),
    )

    print("-" * 60)
    print("Interpretation:")
    print(
        "  - get_accounts/get_products OK but get_fills/list_orders FAIL = key lacks View/Trade perms\n"
        "    for the orders + INTX/CDE scope. Regenerate the CDP key in Coinbase Developer Platform with:\n"
        "      * 'View' on Accounts, Orders, Fills\n"
        "      * 'Trade' (and explicitly enable Derivatives / INTX scope)\n"
        "  - All OK = bot should be able to trade CDE perps."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
