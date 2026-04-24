"""Resting-order snapshot shaping for dashboard (offline)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from server.coinbase_order_manager import CoinbaseOrderManager  # noqa: E402


class TestSlimOpenOrderSnapshot(unittest.TestCase):
    def test_top_level_prices(self) -> None:
        o = {
            "product_id": "BIP-20DEC30-CDE",
            "side": "SELL",
            "status": "OPEN",
            "order_type": "LIMIT",
            "limit_price": "76250",
            "base_size": "1",
        }
        s = CoinbaseOrderManager._slim_open_order_for_snapshot(o)
        self.assertEqual(s["limit_price"], 76250.0)
        self.assertEqual(s["trigger_price"], 0.0)
        self.assertEqual(s["base_size"], 1.0)

    def test_nested_limit_limit_gtc(self) -> None:
        o = {
            "product_id": "BIP-20DEC30-CDE",
            "side": "SELL",
            "status": "OPEN",
            "order_type": "LIMIT",
            "order_configuration": {
                "limit_limit_gtc": {"base_size": "1", "limit_price": "76250.0"},
            },
        }
        s = CoinbaseOrderManager._slim_open_order_for_snapshot(o)
        self.assertEqual(s["limit_price"], 76250.0)
        self.assertEqual(s["trigger_price"], 0.0)
        self.assertEqual(s["base_size"], 1.0)

    def test_nested_stop_limit_stop_limit_gtc(self) -> None:
        o = {
            "product_id": "BIP-20DEC30-CDE",
            "side": "SELL",
            "status": "OPEN",
            "order_type": "STOP_LIMIT",
            "order_configuration": {
                "stop_limit_stop_limit_gtc": {
                    "base_size": "1",
                    "limit_price": "75015",
                    "stop_price": "75050",
                },
            },
        }
        s = CoinbaseOrderManager._slim_open_order_for_snapshot(o)
        self.assertEqual(s["limit_price"], 75015.0)
        self.assertEqual(s["trigger_price"], 75050.0)
        self.assertEqual(s["base_size"], 1.0)


if __name__ == "__main__":
    unittest.main()
