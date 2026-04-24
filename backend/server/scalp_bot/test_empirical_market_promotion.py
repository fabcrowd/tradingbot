"""Tests for empirical limit→market promotion and WFO fee helper."""

from __future__ import annotations

from scalp_bot.empirical_market_promotion import EmpiricalMarketPromotion
from scalp_bot.scalp_config import (
    ScalpBotConfig,
    effective_scalp_fee_bps_per_leg,
    wfo_fee_bps_per_leg,
)


def test_wfo_fee_follows_order_type_by_default() -> None:
    c = ScalpBotConfig()
    c.order_type = "limit"
    c.fee_bps_per_leg = 6.5
    c.fee_bps_taker_per_leg = 9.0
    c.wfo_assume_taker_fee = False
    assert wfo_fee_bps_per_leg(c) == 6.5
    c.order_type = "market"
    assert wfo_fee_bps_per_leg(c) == 9.0


def test_wfo_fee_assume_taker_overrides_limit() -> None:
    c = ScalpBotConfig()
    c.order_type = "limit"
    c.fee_bps_per_leg = 6.5
    c.fee_bps_taker_per_leg = 8.0
    c.wfo_assume_taker_fee = True
    assert wfo_fee_bps_per_leg(c) == 8.0


def test_empirical_resolve_disabled() -> None:
    c = ScalpBotConfig()
    c.empirical_market_promotion_enabled = False
    c.order_type = "limit"
    e = EmpiricalMarketPromotion(c)
    assert e.resolve_order_type("BTC_USD") == ("limit", False)


def test_hybrid_order_type_uses_limit_path_and_maker_fee() -> None:
    c = ScalpBotConfig()
    c.order_type = "hybrid"
    c.fee_bps_per_leg = 6.0
    c.fee_bps_taker_per_leg = 9.0
    assert effective_scalp_fee_bps_per_leg(c) == 6.0
    c.empirical_market_promotion_enabled = False
    e = EmpiricalMarketPromotion(c)
    assert e.resolve_order_type("BTC_USD") == ("limit", False)


def test_empirical_promoted_market_burst() -> None:
    c = ScalpBotConfig()
    c.empirical_market_promotion_enabled = True
    c.order_type = "limit"
    c.empirical_market_missed_move_bps = 5.0
    c.empirical_market_min_pattern_in_window = 3
    c.empirical_market_pattern_window_sec = 86_400.0
    c.empirical_market_promotion_entries = 2
    c.empirical_market_promotion_cooldown_sec = 0.0
    e = EmpiricalMarketPromotion(c)
    for _ in range(3):
        e.note_entry_ttl_cancel("BTC_USD", "BIP-CDE", "long", 100.0, 99.0, session_log=None)
        e.on_pair_mark("BTC_USD", 100.08, session_log=None)
    ot, promoted = e.resolve_order_type("BTC_USD")
    assert ot == "market" and promoted is True
    e.after_promoted_market_entry("BTC_USD")
    ot2, promoted2 = e.resolve_order_type("BTC_USD")
    assert ot2 == "market" and promoted2 is True
    e.after_promoted_market_entry("BTC_USD")
    ot3, promoted3 = e.resolve_order_type("BTC_USD")
    assert ot3 == "limit" and promoted3 is False


def test_dashboard_snapshot_keys() -> None:
    c = ScalpBotConfig()
    e = EmpiricalMarketPromotion(c)
    snap = e.dashboard_snapshot()
    assert "enabled" in snap and "promotion_remaining" in snap


def test_ttl_cancel_arms_promotion_immediately() -> None:
    c = ScalpBotConfig()
    c.empirical_market_promotion_enabled = True
    c.empirical_market_ttl_cancel_arms_promotion = True
    c.empirical_market_ttl_cancel_promotion_entries = 2
    e = EmpiricalMarketPromotion(c)
    e.note_entry_ttl_cancel("BTC_USD", "BIP-CDE", "long", 100.0, 99.5, session_log=None)
    assert e.resolve_order_type("BTC_USD") == ("market", True)
    e.after_promoted_market_entry("BTC_USD")
    assert e.resolve_order_type("BTC_USD") == ("market", True)
    e.after_promoted_market_entry("BTC_USD")
    assert e.resolve_order_type("BTC_USD") == ("limit", False)
