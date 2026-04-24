"""Get contract details for CDE products we'll use."""
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from backend.server.config import _normalize_coinbase_pem
from coinbase.rest import RESTClient

key = os.getenv("COINBASE_API_KEY", "").strip()
secret = _normalize_coinbase_pem(os.getenv("COINBASE_API_SECRET", ""))
client = RESTClient(api_key=key, api_secret=secret)

for pid in ["XPP-20DEC30-CDE", "BIT-24APR26-CDE", "SOL-24APR26-CDE"]:
    print(f"\n=== {pid} ===")
    try:
        resp = client.get_product(product_id=pid)
        raw = getattr(resp, "__dict__", resp) if not isinstance(resp, dict) else resp
        for k in ["product_id", "display_name", "base_currency_id", "quote_currency_id",
                   "price", "base_increment", "quote_increment", "base_min_size", "base_max_size",
                   "quote_min_size", "quote_max_size", "contract_size",
                   "contract_expiry_type", "venue", "status", "trading_disabled",
                   "future_product_details"]:
            val = raw.get(k, "N/A") if isinstance(raw, dict) else getattr(raw, k, "N/A")
            if val != "N/A":
                print(f"  {k}: {val}")
    except Exception as e:
        print(f"  ERROR: {e}")

# Also check if there are BTC/SOL perpetuals on CDE
print("\n=== SEARCH FOR CDE PERPS ===")
try:
    resp = client.get_products(product_type="FUTURE", limit=200)
    raw = getattr(resp, "__dict__", resp) if not isinstance(resp, dict) else resp
    products = raw.get("products", [])
    for p in products:
        pid = p.get("product_id") if isinstance(p, dict) else getattr(p, "product_id", "")
        display = p.get("display_name") if isinstance(p, dict) else getattr(p, "display_name", "")
        if "PERP" in str(display).upper() or "PERP" in str(pid).upper():
            print(f"  {pid:25s} display={display}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\nDone.")
