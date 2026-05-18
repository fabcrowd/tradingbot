"""BotState exchange entry throttle after consecutive rejects."""

from __future__ import annotations

import time

from state import BotState


def test_note_order_reject_sets_pause_after_threshold() -> None:
    st = BotState()
    assert st.exchange_entries_throttled() is False
    st.note_order_reject("bad", max_consecutive=2, consecutive_pause_sec=60.0)
    assert st.exchange_entries_throttled() is False
    st.note_order_reject("bad", max_consecutive=2, consecutive_pause_sec=60.0)
    assert st.exchange_entries_throttled() is True
    st.order_reject_pause_until = 0.0
    assert st.exchange_entries_throttled() is False


def test_note_order_success_resets_consecutive_count() -> None:
    st = BotState()
    st.note_order_reject("a", max_consecutive=2, consecutive_pause_sec=60.0)
    st.note_order_success()
    st.note_order_reject("b", max_consecutive=2, consecutive_pause_sec=60.0)
    assert st.exchange_entries_throttled() is False


def test_insufficient_funds_sets_separate_cooldown() -> None:
    st = BotState()
    st.note_order_reject(
        "INSUFFICIENT_FUNDS",
        max_consecutive=99,
        insufficient_funds_cooldown_sec=600.0,
    )
    assert st.exchange_entries_throttled() is True
    st.insufficient_funds_until = time.time() - 1.0
    st.order_reject_pause_until = 0.0
    assert st.exchange_entries_throttled() is False


def test_scalp_exchange_throttle_diag_reflects_timers() -> None:
    st = BotState()
    st.note_order_reject(
        "PREVIEW_INSUFFICIENT_FUNDS_FOR_FUTURES",
        max_consecutive=99,
        insufficient_funds_cooldown_sec=90.0,
    )
    d = st.scalp_exchange_throttle_diag()
    assert d["exchange_entries_throttled"] is True
    assert d["exchange_entry_cooldown_enabled"] is True
    assert d["exchange_throttle_insufficient_remain_sec"] > 80.0
    assert "INSUFFICIENT" in (d.get("last_order_reject_reason") or "").upper()


def test_exchange_entry_cooldown_disabled_skips_timers() -> None:
    st = BotState()
    st.exchange_entry_cooldown_enabled = False
    st.note_order_reject(
        "INSUFFICIENT_FUNDS_FOR_FUTURES",
        max_consecutive=1,
        consecutive_pause_sec=3600.0,
        insufficient_funds_cooldown_sec=3600.0,
    )
    assert st.exchange_entries_throttled() is False
    assert st.insufficient_funds_until == 0.0
    assert st.order_reject_pause_until == 0.0
    assert "INSUFFICIENT" in (st.last_order_reject_reason or "").upper()
