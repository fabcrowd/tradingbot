# Findings: Cross-window consistency vs “50%” narratives

## Summary

Industry writing distinguishes:

- **Walk-forward efficiency (WFE)** — often described as **OOS performance relative to IS** (e.g. retaining on the order of **half** of IS performance as a **sanity check** for degradation, not as “win half of folds”).  
- **Stability across folds** — many authors expect **most** OOS segments to avoid catastrophic failure for a deployable system, but **few** sources prescribe an exact “must pass N of M folds” rule comparable to the repo’s **`len(window_results) >= len(windows)//2`** on the **same discrete parameter index** after **per-window top-K** selection.

The repo’s rule is **structurally demanding**: the **same grid index** must survive **train gates**, land in **top_k** for that window, meet **holdout trade count**, and repeat for **≥ half** of rolling windows. That is **stricter than “~30 trades per OOS”** advice alone would suggest, and **stricter than a single aggregate WFE number**—it enforces **repeated re-selection** of the same parameterization across regimes.

## Sources

1. **TradeStation WFO FAQ** — cluster / multiple scenarios for stability  
   - URL: https://help.tradestation.com/10_00/eng/tswfo/topics/frequently_asked_questions.htm  
   - Describes incremental WFA scenarios and need for enough optimization runs / trade statistics for trustworthy robustness pictures.

2. **IBKR Campus — Walk forward analysis**  
   - URL: https://www.interactivebrokers.com/campus/ibkr-quant-news/the-future-of-backtesting-a-deep-dive-into-walk-forward-analysis/  
   - Lists **consistency** of returns across segments as an evaluation dimension.

3. **r/algotrading — walk-forward optimization practitioner thread**  
   - URL: https://www.reddit.com/r/algotrading/comments/qjrj7b/this_is_how_i_use_walkforward_optimization/  
   - Community-level diversity of procedures; no single standard “50% of folds” for **parameter identity**, supporting the view that **custom code rules can be stricter than textbook summaries**.

4. **Time series cross-validation (Medium)**  
   - URL: https://medium.com/@pacosun/respect-the-order-cross-validation-in-time-series-7d12beab79a1  
   - Reinforces temporal ordering and multiple splits—supports caring about **multi-segment** behavior without mandating one exact fraction.

## Implication for this codebase

- The **“≥ half of windows with valid holdout scores for the same `pi`”** rule is the **most plausible “too strong” gate** versus what most web articles **explicitly** specify, because it couples **top-K lottery** with **cross-regime persistence** at **fixed parameter tuples**.
