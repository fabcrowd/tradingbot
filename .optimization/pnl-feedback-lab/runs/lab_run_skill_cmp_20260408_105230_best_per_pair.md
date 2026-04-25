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
- **fill_model:** `next_open`
- **intervals_swept:** `[5, 15, 60]`
- **slippage_bps:** `1.0`
- **venue:** `coinbase_perps`
- **windows:** `thirds_of_series_bar_index`

---

## Summary — pick per pair

| Pair | Symbol | **Best bar size** | **Best strategy** | Mean score† | Trades | Sum PnL‡ | Worst window | Conf. |
|------|--------|-------------------|-------------------|-------------|--------|----------|--------------|-------|
| BTC_USD | `BIP-20DEC30-CDE` | **15 min** | **`ema_momentum`** | 311.8346 | 71 | 4588.5814 | -144.7227 | high |
| SOL_USD | `SLP-20DEC30-CDE` | **5 min** | **`daviddtech_scalp`** | 0.7667 | 13 | 4.9223 | 0.7109 | moderate |
| XRP_USD | `XPP-20DEC30-CDE` | **5 min** | **`daviddtech_scalp`** | 0.0066 | 10 | 0.0295 | 0.0000 | moderate |

† Mean of window `score_exp_sqrt_n` where that window had trades.
‡ Backtester-internal PnL, summed over early+mid+late for the chosen triple.

---

## BTC_USD (`BIP-20DEC30-CDE`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 3.4080 | 3/3 | 18 | 162.6920 | -423.0695 |
| 15 min | `ema_momentum` | 311.8346 | 3/3 | 71 | 4588.5814 | -144.7227 |
| 1 hour | `ema_scalp` | 593.4260 | 3/3 | 3 | 1780.2779 | -451.6850 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 1 hour | `ema_scalp` | 593.4260 | 3 | 1780.2779 | -451.6850 |
| 2 | 15 min | `ema_momentum` | 311.8346 | 71 | 4588.5814 | -144.7227 |
| 3 | 15 min | `daviddtech_scalp` | 270.1701 | 7 | 1401.5519 | 3.1325 |
| 4 | 1 hour | `ema_momentum` | 62.0352 | 7 | 297.4207 | -320.7038 |
| 5 | 5 min | `daviddtech_scalp` | 3.4080 | 18 | 162.6920 | -423.0695 |
| 6 | 5 min | `ema_momentum` | -68.7479 | 127 | -1234.0826 | -1671.3132 |
| 7 | 15 min | `rsi_reversion` | -78.2301 | 14 | -1041.9851 | -1215.2523 |
| 8 | 15 min | `ema_scalp` | -85.4156 | 30 | -810.3232 | -555.6075 |
| 9 | 5 min | `ema_scalp` | -95.3906 | 68 | -1381.9348 | -671.3334 |
| 10 | 5 min | `macd_scalp` | -109.3579 | 107 | -2028.4660 | -1534.6258 |
| 11 | 15 min | `macd_scalp` | -136.5189 | 57 | -1711.0469 | -1755.0512 |
| 12 | 1 hour | `macd_scalp` | -376.7350 | 8 | -1899.5006 | -958.7059 |

---

## SOL_USD (`SLP-20DEC30-CDE`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 0.7667 | 3/3 | 13 | 4.9223 | 0.7109 |
| 15 min | `daviddtech_scalp` | 0.4715 | 2/3 | 3 | 1.2118 | 0.0000 |
| 1 hour | `ema_scalp` | 0.6822 | 3/3 | 3 | 2.0466 | -1.3431 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 5 min | `daviddtech_scalp` | 0.7667 | 13 | 4.9223 | 0.7109 |
| 2 | 1 hour | `ema_scalp` | 0.6822 | 3 | 2.0466 | -1.3431 |
| 3 | 15 min | `daviddtech_scalp` | 0.4715 | 3 | 1.2118 | 0.0000 |
| 4 | 15 min | `ema_momentum` | 0.4130 | 70 | 5.9866 | -0.5554 |
| 5 | 15 min | `ema_scalp` | -0.0715 | 34 | -0.5802 | -1.7039 |
| 6 | 5 min | `ema_momentum` | -0.2115 | 122 | -4.1549 | -5.3992 |
| 7 | 5 min | `ema_scalp` | -0.2636 | 69 | -4.0944 | -3.9877 |
| 8 | 1 hour | `ema_momentum` | -0.3248 | 9 | -1.5893 | -1.6456 |
| 9 | 1 hour | `rsi_reversion` | -0.3259 | 3 | -0.9777 | -1.4909 |
| 10 | 5 min | `macd_scalp` | -0.5022 | 99 | -8.6678 | -6.5498 |
| 11 | 1 hour | `macd_scalp` | -0.5420 | 8 | -2.5170 | -1.5200 |
| 12 | 15 min | `macd_scalp` | -0.6208 | 61 | -8.5253 | -6.3432 |

---

## XRP_USD (`XPP-20DEC30-CDE`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 0.0066 | 2/3 | 10 | 0.0295 | 0.0000 |
| 15 min | `daviddtech_scalp` | 0.0058 | 2/3 | 4 | 0.0164 | 0.0000 |
| 1 hour | `ema_scalp` | 0.0197 | 3/3 | 4 | 0.0645 | 0.0184 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 1 hour | `ema_scalp` | 0.0197 | 4 | 0.0645 | 0.0184 |
| 2 | 5 min | `daviddtech_scalp` | 0.0066 | 10 | 0.0295 | 0.0000 |
| 3 | 15 min | `daviddtech_scalp` | 0.0058 | 4 | 0.0164 | 0.0000 |
| 4 | 15 min | `rsi_reversion` | 0.0018 | 12 | 0.0107 | -0.0146 |
| 5 | 15 min | `ema_momentum` | 0.0018 | 77 | 0.0273 | -0.0063 |
| 6 | 5 min | `ema_momentum` | 0.0002 | 128 | 0.0043 | -0.0284 |
| 7 | 15 min | `ema_scalp` | -0.0002 | 34 | -0.0049 | -0.0277 |
| 8 | 5 min | `rsi_reversion` | -0.0030 | 19 | -0.0252 | -0.0155 |
| 9 | 5 min | `ema_scalp` | -0.0038 | 69 | -0.0553 | -0.0527 |
| 10 | 15 min | `macd_scalp` | -0.0054 | 65 | -0.0756 | -0.0545 |
| 11 | 1 hour | `ema_momentum` | -0.0066 | 9 | -0.0345 | -0.0242 |
| 12 | 5 min | `macd_scalp` | -0.0070 | 107 | -0.1264 | -0.1056 |

---
