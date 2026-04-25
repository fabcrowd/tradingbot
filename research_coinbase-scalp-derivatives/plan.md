# Research plan: Coinbase derivatives for scalp bot (futures vs perps)

## Core question

For a short-hold scalp bot integrated with Coinbase, which derivatives **product set** should the operator prefer—**dated futures** or **perpetuals**—and how does that map to Coinbase’s actual venues (US CDE vs international)?

## Subtopics

1. **Coinbase CDE product model**: How Coinbase Derivatives Exchange labels and structures contracts (e.g. nano BTC “perp” with far-dated symbol vs true quarterly futures), fees, FCM/cleared context, and what the API treats as the same instrument class.

2. **Futures vs perpetuals for scalping**: Hold-time, roll/basis risk, funding (where applicable), liquidity depth, and common practitioner framing for sub-hour strategies.

3. **Coinbase venue map**: Coinbase International perpetuals vs US CDE vs (if relevant) retail Coinbase “futures” naming—access, jurisdiction, and which stack matches a Python bot using Coinbase derivatives APIs.

## Output format

`report.md`: answer-first summary, findings by subtopic, source table, gaps, next steps. Inline `[title](url)` citations only.
