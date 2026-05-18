"""Build pine/review_packages/<strategy>/ folders for LLM handoff (one zip-able folder per mode).

Run from repo: python pine/package_review_folders.py

Copies:
  - strategy.pine from pine/by_mode/TradingBotScalp__<mode>.pine
  - bot_excerpt_scalp_vec_backtest.py — stitched line ranges from
    backend/server/scalp_bot/scalp_vec_backtest.py
  - review_packages/_shared/ — indicator_warmup.py, strategies.md, exit bundles, handoff

Line numbers refer to backend/server/scalp_bot/scalp_vec_backtest.py at generation time.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
VEC = REPO / "backend" / "server" / "scalp_bot" / "scalp_vec_backtest.py"
IWUP = REPO / "backend" / "server" / "scalp_bot" / "indicator_warmup.py"
STRAT_MD = REPO / "strategies.md"
HANDOFF = ROOT / "REVIEW_HANDOFF_FOR_LLM.txt"
BY_MODE = ROOT / "by_mode"
OUT = ROOT / "review_packages"
SHARED = OUT / "_shared"

# Line ranges are 1-based inclusive from scalp_vec_backtest.py.
# After edits to scalp_vec_backtest.py, re-grep ``^def detect_signals_`` and update slices.
# ``MODE_DETECT_FN`` is used by ``_validate_excerpt`` after generation.
MODE_DETECT_FN: dict[str, str] = {
    "daviddtech_scalp": "def detect_signals_daviddtech",
    "ema_momentum": "def detect_signals_ema",
    "ema_scalp": "def detect_signals_ema_scalp",
    "macd_scalp": "def detect_signals_macd",
    "rsi_reversion": "def detect_signals_rsi",
    "supertrend": "def detect_signals_supertrend",
    "squeeze_momentum": "def detect_signals_squeeze",
    "qqe_mod": "def detect_signals_qqe",
    "utbot_alert": "def detect_signals_utbot",
    "hull_suite": "def detect_signals_hull",
    "sar_chop": "def detect_signals_sar_chop",
}

MODE_RANGES: dict[str, list[tuple[int, int]]] = {
    "daviddtech_scalp": [(37, 54), (61, 159), (196, 391), (415, 491)],
    # ema + _rising_edge + atr, then detector (detect_signals_ema = mode ema_momentum)
    "ema_momentum": [(37, 54), (61, 139), (788, 848)],
    # ema/atr, rolling_* aliases, detector
    "ema_scalp": [(61, 83), (86, 96), (123, 239), (1108, 1168)],
    # touch helpers + super_smooth + detector (uses _touch_crossover)
    "macd_scalp": [(61, 159), (99, 116), (1279, 1346)],
    "rsi_reversion": [(119, 159), (853, 886)],
    "supertrend": [(143, 159), (1353, 1442)],
    "squeeze_momentum": [(61, 159), (146, 193), (1507, 1623)],
    # ema, rsi, atr, _rising_edge, _touch_cross* (lines 61–159), detect_signals_qqe + live bundle
    "qqe_mod": [(61, 159), (1630, 1730)],
    "utbot_alert": [(143, 159), (1737, 1826)],
    "hull_suite": [(143, 159), (162, 174), (1886, 1965)],
    # Full SAR+CHOP block: PSAR/CHOP/MACD/UT helpers through live bundle (not trade sim).
    "sar_chop": [(61, 159), (1978, 2551)],
}

# simulate_trades_bidir deps + body (same module file — stitched into review bundle)
BIDIR_SUPPORT_LINES = [(2316, 2327), (2330, 2332), (2335, 2355), (2378, 2387), (494, 689)]

RSI_SIM_LINES = [(853, 1065)]

HEADER_ENTRY = """# AUTO-GENERATED review excerpt from scalp_vec_backtest.py
# Not intended as a runnable module alone — imports below match typical usage in the full file.
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from indicator_warmup import vec_warmup_prefix_len

"""

HEADER_EXIT = """# AUTO-GENERATED review excerpt from scalp_vec_backtest.py
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

"""


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _slice_lines(lines: list[str], start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


def _validate_excerpt(mode: str, excerpt: str) -> None:
    needle = MODE_DETECT_FN.get(mode)
    if needle and needle not in excerpt:
        raise SystemExit(
            f"{mode}: excerpt missing {needle!r} — update MODE_RANGES in package_review_folders.py "
            f"(grep detect_signals_* in scalp_vec_backtest.py)",
        )


def _build_excerpt(lines: list[str], ranges: list[tuple[int, int]]) -> str:
    parts: list[str] = []
    for a, b in ranges:
        parts.append(f"\n# ----- scalp_vec_backtest.py lines {a}–{b} -----\n")
        parts.append(_slice_lines(lines, a, b))
    return "".join(parts)


def _build_bidir_bundle(vec_lines: list[str]) -> str:
    parts = [
        HEADER_EXIT,
        "# ----- Trade sim helpers + simulate_trades_bidir -----\n",
        _build_excerpt(vec_lines, BIDIR_SUPPORT_LINES),
    ]
    return "".join(parts)


def _build_rsi_sim(vec_lines: list[str]) -> str:
    return HEADER_EXIT + "# ----- simulate_trades_rsi -----\n" + _build_excerpt(vec_lines, RSI_SIM_LINES)


def main() -> None:
    vec_lines = _read_lines(VEC)
    shutil.rmtree(OUT, ignore_errors=True)
    SHARED.mkdir(parents=True, exist_ok=True)

    shutil.copy2(IWUP, SHARED / "indicator_warmup.py")
    shutil.copy2(STRAT_MD, SHARED / "strategies.md")
    if HANDOFF.is_file():
        shutil.copy2(HANDOFF, SHARED / "REVIEW_HANDOFF_FOR_LLM.txt")

    (SHARED / "simulate_trades_bidir_review_bundle.py").write_text(
        _build_bidir_bundle(vec_lines),
        encoding="utf-8",
    )
    (SHARED / "simulate_trades_rsi_review_bundle.py").write_text(
        _build_rsi_sim(vec_lines),
        encoding="utf-8",
    )

    (SHARED / "README_SHARED.txt").write_text(
        """Contents of review_packages/_shared/

indicator_warmup.py
  Warmup bar counts (vec_warmup_prefix_len / min_bars_ready_for_mode).

strategies.md
  Human-readable description of all registered modes.

simulate_trades_bidir_review_bundle.py
  TradeResult + fee helper + intrabar helper + simulate_trades_bidir — used by most modes in WFO.

simulate_trades_rsi_review_bundle.py
  simulate_trades_rsi — used only by rsi_reversion mode.

REVIEW_HANDOFF_FOR_LLM.txt
  Global briefing for reviewers (paths, checklist, mode list).
""",
        encoding="utf-8",
    )

    for mode, ranges in MODE_RANGES.items():
        d = OUT / mode
        d.mkdir(parents=True)
        src_pine = BY_MODE / f"TradingBotScalp__{mode}.pine"
        if not src_pine.is_file():
            raise SystemExit(f"missing {src_pine}; run pine/_gen_by_mode.py first")
        shutil.copy2(src_pine, d / "strategy.pine")

        excerpt = HEADER_ENTRY + _build_excerpt(vec_lines, ranges)
        _validate_excerpt(mode, excerpt)
        (d / "bot_excerpt_scalp_vec_backtest.py").write_text(excerpt, encoding="utf-8")

        strat_anchor = {
            "daviddtech_scalp": "## 1.",
            "ema_momentum": "## 2.",
            "ema_scalp": "## 3.",
            "macd_scalp": "## 4.",
            "rsi_reversion": "## 5.",
            "supertrend": "## 6.",
            "squeeze_momentum": "## 7.",
            "qqe_mod": "## 8.",
            "utbot_alert": "## 9.",
            "hull_suite": "## 10.",
            "sar_chop": "## 11.",
        }.get(mode, "")

        readme = f"""Review package: {mode}
==============================

Chart / Pine
  strategy.pine — paste into TradingView Strategy Editor (5-minute chart).

Bot entry logic (numpy)
  bot_excerpt_scalp_vec_backtest.py — helpers + detect_signals_* for this mode only
  (line-number markers inside file).

Warmup / first-bar masking
  ../_shared/indicator_warmup.py — search mode string "{mode}".

Human-readable spec
  ../_shared/strategies.md — section starting "{strat_anchor}" for this mode.

Exit simulation (WFO / vec)
"""
        if mode == "rsi_reversion":
            readme += (
                "  ../_shared/simulate_trades_rsi_review_bundle.py\n"
                "  (rsi_reversion-only exit loop)\n\n"
            )
        else:
            readme += (
                "  ../_shared/simulate_trades_bidir_review_bundle.py\n"
                "  (ATR stop / TP / time / optional counter-exit path)\n\n"
            )

        readme += """Full module (if you need full context)
  ../../../backend/server/scalp_bot/scalp_vec_backtest.py

Multi-mode Pine (dropdown)
  ../../TradingBotScalp_AllModes.pine

Global reviewer briefing
  ../_shared/REVIEW_HANDOFF_FOR_LLM.txt
"""
        (d / "README.txt").write_text(readme, encoding="utf-8")

    (OUT / "README.txt").write_text(
        """review_packages/ — per-strategy LLM review bundles
==================================================

Generated by:  python pine/package_review_folders.py

Layout:
  _shared/     Indicator warmup, strategies doc, exit-simulation excerpts (use with every mode).
  <mode>/      strategy.pine + bot_excerpt_scalp_vec_backtest.py + README.txt for that mode.

Send another LLM one folder at a time (zip ``ema_momentum/`` plus sibling ``_shared/``),
or zip the entire ``review_packages`` directory for full audit.

Regenerate after changing Pine outputs (run ``pine/_gen_by_mode.py``) or editing MODE_RANGES
in ``package_review_folders.py`` when scalp_vec_backtest.py shifts materially.

The generator validates each excerpt contains the mode's ``detect_signals_*`` function
(see ``MODE_DETECT_FN``). If validation fails, update line ranges and re-run.
""",
        encoding="utf-8",
    )

    print(f"wrote {len(MODE_RANGES)} strategy folders under {OUT}")
    print(f"shared artifacts under {SHARED}")


if __name__ == "__main__":
    main()
