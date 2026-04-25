# Research plan: empirical TTL-cancel promotion arm (scalp bot)

## Core question

For a systematic scalp bot using **limit-first entries** with **TTL cancel** and optional **empirical promotion** (pattern-based missed-move vs **immediate** arming on every TTL cancel), what does execution microstructure and practitioner literature suggest about **when the TTL-direct arm adds value vs unnecessary taker/fee risk**?

## Context (repo)

- Bot: Coinbase CDE scalp (`empirical_market_promotion.py`).
- **Pattern path:** TTL cancel → watch favorable drift vs limit → count hits in window → arm N market entries (gated by arm cooldown).
- **TTL-direct path:** `empirical_market_ttl_cancel_arms_promotion` adds market slots on **each** TTL cancel, **without** drift proof or pattern cooldown.

## Subtopics

1. **Limit TTL / cancel drivers:** Why resting limits expire unfilled; adverse selection; queue vs price improvement (academic/industry summaries).
2. **Limit-to-market escalation:** When practitioners escalate after non-fill; urgency vs fee drag; hybrid execution policies.
3. **Maker–taker economics for short-hold strategies:** How taker promotion after failed maker interacts with expectancy (conceptual + cited sources).
4. **Bot-specific synthesis:** Map (1)–(3) to the two-arm design; state falsifiers and what to measure in logs/backtests.

## Output format

- `findings_*.md` per subtopic (Sources sections with URLs).
- `report.md`: answer-first, inline `[title](url)` citations, low-confidence flags, open gaps, falsifiers for tape/backtest.
