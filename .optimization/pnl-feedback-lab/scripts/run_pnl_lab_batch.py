#!/usr/bin/env python3
"""Run PnL multi-window lab multiple times with sleep between runs; log everything.

From repo root:
  python .optimization/pnl-feedback-lab/scripts/run_pnl_lab_batch.py
  python .../run_pnl_lab_batch.py --iterations 12 --sleep-seconds 1800 --variants default,5_15_60
  python .../run_pnl_lab_batch.py --overnight-mega   # ~10h, 600 runs, prevent Windows idle sleep
  # Windows: .optimization/pnl-feedback-lab/scripts/run_overnight.ps1 -Mega

Each iteration writes:
  runs/lab_run_<UTC>_<idx>.jsonl
  runs/05_compare_<UTC>_<idx>.md   (auto four-section compare from same run)
  runs/lab_run_<UTC>_<idx>_{best_per_pair,profit_factor,pnl_matrix,pnl_long.*}.md|.csv  (--export-pnl-details)
  runs/lab_run_<UTC>_<idx>.stderr.txt
  runs/lab_run_<UTC>_<idx>.meta.json  (exit code, duration, argv)

Appends a row to RUN_LOG.md and a line to runs/batch_session_<session_id>.jsonl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SERVER = _REPO / "backend" / "server"
_LAB = _REPO / ".optimization" / "pnl-feedback-lab"
_RUNS = _LAB / "runs"
_SCRIPT = Path(__file__).resolve().parent / "run_multiwindow_lab.py"

if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _append_run_log(row: dict) -> None:
    log_path = _LAB / "RUN_LOG.md"
    line = f"| {row['run_id']} | {row['date_utc']} | {row.get('git', '')} | {row.get('notes', '')} |"
    footer = "Append a row after each lab execution."
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8")
        if footer in text:
            idx = text.index(footer)
            text = text[:idx].rstrip() + "\n" + line + "\n\n" + text[idx:]
        else:
            text = text.rstrip() + "\n" + line + "\n"
        log_path.write_text(text, encoding="utf-8")
    else:
        log_path.write_text(
            "# Lab run log\n\n| run_id | date (UTC) | git | notes |\n|--------|------------|-----|-------|\n"
            + line
            + "\n\n"
            + footer
            + "\n",
            encoding="utf-8",
        )


def _git_sha_short() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (r.stdout or "").strip() or "(no git)"
    except Exception:
        return "(git error)"


def _run_one(
    iteration: int,
    intervals_arg: str | None,
    config_path: str | None,
    session_id: str,
    batch_log: Path,
) -> dict:
    stamp = _utc_stamp()
    run_id = f"{stamp}_i{iteration}"
    base = _RUNS / f"lab_run_{run_id}"
    jsonl_path = base.with_suffix(".jsonl")
    stderr_path = Path(str(base) + ".stderr.txt")
    compare_path = _RUNS / f"05_compare_{run_id}.md"

    cmd = [sys.executable, str(_SCRIPT)]
    if intervals_arg:
        cmd.extend(["--intervals", intervals_arg])
    if config_path:
        cmd.extend(["--config", config_path])
    cmd.extend(
        [
            "--jsonl-out",
            str(jsonl_path),
            "--compare-md",
            str(compare_path),
            "--export-pnl-details",
            "--run-id",
            run_id,
        ]
    )

    t0 = time.perf_counter()
    with open(stderr_path, "wb") as err_f:
        p = subprocess.run(cmd, cwd=_REPO, stdout=subprocess.DEVNULL, stderr=err_f)
    elapsed = time.perf_counter() - t0

    meta = {
        "run_id": run_id,
        "iteration": iteration,
        "session_id": session_id,
        "cmd": cmd,
        "exit_code": p.returncode,
        "elapsed_sec": round(elapsed, 2),
        "jsonl": str(jsonl_path.relative_to(_REPO)),
        "compare_md": str(compare_path.relative_to(_REPO)),
        "export_prefix": str(jsonl_path.with_suffix("").relative_to(_REPO)),
        "stderr": str(stderr_path.relative_to(_REPO)),
        "intervals": intervals_arg,
    }
    meta_path = Path(str(base) + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    with open(batch_log, "a", encoding="utf-8") as bf:
        bf.write(json.dumps(meta) + "\n")

    notes = f"batch i{iteration} exit={p.returncode} {elapsed:.0f}s"
    if intervals_arg:
        notes += f" intervals={intervals_arg}"
    _append_run_log(
        {
            "run_id": run_id,
            "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
            "git": _git_sha_short(),
            "notes": notes,
        }
    )
    return meta


def _sleep_with_heartbeat(total_sec: float, heartbeat_sec: float) -> None:
    if total_sec <= 0:
        return
    if heartbeat_sec <= 0:
        time.sleep(total_sec)
        return
    deadline = time.monotonic() + total_sec
    n = 0
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0:
            break
        slice_sec = min(heartbeat_sec, rem)
        time.sleep(slice_sec)
        n += 1
        left = max(0.0, deadline - time.monotonic())
        print(f"[batch] sleep heartbeat #{n} ~{left:.0f}s left until next lab", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Repeat PnL multi-window lab with logging.")
    ap.add_argument(
        "--overnight-mega",
        action="store_true",
        help="Preset: ~10h wall time, 600 lab runs, 60s between runs, prevent sleep + heartbeat. "
        "Largest JSONL/meta volume for one night.",
    )
    ap.add_argument(
        "--overnight",
        action="store_true",
        help="Preset: ~10h wall time, 360 lab runs, 100s between runs, prevent Windows idle sleep, "
        "heartbeat every 5min during sleeps, variants default+5,15,60. "
        "For custom counts, omit this flag and pass --iterations / --sleep-seconds / --prevent-sleep.",
    )
    ap.add_argument("--iterations", type=int, default=1, help="Number of lab runs.")
    ap.add_argument("--sleep-seconds", type=float, default=0.0, help="Sleep between runs.")
    ap.add_argument(
        "--variants",
        type=str,
        default="default",
        help="Comma-separated: 'default' (config interval only) or '5_15_60' (discovery). "
        "Cycles through list each iteration.",
    )
    ap.add_argument("--config", type=str, default=None, help="Alternate config.toml path.")
    ap.add_argument(
        "--prevent-sleep",
        action="store_true",
        help="Windows: keep system from idle-sleeping for the lifetime of this process "
        "(SetThreadExecutionState). Does not block manual sleep or lid policy.",
    )
    ap.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=0.0,
        help="During inter-run sleep, print a line every N seconds (keeps logs/session visibly alive). 0=off.",
    )
    args = ap.parse_args()

    overnight_mode: str | None = None
    if args.overnight_mega:
        overnight_mode = "mega"
        args.iterations = 600
        args.sleep_seconds = 60.0
        args.variants = "default,5_15_60"
        args.prevent_sleep = True
        if args.heartbeat_seconds == 0.0:
            args.heartbeat_seconds = 300.0
    elif args.overnight:
        overnight_mode = "standard"
        args.iterations = 360
        args.sleep_seconds = 100.0
        args.variants = "default,5_15_60"
        args.prevent_sleep = True
        if args.heartbeat_seconds == 0.0:
            args.heartbeat_seconds = 300.0

    from windows_power import allow_system_sleep, prevent_system_sleep

    if args.prevent_sleep:
        prevent_system_sleep()

    _RUNS.mkdir(parents=True, exist_ok=True)
    session_id = _utc_stamp()
    batch_log = _RUNS / f"batch_session_{session_id}.jsonl"

    variant_list: list[str | None] = []
    for v in args.variants.split(","):
        v = v.strip()
        if v == "default" or v == "":
            variant_list.append(None)
        elif v == "5_15_60":
            variant_list.append("5,15,60")
        else:
            # allow raw e.g. 15,60
            variant_list.append(v.replace("_", ","))

    if not variant_list:
        variant_list = [None]

    header = {
        "type": "batch_start",
        "session_id": session_id,
        "iterations": args.iterations,
        "sleep_seconds": args.sleep_seconds,
        "variants": variant_list,
        "git": _git_sha_short(),
        "prevent_sleep": bool(args.prevent_sleep),
        "overnight_preset": overnight_mode,
        "heartbeat_seconds": args.heartbeat_seconds,
    }
    batch_log.write_text(json.dumps(header) + "\n", encoding="utf-8")

    try:
        for i in range(1, args.iterations + 1):
            intervals = variant_list[(i - 1) % len(variant_list)]
            print(f"[batch] iteration {i}/{args.iterations} intervals={intervals!r}", flush=True)
            meta = _run_one(i, intervals, args.config, session_id, batch_log)
            print(
                f"[batch] done run_id={meta['run_id']} exit={meta['exit_code']} elapsed={meta['elapsed_sec']}s",
                flush=True,
            )
            if i < args.iterations and args.sleep_seconds > 0:
                print(
                    f"[batch] sleeping {args.sleep_seconds}s (heartbeat every {args.heartbeat_seconds or 'off'}s) ...",
                    flush=True,
                )
                _sleep_with_heartbeat(args.sleep_seconds, args.heartbeat_seconds)

        tail = {"type": "batch_end", "session_id": session_id, "completed_iterations": args.iterations}
        with open(batch_log, "a", encoding="utf-8") as bf:
            bf.write(json.dumps(tail) + "\n")

        print(f"[batch] session log: {batch_log.relative_to(_REPO)}", flush=True)
        return 0
    finally:
        if args.prevent_sleep:
            allow_system_sleep()


if __name__ == "__main__":
    raise SystemExit(main())
