# Phase 3 — Experiment plan (run 20260407)

## Research verdict (RULE R) — required for new runs

For each **H-xxx** executed on charts or live, add a subsection:

```markdown
### H-xxx
- **Research:** `.optimization/pnl-feedback-lab/research/H-xxx/report.md`
- **Verdict:** supports | mixed | contradicts | insufficient evidence (one paragraph)
```

Run **20260407** predates RULE R (baseline screen only). Future plans must attach research before scheduling backtests.

## Commands

```bash
cd <repo-root>
python .optimization/pnl-feedback-lab/scripts/run_multiwindow_lab.py
# stdout JSONL → runs/lab_run_20260407.jsonl
# stderr summaries → runs/lab_run_stderr.txt
```

## Pre-registered success (RULE C)

For any strategy **CONFIRMED** as steady PnL candidate on BTC 15m:

1. `total_pnl > 0` in **≥ 2/3** windows  
2. `trade_count ≥ 5` in **≥ 2/3** windows  
3. `profit_factor ≥ 1` when defined (finite) in **≥ 2/3** windows  
4. Worst-window `total_pnl` not catastrophic vs baseline worst (G5 qualitative this run)

## Not in scope this run

- Code edits (RULE D not invoked)
- Param sweeps (next cycle: add `tools/pnl_lab_sweep.py` or extend script with argparse)

---

## Skill run `skill_20260408_tape` (2026-04-08) — live PnL incident

### H-LIVE-NEG-20260408

- **Research:** `.optimization/pnl-feedback-lab/research/H-LIVE-NEG-20260408/report.md` (Lens B — **mixed**)
- **Tape contract:** `next_open`, fee 0, slippage 1 bps, windows = early/mid/late thirds of bar index, operating interval **15m**, optional interval vector `5,15,60` (5m/60m skipped — missing Parquet).

**Command:**

```bash
python .optimization/pnl-feedback-lab/scripts/run_multiwindow_lab.py --intervals 5,15,60
# Artifacts: runs/skill_20260408_tape/lab.jsonl, lab.stderr.txt
```

### Pre-registered checks (RULE C orientation)

- **Operating mode in live:** confirm from `data/scalp_champion.json` + session JSONL (`champion_period_start`, fills) — not assumed from `config.toml` `strategy_mode` alone.
- **Guards:** `daviddtech_scalp` on BTC must not be treated as CONFIRMED steady-PnL: **late window** shows **n=2, total_pnl &lt; 0, PF &lt; 1** in this tape run (see compare doc).
