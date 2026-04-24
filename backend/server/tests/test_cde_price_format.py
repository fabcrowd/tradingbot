"""CDE price tick resolution and REST string formatting (offline, no API keys).

Run from repo ``backend`` directory:
  python -m unittest server.tests.test_cde_price_format
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from server.coinbase_order_manager import (  # noqa: E402
    _build_coinbase_scalp_order_configurations,
    _cde_price_increment_from_product,
    _format_cde_order_price,
    _normalize_get_product_payload,
    _retry_price_precision_from_coinbase,
)


class TestNormalizeGetProduct(unittest.TestCase):
    def test_merges_nested_product(self) -> None:
        raw = {"product": {"price_increment": "1", "quote_increment": "0.01"}}
        d = _normalize_get_product_payload(raw)
        self.assertEqual(d.get("price_increment"), "1")
        self.assertEqual(d.get("quote_increment"), "0.01")


class TestCdePriceIncrement(unittest.TestCase):
    def test_bip_disagreement_uses_coarser_tick(self) -> None:
        raw = {"price_increment": "1", "quote_increment": "0.01"}
        inc = _cde_price_increment_from_product(raw)
        self.assertEqual(inc, 1.0)

    def test_future_product_details_price(self) -> None:
        raw = {
            "quote_increment": "0.01",
            "future_product_details": {"price_increment": "1"},
        }
        inc = _cde_price_increment_from_product(raw)
        self.assertEqual(inc, 1.0)

    def test_slp_quote_only(self) -> None:
        raw = {"quote_increment": "0.01"}
        inc = _cde_price_increment_from_product(raw)
        self.assertEqual(inc, 0.01)

    def test_xpp_nested_product(self) -> None:
        raw = {"product": {"price_increment": "0.0001"}}
        inc = _cde_price_increment_from_product(raw)
        self.assertEqual(inc, 0.0001)


class TestFormatCdeOrderPrice(unittest.TestCase):
    def test_bip_integer_dollars(self) -> None:
        self.assertEqual(_format_cde_order_price(95000.12, 1.0), "95000")

    def test_slp_cents(self) -> None:
        self.assertEqual(_format_cde_order_price(84.0003, 0.01), "84")

    def test_slp_sub_cent_input(self) -> None:
        self.assertEqual(_format_cde_order_price(84.015, 0.01), "84.02")

    def test_xpp_four_decimals(self) -> None:
        self.assertEqual(_format_cde_order_price(2.345678, 0.0001), "2.3457")


class TestBuildConfigurations(unittest.TestCase):
    def test_take_profit_limit_bip(self) -> None:
        cfgs = _build_coinbase_scalp_order_configurations(
            order_type="take-profit-limit",
            side="SELL",
            base_size="1",
            is_perp=True,
            cde_inc=1.0,
            params={"limit_price": 95000.99},
        )
        self.assertEqual(len(cfgs), 2)
        inner = cfgs[0]["limit_limit_gtc"]
        self.assertEqual(inner["limit_price"], "95001")

    def test_limit_entry_slp(self) -> None:
        cfgs = _build_coinbase_scalp_order_configurations(
            order_type="limit",
            side="BUY",
            base_size="2",
            is_perp=True,
            cde_inc=0.01,
            params={"limit_price": 84.3},
        )
        lim = cfgs[0]["limit_limit_gtc"]["limit_price"]
        self.assertEqual(lim, "84.3")


class TestRetryPricePrecision(unittest.TestCase):
    def test_detects_preview(self) -> None:
        self.assertTrue(
            _retry_price_precision_from_coinbase(
                "preview_failure_reason=PREVIEW_INVALID_PRICE_PRECISION",
            ),
        )

    def test_detects_stop_precision(self) -> None:
        self.assertTrue(
            _retry_price_precision_from_coinbase("PREVIEW_INVALID_STOP_PRICE_PRECISION"),
        )

    def test_ignores_other(self) -> None:
        self.assertFalse(_retry_price_precision_from_coinbase("INSUFFICIENT_FUNDS"))


if __name__ == "__main__":
    unittest.main()
