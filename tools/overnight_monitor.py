"""Overnight scalp bot monitor — watches logs, tracks trades, and writes periodic reports.

Run (from repo root):  python -m tools.overnight_monitor

Log source (first match):
  - TERMINAL_LOG or ARCEUS_MONITOR_LOG — explicit path (e.g. Cursor terminal *.txt)
  - Else any terminal in CURSOR_TERMINALS_DIR whose header shows `python -m backend.server.main`,
    picking the newest-by-mtime file (tie-break: largest) — your active bot tab
  - Else fallback: tail-content heuristics on all *.txt in that folder

Outputs: data/overnight_report.jsonl (append-only summaries + trade events)
"""

import json
import re
import time
import sys
import os
from datetime import datetime
from pathlib import Path
from collections import defaultdict

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO_ROOT / "data" / "overnight_report.jsonl"


def resolve_log_path() -> Path | None:
    """Path to tail. Cursor stores integrated-terminal output in terminals/*.txt."""
    explicit = (
        os.environ.get("TERMINAL_LOG")
        or os.environ.get("ARCEUS_MONITOR_LOG")
        or os.environ.get("MITCH_MONITOR_LOG")
        or ""
    ).strip()
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None

    terminals_dir = Path(
        os.environ.get(
            "CURSOR_TERMINALS_DIR",
            str(Path.home() / ".cursor/projects/c-Users-daroo-Desktop-Repos-tradingbot-1/terminals"),
        )
    )
    if not terminals_dir.is_dir():
        return None
    txts = [p for p in terminals_dir.glob("*.txt") if p.is_file()]
    if not txts:
        return None

    def _is_bot_terminal(p: Path) -> bool:
        """Cursor YAML front-matter: bot sessions include `python -m backend.server.main`."""
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                head = f.read(2500)
        except OSError:
            return False
        return "backend.server.main" in head

    bot_logs = [p for p in txts if _is_bot_terminal(p)]
    if bot_logs:
        try:
            return max(bot_logs, key=lambda p: (p.stat().st_mtime, p.stat().st_size))
        except OSError:
            pass

    markers = ("ScalpRuntime", "backend.server.scalp_bot", "arceus")
    scored: list[tuple[int, float, int, Path]] = []
    for p in txts:
        try:
            st = p.stat()
            if st.st_size < 400:
                continue
            with open(p, "rb") as f:
                f.seek(0, 2)
                n = min(12000, f.tell())
                f.seek(-n, 2)
                tail = f.read().decode("utf-8", errors="replace")
            hits = sum(1 for m in markers if m in tail)
            if hits:
                scored.append((hits, st.st_mtime, int(st.st_size), p))
        except OSError:
            continue
    if scored:
        scored.sort(key=lambda t: (t[0], t[1], t[2]))
        return scored[-1][3]
    return max(txts, key=lambda p: p.stat().st_mtime)


# Patterns
RE_SIGNAL = re.compile(
    r"SIGNAL \[(\w+)\] (\S+): long @ ([\d.]+) \| stop=([\d.]+)"
)
RE_PAPER_STOP = re.compile(
    r"ScalpTrader (\S+): PAPER stop hit @ ([\d.]+) \| pnl=([\d.e+-]+) \| daily_pnl=([\d.e+-]+)"
)
RE_PAPER_TP = re.compile(
    r"ScalpTrader (\S+): PAPER TP hit @ ([\d.]+) \| pnl=([\d.e+-]+) \| daily_pnl=([\d.e+-]+)"
)
# Paper/sim time stop uses same "TIME STOP" line as live (see scalp_trader.check_time_stop).
# RSI exit uses "RSI EXIT" for paper and live (see check_rsi_exit).
# Live / sim (same log strings as live — not prefixed with PAPER)
RE_LIVE_CLOSE = re.compile(
    r"ScalpTrader (\S+): closed via (\w+) @ ([\d.]+) \| pnl=([\d.e+-]+) \| daily_pnl=([\d.e+-]+)"
)
RE_TIME_STOP = re.compile(
    r"ScalpTrader (\S+): TIME STOP after \d+s \(max=\d+s\) @ ([\d.]+) \| pnl=([\d.e+-]+)"
)
RE_RSI_EXIT = re.compile(
    r"ScalpTrader (\S+): RSI EXIT @ ([\d.]+) \| pnl=([\d.e+-]+) \| daily_pnl=([\d.e+-]+)"
)
# Periodic monitor lines (ScalpRuntime._log_status)
RE_MONITOR_READY = re.compile(
    r"ScalpRuntime MONITOR (\S+) \[(\S+)\]: rsi=([\d.]+) ema=(\w+) vwap=(\w+) vol=(\w+) \| "
    r"open_pos=(\d+) \| daily_pnl=([\d.e+-]+)"
)
RE_MONITOR_WAIT = re.compile(
    r"ScalpRuntime MONITOR (\S+) \[(\S+)\]: waiting for first indicator update \| "
    r"open_pos=(\d+) \| daily_pnl=([\d.e+-]+)"
)
RE_MONITOR_WARM = re.compile(
    r"ScalpRuntime MONITOR (\S+) \[(\S+)\]: warming up \(candles=(\d+)\) \| "
    r"open_pos=(\d+) \| daily_pnl=([\d.e+-]+)"
)
RE_CANDLE = re.compile(
    r"ScalpRuntime (\S+): candle close=([\d.]+) ema_f=([\d.]+) ema_s=([\d.]+) rsi=([\d.]+) atr=([\d.]+) vwap=([\d.]+) vol_ok=(\w+) ready=(\w+)"
)


class TradeTracker:
    def __init__(self):
        self.trades = []
        self.by_mode = defaultdict(list)
        self.by_pair = defaultdict(list)
        self.by_exit_type = defaultdict(list)
        self.signals = []
        self.start_ts = time.time()

    def record_signal(self, mode, pair, entry, stop, ts_str):
        self.signals.append({
            "ts": ts_str, "mode": mode, "pair": pair,
            "entry": float(entry), "stop": float(stop),
        })

    def record_trade(self, pair, exit_price, pnl, daily_pnl, exit_type, ts_str):
        t = {
            "ts": ts_str, "pair": pair, "exit_price": float(exit_price),
            "pnl": float(pnl), "daily_pnl": float(daily_pnl),
            "exit_type": exit_type,
        }
        if self.signals:
            last_sig = self.signals[-1]
            if last_sig["pair"] == pair:
                t["mode"] = last_sig["mode"]
                t["entry"] = last_sig["entry"]
                t["stop"] = last_sig["stop"]

        self.trades.append(t)
        mode = t.get("mode", "unknown")
        self.by_mode[mode].append(t)
        self.by_pair[pair].append(t)
        self.by_exit_type[exit_type].append(t)

    def summary(self):
        total = len(self.trades)
        if total == 0:
            return {"total_trades": 0, "message": "No trades recorded yet"}

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in self.trades)
        win_rate = len(wins) / total * 100

        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else float("inf")

        by_mode_summary = {}
        for mode, trades in self.by_mode.items():
            m_wins = [t for t in trades if t["pnl"] > 0]
            m_total = len(trades)
            by_mode_summary[mode] = {
                "trades": m_total,
                "wins": len(m_wins),
                "win_rate": len(m_wins) / m_total * 100 if m_total > 0 else 0,
                "total_pnl": round(sum(t["pnl"] for t in trades), 6),
                "avg_pnl": round(sum(t["pnl"] for t in trades) / m_total, 6) if m_total > 0 else 0,
            }

        by_pair_summary = {}
        for pair, trades in self.by_pair.items():
            p_wins = [t for t in trades if t["pnl"] > 0]
            p_total = len(trades)
            by_pair_summary[pair] = {
                "trades": p_total,
                "wins": len(p_wins),
                "win_rate": len(p_wins) / p_total * 100 if p_total > 0 else 0,
                "total_pnl": round(sum(t["pnl"] for t in trades), 6),
            }

        by_exit_summary = {}
        for et, trades in self.by_exit_type.items():
            by_exit_summary[et] = {
                "count": len(trades),
                "total_pnl": round(sum(t["pnl"] for t in trades), 6),
            }

        return {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 6),
            "avg_win": round(avg_win, 6),
            "avg_loss": round(avg_loss, 6),
            "profit_factor": round(profit_factor, 3),
            "by_mode": by_mode_summary,
            "by_pair": by_pair_summary,
            "by_exit_type": by_exit_summary,
            "elapsed_min": round((time.time() - self.start_ts) / 60, 1),
        }


def write_report(tracker, label="periodic"):
    s = tracker.summary()
    s["type"] = label
    s["timestamp"] = datetime.now().isoformat()
    with open(REPORT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(s) + "\n")
    return s


def tail_follow(path, tracker):
    """Tail -f style: read new lines as they appear."""
    last_report = time.time()
    report_interval = 900  # 15 min

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # start at end
        log(f"[MONITOR] Watching {path}")
        log(f"[MONITOR] Reports -> {REPORT_PATH}")
        log(f"[MONITOR] Started at {datetime.now().isoformat()}")

        while True:
            line = f.readline()
            if not line:
                time.sleep(1)
                if time.time() - last_report > report_interval:
                    s = write_report(tracker, "periodic")
                    log(f"[REPORT {datetime.now().strftime('%H:%M')}] "
                        f"trades={s['total_trades']} win_rate={s.get('win_rate', 0)}% "
                        f"pnl={s.get('total_pnl', 0):.4f} "
                        f"by_mode={json.dumps(s.get('by_mode', {}))}")
                    last_report = time.time()
                continue

            line = line.strip()
            ts_str = line[:8] if len(line) > 8 else ""

            m = RE_SIGNAL.search(line)
            if m:
                mode, pair, entry, stop = m.groups()
                tracker.record_signal(mode, pair, entry, stop, ts_str)
                log(f"  [SIGNAL] {ts_str} {mode} {pair} entry={entry} stop={stop}")

            for pattern, exit_type in [
                (RE_PAPER_STOP, "stop"),
                (RE_PAPER_TP, "tp"),
                (RE_LIVE_CLOSE, None),  # pair, reason, exit_px, pnl, daily
                (RE_TIME_STOP, "time_stop"),
                (RE_RSI_EXIT, "rsi_exit"),
            ]:
                m = pattern.search(line)
                if m:
                    if pattern is RE_LIVE_CLOSE:
                        pair, reason, exit_px, pnl, daily = m.groups()
                        exit_lbl = reason  # stop | tp
                    elif pattern is RE_TIME_STOP:
                        pair, exit_px, pnl = m.groups()
                        daily = pnl
                        exit_lbl = exit_type
                    else:
                        pair, exit_px, pnl, daily = m.groups()
                        exit_lbl = exit_type
                    tracker.record_trade(pair, exit_px, pnl, daily, exit_lbl, ts_str)
                    tag = "WIN" if float(pnl) > 0 else "LOSS"
                    log(f"  [TRADE {tag}] {ts_str} {pair} {exit_lbl} pnl={pnl} daily={daily}")
                    s = tracker.summary()
                    log(f"    -> cumulative: {s['total_trades']} trades, "
                        f"win_rate={s.get('win_rate', 0):.1f}%, "
                        f"total_pnl={s.get('total_pnl', 0):.4f}")
                    break

            m = RE_MONITOR_READY.search(line)
            if m:
                pair, mode, rsi, ema, vwap, vol, open_pos, daily = m.groups()
                log(f"  [MONITOR] {ts_str} {pair} [{mode}] rsi={rsi} ema={ema} vwap={vwap} vol={vol} "
                    f"open_pos={open_pos} daily_pnl={daily}")
            m = RE_MONITOR_WAIT.search(line)
            if m:
                pair, mode, open_pos, daily = m.groups()
                log(f"  [MONITOR] {ts_str} {pair} [{mode}] waiting first IV open_pos={open_pos} daily_pnl={daily}")
            m = RE_MONITOR_WARM.search(line)
            if m:
                pair, mode, candles, open_pos, daily = m.groups()
                log(f"  [MONITOR] {ts_str} {pair} [{mode}] warmup candles={candles} open_pos={open_pos} daily_pnl={daily}")


def log(msg):
    print(msg, flush=True)


def main():
    tracker = TradeTracker()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_path = resolve_log_path()
    if log_path is None:
        log("[MONITOR] No log file found.")
        log("  Set TERMINAL_LOG or ARCEUS_MONITOR_LOG to the bot terminal output file,")
        log("  or CURSOR_TERMINALS_DIR to a folder of Cursor terminals/*.txt (uses newest).")
        sys.exit(1)

    write_report(tracker, "session_start")
    log("[MONITOR] Overnight scalp bot monitor started")
    log(f"[MONITOR] Tailing: {log_path}")
    log(f"[MONITOR] Reports -> {REPORT_PATH}")
    log("[MONITOR] Reporting every 15 min (plus live MONITOR / TRADE lines)")
    log("")

    try:
        tail_follow(str(log_path), tracker)
    except KeyboardInterrupt:
        s = write_report(tracker, "session_end")
        log(f"\n[FINAL REPORT]")
        log(json.dumps(s, indent=2))


if __name__ == "__main__":
    main()
