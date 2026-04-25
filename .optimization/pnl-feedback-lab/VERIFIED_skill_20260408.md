# VERIFIED — skill run `skill_20260408_tape` (2026-04-08)

## Operating interval

**15m** (Coinbase CDE perps; symbols BIP/SLP/XPP per `config.toml`).

## Time windows

**Early / mid / late** thirds of bar index per series (`run_multiwindow_lab.py`).

## Hypothesis H-LIVE-NEG-20260408

| Field | Value |
|-------|--------|
| **Dual-lens verdict** | **CORROBORATED (exploratory)** — theory allows variance/mismatch/regime; tape shows **BTC `daviddtech_scalp` late window negative** and **WFO logs inconsistent with “obviously safe live.”** |
| **CONFIRMED (RULE C)** | **NO** — fails G1/G3/P1 on ≥2/3 windows for `daviddtech_scalp` on BTC. |
| **RULE G (merge/live)** | **NO** — do not merge or scale live on this evidence alone. |
| **Code change (RULE D)** | **NO** — diagnosis only; WFO gate tightening is a **separate** H-WFO-NEG-HOLD change set. |

## Live incident (operator report)

**Three** closed trades, **all negative PnL** — **not** explained away by this doc; **acceptable only** after mode audit, gate review, and (if still live) larger **N** with documented expectancy.

## Queue

- Prove **H-WFO-NEG-HOLD** with trace from `scalp_wfo.py` → session JSONL.  
- Optional **deep-research** on WFO OOS PnL floors (Lens B extension).  
- Add 5m/60m Parquet or document permanent skip for RULE B2.
