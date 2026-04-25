"""
Download Binance spot klines (public API) for backtesting proxies.
No API key required.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path


def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = 1000,
) -> list[list]:
    """Return raw kline rows: [open_time, open, high, low, close, volume, ...]."""
    url = (
        "https://api.binance.com/api/v3/klines?"
        f"symbol={symbol}&interval={interval}&startTime={start_ms}&endTime={end_ms}&limit={limit}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-backtest-research/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def download_range(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    out_path: Path,
    sleep_s: float = 0.15,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    all_rows: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        batch = fetch_klines(symbol, interval, cursor, end_ms, 1000)
        if not batch:
            break
        all_rows.extend(batch)
        last_open = int(batch[-1][0])
        cursor = last_open + 1
        if len(batch) < 1000:
            break
        time.sleep(sleep_s)
    # trim to window
    all_rows = [r for r in all_rows if start_ms <= int(r[0]) <= end_ms]
    out_path.write_text(json.dumps({"symbol": symbol, "interval": interval, "klines": all_rows}))
    return len(all_rows)


def klines_to_ohlc(path: Path) -> tuple[list[int], list[float]]:
    data = json.loads(path.read_text())
    rows = data["klines"]
    ts = [int(r[0]) // 1000 for r in rows]
    close = [float(r[4]) for r in rows]
    return ts, close


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1m")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--out", type=Path, default=Path("data/btcusdt_1m.json"))
    args = p.parse_args()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * 24 * 60 * 60 * 1000
    n = download_range(args.symbol, args.interval, start_ms, end_ms, args.out)
    print(f"wrote {n} candles to {args.out}")
