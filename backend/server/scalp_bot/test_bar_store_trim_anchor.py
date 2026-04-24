"""bar_store.load_bars trim_anchor: wall vs latest-bar (WFO alignment)."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pyarrow as pa

from scalp_bot import bar_store


def test_latest_bar_anchor_retains_stale_tape_when_wall_trims_all(monkeypatch) -> None:
    """If newest candle is days behind wall clock, wall-anchor trim can drop everything."""
    latest = 1_800_000_000
    oldest = latest - 10 * 86400
    ts = np.arange(oldest, latest + 1, 3600, dtype=np.int64)
    n = len(ts)
    z = np.zeros(n, dtype=np.float64)
    o = np.ones(n, dtype=np.float64)
    table = pa.table(
        {
            "timestamp": ts,
            "open": o,
            "high": o,
            "low": o,
            "close": o,
            "volume": o,
            "vwap": o,
            "trades": z.astype(np.int64),
        }
    )
    monkeypatch.setattr(bar_store, "_load_table", lambda _path: table)
    now_wall = float(latest) + 8 * 86400.0
    with patch("scalp_bot.bar_store.time.time", return_value=now_wall):
        wall_bars = bar_store.load_bars("TEST-PERP", 5, last_n_days=5, trim_anchor="wall")
        lb_bars = bar_store.load_bars("TEST-PERP", 5, last_n_days=5, trim_anchor="latest_bar")
    assert wall_bars is None
    assert lb_bars is not None
    assert len(lb_bars["timestamp"]) >= 10
    lb_ts = lb_bars["timestamp"]
    assert int(lb_ts[0]) >= int(latest - 5 * 86400)


def test_rolling_windows_counts_skipped_positions() -> None:
    from scalp_bot.scalp_wfo import rolling_windows

    t0 = 1_000_000
    # Sparse bars: many window positions fail the 50/20 bar floors
    ts = np.array([t0 + i * 3600 for i in range(80)], dtype=np.int64)
    o = np.ones(80, dtype=np.float64)
    bars = {
        "timestamp": ts,
        "open": o,
        "high": o,
        "low": o,
        "close": o,
        "volume": o,
        "vwap": o,
        "trades": np.zeros(80, dtype=np.int64),
    }
    wins, skipped = rolling_windows(bars, train_hours=500.0, holdout_hours=200.0, step_hours=50.0)
    assert isinstance(skipped, int)
    assert skipped >= 0
    assert isinstance(wins, list)
