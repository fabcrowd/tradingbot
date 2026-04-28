---
name: deep-bug-hunt
description: >
  Inspects **recent commits** for **critical** correctness issues that escaped review: data loss,
  crashes, security holes, auth bypasses, races that drop writes, resource leaks, infinite loops,
  silent truncation. Traces full caller chains and concrete trigger scenarios; implements **minimal**
  fixes with tests when confident. Activates on "deep bug hunt", "critical bug sweep", "post-merge
  audit", "find severe bugs in recent commits", "high-severity correctness review", "escape review
  bugs", "commit audit for crashes/data loss", or "severity-only diff review". Do NOT use for style
  nits, broad refactors, low-UX polish, theoretical issues without a trigger, full PR style review
  (use code-review-security when scoped), PnL/strategy questions (use pnl-feedback-lab), or opening
  PRs without high confidence in bug + fix.
---

## Overview

This skill drives **automation-style** review of **recent behavioral changes** with large blast
radius. The default outcome is **no critical bug found** — report that briefly. Only **escalate to
a code change / PR** when the model can state a **plausible trigger**, **impact**, and a **minimal,
high-confidence** fix.

## Goal

Surface **only** issues that would cause:

- Data loss or corruption  
- Crashes or unhandled fatal paths  
- Security holes or auth / permission bypasses  
- Significant user-facing breakage (wrong money, wrong orders, silent wrong state)

Ignore style, minor edge cases, theoretical concerns without a concrete trigger, and low-severity UX
degradation.

## Investigation strategy

1. **Scope blast radius** — Prefer commits that change persistence, concurrency, auth, parsing,
   financial/order paths, shutdown, or cross-process contracts.
2. **Hunt categories** — Data corruption; races that lose writes; null / missing-key dereferences on
   critical paths; auth or permission gaps; infinite loops; resource leaks (handles, tasks, locks);
   silent truncation (buffers, JSON, DB limits).
3. **Trace, do not grep-only** — Follow the **full** path: caller → callee → persistence / IO /
   error handling. Note ordering with async, locks, and retries.
4. **Evidence over pattern** — A suspicious idiom is a **hint** until a chain proves impact.

## Confidence bar

- **Reportable critical finding** requires a **concrete scenario** (inputs, ordering, or state) that
  triggers the failure. If you cannot construct one, **do not** treat it as critical; omit or
  downgrade to "watch item" in prose only.
- **PR / merge candidate fix** requires **high** confidence the bug is **real** and the fix is
  **correct** (reasoned + tests or repro when feasible).
- **When in doubt** — Summarize uncertainty in the operator thread (e.g. chat); **do not** open a PR
  on speculation. If the org uses Slack for async triage, post there instead of a PR when confidence
  is below the bar.

## Fix strategy

- If critical and confidence is high: implement the **smallest** fix that addresses the root cause.
- **Add or update tests** when the codebase has a clear place to lock behavior (regression test for
  the trigger).
- **No** broad refactors in the same change as the fix.

## Safety rules

- **Do not open a PR** unless both **bug reality** and **fix correctness** meet the confidence bar
  above.
- **No critical bugs** after review → post a **short** summary, for example: scope (SHAs / time
  range), what was traced, **"No critical bugs found."** This is the **expected** outcome most days.

## Output format

### When a critical bug was found and fixed

Include all of the following in the closing reply (and PR body if applicable):

1. **Bug and impact** — What breaks, for whom, severity.  
2. **Root cause** — Which change or invariant failed; link to files / functions.  
3. **Trigger scenario** — Concrete steps or conditions (required).  
4. **Fix** — What changed and why it is minimal.  
5. **Validation** — Tests run, manual repro, or trace confirmation.

### When no critical bug (or only watch items)

- **Scope** — Commit range or branch / SHA reviewed.  
- **Focus** — Which subsystems or paths were traced.  
- **Verdict** — **No critical bugs found** (or list **non-blocking** watch items with explicit
  "no PR" / low confidence).

### Forbidden

- Long lists of nitpicks.  
- PRs without trigger scenario + impact.  
- Merging or implying merge approval without operator sign-off where the repo requires it.

## Workflow

1. Confirm **scope** (e.g. `main` since date, last N commits, or named branch diff against base).
2. List commits touching **high-risk** areas; read full diffs and **call sites**, not only hunks.
3. For each candidate issue: write the **trigger scenario**; if missing, **discard** as critical.
4. If a critical issue survives: design **minimal** fix → implement → run **compileall / pytest /
   targeted test** as available.
5. Close with the **Output format** section above; open PR only if confidence rules pass.

## Examples

### Happy path (critical found)

**Input:** "Deep bug hunt last 10 commits on `feature/orders`."

**Expected:** One issue with trigger + impact, minimal patch, test if applicable, structured closing
sections.

### Edge case (nothing critical)

**Input:** "Audit yesterday's merges for severe bugs."

**Expected:** Short summary, subsystems checked, **No critical bugs found.**

### Negative (wrong skill)

**Input:** "Review this PR for naming and formatting."

**Expected:** Do **not** apply this skill as primary; use a general / style review request; this
skill stays severity-only.

## Relation to other skills

| Skill | Use when |
|-------|----------|
| **deep-bug-hunt** (this) | Recent commits; **severity-first**; concrete triggers; minimal fixes. |
| **code-review-security** | Phased security / correctness on a **scoped** diff or module with verify steps. |
| **pnl-feedback-lab** | Strategy / tape / champion questions — not commit-severity sweeps. |

## Anti-patterns

- Opening PRs for "might be wrong" without a trigger.  
- Conflating **refactor opportunity** with **critical bug**.  
- Skipping **downstream** effects (e.g. fix breaks idempotency or migration).  
- **Large** refactors bundled with a one-line bugfix.
