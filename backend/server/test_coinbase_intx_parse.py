"""Offline tests for INTX position row parsing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from server.coinbase_intx_reconcile import parse_perps_position_row, venue_legs_by_product  # noqa: E402


class TestParsePerpsPosition(unittest.TestCase):
    def test_fcm_long_contracts(self) -> None:
        row = {
            "product_id": "SLP-20DEC30-CDE",
            "side": "LONG",
            "number_of_contracts": "1",
            "avg_entry_price": "91.23",
        }
        leg = parse_perps_position_row(row, {"SLP-20DEC30-CDE": "SOL_USD"})
        assert leg is not None
        self.assertEqual(leg.direction, "long")
        self.assertEqual(leg.qty, 1.0)
        self.assertEqual(leg.entry_price, 91.23)
        self.assertEqual(leg.pair_key, "SOL_USD")

    def test_net_size_short(self) -> None:
        row = {
            "product_id": "SLP-20DEC30-CDE",
            "net_size": "-5",
            "entry_vwap": "91.23",
        }
        leg = parse_perps_position_row(row, {"SLP-20DEC30-CDE": "SOL_USD"})
        assert leg is not None
        self.assertEqual(leg.direction, "short")
        self.assertEqual(leg.qty, 5.0)
        self.assertEqual(leg.entry_price, 91.23)
        self.assertEqual(leg.pair_key, "SOL_USD")

    def test_zero_skipped(self) -> None:
        row = {"product_id": "BIP-20DEC30-CDE", "net_size": "0"}
        self.assertIsNone(parse_perps_position_row(row, {}))


if __name__ == "__main__":
    unittest.main()
