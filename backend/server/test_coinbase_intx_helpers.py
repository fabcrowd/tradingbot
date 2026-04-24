"""Offline tests for Coinbase order helper logic (no API keys).

Run from repo root:
  python -m unittest server.test_coinbase_intx_helpers
(with cwd=backend and PYTHONPATH including .)

Or:
  cd backend && python -m unittest server.test_coinbase_intx_helpers
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# ``server`` package root (directory containing this file's parent as ``server``)
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from server.coinbase_order_manager import (  # noqa: E402
    _coinbase_order_error_text,
    _fcm_cde_order_base_size,
    _retry_exit_without_reduce_only,
)


class TestReduceOnlyRetry(unittest.TestCase):
    def test_retry_on_venue_reject(self) -> None:
        self.assertTrue(
            _retry_exit_without_reduce_only(
                "new_order_failure_reason=REDUCE_ONLY_NOT_ALLOWED_ON_VENUE",
            ),
        )

    def test_retry_on_preview_wording(self) -> None:
        self.assertTrue(
            _retry_exit_without_reduce_only("PREVIEW_REDUCE_ONLY_NOT_ALLOWED_ON_VENUE"),
        )

    def test_no_retry_on_other(self) -> None:
        self.assertFalse(_retry_exit_without_reduce_only("INSUFFICIENT_FUND"))
        self.assertFalse(_retry_exit_without_reduce_only(""))


class TestFcmCdeBaseSize(unittest.TestCase):
    def test_one_contract(self) -> None:
        self.assertEqual(_fcm_cde_order_base_size(1.0), "1")

    def test_rounds_and_floors_at_one(self) -> None:
        self.assertEqual(_fcm_cde_order_base_size(2.4), "2")
        self.assertEqual(_fcm_cde_order_base_size(0.1), "1")
        self.assertEqual(_fcm_cde_order_base_size(0.0), "1")


class TestErrorText(unittest.TestCase):
    def test_extracts_failure_reason(self) -> None:
        d = {
            "message": "bad",
            "new_order_failure_reason": "INVALID_LIMIT_PRICE",
            "error_details": "limit too far",
        }
        t = _coinbase_order_error_text(d)
        self.assertIn("INVALID_LIMIT_PRICE", t)
        self.assertIn("limit too far", t)


if __name__ == "__main__":
    unittest.main()
