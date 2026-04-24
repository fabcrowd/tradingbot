"""Tail the latest data/session_*.jsonl and write compact summaries to data/session_observer.jsonl.

Unlike tools/overnight_monitor.py (terminal log regexes), this follows structured session JSONL.

From repo root:
  python -m tools.session_jsonl_observer

Background (Windows / Cursor): run the same command; stop with Ctrl+C or taskkill.

Environment:
  SESSION_OBSERVER_LOG          Append-only JSONL output (default: data/bot_session_observer.jsonl)
  SESSION_OBSERVER_HTTP_SEC     If > 0, GET http://127.0.0.1:{port}/health every N seconds (default: 60)
  SESSION_OBSERVER_DATA_DIR     Session directory (default: data)
  SESSION_OBSERVER_FROM_START   If "1", read the whole current file once then follow (default: tail from EOF)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SESSION_OBSERVER_DATA_DIR", str(REPO_ROOT / "data")))
OUT_PATH = Path(os.environ.get("SESSION_OBSERVER_LOG", str(REPO_ROOT / "data" / "bot_session_observer.jsonl")))
# Bot session logs only — do not use session_*.jsonl (matches session_observer.jsonl).
_SESSION_NAME = re.compile(r"^session_\d{8}_\d{6}\.jsonl$")
HTTP_INTERVAL = float(os.environ.get("SESSION_OBSERVER_HTTP_SEC", "60"))
FROM_START = os.environ.get("SESSION_OBSERVER_FROM_START", "").strip() in {"1", "true", "yes"}
ROTATE_CHECK_SEC = float(os.environ.get("SESSION_OBSERVER_ROTATE_SEC", "15"))
# Session JSONL can include very frequent scalp_snapshot lines; skip duplicates within this window.
SNAPSHOT_MIN_SEC = float(os.environ.get("SESSION_OBSERVER_SNAPSHOT_MIN_SEC", "15"))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_session_path() -> Path | None:
    if not DATA_DIR.is_dir():
        return None
    files = [p for p in DATA_DIR.glob("session_*.jsonl") if _SESSION_NAME.match(p.name)]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _read_server_port() -> int:
    port = int(os.environ.get("TRADINGBOT_HTTP_PORT", "0") or 0)
    if port > 0:
        return port
    cfg = REPO_ROOT / "config.toml"
    if cfg.exists():
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[import-not-found]
        with cfg.open("rb") as f:
            data = tomllib.load(f)
        return int(data.get("server", {}).get("port", 8080))
    return 8080


def _health_ok(port: int) -> tuple[bool, str]:
    url = f"http://127.0.0.1:{port}/health"
    try:
        req = Request(url, method="GET", headers={"User-Agent": "session-jsonl-observer"})
        with urlopen(req, timeout=6.0) as resp:
            body = resp.read()
        data = json.loads(body.decode("utf-8"))
        return bool(data.get("ok")), json.dumps(data, separators=(",", ":"))[:500]
    except (URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as e:
        return False, str(e)[:500]


def _slim_record(obj: dict) -> dict:
    ev = obj.get("event")
    base: dict = {"ts": _utc_iso(), "session_event": ev}
    if ev == "session_start":
        base["mode"] = obj.get("mode")
        return base
    if ev == "snapshot":
        base["risk_halted"] = obj.get("risk_halted")
        base["risk_halt_reason"] = obj.get("risk_halt_reason")
        base["session_sec"] = obj.get("session_sec")
        return base
    if ev == "scalp":
        for k in (
            "subtype",
            "pair_key",
            "symbol",
            "outcome",
            "skip_reason",
            "champion_pairs",
            "n_pairs",
            "champion_found",
            "elapsed_sec",
            "pairs",
        ):
            if k in obj:
                base[k] = obj[k]
        return base
    if ev == "scalp_snapshot":
        s = obj.get("scalp") or {}
        op = s.get("operator") or {}
        tr = s.get("trader") or {}
        wfo = s.get("wfo") or {}
        ft = s.get("fee_tier") or {}
        base["startup_phase"] = s.get("startup_phase")
        base["standby"] = op.get("standby")
        base["can_go_live"] = op.get("can_go_live")
        base["open_count"] = tr.get("open_count")
        base["daily_pnl"] = tr.get("daily_pnl")
        base["champion_active"] = wfo.get("champion_active")
        base["active_modes"] = s.get("active_modes")
        base["has_champion_row"] = s.get("champion") is not None or bool(s.get("champions"))
        base["fee_last_poll_ts"] = ft.get("last_poll_ts")
        base["fee_poll_error"] = ft.get("poll_error")
        base["effective_maker_bps"] = ft.get("effective_maker_bps")
        return base
    base["note"] = "unhandled_event_shape"
    base["top_keys"] = list(obj.keys())[:24]
    return base


def _emit(rec: dict) -> None:
    line = json.dumps(rec, separators=(",", ":"), default=str)
    print(line, flush=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    path = _latest_session_path()
    if path is None:
        print("No data/session_*.jsonl under", DATA_DIR, file=sys.stderr)
        sys.exit(1)

    port = _read_server_port()
    last_http = 0.0
    last_rotate_check = 0.0
    last_scalp_snapshot_emit = 0.0
    current = path

    def _open_seek(p: Path):
        fh = p.open("r", encoding="utf-8", errors="replace")
        if FROM_START:
            fh.seek(0, 0)
        else:
            fh.seek(0, 2)
        return fh

    f = _open_seek(current)
    try:
        _emit(
            {
                "ts": _utc_iso(),
                "session_event": "observer_start",
                "watching": str(current),
                "from_start": FROM_START,
                "health_port": port,
                "http_probe_sec": HTTP_INTERVAL,
                "out": str(OUT_PATH),
            }
        )
        while True:
            line = f.readline()
            if not line:
                now = time.time()
                if HTTP_INTERVAL > 0 and now - last_http >= HTTP_INTERVAL:
                    ok, detail = _health_ok(port)
                    _emit(
                        {
                            "ts": _utc_iso(),
                            "session_event": "http_health",
                            "port": port,
                            "ok": ok,
                            "detail": detail,
                        }
                    )
                    last_http = now
                if now - last_rotate_check >= ROTATE_CHECK_SEC:
                    last_rotate_check = now
                    nxt = _latest_session_path()
                    if nxt is not None and nxt.resolve() != current.resolve():
                        f.close()
                        current = nxt
                        f = _open_seek(current)
                        _emit(
                            {
                                "ts": _utc_iso(),
                                "session_event": "observer_switch_file",
                                "watching": str(current),
                                "from_start": False,
                            }
                        )
                time.sleep(0.25)
                continue

            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                _emit({"ts": _utc_iso(), "session_event": "parse_error", "line_prefix": line[:200]})
                continue
            if "event" not in obj:
                continue
            if obj.get("event") == "scalp_snapshot":
                nowt = time.time()
                if nowt - last_scalp_snapshot_emit < SNAPSHOT_MIN_SEC:
                    continue
                last_scalp_snapshot_emit = nowt
            _emit(_slim_record(obj))
    finally:
        f.close()


if __name__ == "__main__":
    main()
