"""Parquet-backed bar store — appends closed candles and serves OHLCV arrays.

Windows: a single Parquet per symbol is read by WFO/tuner threads and written by
the candle append + REST backfill paths. Concurrent access without coordination
can raise ``PermissionError`` / sharing violations and, in the worst case, leave
a tiny file if a read fails mid-append. All public I/O for a path is serialized
via a per-file lock; transient lock errors are retried before failing.

Storage layout:
    data/coinbase_bars/{SYMBOL}_{INTERVAL}m.parquet   (Coinbase CDE perps)

Each Parquet file has columns:
    timestamp  int64   (unix epoch seconds, candle open)
    open       float64
    high       float64
    low        float64
    close      float64
    volume     float64
    vwap       float64
    trades     int64
"""

from __future__ import annotations

import errno
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Literal

_ui_alert_notifier: Callable[[str, str, str, str], None] | None = None


def set_ui_alert_notifier(fn: Callable[[str, str, str, str], None] | None) -> None:
    """Register ``fn(level, title, detail, source)`` for dashboard toasts (e.g. ``BotState.push_alert``)."""
    global _ui_alert_notifier
    _ui_alert_notifier = fn


def notify_ui_alert(level: str, title: str, detail: str, source: str = "bar_store") -> None:
    """Fire a dashboard alert when ``set_ui_alert_notifier`` has been wired (no-op otherwise)."""
    fn = _ui_alert_notifier
    if fn is None:
        return
    try:
        fn(level, title, detail, source)
    except Exception:
        LOG.debug("bar_store notify_ui_alert failed", exc_info=True)

import numpy as np

LOG = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PROJECT_DATA = _PROJECT_ROOT / "data"


def coinbase_rest_client_from_env():
    """Build ``coinbase.rest.RESTClient`` for Advanced Trade.

    If CDP credentials are set in ``.env`` at repo root, uses the same key selection as live
    trading (``COINBASE_CDP_CREDENTIAL_SLOT=2`` selects ``COINBASE_API_KEY2`` /
    ``COINBASE_API_SECRET2``); public candle endpoints still apply auth for better rate-limit headroom.

    Raises ``ImportError`` if ``dotenv`` / config helpers are missing or ``coinbase-advanced-py`` is not installed.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(_PROJECT_ROOT / ".env")
        from ..config import read_coinbase_creds_from_env
    except ImportError:
        raise

    from coinbase.rest import RESTClient

    _, key, secret = read_coinbase_creds_from_env()
    if key and secret:
        LOG.info("bar_store: Coinbase REST using API credentials from environment")
        return RESTClient(api_key=key, api_secret=secret)
    LOG.info(
        "bar_store: Coinbase REST unauthenticated public client (optional: COINBASE_API_KEY/SECRET or KEY2 + slot)"
    )
    return RESTClient()
_BAR_SUBDIR = "coinbase_bars"


def set_bar_store_venue(_venue: str = "") -> None:
    """Parquet directory is always ``coinbase_bars`` (Coinbase CDE only)."""
    global _BAR_SUBDIR
    _BAR_SUBDIR = "coinbase_bars"


def _data_dir() -> Path:
    return _PROJECT_DATA / _BAR_SUBDIR

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "vwap", "trades"]
_DTYPES = {
    "timestamp": np.int64,
    "open": np.float64,
    "high": np.float64,
    "low": np.float64,
    "close": np.float64,
    "volume": np.float64,
    "vwap": np.float64,
    "trades": np.int64,
}


def _file_key(symbol: str, interval: int) -> str:
    return f"{symbol.replace('/', '_')}_{interval}m"


def _parquet_path(symbol: str, interval: int) -> Path:
    return _data_dir() / f"{_file_key(symbol, interval)}.parquet"


_PATH_LOCK_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.Lock] = {}


def _parquet_io_lock(path: Path) -> threading.Lock:
    """One ``threading.Lock`` per resolved Parquet path (cross-thread, not async)."""
    key = str(path.resolve())
    with _PATH_LOCK_GUARD:
        lk = _PATH_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _PATH_LOCKS[key] = lk
        return lk


def _is_windows_sharing_violation(exc: BaseException) -> bool:
    """WinError 32 / transient file lock while another reader/writer holds the Parquet."""
    if isinstance(exc, PermissionError):
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 32:
            return True
        if getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM):
            return True
    if isinstance(exc, OSError):
        if getattr(exc, "winerror", None) == 32:
            return True
    return False


def _ensure_dir() -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# pandas-free Parquet I/O via pyarrow
# ---------------------------------------------------------------------------

def _parquet_starts_with_magic(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) == b"PAR1"
    except OSError:
        return False


def _is_likely_corrupt_parquet_error(exc: BaseException) -> bool:
    """Heuristic: truncated write, HTML error body saved as .parquet, or disk glitch."""
    msg = str(exc).lower()
    if "magic" in msg and ("footer" in msg or "parquet" in msg):
        return True
    if "not a parquet" in msg or "is this a 'parquet' file" in msg:
        return True
    if "thrift" in msg and ("deserialize" in msg or "tprotocolexception" in msg):
        return True
    if "deserializing page header" in msg:
        return True
    if "arrowinvalid" in type(exc).__name__.lower():
        return any(
            k in msg
            for k in (
                "magic",
                "footer",
                "not a 'parquet'",
                "could not read schema",
                "error creating dataset",
            )
        )
    return False


def _quarantine_bad_parquet(path: Path, reason: str) -> None:
    """Rename away so append/load can start fresh; operator can delete ``*.parquet.bad.*`` after review."""
    ts = int(time.time())
    dest = path.parent / f"{path.name}.bad.{ts}"
    try:
        path.rename(dest)
        LOG.warning(
            "bar_store: moved unreadable file to %s (%s) — history for this symbol will re-grow from REST/live feed",
            dest.name,
            reason,
        )
    except OSError:
        try:
            path.unlink()
            LOG.warning("bar_store: deleted unreadable %s (%s) after rename failed", path.name, reason)
        except OSError as e2:
            LOG.error("bar_store: could not quarantine %s: %s", path, e2)


def _load_table(path: Path):
    """Load Parquet file as a pyarrow Table, or None if missing/empty/unreadable."""
    if not path.exists():
        return None
    try:
        sz = path.stat().st_size
    except OSError:
        return None
    if sz == 0:
        try:
            path.unlink()
        except OSError:
            pass
        LOG.warning("bar_store: removed empty Parquet %s", path.name)
        return None

    import pyarrow.parquet as pq

    busy_delays = (0.02, 0.04, 0.08, 0.12, 0.2, 0.35, 0.55)
    for attempt, delay in enumerate((*busy_delays, 0.0)):
        try:
            return pq.read_table(path)
        except (PermissionError, OSError) as e:
            if attempt < len(busy_delays) and _is_windows_sharing_violation(e):
                time.sleep(delay)
                continue
            LOG.warning("bar_store: failed to read %s after retries: %s", path, type(e).__name__)
            return None
        except Exception as e:
            bad_magic = not _parquet_starts_with_magic(path)
            if bad_magic or _is_likely_corrupt_parquet_error(e):
                why = "missing PAR1 header" if bad_magic else "corrupt or truncated Parquet"
                LOG.warning("bar_store: cannot read %s: %s — %s", path.name, type(e).__name__, why)
                notify_ui_alert(
                    "warning",
                    "Bar file quarantined",
                    f"{path.name}: {why}. History will re-grow from REST/live candles; WFO may be thin until backfill completes.",
                    "bar_store",
                )
                _quarantine_bad_parquet(path, why)
            else:
                LOG.warning("bar_store: failed to read %s", path, exc_info=True)
            return None
    return None


def _write_table(path: Path, table) -> None:
    import os

    import pyarrow.parquet as pq

    _ensure_dir()
    tmp = path.parent / f"{path.name}.{os.getpid()}.tmp"
    pq.write_table(table, tmp, compression="zstd")
    busy_delays = (0.02, 0.03, 0.05, 0.08, 0.12, 0.18, 0.28, 0.4, 0.55)
    last_err: OSError | None = None
    for attempt, delay in enumerate((*busy_delays, 0.0)):
        try:
            os.replace(tmp, path)
            last_err = None
            break
        except OSError as e:
            last_err = e
            if attempt < len(busy_delays) and _is_windows_sharing_violation(e):
                time.sleep(delay)
                continue
            break
    if last_err is not None:
        LOG.error(
            "bar_store: os.replace failed for %s (%s) — leaving temp %s for recovery",
            path.name,
            last_err,
            tmp.name,
        )
        notify_ui_alert(
            "error",
            "Bar save failed (file lock)",
            f"Could not commit {path.name} after retries ({last_err!s}). "
            f"Leave temp {tmp.name} for recovery; close other programs using this Parquet.",
            "bar_store",
        )
        raise last_err
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass


def _arrays_to_table(arrays: dict):
    """Build a pyarrow Table from column-name → numpy-array mapping."""
    import pyarrow as pa
    return pa.table({col: arrays[col] for col in _COLUMNS})


def _table_to_arrays(table) -> dict[str, np.ndarray]:
    return {col: table.column(col).to_numpy() for col in _COLUMNS}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def append_candles(symbol: str, interval: int, candles: list[dict]) -> int:
    """Append candle dicts (keys matching _COLUMNS) to the Parquet file.

    Deduplicates by timestamp. Returns number of new rows written.
    """
    if not candles:
        return 0
    _ensure_dir()
    path = _parquet_path(symbol, interval)
    lock = _parquet_io_lock(path)
    with lock:
        try:
            st_existing = path.stat().st_size if path.exists() else 0
        except OSError:
            st_existing = 0
        existing = _load_table(path)
        # If a non-trivial file exists but read failed, do not write only new_rows
        # (would replace weeks of tape with one batch — the SOL/XRP regression).
        if existing is None and st_existing > 4096:
            LOG.error(
                "bar_store: refusing append for %s — existing file (%d bytes) unreadable; "
                "close other handles on this Parquet (Explorer, second bot, indexer) and retry.",
                path.name,
                st_existing,
            )
            notify_ui_alert(
                "warning",
                "Bar history locked or unreadable",
                f"{path.name} ({st_existing} bytes) could not be read — append skipped so WFO tape is not wiped. "
                "Close handles on that file (Explorer preview, second bot, DuckDB) and restart if needed.",
                "bar_store",
            )
            return 0

        existing_ts: set[int] = set()
        if existing is not None:
            existing_ts = set(existing.column("timestamp").to_pylist())

        new_rows = [c for c in candles if int(c["timestamp"]) not in existing_ts]
        if not new_rows:
            return 0

        new_arrays = {
            col: np.array([r[col] for r in new_rows], dtype=_DTYPES[col])
            for col in _COLUMNS
        }
        new_table = _arrays_to_table(new_arrays)

        if existing is not None:
            import pyarrow as pa
            merged = pa.concat_tables([existing, new_table])
        else:
            merged = new_table

        sort_idx = np.argsort(merged.column("timestamp").to_numpy())
        merged = merged.take(sort_idx.tolist())

        _write_table(path, merged)
        LOG.debug(
            "bar_store: appended %d candles to %s (total %d)",
            len(new_rows),
            path.name,
            merged.num_rows,
        )
        return len(new_rows)


def load_bars(
    symbol: str,
    interval: int,
    last_n_days: float | None = None,
    *,
    trim_anchor: Literal["wall", "latest_bar"] = "wall",
) -> dict[str, np.ndarray] | None:
    """Load OHLCV arrays from Parquet. Returns dict of numpy arrays or None.

    When ``last_n_days`` is set:
    - ``trim_anchor="wall"`` (default): keep rows with ``timestamp >= now − last_n_days`` (calendar trim from wall clock).
    - ``trim_anchor="latest_bar"``: keep rows with ``timestamp >= latest_bar_ts − last_n_days`` so stale/offline
      machines still retain a full rolling window ending at the last stored candle (WFO / readiness).
    """
    path = _parquet_path(symbol, interval)
    lock = _parquet_io_lock(path)
    with lock:
        table = _load_table(path)
        if table is None or table.num_rows == 0:
            return None

        arrays = _table_to_arrays(table)

        if last_n_days is not None:
            if trim_anchor == "wall":
                cutoff = time.time() - last_n_days * 86400.0
            else:
                latest_ts = float(arrays["timestamp"][-1])
                cutoff = latest_ts - last_n_days * 86400.0
            # Compare as float — timestamps are whole seconds but wall-clock cutoff is fractional.
            mask = arrays["timestamp"] >= cutoff
            if not mask.any():
                return None
            arrays = {k: v[mask] for k, v in arrays.items()}

        return arrays


def bar_count(symbol: str, interval: int) -> int:
    path = _parquet_path(symbol, interval)
    lock = _parquet_io_lock(path)
    with lock:
        table = _load_table(path)
        return table.num_rows if table is not None else 0


def candle_dict_from_feed(candle) -> dict:
    """Convert a candle_feed.Candle dataclass to the dict format bar_store expects."""
    return {
        "timestamp": int(candle.timestamp),
        "open": float(candle.open),
        "high": float(candle.high),
        "low": float(candle.low),
        "close": float(candle.close),
        "volume": float(candle.volume),
        "vwap": float(candle.vwap),
        "trades": int(candle.trades),
    }


# ---------------------------------------------------------------------------
# REST backfill — Coinbase Advanced Trade public candles
# ---------------------------------------------------------------------------


async def backfill_from_rest(
    symbol: str,
    interval: int,
    hours_needed: float,
    *,
    venue: str = "coinbase_perps",
    rate_limit_sec: float = 0.25,
) -> int:
    """Paginate Coinbase REST candles to fill *hours_needed* of history."""
    return await backfill_coinbase_public_candles(
        symbol, interval, hours_needed, rate_limit_sec=rate_limit_sec,
    )


def _coinbase_granularity(interval_minutes: int) -> str:
    m = max(1, int(interval_minutes))
    if m <= 1:
        return "ONE_MINUTE"
    if m <= 5:
        return "FIVE_MINUTE"
    if m <= 15:
        return "FIFTEEN_MINUTE"
    if m <= 60:
        return "ONE_HOUR"
    return "SIX_HOUR"


async def backfill_coinbase_public_candles(
    product_id: str,
    interval: int,
    hours_needed: float,
    *,
    rate_limit_sec: float = 0.25,
) -> int:
    """Fetch historical candles from Coinbase Advanced Trade public REST (unix start/end strings)."""
    import asyncio
    import time as _time

    try:
        client = coinbase_rest_client_from_env()
    except ImportError:
        LOG.error("bar_store: coinbase-advanced-py not installed — cannot backfill Coinbase candles")
        return 0

    gran = _coinbase_granularity(interval)
    sec_per_bar = max(60, interval * 60)
    target_ts = int(_time.time() - hours_needed * 3600.0)
    # Coinbase rejects time ranges that imply >350 candles; paginate in ~320-bar chunks.
    _max_candles_per_req = 320
    chunk_sec = _max_candles_per_req * sec_per_bar
    window_hi = int(_time.time())
    total_written = 0
    page = 0

    def _fetch_window(start: int, end: int) -> list[dict]:
        resp = client.get_public_candles(
            product_id, str(start), str(end), gran, limit=_max_candles_per_req,
        )
        if isinstance(resp, dict):
            candles = resp.get("candles") or []
        elif isinstance(resp, list):
            candles = resp
        else:
            raw = getattr(resp, "__dict__", {})
            candles = raw.get("candles") or getattr(resp, "candles", []) or []
        out: list[dict] = []
        for c in candles:
            try:
                _g = c.get if isinstance(c, dict) else lambda k, d=None: getattr(c, k, d)
                ts = int(float(_g("start", 0)))
                out.append({
                    "timestamp": ts,
                    "open": float(_g("open", 0)),
                    "high": float(_g("high", 0)),
                    "low": float(_g("low", 0)),
                    "close": float(_g("close", 0)),
                    "volume": float(_g("volume", 0)),
                    "vwap": float(_g("close", 0)),
                    "trades": int(_g("trade_count", 0) or 0),
                })
            except (TypeError, ValueError, KeyError, AttributeError):
                continue
        out.sort(key=lambda x: x["timestamp"])
        return out

    oldest_fetched = window_hi
    while window_hi > target_ts and page < 500:
        page += 1
        window_lo = max(target_ts, window_hi - chunk_sec)
        try:
            batch = await asyncio.to_thread(_fetch_window, window_lo, window_hi)
        except Exception:
            LOG.warning("bar_store Coinbase backfill: request failed for %s", product_id, exc_info=True)
            break

        if not batch:
            window_hi = window_lo - 1
            if window_hi <= target_ts:
                break
            await asyncio.sleep(rate_limit_sec)
            continue

        written = await asyncio.to_thread(append_candles, product_id, interval, batch)
        total_written += written
        oldest_in_batch = min(c["timestamp"] for c in batch)
        oldest_fetched = min(oldest_fetched, oldest_in_batch)

        LOG.info(
            "bar_store Coinbase backfill: %s/%dm page=%d rows=%d new=%d oldest=%s",
            product_id,
            interval,
            page,
            len(batch),
            written,
            _time.strftime("%Y-%m-%d %H:%M", _time.gmtime(oldest_fetched)),
        )

        if oldest_in_batch <= target_ts:
            break

        window_hi = oldest_in_batch - sec_per_bar
        if window_hi <= target_ts:
            break

        await asyncio.sleep(rate_limit_sec)

    LOG.info(
        "bar_store Coinbase backfill: %s/%dm — wrote %d new candles",
        product_id, interval, total_written,
    )
    return total_written
