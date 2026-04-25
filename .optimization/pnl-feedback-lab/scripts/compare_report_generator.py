#!/usr/bin/env python3
"""Build `05_compare`-style markdown from PnL lab JSONL rows (skill contract §1–§4)."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_WINDOW_ORDER = ("early", "mid", "late", "full")


def _window_sort_key(w: str) -> int:
    try:
        return _WINDOW_ORDER.index(w)
    except ValueError:
        return 99


def _git_short_sha(repo: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (r.stdout or "").strip() or "(no git)"
    except Exception:
        return "(git error)"


def read_jsonl_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def parse_jsonl_lab(text: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Accept lab output: optional pretty-printed leading ``{"contract": ...}``, then one JSON row per line."""
    contract: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    s = text.lstrip()
    if s.startswith("{"):
        depth = 0
        start = 0
        end = -1
        for j, ch in enumerate(s):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end > 0:
            try:
                obj = json.loads(s[start:end])
                if isinstance(obj, dict) and "contract" in obj:
                    c = obj["contract"]
                    if isinstance(c, dict):
                        contract = c
            except json.JSONDecodeError:
                pass
            s = s[end:].lstrip()
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "pair_key" in obj:
            rows.append(obj)
    return contract, rows


def _best_per_window(
    rows: Iterable[dict[str, Any]],
) -> dict[tuple[str, int, str], dict[str, Any]]:
    """(pair_key, interval_m, window) -> best row by score_exp_sqrt_n."""
    buckets: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        k = (r["pair_key"], int(r["interval_m"]), r["window"])
        buckets[k].append(r)
    out: dict[tuple[str, int, str], dict[str, Any]] = {}
    for k, lst in buckets.items():
        out[k] = max(lst, key=lambda x: float(x.get("score_exp_sqrt_n") or -1e18))
    return out


def _focus_rows(
    rows: Iterable[dict[str, Any]],
    pair_key: str,
    strategy: str,
) -> list[dict[str, Any]]:
    out = [
        r
        for r in rows
        if r["pair_key"] == pair_key
        and r["strategy"] == strategy
        and int(r["interval_m"]) == int(r["config_interval_m"])
    ]
    out.sort(key=lambda x: _window_sort_key(str(x["window"])))
    return out


def _fmt_pf(v: Any) -> str:
    if v is None:
        return "—"
    return f"{float(v):.4f}"


def _validation_table_for_pair(
    focus_rows: list[dict[str, Any]],
    min_trades: int = 5,
) -> tuple[list[str], list[str]]:
    """Returns (table_lines, recommendation_bullets)."""
    lines: list[str] = []
    recs: list[str] = []
    if not focus_rows:
        lines.append("| (no rows for focus mode on operating interval) | — |")
        return lines, ["Add bars or check `strategy_mode` vs lab `STRATEGIES`."]

    wins = sorted({r["window"] for r in focus_rows}, key=_window_sort_key)
    g1_pass = sum(1 for r in focus_rows if int(r["trades"]) >= min_trades)
    g1_tot = len(focus_rows)

    pnl_pos = sum(1 for r in focus_rows if float(r["total_pnl"]) > 0)
    pf_ok = 0
    pf_chk = 0
    for r in focus_rows:
        pf = r.get("profit_factor")
        if pf is None:
            continue
        pf_chk += 1
        if float(pf) >= 1.0:
            pf_ok += 1

    rule_c = pnl_pos >= max(1, (2 * len(focus_rows) + 2) // 3) if focus_rows else False

    lines.append("| Check | Result |")
    lines.append("|-------|--------|")
    lines.append(
        f"| G1 (≥{min_trades} trades per window) | "
        f"**{'PASS' if g1_pass == g1_tot else 'FAIL'}** — {g1_pass}/{g1_tot} windows |"
    )
    if g1_pass < g1_tot:
        thin = [r["window"] for r in focus_rows if int(r["trades"]) < min_trades]
        recs.append(
            f"Raise sample bar (e.g. `wfo_min_trades` / longer history) — thin windows: {', '.join(thin)}."
        )

    pf_line = (
        f"| G3 (profit factor ≥ 1 where finite) | **{'PASS' if pf_chk == 0 or pf_ok == pf_chk else 'FAIL'}** — {pf_ok}/{pf_chk} windows with finite PF |"
    )
    lines.append(pf_line)
    if pf_chk and pf_ok < pf_chk:
        bad = [r["window"] for r in focus_rows if r.get("profit_factor") is not None and float(r["profit_factor"]) < 1.0]
        recs.append(f"Windows with PF < 1: {', '.join(bad)} — review mode or gates before scaling live.")

    lines.append(
        f"| RULE C-style (≥2/3 windows with total_pnl > 0) | **{'PASS' if rule_c else 'FAIL'}** — {pnl_pos}/{len(focus_rows)} positive |"
    )
    if not rule_c:
        neg = [r["window"] for r in focus_rows if float(r["total_pnl"]) <= 0]
        recs.append(f"Negative or flat windows on focus mode: {', '.join(neg)}.")

    return lines, recs


def generate_compare_document(
    *,
    contract: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    pair_focus_modes: dict[str, str],
    run_id: str,
    repo: Path,
    command_line: str,
    jsonl_relative: str | None,
    skipped_messages: list[str],
    lens_b_path: str | None = None,
    focus_mode_caption: str | None = None,
) -> str:
    sha = _git_short_sha(repo)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")

    lines: list[str] = []
    lines.append(f"# Compare — run `{run_id}`")
    lines.append("")
    lines.append(f"**Generated:** {now} (auto)")
    lines.append(f"**Git:** `{sha}`")
    if lens_b_path:
        lines.append(f"**Lens B:** `{lens_b_path}`")
    else:
        lines.append("**Lens B:** *not linked — add `--lens-b` on lab run or edit this file*")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. What we tested")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    lines.append(f"| Command | `{command_line}` |")
    if jsonl_relative:
        lines.append(f"| Artifacts | `{jsonl_relative}` (+ stderr from same run) |")
    strategies = sorted({r["strategy"] for r in rows})
    lines.append(f"| Strategies compared | {', '.join(strategies)} |")
    pairs = sorted({r["pair_key"] for r in rows})
    lines.append(f"| Pairs | {', '.join(pairs)} |")
    lines.append("| Time windows | early / mid / late (or `full` if series < 90 bars) — thirds of bar index |")
    iv = (contract or {}).get("intervals_swept", "config_only")
    lines.append(f"| Intervals swept | `{iv}` |")
    if skipped_messages:
        sk = "<br>".join(f"`{s}`" for s in skipped_messages[:12])
        if len(skipped_messages) > 12:
            sk += "<br>…"
        lines.append(f"| Skipped (stderr) | {sk} |")
    lines.append("| Simulation contract | see table below |")
    lines.append("")
    lines.append("| Contract key | Value |")
    lines.append("|--------------|-------|")
    if contract:
        for k, v in sorted(contract.items()):
            lines.append(f"| {k} | `{v}` |")
    else:
        lines.append("| (missing) | first JSON line was not a contract object |")
    lines.append("")
    lines.append("**Units:** `total_pnl` is the **vector backtester’s internal PnL** for the sim — not guaranteed live USD.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. PnL impact during the test windows")
    lines.append("")

    best = _best_per_window(rows)
    pairs_sorted = sorted({r["pair_key"] for r in rows})
    intervals_by_pair: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        intervals_by_pair[r["pair_key"]].add(int(r["interval_m"]))

    for pk in pairs_sorted:
        lines.append(f"### {pk} — best mode by window (highest `score_exp_sqrt_n`)")
        lines.append("")
        lines.append("| Interval | Window | Best mode | total_pnl | trades | PF |")
        lines.append("|----------|--------|-----------|-----------|--------|-----|")
        keys = [k for k in best if k[0] == pk]
        keys.sort(key=lambda x: (x[1], _window_sort_key(x[2])))
        for pair_k, iv, wn in keys:
            b = best[(pair_k, iv, wn)]
            lines.append(
                f"| {iv}m | {wn} | {b['strategy']} | {b['total_pnl']} | {b['trades']} | {_fmt_pf(b.get('profit_factor'))} |"
            )
        lines.append("")

        focus = pair_focus_modes.get(pk) or "ema_momentum"
        fr = _focus_rows(rows, pk, focus)
        if fr:
            cap = "`config.strategy_mode`" if focus_mode_caption is None else focus_mode_caption
            lines.append(f"**Focus mode ({cap}): `{focus}`** (operating interval = `config_interval_m` rows only)")
            lines.append("")
            lines.append("| Window | total_pnl | trades | PF |")
            lines.append("|--------|-----------|--------|-----|")
            for r in fr:
                lines.append(
                    f"| {r['window']} | {r['total_pnl']} | {r['trades']} | {_fmt_pf(r.get('profit_factor'))} |"
                )
            worst = min(fr, key=lambda x: float(x["total_pnl"]))
            thin = [r for r in fr if int(r["trades"]) < 5]
            lines.append("")
            lines.append(
                f"**Weakest window (focus):** `{worst['window']}` (total_pnl={worst['total_pnl']}). "
                f"**Thin sample (trades<5):** {', '.join(r['window'] for r in thin) or 'none'}."
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 3. How we validated")
    lines.append("")
    lines.append("| Scope | Note |")
    lines.append("|-------|------|")
    lines.append("| Multi-window tape | ✓ Thirds (or full) per pair/interval present in JSONL |")
    lines.append("| Dual lens | *Manual:* link `report.md` and set CORROBORATED/REFUTED/DEFERRED after Lab Loop |")
    lines.append("| Live / funding | ✗ Not covered by this JSONL |")
    lines.append("")
    lines.append("### Automatic gates (focus mode, operating interval only)")
    lines.append("")

    all_recs: list[str] = []
    for pk in pairs_sorted:
        focus = pair_focus_modes.get(pk) or "ema_momentum"
        fr = _focus_rows(rows, pk, focus)
        lines.append(f"#### {pk} — `{focus}`")
        lines.append("")
        vlines, vrecs = _validation_table_for_pair(fr)
        lines.extend(vlines)
        lines.append("")
        all_recs.extend(vrecs)

    lines.append("**What this run does not prove:** live fills, funding, WFO train/holdout alignment with these thirds, or profitability if fees/slippage change.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. Recommended optimizations")
    lines.append("")

    # Dedupe while preserving order
    seen: set[str] = set()
    merged = list(all_recs)
    if skipped_messages:
        merged.append(
            "Add or backfill Parquet for skipped interval×symbol combinations, or stop passing unused `--intervals`."
        )
    merged.append(
        "Tighten WFO promotion rules if `scalp_champion.json` modes disagree with per-window winners above."
    )
    merged.append(
        "Re-run lab after bar file updates; append a new `run_id` rather than repeating identical tape for 'more data'."
    )
    n = 1
    for r in merged:
        if r in seen:
            continue
        seen.add(r)
        lines.append(f"{n}. {r}")
        n += 1
    if n == 1:
        lines.append("1. No automatic findings — focus rows empty or all gates passed.")

    lines.append("")
    return "\n".join(lines)


def _pair_focus_modes_from_config(raw_toml: dict) -> dict[str, str]:
    """Mirror run_multiwindow_lab: auto/empty -> ``[scalp] auto_mode_fallback`` for §2–§3 focus rows."""
    out: dict[str, str] = {}
    scalp = raw_toml.get("scalp") or {}
    fb_global = str(scalp.get("auto_mode_fallback", "ema_momentum") or "ema_momentum")
    pairs = scalp.get("pairs") or {}
    if isinstance(pairs, dict):
        for pk, pv in pairs.items():
            if not isinstance(pv, dict):
                continue
            m = str(pv.get("strategy_mode", "auto") or "auto").strip().lower()
            if m in ("", "auto"):
                out[str(pk)] = str(pv.get("auto_mode_fallback", fb_global) or fb_global)
            else:
                out[str(pk)] = str(pv.get("strategy_mode"))
    return out


def write_compare_from_jsonl_file(
    jsonl_path: Path,
    *,
    repo: Path,
    pair_focus_modes: dict[str, str],
    run_id: str,
    command_line: str,
    out_md_path: Path,
    skipped_messages: list[str],
    lens_b_path: str | None = None,
    focus_mode_caption: str | None = None,
) -> None:
    text = read_jsonl_text(jsonl_path)
    contract, rows = parse_jsonl_lab(text)
    rel = None
    try:
        rel = str(jsonl_path.resolve().relative_to(repo.resolve()))
    except ValueError:
        rel = str(jsonl_path)
    md = generate_compare_document(
        contract=contract,
        rows=rows,
        pair_focus_modes=pair_focus_modes,
        run_id=run_id,
        repo=repo,
        command_line=command_line,
        jsonl_relative=rel,
        skipped_messages=skipped_messages,
        lens_b_path=lens_b_path,
        focus_mode_caption=focus_mode_caption,
    )
    out_md_path.parent.mkdir(parents=True, exist_ok=True)
    out_md_path.write_text(md, encoding="utf-8")


def main_cli() -> int:
    import argparse

    _repo = Path(__file__).resolve().parents[3]
    ap = argparse.ArgumentParser(description="Regenerate 05_compare markdown from lab JSONL.")
    ap.add_argument("jsonl", type=str, help="Path to lab.jsonl")
    ap.add_argument("--out-md", type=str, required=True, help="Output markdown path")
    ap.add_argument("--config", type=str, default=None, help="config.toml for strategy_mode (default: repo root)")
    ap.add_argument("--run-id", type=str, default=None, help="Compare run label (default: stem of jsonl)")
    ap.add_argument("--command-line", type=str, default="(regenerated via compare_report_generator.py)")
    ap.add_argument("--lens-b", type=str, default=None)
    ap.add_argument("--stderr", type=str, default=None, help="Optional stderr log with # skip lines")
    ap.add_argument(
        "--focus-strategy",
        type=str,
        default=None,
        metavar="MODE",
        help="Force §2–§3 focus mode for every pair_key in the JSONL (e.g. ema_momentum). "
        "Default: use each pair's strategy_mode from --config.",
    )
    ns = ap.parse_args()
    jsonl_path = Path(ns.jsonl).resolve()
    cfg_path = Path(ns.config).resolve() if ns.config else _repo / "config.toml"
    if not jsonl_path.is_file():
        print("jsonl not found:", jsonl_path, file=sys.stderr)
        return 2
    skipped: list[str] = []
    if ns.stderr:
        sp = Path(ns.stderr)
        if sp.is_file():
            for line in sp.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("# skip"):
                    skipped.append(line.strip())
    text0 = read_jsonl_text(jsonl_path)
    _, rows0 = parse_jsonl_lab(text0)
    pair_modes: dict[str, str] = {}
    if ns.focus_strategy:
        mode = str(ns.focus_strategy).strip()
        for pk in sorted({str(r["pair_key"]) for r in rows0}):
            pair_modes[pk] = mode
    elif cfg_path.is_file():
        import tomllib

        with cfg_path.open("rb") as f:
            pair_modes = _pair_focus_modes_from_config(tomllib.load(f))
    run_id = ns.run_id or jsonl_path.stem
    write_compare_from_jsonl_file(
        jsonl_path,
        repo=_repo,
        pair_focus_modes=pair_modes,
        run_id=run_id,
        command_line=ns.command_line,
        out_md_path=Path(ns.out_md).resolve(),
        skipped_messages=skipped,
        lens_b_path=ns.lens_b,
        focus_mode_caption="`--focus-strategy` override" if ns.focus_strategy else None,
    )
    print("wrote", ns.out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
