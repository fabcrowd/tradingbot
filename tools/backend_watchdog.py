"""HTTP health watchdog — checks dashboard every 15 minutes; restarts backend if down.

Designed for Windows (PowerShell + taskkill). Run in a separate terminal from the bot:

  cd <repo-root>
  python tools/backend_watchdog.py

Environment:
  TRADINGBOT_HTTP_PORT        Primary bind port from config / kill target (override)
  WATCHDOG_EXTRA_HEALTH_PORTS Comma-separated extra ports to probe (e.g. 8095 if you run
                              off-config). If ANY port responds OK, backend is healthy.
  WATCHDOG_INTERVAL_SEC       Seconds between checks (default: 900 = 15 min)
  WATCHDOG_LOG                Log file path (default: data/backend_watchdog.log)
  WATCHDOG_DRY_RUN            If "1", log actions but do not kill or start processes

The bot is started as: python -m backend.server.main  (cwd = repo root)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "data"
DEFAULT_LOG = LOG_DIR / "backend_watchdog.log"
SERVER_OUT = LOG_DIR / "watchdog_server_restart.log"

INTERVAL_SEC = float(os.environ.get("WATCHDOG_INTERVAL_SEC", "900"))
DRY_RUN = os.environ.get("WATCHDOG_DRY_RUN", "").strip() in {"1", "true", "yes"}


def _log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} | {msg}"
    print(line, flush=True)
    log_path = Path(os.environ.get("WATCHDOG_LOG", str(DEFAULT_LOG)))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _read_primary_port() -> int:
    port = int(os.environ.get("TRADINGBOT_HTTP_PORT", "0") or 0)
    if port > 0:
        return port
    cfg = REPO_ROOT / "config.toml"
    if cfg.exists():
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore
        with cfg.open("rb") as f:
            data = tomllib.load(f)
        return int(data.get("server", {}).get("port", 8080))
    return 8080


def _health_ports(primary: int) -> list[int]:
    raw = os.environ.get("WATCHDOG_EXTRA_HEALTH_PORTS", "").strip()
    extra: list[int] = []
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                extra.append(int(part))
    seen: set[int] = set()
    out: list[int] = []
    for p in [primary, *extra]:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/health"


def _check_port_ok(port: int, timeout: float = 8.0) -> bool:
    try:
        req = Request(_health_url(port), method="GET", headers={"User-Agent": "backend-watchdog"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        data = json.loads(body.decode("utf-8"))
        return bool(data.get("ok"))
    except (URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return False


def _any_healthy(ports: list[int]) -> tuple[bool, int | None]:
    for p in ports:
        if _check_port_ok(p):
            return True, p
    return False, None


def _pids_listening_on_port_win(port: int) -> list[int]:
    ps = (
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue; "
        f"if ($c) {{ $c | Select-Object -ExpandProperty OwningProcess -Unique }}"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=45,
    )
    pids: list[int] = []
    for line in (r.stdout or "").splitlines():
        s = line.strip()
        if s.isdigit():
            pids.append(int(s))
    seen: set[int] = set()
    out: list[int] = []
    for p in pids:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _kill_pids_win(pids: list[int]) -> None:
    my = os.getpid()
    for pid in pids:
        if pid == my:
            continue
        if DRY_RUN:
            _log(f"DRY_RUN: would taskkill /PID {pid} /F")
            continue
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True,
            text=True,
            timeout=60,
        )


def _start_server() -> None:
    py = sys.executable
    if DRY_RUN:
        _log(f"DRY_RUN: would start {py} -m backend.server.main cwd={REPO_ROOT}")
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = SERVER_OUT.open("a", encoding="utf-8", buffering=1)
    out.write(f"\n--- restart {datetime.now(timezone.utc).isoformat()} ---\n")
    out.flush()
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [py, "-m", "backend.server.main"],
        cwd=str(REPO_ROOT),
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


def _wait_for_healthy(ports: list[int], max_wait: float = 120.0) -> tuple[bool, int | None]:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        ok, which = _any_healthy(ports)
        if ok:
            return True, which
        time.sleep(2.0)
    return False, None


def main() -> None:
    primary = _read_primary_port()
    check_ports = _health_ports(primary)
    _log(
        f"backend_watchdog starting | primary_port={primary} "
        f"health_ports={check_ports} interval={INTERVAL_SEC}s dry_run={DRY_RUN}",
    )

    # Grace after watchdog start so we don't fight a simultaneous manual boot
    time.sleep(30)

    while True:
        ok, which = _any_healthy(check_ports)
        if ok:
            if which != primary:
                _log(f"health OK on port {which} (primary config {primary} — align config or set TRADINGBOT_HTTP_PORT)")
            else:
                _log(f"health OK (port {primary})")
        else:
            _log(f"health FAIL on all ports {check_ports} — attempting restart (kill/bind primary {primary})")
            if sys.platform == "win32":
                for p in check_ports:
                    pids = _pids_listening_on_port_win(p)
                    if pids:
                        _log(f"listeners on {p}: {pids}")
                        _kill_pids_win(pids)
                time.sleep(3)
            else:
                _log("non-Windows: kill stale listeners on dashboard ports manually if needed")
            _start_server()
            restarted_ok, rw = _wait_for_healthy(check_ports)
            if restarted_ok:
                _log(f"restart succeeded — /health OK on port {rw}")
            else:
                _log("restart may have failed — no /health on any probe port after 120s")

        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _log("watchdog stopped (KeyboardInterrupt)")
