# Phase 2 — Hypotheses (queued)

| ID | Claim | Primary | Falsifier |
|----|-------|---------|-----------|
| H-01 | `ema_momentum` on BTC 15m is the most **consistent** positive mode across early/mid/late when trade count ≥ 5 | Mean P2 across windows vs `daviddtech_scalp` | Loses 2/3 windows on P2 or negative total_pnl in ≥2 windows |
| H-02 | `daviddtech_scalp` maximizes peak P2 but fails **steadiness** (high cross-window variance of P2) | std(P2 windows) lower for ema_momentum | daviddtech has lower std AND higher mean P2 |
| H-03 | Extending bar history (more Parquet days) will allow CONFIRMED verdicts for alts | G1 satisfied in ≥2/3 windows after backfill | Still &lt;5 trades per window after data extend |
| H-04 | Tighter stops (lower `atr_stop_mult`) reduce worst-window drawdown without killing late-window PnL | G2 improved; P1 late within 90% of baseline | Both P1 and G2 worse in ≥2 windows |
| **H-LIVE-NEG-20260408** | Live losses are explained by **WFO-selected mode** + **small-n** + **late-window weakness** of the active mode on BTC | Session + `scalp_champion.json` show `daviddtech` live; tape shows daviddtech strong where live traded | Champion is daviddtech and lab still predicts positive expectancy for that window |
| **H-WFO-NEG-HOLD** | WFO can promote modes whose **logged latest holdout** is negative on BTC while still saving champion | Code review + logs show holdout aggregate score &gt; `min_mean_score` only from **other** windows | Every saved champion has latest holdout `total_pnl ≥ 0` and `profit_factor ≥ 1` |

**Status:** PROPOSED — tested indirectly by run `20260407` for H-01/H-02 (see compare doc). **H-LIVE-NEG-20260408** opened for skill run `skill_20260408_tape` (see `05_compare_skill_20260408.md`).
