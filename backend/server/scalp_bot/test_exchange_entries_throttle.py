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
