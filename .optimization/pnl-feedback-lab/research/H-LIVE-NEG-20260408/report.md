# Lens B — H-LIVE-NEG-20260408

**Claim:** Last session’s live outcome (operator reports **three closed trades, all negative PnL**) is unacceptable for a system that is supposed to express a **positive-expectancy, multi-window** edge.

**Verdict:** **mixed** — consistent with several honest explanations; none justify continuing unchanged without tape checks.

## Mechanisms (why live can lose while “the bot” looks fine in places)

1. **Sample size** — Three trades is far below any stable estimate of expectancy. Loss streaks happen by chance even for edges with true positive EV.
2. **Mode mismatch** — Config pins `strategy_mode = daviddtech_scalp`, but **WFO can own mode** when a champion exists. Session logs show **`macd_scalp`** (and sometimes **`ema_momentum`**) saved as champions with **negative latest-window holdout `total_pnl`** on BTC while still passing aggregate scoring. Live may be running a mode that is **not** the one the operator assumes.
3. **Regime / window** — Vector lab on frozen Parquet shows **`daviddtech_scalp` on BTC 15m late third**: **2 trades, negative `total_pnl`, profit factor &lt; 1**. If “last night” aligned with a late-window-style regime, small-n losses are **tape-consistent** for that mode slice.
4. **Execution** — Limits, partial fills, funding, and gap vs `next_open` lab assumptions can drag realized PnL below backtest; three trades amplify any single bad fill.

## Falsifiers (what would prove specific stories wrong)

| Story | Falsified if |
|-------|----------------|
| “Just bad luck” | Expectancy over **N ≥ 30** live trades (or pooled windows) stays negative while lab positive with same mode + costs. |
| “Wrong mode” | Live trade log shows mode = `daviddtech_scalp` and losses still cluster while WFO holdout for that mode is positive across windows. |
| “Lab is fantasy” | Replicated session replay or bar-exact sim matches live fills within documented slippage; still divergent → model bug. |

## Lens A asks (for loop)

- What **mode** was active per fill (from runtime / champion file / session)?
- Do the **three** trades map to **BTC / SOL / XRP** and which **window** of the 15m series?
- Is **`min_mean_score = -0.1`** in WFO allowing **mean `expectancy_sqrt_n` barely above zero** while **latest holdout is deeply negative**?

## Next Lens B pass

- Short **deep-research** cycle on WFO objective choice vs **mandatory non-negative holdout PnL** (industry practice: never promote a champion with negative OOS net on the primary horizon unless explicitly a hedge hypothesis).
