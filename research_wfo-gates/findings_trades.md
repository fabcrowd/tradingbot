# Findings: Trade counts and WFO sample size

## Summary

Vendor and platform documentation emphasizes **many trades per OOS segment** (on the order of **tens** per run, and **hundreds** across a multi-run WFA) for trustworthy conclusions. The repo’s live-style setting of **`wfo_min_trades = 3`** is **far below** that statistical guidance—so the **train/holdout trade floor is not “too strong” by industry standards**; if anything it is **very permissive** and increases noise / false positives unless other layers compensate.

## Sources

1. **TradeStation — Walk-Forward Optimizer FAQ** (minimum trades)  
   - URL: https://help.tradestation.com/10_00/eng/tswfo/topics/frequently_asked_questions.htm  
   - Quote (FAQ “What is the minimum number of trades…”): recommends the strategy **ideally produce at least 30 trades during each out-of-sample run**; worked example for 10-run WFA implies **hundreds** of combined OOS trades for a clear robustness picture.

2. **Interactive Brokers Campus — Walk forward analysis overview**  
   - URL: https://www.interactivebrokers.com/campus/ibkr-quant-news/the-future-of-backtesting-a-deep-dive-into-walk-forward-analysis/  
   - Emphasizes segmenting history, repeated OOS testing, and evaluating **consistency** across segments—not a specific trade count, but aligns with “multiple segments” rather than single-window vanity metrics.

## Implication for this codebase

- If WFO returns **no champion**, it is **unlikely** to be because `min_trades=3` is too high relative to mainstream advice; other gates (especially **cross-window presence** and per-window train survival) dominate.
