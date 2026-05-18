"""Persistence helpers for ``scalp_trade_history.jsonl``."""

from __future__ import annotations

import json

import pytest

from scalp_bot import trade_history_store as store


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    return tmp_path


def test_append_and_load_roundtrip(isolated_data_dir) -> None:
    row = {
        "pair_key": "SOL_USD",
        "direction": "long",
        "strategy_mode": "sar_chop",
        "entry_ts": 100.0,
        "exit_ts": 200.0,
        "entry_price": 90.0,
        "exit_price": 91.0,
        "qty": 1.0,
        "pnl": 1.25,
        "reason": "tp",
        "simulated": False,
        "entry_cl_ord_id": "scalp_entry_abc123",
    }
    store.append_trade_history_row(row)
    path = isolated_data_dir / store.TRADE_HISTORY_FILE
    assert path.is_file()
    loaded = store.load_trade_history_tail(50)
    assert len(loaded) == 1
    assert loaded[0]["entry_cl_ord_id"] == "scalp_entry_abc123"
    assert loaded[0]["pnl"] == 1.25


def test_tail_limit_and_dedupe(isolated_data_dir) -> None:
    base = {
        "pair_key": "BTC_USD",
        "direction": "long",
        "strategy_mode": "sar_chop",
        "entry_ts": 1.0,
        "exit_ts": 2.0,
        "entry_price": 100.0,
        "exit_price": 101.0,
        "qty": 1.0,
        "pnl": 0.5,
        "reason": "stop",
        "simulated": False,
        "entry_cl_ord_id": "same-id",
    }
    store.append_trade_history_row({**base, "exit_ts": 10.0, "pnl": 0.1})
    store.append_trade_history_row({**base, "exit_ts": 10.0, "pnl": 0.9})
    store.append_trade_history_row({**base, "entry_cl_ord_id": "other", "exit_ts": 20.0, "pnl": 0.2})
    loaded = store.load_trade_history_tail(1)
    assert len(loaded) == 1
    assert loaded[0]["entry_cl_ord_id"] == "other"
    loaded2 = store.load_trade_history_tail(10)
    assert len(loaded2) == 2
    assert [r["exit_ts"] for r in loaded2] == [10.0, 20.0]
    assert loaded2[0]["pnl"] == 0.9


def test_malformed_lines_skipped(isolated_data_dir) -> None:
    path = isolated_data_dir / store.TRADE_HISTORY_FILE
    path.write_text('not json\n{"pair_key":"X","entry_cl_ord_id":"a","exit_ts":1}\n', encoding="utf-8")
    loaded = store.load_trade_history_tail(10)
    assert len(loaded) == 1
    assert loaded[0]["pair_key"] == "X"


def test_row_from_position_closed_event() -> None:
    ev = {
        "pair_key": "SOL_USD",
        "symbol": "SOL-PERP",
        "direction": "short",
        "strategy_mode": "sar_chop",
        "entry_ts": 100.0,
        "ts": 200.0,
        "entry_price": 84.0,
        "exit_price": 85.0,
        "qty": 2.0,
        "pnl": -2.0,
        "reason": "exchange_orphan_fill",
        "simulated": False,
        "entry_cl_ord_id": "scalp_entry_abc",
    }
    row = store.row_from_position_closed_event(ev)
    assert row is not None
    assert row["exit_ts"] == 200.0
    assert row["entry_cl_ord_id"] == "scalp_entry_abc"
    assert row["pnl"] == -2.0


def test_upsert_replaces_row(isolated_data_dir) -> None:
    base = {
        "pair_key": "XRP_USD",
        "direction": "long",
        "strategy_mode": "m",
        "entry_ts": 1.0,
        "exit_ts": 50.0,
        "entry_price": 1.0,
        "exit_price": 1.1,
        "qty": 10.0,
        "pnl": 0.5,
        "reason": "tp",
        "simulated": False,
        "entry_cl_ord_id": "scalp_entry_x",
    }
    store.append_trade_history_row(base)
    store.upsert_trade_history_row({**base, "pnl": 0.9})
    loaded = store.load_trade_history_tail(10)
    assert len(loaded) == 1
    assert loaded[0]["pnl"] == 0.9


def test_backfill_from_session_jsonl(isolated_data_dir) -> None:
    session = isolated_data_dir / "session_test.jsonl"
    session.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "scalp",
                        "subtype": "position_closed",
                        "pair_key": "BTC_USD",
                        "symbol": "BTC-PERP",
                        "direction": "long",
                        "entry_ts": 10.0,
                        "ts": 20.0,
                        "entry_price": 100.0,
                        "exit_price": 99.0,
                        "qty": 1.0,
                        "pnl": -1.0,
                        "reason": "stop",
                        "simulated": False,
                        "entry_cl_ord_id": "scalp_entry_bf1",
                    }
                ),
                json.dumps(
                    {
                        "event": "scalp",
                        "subtype": "position_closed",
                        "pair_key": "ETH_USD",
                        "simulated": True,
                        "entry_cl_ord_id": "skip_simulated",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    counts = store.backfill_trade_history_from_sessions(isolated_data_dir, dry_run=False)
    assert counts["found"] == 2
    assert counts["eligible"] == 1
    assert counts["inserted"] == 1
    loaded = store.load_trade_history_tail(10)
    assert len(loaded) == 1
    assert loaded[0]["entry_cl_ord_id"] == "scalp_entry_bf1"
