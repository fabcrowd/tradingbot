"""Debug Coinbase CDP auth (401) — clock skew, PEM shape, key/secret pair.

Run from repo root:
  python -m backend.server.coinbase_credential_diagnostics
  python -m backend.server.coinbase_credential_diagnostics 2

Does not print secrets; only masked key tail and structural checks."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from email.utils import parsedate_to_datetime

import requests
from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv

from .config import (
    PROJECT_ROOT,
    _normalize_coinbase_pem,
    _sanitize_coinbase_api_key,
)


def _mask_key(k: str) -> str:
    k = k.strip()
    if len(k) <= 24:
        return "(too short to mask)"
    return f"{k[:28]}…{k[-8:]}"


def main() -> int:
    logging.getLogger("coinbase.RESTClient").setLevel(logging.CRITICAL)

    p = argparse.ArgumentParser(description="Coinbase CDP credential diagnostics")
    p.add_argument(
        "slot",
        nargs="?",
        default="1",
        choices=("1", "2"),
        help="Which .env key pair: 1=COINBASE_API_KEY, 2=COINBASE_API_KEY2",
    )
    args = p.parse_args()

    load_dotenv(PROJECT_ROOT / ".env", override=True)

    if args.slot == "2":
        raw_k = _sanitize_coinbase_api_key(os.getenv("COINBASE_API_KEY2", ""))
        raw_s = _normalize_coinbase_pem(os.getenv("COINBASE_API_SECRET2", ""))
        label = "COINBASE_API_KEY2"
    else:
        raw_k = _sanitize_coinbase_api_key(os.getenv("COINBASE_API_KEY", ""))
        raw_s = _normalize_coinbase_pem(os.getenv("COINBASE_API_SECRET", ""))
        label = "COINBASE_API_KEY"

    print("Coinbase CDP diagnostics")
    print("-" * 50)
    print(f"Slot: {args.slot} ({label})")
    print(f"Key (masked): {_mask_key(raw_k)}")
    print(f"Key length: {len(raw_k)}  |  starts with 'organizations/': {raw_k.startswith('organizations/')}")

    if not raw_k or not raw_s:
        print("FAIL: missing key or secret in .env for this slot")
        return 1

    # PEM load (same as coinbase-advanced-py jwt_generator)
    try:
        serialization.load_pem_private_key(raw_s.encode("utf-8"), password=None)
        print("PEM: OK (EC private key parses)")
    except Exception as e:
        print(f"PEM: FAIL — {e}")
        print("  Fix: re-copy the full EC private key from CDP (one PEM block, matching this API key).")
        return 1

    # Clock vs Coinbase (JWT nbf/exp are ±120s in SDK)
    try:
        r = requests.head("https://api.coinbase.com/", timeout=10)
        ds = r.headers.get("Date")
        if ds:
            server_ts = parsedate_to_datetime(ds).timestamp()
            local_ts = time.time()
            skew = local_ts - server_ts
            print(f"Clock: local UNIX {local_ts:.0f}  |  api.coinbase.com Date skew {skew:+.1f}s")
            if abs(skew) > 90:
                print(
                    "WARN: skew > 90s can cause 401 on CDP JWT. "
                    "Sync Windows time: Settings → Time & language → Sync now.",
                )
        else:
            print("Clock: no Date header from api.coinbase.com (skipped)")
    except Exception as e:
        print(f"Clock: could not reach api.coinbase.com — {e}")

    # Live REST
    try:
        from coinbase.rest import RESTClient

        c = RESTClient(api_key=raw_k, api_secret=raw_s)
        out = c.get_accounts(limit=1)
        d = out if isinstance(out, dict) else getattr(out, "__dict__", {})
        n = len(d.get("accounts", [])) if isinstance(d, dict) else -1
        print(f"REST get_accounts(1): OK (accounts in response: {n})")
        return 0
    except Exception as e:
        print(f"REST get_accounts(1): FAIL — {type(e).__name__}: {e}")
        print(
            "If PEM OK but REST 401: key id and secret are not the same CDP key, "
            "or the key was revoked/regenerated after you copied the secret.",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
