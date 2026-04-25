# Findings: Profit factor, win rate, drawdown gates

## Summary

Public articles stress **robustness and avoiding over-optimization** more than a single universal PF/WR cutoff. Practical posts note that **very high** PF/WR filters encourage curve-fitting. The codebase’s **default train gates** (`min_profit_factor=0.8`, `min_win_rate=0.20`, `max_drawdown_pct=30`) are **moderate “floor” filters**—not aggressive “elite strategy only” thresholds. **They are weaker than** common informal targets cited for “solid” systems (e.g. PF well above 1.0), so they are **unlikely** to be the primary “too strong” lever unless live config overrides them.

## Sources

1. **TradeZella — Profit factor overview**  
   - URL: https://www.tradezella.com/blog/profit-factor  
   - General framing: PF compares gross profit to gross loss; values **≤1** mean the strategy does not cover losses; **>1** needed for edge—implies **0.8 PF floor** in code is **below** a true breakeven PF definition in strict terms, but the code pairs PF with other gates.

2. **QuantStart — Successful backtesting (Part I)**  
   - URL: https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-I/  
   - Stresses careful methodology, data snooping, and validation—not a single numeric PF gate—supports interpreting PF/WR as **diagnostic**, not one magic threshold.

3. **Reddit r/algotrading — high win rate, negative PnL scalping**  
   - URL: https://www.reddit.com/r/algotrading/comments/1pyj8t1/55_win_rate_but_negative_pnl_on_a_scalping/  
   - Illustrates WR alone is misleading; aligns with using **multiple metrics** (the bot uses objective + PF + optional holdout PF).

## Implication for this codebase

- **Default PF 0.8 / WR 20%** are **lenient** vs “only trade PF>1.5” culture.  
- **`wfo_min_holdout_pf = 0.5`** in `config.toml` (when `require_positive_latest_holdout` is false) is **very loose** for the latest-holdout PF check path.  
- **Conclusion:** PF/WR/DD defaults are **not** the obvious “too strict” culprit relative to internet-stated norms.
