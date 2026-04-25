# Related systems: research notes (external)

Internal notes on two external references and how they relate to this repo (spread MM + scalp). Not endorsements; use for architecture and product thinking only.

**Sources (visited 2026-04-05):**

- [Core Alpha Systems — Trade Engine](https://www.corealphasystems.com/) (trade-prevention / filtering product narrative)
- [pazhenchira/meta-metacognition](https://github.com/pazhenchira/meta-metacognition) (research repo: meta-orchestration, wisdom/patterns, multi-repo coordination)

**LinkedIn:** No post was identified that clearly maps to the meta-metacognition repo author or announcement. If you have a specific URL, add it here as a one-line citation for future readers.

---

## 1. Core Alpha Systems (Trade Engine)

### What it emphasizes (structurally)

- **North star is restraint**, not more activity: filter and block bad trades; success framed as *trades not taken*.
- **Two-stage pipeline**: (1) offline / end-of-day scan → shortlist; (2) **re-validation at execution time** (conditions changed → block with a reason).
- **Explainability**: every rejection has a human-readable rationale (audit trail, not a black box).
- **Operational safety**: kill switch, overrides tracked separately, broker holds funds (execution is separate from “advice”).

### Parallels to *this* project

| Their idea | Our analogue (already or partially) |
|------------|-------------------------------------|
| Second gate before acting | Re-quote / risk gates before `add_order`; `risk_halted`; book staleness; threat detector widening |
| “Conditions changed overnight” | Regime shift: WFO holdout, recency-weighted metrics, bar_store freshness |
| Explain why we did not trade | Session logs, alerts, dashboard state — could be **more structured** (one line per skipped signal) |
| Track overrides | Manual strategy mode vs adaptive; any future “force trade” should be logged explicitly |

### Potential **implementation** ideas (lightweight)

1. **Pre-order confirmation object** (optional, small): right before placing a scalp/MM order, run a checklist (spread vs limit, fee floor, halt flags, min notional) and append one JSON line or log field: `decision: place \| skip`, `reason_codes: [...]`. Aligns with their “every no explained” without building a separate product.
2. **Time-split validation**: for scalp, an explicit “signal at candle close” vs “order at T+N seconds” re-check (spread still valid?) — same *spirit* as the morning gate, much simpler scope.

### Reasons **not** to implement wholesale

- Product domain is **options + Schwab workflow**; our stack is **Kraken spot + MM + scalp**.
- Copying “trade less” philosophy literally could **fight** market-making (MM needs continuous quoting unless you intentionally run opportunistic mode).
- Legal/compliance framing on their site is **their** product; we should not imply similar registrations or protections.

**Verdict:** Treat as **UX and governance inspiration** (second gate, explicit skip reasons, kill switch discipline). Avoid scope creep into a full “prevention engine” unless product direction changes.

---

## 2. meta-metacognition (GitHub)

### What it is

- A **research / thinking space** for multi-agent orchestration: beliefs under uncertainty, coordination without central control, **restraint** when action would be harmful.
- **Not** positioned as a finished production framework (per README).
- Concrete artifacts: `.brain/` playbooks and principles, `patterns/` (antipatterns, success patterns, trade-off matrices), `INTUITION.md`-style **wisdom libraries**, optional **system-of-systems** coordination modes (`standalone` → `governed`), explicit **sources of truth** (`app_intent.md`, `essence.md`, `meta_config.json`).

### Parallels to *this* project

| Their structure | Our analogue |
|-----------------|--------------|
| Hierarchical orchestration | `main.py` + `ScalpRuntime` + WFO + param tuner + `LiveOrderManager` |
| “Who decides?” governance | **WFO champion → self-tuner → config default** (see scalp UI “ACTIVE STRATEGY” / backend `_active_mode`) |
| Wisdom + antipatterns | Implicit in code review; could be **documented** for trading-specific pitfalls (overfitting, fee/ATR trap, stale champion) |
| Confidence / phase gates | Warmup, WFO data readiness, `wfo_min_trades`, tuner aggressiveness bands |
| Multi-repo contracts | Less relevant today; relevant if MM, scalp, and research split into separate repos |

### Potential **implementation** ideas

1. **Keep a single “decision ledger” markdown or JSONL** (developer-facing): intent (“optimize win rate with recency”), invariants (“never exceed allocated capital”), and links to config keys — mirrors their `app_intent.md` / `essence.md` split without adopting their engine.
2. **Trading-bot antipattern list** in `research/` (short): e.g. “champion from dead regime,” “tuner frozen on tiny sample,” “MM and scalp fighting for same API budget.”
3. **Optional**: reuse their *idea* of explicit trade-off matrices when choosing between objectives (`wfo_objective` vs win-rate targets).

### Reasons **not** to integrate the repo directly

- **Different mission**: meta-cognition / multi-agent **software** orchestration vs **low-latency trading** and exchange integration.
- **Weight**: thousands of lines of wisdom and playbooks — high maintenance and poor fit for runtime hot path.
- **Licensing / ops**: pulling it in as a dependency adds process overhead; cherry-pick *patterns* instead.

**Verdict:** **Do not embed** the meta-metacognition codebase into this bot. **Do** borrow *documentation and governance patterns* (sources of truth, antipatterns, explicit decision hierarchy) where they reduce confusion between WFO, tuner, and live behavior.

---

## 3. Synthesis for this repo

- **Core Alpha** → product psychology: **validate twice, log every skip, prefer discipline over impulse**. Map to small logging/gating improvements.
- **meta-metacognition** → engineering psychology: **explicit hierarchy, documented trade-offs, restraint as first-class**. Map to docs and optional `research/` checklists, not a new runtime dependency.

Review this file when changing optimization objectives (win rate vs expectancy vs Sharpe) or adding new autonomous agents, so scope and “who is in charge” stay legible.
