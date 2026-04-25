# Best strategy & timeframe — per pair

**Goal:** Choose a **bar size** and **strategy** for each pair. This file ranks lab outputs toward that decision; it does **not** replace WFO/champion logic on your production schedule.

## How winners are chosen

1. For each **pair × bar size × strategy**, average **score_exp_sqrt_n** over **early / mid / late**, using only windows with **≥1 trade**.
2. **Best strategy at a bar size** = highest average score among strategies that traded at that size.
3. **Best bar size for the pair** = among bar sizes where the interval winner has **≥ 10** trades (across early+mid+late), pick the **highest** average score. If none qualify, we fall back to raw best score (**low** confidence — often 1 hour with tiny n).
4. **Sum PnL** = sum of window PnLs for that triple (backtest units). **Worst window PnL** = weakest third — regime risk.

### Confidence

- **high** — trades in **all 3** windows and **≥15** trades total across them.
- **moderate** — **≥8** trades and **≥2** windows with trades.
- **low** — thin sample (typical on **1 hour** with short history).

## Contract (this run)

- **fee_bps_per_leg:** `0.0`
- **fee_bps_source:** `config`
- **fill_model:** `next_open`
- **intervals_swept:** `[5, 15, 60]`
- **min_trades_per_window:** `None`
- **slippage_bps:** `1.0`
- **venue:** `coinbase_perps`
- **windows:** `thirds_of_series_bar_index`

---

## Summary — pick per pair

| Pair | Symbol | **Best bar size** | **Best strategy** | Mean score† | Trades | Sum PnL‡ | Worst window | Conf. |
|------|--------|-------------------|-------------------|-------------|--------|----------|--------------|-------|
| BTC_USD | `BIP-20DEC30-CDE` | **15 min** | **`rsi_reversion`** | 403.2630 | 36 | 3821.5848 | -1006.3396 | high |
| SOL_USD | `SLP-20DEC30-CDE` | **1 hour** | **`ema_scalp`** | 1.6792 | 26 | 15.8286 | -1.0516 | high |
| XRP_USD | `XPP-20DEC30-CDE` | **5 min** | **`rsi_reversion`** | 0.0024 | 82 | 0.0457 | -0.0074 | high |

† Mean of window `score_exp_sqrt_n` where that window had trades.
‡ Backtester-internal PnL, summed over early+mid+late for the chosen triple.

---

## BTC_USD (`BIP-20DEC30-CDE`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `rsi_reversion` | 15.5659 | 3/3 | 101 | 180.0183 | -1937.5472 |
| 15 min | `rsi_reversion` | 403.2630 | 3/3 | 36 | 3821.5848 | -1006.3396 |
| 1 hour | `rsi_reversion` | 837.6379 | 3/3 | 9 | 4481.8675 | 835.8065 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 1 hour | `rsi_reversion` | 837.6379 | 9 | 4481.8675 | 835.8065 |
| 2 | 1 hour | `daviddtech_scalp` | 578.8329 | 8 | 2835.0036 | 373.3670 |
| 3 | 15 min | `rsi_reversion` | 403.2630 | 36 | 3821.5848 | -1006.3396 |
| 4 | 1 hour | `ema_scalp` | 341.1133 | 25 | 2274.1060 | -3902.1821 |
| 5 | 1 hour | `ema_momentum` | 101.6947 | 47 | 1033.6794 | -4937.5986 |
| 6 | 15 min | `daviddtech_scalp` | 56.7737 | 43 | 660.1270 | -1213.8512 |
| 7 | 5 min | `rsi_reversion` | 15.5659 | 101 | 180.0183 | -1937.5472 |
| 8 | 5 min | `ema_scalp` | -78.1696 | 360 | -2666.6033 | -3692.6198 |
| 9 | 15 min | `macd_scalp` | -100.7163 | 223 | -2586.1352 | -1899.2832 |
| 10 | 5 min | `ema_momentum` | -197.4981 | 660 | -8857.6855 | -4896.9800 |
| 11 | 5 min | `macd_scalp` | -213.1903 | 570 | -8771.0380 | -5861.6962 |
| 12 | 15 min | `ema_scalp` | -281.3879 | 138 | -5799.8337 | -4314.7579 |

---

## SOL_USD (`SLP-20DEC30-CDE`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 0.1630 | 3/3 | 79 | 2.3258 | -5.2344 |
| 15 min | `ema_scalp` | 0.4162 | 3/3 | 142 | 8.3683 | -0.2987 |
| 1 hour | `ema_scalp` | 1.6792 | 3/3 | 26 | 15.8286 | -1.0516 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 1 hour | `ema_scalp` | 1.6792 | 26 | 15.8286 | -1.0516 |
| 2 | 1 hour | `macd_scalp` | 1.4120 | 42 | 16.4474 | -1.2614 |
| 3 | 15 min | `ema_scalp` | 0.4162 | 142 | 8.3683 | -0.2987 |
| 4 | 5 min | `daviddtech_scalp` | 0.1630 | 79 | 2.3258 | -5.2344 |
| 5 | 1 hour | `daviddtech_scalp` | 0.1045 | 5 | 0.2484 | -0.3283 |
| 6 | 5 min | `ema_scalp` | 0.0165 | 346 | 0.5952 | -2.3975 |
| 7 | 15 min | `daviddtech_scalp` | 0.0099 | 42 | -0.8599 | -9.5429 |
| 8 | 1 hour | `ema_momentum` | -0.0544 | 49 | -1.5095 | -11.6440 |
| 9 | 15 min | `rsi_reversion` | -0.2842 | 25 | -3.3490 | -6.5382 |
| 10 | 15 min | `macd_scalp` | -0.3235 | 221 | -8.5553 | -6.3684 |
| 11 | 15 min | `ema_momentum` | -0.4438 | 269 | -12.4269 | -11.4729 |
| 12 | 5 min | `rsi_reversion` | -0.5897 | 77 | -9.4296 | -6.6649 |

---

## XRP_USD (`XPP-20DEC30-CDE`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `rsi_reversion` | 0.0024 | 3/3 | 82 | 0.0457 | -0.0074 |
| 15 min | `rsi_reversion` | 0.0016 | 3/3 | 31 | 0.0233 | -0.0257 |
| 1 hour | `daviddtech_scalp` | 0.0119 | 3/3 | 7 | 0.0490 | 0.0076 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 1 hour | `daviddtech_scalp` | 0.0119 | 7 | 0.0490 | 0.0076 |
| 2 | 1 hour | `ema_scalp` | 0.0084 | 32 | 0.0821 | -0.0087 |
| 3 | 5 min | `rsi_reversion` | 0.0024 | 82 | 0.0457 | -0.0074 |
| 4 | 5 min | `ema_scalp` | 0.0022 | 374 | 0.0754 | 0.0000 |
| 5 | 15 min | `rsi_reversion` | 0.0016 | 31 | 0.0233 | -0.0257 |
| 6 | 15 min | `ema_momentum` | 0.0012 | 304 | 0.0352 | -0.0565 |
| 7 | 1 hour | `ema_momentum` | -0.0003 | 59 | -0.0110 | -0.1205 |
| 8 | 15 min | `ema_scalp` | -0.0007 | 157 | -0.0144 | -0.0302 |
| 9 | 15 min | `daviddtech_scalp` | -0.0010 | 39 | -0.0107 | -0.0484 |
| 10 | 1 hour | `rsi_reversion` | -0.0036 | 9 | -0.0438 | -0.0982 |
| 11 | 5 min | `ema_momentum` | -0.0045 | 749 | -0.2115 | -0.1772 |
| 12 | 5 min | `macd_scalp` | -0.0046 | 626 | -0.2037 | -0.2870 |

---
