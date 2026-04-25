---
name: proposed-changes-pnl-test
description: >
  Compares **structural** code or harness changes against a **deployed** or baseline build with
  **strategy, config, and venue held constant**; reports PnL and health from logs or identical
  harnesses. Activates when the user says "proposed changes P&L test", "structural A/B", "baseline
  vs candidate SHA", "did this refactor hurt PnL", "deploy comparison", "measure P&L impact of this
  branch", "GO NO-GO on this change", or "Track B knob suite" for WFO harness deltas. Also activates
  whenever the user asks to test PnL impact of a **code/build** change (not a new strategy pick).
  Do NOT use for champion-vs-lab strategy discovery (use pnl-feedback-lab); do NOT imply merge
  approval; do NOT skip baseline→candidate→verify order; do NOT invent windows or fields missing
  from telemetry—state gaps honestly.
---

## Overview

This skill enforces a **three-step evidence chain** (baseline, single candidate delta, verify with
the same commands) and a **mandatory closing brief** with explicit GO / NO-GO / HOLD tied to
numbers.

## Workflow

1. Freeze **baseline** artifact (SHA, command, first JSON/log output).
2. Apply exactly **one** approved candidate change; re-run the **same** measurement commands.
3. Discover telemetry limits from code (`session_logger`, snapshots, trades JSONL) before claiming
   multi-window lab metrics.
4. Fill sections **1–7** of the mandatory summary (Context → Go/No-Go) in chat—**no** "see file only"
   endings.
5. Track B (`.optimization/` harness): follow the staged CLI block in this file; still end with GO /
   NO-GO / HOLD and operator approval gate for production edits.

## Output format

- Closing reply must include: context, **proposed change intent** bullets, factual delta, bottlenecks,
  **PnL verdict line** (positive/negative/neutral/inconclusive with numbers or explicit
  not-measurable), recommendation, **GO|NO-GO|HOLD** plus merge disclaimer.
- **Forbidden:** merging or saying "merge-ready" without explicit in-thread operator approval;
  conflating strategy tournaments with structural tests.

## Examples

### Happy path

**Input:** “Compare live `trades_live.jsonl` last 48h: main vs this branch, same config.”

**Expected:** Window defined, confounds listed, PnL delta stated, HOLD if market path confounded.

### Edge case

**Input:** “No trades in window.”

**Expected:** Verdict **inconclusive / not measurable**; no fabricated PnL.

### Negative

**Input:** “Which lab mode beats champion on tape?”

**Expected:** Redirect to **pnl-feedback-lab**; do not frame as structural A/B.

# Proposed changes P&L test (structural code only)

## Operator contract (read first)

Whenever the request is about **PnL impact**, **effect of a change**, **baseline vs candidate**, or **structural A/B** (including offline harness arms), **use this skill for the full workflow** — not a one-off script summary without the sections below.

**Order (non-negotiable):**

1. **Baseline** — freeze inputs: SHA or harness command + first JSON/log artifact.  
2. **Candidate** — exactly one approved delta from baseline.  
3. **Verify** — repeat the **same** command(s); lead the verdict with **PnL numbers** (then skips, gates, confounds).

**Implementation bar (when P&L is the goal):** If the change was proposed to **improve P&L** (or reduce loss) on the **agreed primary metric** vs baseline, **do not implement** when the verified result is **net negative**, **neutral** (within tolerance), or **inconclusive** — unless the operator **explicitly** reopens the goal (e.g. “this one is for latency / safety only”) **before** the run. **Net positive** is the default bar to recommend shipping a **PnL-motivated** change.

**Merge / deploy gate:** The agent **must not** merge to the default branch, mark a PR merge-ready as if approved, or instruct “go ahead and merge” **without explicit written approval from the operator in the same thread.** The closing recommendation is **evidence + suggestion only**; **merge is always the operator’s call.**

Strategy discovery (best mode on tape, champion vs lab modes) belongs in **pnl-feedback-lab**, not here — unless the question is strictly “did this **structural** diff change outcomes holding strategy constant?”

## What is under test

| Under test | **Not** under test |
|------------|-------------------|
| **Structural code** — layout, refactors, modules, wiring, execution path, bugfixes, performance of **the same** bot **without** changing the trading hypothesis | **Strategy / parameter / market** experiments: new mode, new champion, new pair set, “does edge X beat Y on history” |

**Independent variable:** **built artifact** (e.g. `git` SHA) — **deployed** baseline vs **proposed** candidate.

**Held constant** between arms (unless the change *is* explicitly config-only, in which case isolate a **code-only** comparison separately): `[scalp]` strategy selection, params the operator treats as fixed, venue, portfolio, and any env the **deployment** reads.

**Dependent variables (outcomes you may measure):** Anything the **running** system (or an **identical** structural regression harness) actually emits — including **realized PnL** over a window, net / open legs, fills, errors, reconcile drift. Use **testing methodology** (pre-registered UTC windows, rollup rules, worst-window callout, thin-n, limits) to **summarize and compare** those outcomes — **provided** the data exists or the harness is the same for both SHAs.

---

## Methodology vs subject

- **Allowed:** Rigorous methodology to turn **PnL and position data** into comparable results (per-window sums, rollups, guards, explicit tolerances). Same discipline as the lab skill **where it applies to structural A/B**, not to picking a new strategy.
- **Wrong skill:** Using tape/lab runs whose **purpose** is **strategy selection** (e.g. champion vs five modes, best bar size discovery) as the main answer to “should I merge this **refactor**?”
- **Optional supplement:** Run **the same** deterministic check twice (same fixture, same inputs, two builds) if it exercises **deployed structural paths** — still a **structural** comparison, not a new hypothesis on edge.

---

## Deployment telemetry gate (unchanged)

Multi-window slices, rollup verdicts, and guard fields apply **only** when **current code** logs or exposes enough to support them — see discovery steps below. Do not invent intervals the bot does not record.

**Discovery step (required before claiming lab-shaped checklists):**

1. Inspect `session_logger.py`, `scalp_runtime.snapshot()`, `state.snapshot()`, `ws_server` scalp payload, `coinbase_order_manager` reconcile / logging.  
2. List concrete fields and event types per time step.  
3. Define windows only from slices you can cut from artifacts (`ts` ranges, etc.).  
4. **PnL per window:** from `trades_*.jsonl` / session events **if** timestamps and amounts exist there — same methodology baseline vs candidate.

If telemetry is thin, report at the **maximum** resolution available and state gaps.

---

## When lab-shaped ideas apply (conditional)

| Idea | Use **only if** … |
|------|-------------------|
| **≥3 windows** | Timestamped records support **three** disjoint intervals with the same metric definitions. |
| **Same contract** | Same loaded `[scalp]` + env; **only** SHA / binary differs between arms. |
| **Primary + guards** | Deployment (or shared harness) exposes the fields; **primary** can be net position **and/or** realized PnL in window — state which. |
| **Worst window** | Multiple windows valid. |
| **Thin n** | Counts per window (fills, trades) recoverable or note low power. |
| **Table + verdict** | Always — from **pulled** numbers. |
| **Limits** | Always — funding, manual orders, market confound for sequential deploys, etc. |

---

## Mandatory summary output (in-chat)

Closing this skill **requires** a single consolidated reply using **facts from evidence** (numbers, log lines, SHAs, window definitions). Do not end with only “see `data/...`.” Use this **outline**; omit a subsection only if **inapplicable**, and say **why** (e.g. “no PnL lines in window — PnL verdict: not measurable”).

### 1. Context (short)

- Baseline SHA / artifact id and candidate SHA / branch name.  
- Comparison window(s): UTC bounds or “single session since deploy.”  
- Data sources used (e.g. `trades_live.jsonl` lines filtered by `ts`, `session_*.jsonl` event types).  
- **Confounders** if any (sequential deploy = different market path; thin n; manual orders).

### 2. Proposed change summary (required)

Before numbers, state in **plain language** what the candidate is **trying to achieve** (operator intent), in **3–5 short bullets**:

- **Goal** — e.g. “raise net PnL”, “add fee stress to lab”, “stop champion overfit”, “fix reconcile drift”.  
- **Mechanism** — what lever moves (config key, branch behavior, harness flag).  
- **Scope** — pairs, venue, time windows, what is **held constant** on purpose.  
- **Success criterion** — what would count as “better” for this test (primary metric).

This block answers: *If we merged this, what would be different for the bot or the harness?*

### 3. What the proposed change did (factual)

- **Code delta in plain language:** which modules/paths changed and what behavior moved (routing, timing, error handling, API calls, snapshot fields). Tie to **observed** deltas (e.g. “reject count dropped from N→M”, “reconcile now emits X”).  
- **No effect observed:** state explicitly if metrics and logs match within tolerance.

### 4. Bottlenecks and cascade effects

- **Bottlenecks:** rate limits, stale book, slow paths, lock contention, WS backlog, single-thread hotspots — **only** if **evidence** in logs, metrics, or code path supports it; otherwise “**None identified** from available telemetry.”  
- **Cascade effects:** downstream impact (e.g. missed fills → position drift → reconcile alerts; config reload → mode mismatch). Trace **cause → effect** with **timestamps or sequence** when possible.  
- If none: **“No cascade or secondary failure observed in the comparison window.”**

### 5. PnL vs deployed bot (verdict line required)

State **one** of these **exactly**, with **numbers** when data exists:

| Verdict | When |
|---------|------|
| **Net positive vs deployed** | Realized PnL (or agreed PnL proxy) **higher** for candidate than baseline over the **same-defined** comparison, beyond stated tolerance — cite Δ$ or Δ%. |
| **Net negative vs deployed** | **Lower** for candidate — cite Δ. |
| **Neutral vs deployed** | Within tolerance — cite both totals and tolerance. |
| **Inconclusive / not measurable** | Overlapping windows impossible, no trades, missing `pnl_delta`, or confound dominates — **do not** guess. |

For changes whose **stated goal** is P&L: **net positive** is the default **implement** bar; **net negative**, **neutral**, or **inconclusive** → default **do not implement** (unless the operator pre-declared a different primary outcome).

Repeat **per window** if multi-window rollup applies; then **one line** rollup using the **Rollup** rules above.

### 6. Operator recommendation (one line)

Tied to sections 3–5, not generic encouragement. For **PnL-motivated** changes: suggest **implement** only when section 5 is **net positive vs deployed**; otherwise **do not implement** / **hold** / **inconclusive — no implement**. **Do not** state or imply merge approval — merging is **only** after the operator **explicitly** approves in-thread.

### 7. Go / no-go (required)

End with an explicit label and a short justification:

- **Line 1:** One of **`GO`** (safe to proceed toward implementation / merge **after** your approval), **`NO-GO`** (do not implement or merge on this evidence), or **`HOLD`** (need more data, different window, or confound unresolved).  
- **Line 2–4:** **Why** — tie to section **5** (PnL verdict), section **2** (whether the proposal still matches the result), and any non-PnL factors (risk, ops, complexity).  
- **Merge:** Restate that **merge/deploy** requires **explicit** operator approval regardless of GO wording.

---

## Baseline vs proposed

| Arm | Definition |
|-----|------------|
| **Baseline** | Deployed build — record SHA when capturing metrics. |
| **Candidate** | Proposed merge / branch / image, same environment class. |

Sequential deploys: market path confounds PnL — state explicitly.

---

## Rollup (only if ≥3 comparable windows exist)

| Verdict | Condition |
|---------|-----------|
| **Better overall** | ≥2 windows **Better**, none **Worse** (on agreed primary — PnL and/or net exposure). |
| **Worse overall** | ≥2 **Worse**. |
| **Same overall** | ≥2 **Same**, no **Worse**. |
| **Mixed** | Else. |

Fewer than three windows → no lab-style rollup; interval-limited conclusion only.

---

## Evidence (priority)

1. **Live / paper-deployed** logs: `data/session_*.jsonl`, `trades_live.jsonl` / `trades_paper.jsonl`, snapshots — **confirm** event shapes in code.  
2. **Structural regression:** same test/fixture, two SHAs, if it mirrors deploy paths.  
3. Exchange ground truth when bot state is ambiguous.

**Do not** treat **strategy-discovery** lab artifacts (oracle mode picks, champion vs lab winner tables) as the **deciding** evidence for a **structural** merge — wrong question.

---

## Track A — Infra only

Compile, pytest, smoke — no claim on PnL/position from Track A alone.

---

## Anti-patterns

- Framing a **strategy** comparison as a **structural** one (changing modes/champion between arms).  
- Forcing three windows when logs don’t support them.  
- **Deciding** a refactor on **champion vs lab** tape without structural A/B on constant strategy.  
- Citing snapshot fields without verifying names in **current** source.

---

## Relation to pnl-feedback-lab

| Skill | Question |
|-------|----------|
| **Proposed changes P&L test** (this skill) | Did **this structural code change** vs **deployed** alter **observed** outcomes (PnL, position, health) under **fixed** strategy/config? |
| **pnl-feedback-lab** | Does **hypothesis / mode / champion** hold on **historical tape** (multi-window, oracle compare, etc.)? |

Use **both** when needed; keep the **question** explicit in the report.

---

**Rule of thumb:** **Structure in diff, strategy frozen, methodology honest** — PnL is a valid outcome column, not a forbidden word.

---

## Track B — WFO / config-knob regression (**P&L-first**, harness under `.optimization/`)

Use this track when the question is **not** a strategy oracle (that stays **pnl-feedback-lab**), but **whether a configuration knob changes simulated P&L** on a fixed tape. **Do not edit production modules** (`backend/server/...`) until baseline vs candidate numbers exist **and** the operator **explicitly** approves merge or implementation in-thread.

### Required workflow (no exceptions)

| Step | Action |
|------|--------|
| **1. Baseline** | Same commit, same `config.toml` slice, same `--load-days` / flags. Save JSON or pasted table (**frozen reference**). |
| **2. Candidate** | Only after step 1: branch, patch, or config edit the operator approved for **measurement**. |
| **3. Verify** | Re-run **identical** harness CLI; compare **P&L columns** first (`wf_diagnostic_total_pnl`, champion `latest_holdout_total_pnl` when `ok`, `bootstrap_window_total_pnl`). If the goal is P&L uplift, **do not implement** unless candidate is **better** than baseline on the agreed metric. **Never merge** production paths without **explicit operator sign-off** in-thread. |

**Forbidden:** implement → exercise → hope. **Never** change `scalp_wfo.py` (or other production paths) only to “support the harness” before baseline + approval.

### Stage 1 vs stage 2 (terminology)

| Stage | Harness | Production parity |
|-------|---------|---------------------|
| **1** | `.optimization/wfo_window_ab_test.py` — train-slice mode pick + fixed forward segments | **Low** — no holdout slice, no full param grid, no latest-holdout gate |
| **2** | `.optimization/wfo_knob_suite.py` — calls **stock** **`scalp_wfo.optimize_pair`** + adds **`wf_diagnostic_*`** on longer `bar_store` windows | **Mixed** — `optimize_pair` uses the **normal** WFO bar-load formula; **`--load-days`** applies only to **diagnostic** walk-forward P&L so you still get **money** deltas on long tape without patching production |

**Recency divisor suite:** one `optimize_pair` per pair (production always uses `n/3` recency). Rows differ in **`wf_diagnostic_total_pnl`**, which sweeps train-slice recency **divisor** for P&L-only sensitivity.

### CLI — knob suite

From repo root (PowerShell; use `;` not `&&` where needed):

```powershell
Set-Location <repo>
# Baseline (example): long diagnostic tape, full grid inside optimize_pair
python .optimization/wfo_knob_suite.py --suite all --load-days 45 --output-json .optimization/runs/wfo_knob_baseline.json

# Shorter diagnostic window + coarser WFO step (--fast)
python .optimization/wfo_knob_suite.py --suite all --fast --output-json .optimization/runs/wfo_knob_fast.json

# Synthetic Parquet if missing (not market-realistic)
python .optimization/wfo_knob_suite.py --suite stage2_train_hours --fast --synth-if-missing --pairs BTC_USD

# Single pair / suite
python .optimization/wfo_knob_suite.py --suite recency_divisor --load-days 40 --pairs BTC_USD
```

Suites: `stage2_train_hours`, `recency_divisor`, `wfo_min_trades`, `holdout_pf`, `bootstrap`. **`all`** runs them in order.

### Parallel execution (subagents / workers)

Split by `--suite` and/or `--pairs`; each job writes its **own** `--output-json`. Merge rows for the report.

### Mandatory closing summary (Track B extension)

Reuse sections **1–7** from this skill (including **proposed change summary** and **go / no-go**). Add:

- **Stage:** **1** or **2**; **baseline SHA / artifact** vs **candidate**.  
- **CLI:** exact command + `wf_diagnostic_load_days`, `fast`, `synth-if-missing`.  
- **P&L (primary):** lead with **Δ** on `wf_diagnostic_total_pnl` (and champion holdout P&L when `ok`). Bootstrap: `bootstrap_window_total_pnl`.  
- **Verdict:** **adopt** only if P&L is **better** than baseline on the agreed primary and the operator will merge; else **hold** / **do not implement** / **inconclusive — no implement**. Never imply merge without operator approval.

### Relation to `wfo_window_ab_test.py`

**Stage 1** for cheap train-window sensitivity; **stage 2** + **diagnostic P&L** for knobs that touch WFO gates or `optimize_pair` — still **no production edits** until baseline → verify passes, P&L is **better** on the agreed metric (when that is the goal), and the operator **explicitly** approves merge.
