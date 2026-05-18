"""Debug Coinbase CDP auth — clock skew, PEM shape, key/secret pair.

Run from repo root:
  python -m backend.server.coinbase_credential_diagnostics
  python -m backend.server.coinbase_credential_diagnostics 2

If REST returns HTTP 402 (disabled / entitlement), credential lines for that slot are removed
from ``.env``. Does not print secrets; only masked key tail and structural checks."""

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
from requests import HTTPError

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


def _rest_http_status(exc: BaseException) -> int | None:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, HTTPError) and cur.response is not None:
            return int(cur.response.status_code)
        nxt = getattr(cur, "__cause__", None)
        if nxt is None:
            nxt = getattr(cur, "__context__", None)
        cur = nxt
    return None


def _line_is_coinbase_primary_key(line: str) -> bool:
    s = line.lstrip("\ufeff")
    return bool(
        (
            (s.startswith("COINBASE_API_KEY=") or s.startswith("export COINBASE_API_KEY="))
            and not (
                s.startswith("COINBASE_API_KEY2")
                or s.startswith("export COINBASE_API_KEY2")
            )
        )
    )


def _line_is_coinbase_primary_secret(line: str) -> bool:
    s = line.lstrip("\ufeff")
    return bool(
        (s.startswith("COINBASE_API_SECRET=") or s.startswith("export COINBASE_API_SECRET="))
        and not (
            s.startswith("COINBASE_API_SECRET2")
            or s.startswith("export COINBASE_API_SECRET2")
        )
    )


def _line_is_coinbase_secondary_key(line: str) -> bool:
    s = line.lstrip("\ufeff")
    return s.startswith("COINBASE_API_KEY2=") or s.startswith("export COINBASE_API_KEY2=")


def _line_is_coinbase_secondary_secret(line: str) -> bool:
    s = line.lstrip("\ufeff")
    return s.startswith("COINBASE_API_SECRET2=") or s.startswith("export COINBASE_API_SECRET2=")


def _line_is_coinbase_slot_selector(line: str) -> bool:
    s = line.lstrip("\ufeff")
    return bool(
        s.startswith("COINBASE_CDP_CREDENTIAL_SLOT=")
        or s.startswith("export COINBASE_CDP_CREDENTIAL_SLOT=")
        or s.startswith("COINBASE_API_KEY_SLOT=")
        or s.startswith("export COINBASE_API_KEY_SLOT=")
    )


def _purge_coinbase_slot_from_dotenv(slot: str) -> int:
    """Remove credential assignment lines for that slot from ``.env``.

    Called when Coinbase REST returns HTTP 402 (often disabled entitlement). Drops the matching
    key/secret lines; if slot ``\"2\"`` is cleared, also removes ``COINBASE_CDP_CREDENTIAL_SLOT`` /
    ``COINBASE_API_KEY_SLOT`` lines so startup does not point at missing KEY2.

    Returns the number of lines removed (0 when no ``.env`` or no matches).
    """
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return 0

    def assignment_prefix_should_drop(assign: str) -> bool:
        a = assign.lstrip("\ufeff").strip()
        if slot == "2":
            return (
                _line_is_coinbase_secondary_key(a)
                or _line_is_coinbase_secondary_secret(a)
                or _line_is_coinbase_slot_selector(a)
            )
        return _line_is_coinbase_primary_key(a) or _line_is_coinbase_primary_secret(a)

    raw = path.read_text(encoding="utf-8")
    ends_with_newline = raw.endswith("\n") or raw.endswith("\r\n")
    out_lines: list[str] = []
    removed = 0
    for raw_line in raw.splitlines():
        before_comment = raw_line.split("#", 1)[0]
        stripped = before_comment.strip()
        if stripped and assignment_prefix_should_drop(stripped):
            removed += 1
            continue
        out_lines.append(raw_line)

    rebuilt = "\n".join(out_lines)
    if ends_with_newline:
        rebuilt += "\n"
    path.write_text(rebuilt, encoding="utf-8", newline="\n")
    return removed


def main() -> int:
    logging.getLogger("coinbase.RESTClient").setLevel(logging.CRITICAL)

    p = argparse.ArgumentParser(description="Coinbase CDP credential diagnostics")
    p.add_argument(
        "slot",
        nargs="?",
        default=None,
        choices=("1", "2"),
        help="Which .env key pair: 1=primary, 2=KEY2 (default: COINBASE_CDP_CREDENTIAL_SLOT or 1)",
    )
    args = p.parse_args()

    load_dotenv(PROJECT_ROOT / ".env", override=True)

    slot = args.slot
    if slot is None:
        raw = (
            os.getenv("COINBASE_CDP_CREDENTIAL_SLOT") or os.getenv("COINBASE_API_KEY_SLOT") or "1"
        ).strip().lower()
        slot = "2" if raw in {"2", "secondary", "alt", "b", "key2"} else "1"

    if slot == "2":
        raw_k = _sanitize_coinbase_api_key(os.getenv("COINBASE_API_KEY2", ""))
        raw_s = _normalize_coinbase_pem(os.getenv("COINBASE_API_SECRET2", ""))
        label = "COINBASE_API_KEY2"
    else:
        raw_k = _sanitize_coinbase_api_key(os.getenv("COINBASE_API_KEY", ""))
        raw_s = _normalize_coinbase_pem(os.getenv("COINBASE_API_SECRET", ""))
        label = "COINBASE_API_KEY"

    print("Coinbase CDP diagnostics")
    print("-" * 50)
    print(f"Slot: {slot} ({label})")
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
        http_code = _rest_http_status(e)
        if http_code == 402:
            try:
                n = _purge_coinbase_slot_from_dotenv(slot)
                print(
                    f"PURGE: removed {n} line(s) for slot {slot} from .env "
                    f"(HTTP {http_code}: key likely disabled or plan blocks this API)."
                )
            except OSError as pe:
                print(f"PURGE: could not rewrite .env — {pe}")
        else:
            print(
                "If PEM OK but REST 401: key id and secret are not the same CDP key, "
                "or the key was revoked/regenerated after you copied the secret.",
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
