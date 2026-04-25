# Best strategy & timeframe — per pair

**Goal:** Choose a **bar size** and **strategy** for each pair. This file ranks lab outputs toward that decision; it does **not** replace WFO/champion logic on your production schedule.

## How winners are chosen

1. For each **pair × bar size × strategy**, average **score_exp_sqrt_n** over **early / mid / late**, using only windows with **≥1 trade** and **not** flagged **[LOW_N]** (when the lab was run with `--min-trades-per-window`).
2. **Best strategy at a bar size** = highest average score among strategies that traded at that size.
3. **Best bar size for the pair** = among bar sizes where the interval winner has **≥ 10** trades (across early+mid+late), pick the **highest** average score. If none qualify, we fall back to raw best score (**low** confidence — often 1 hour with tiny n).
4. **Sum PnL** = sum of window PnLs for that triple (backtest units). **Worst window PnL** = weakest third — regime risk.

### Confidence

- **high** — trades in **all 3** windows and **≥15** trades total across them.
- **moderate** — **≥8** trades and **≥2** windows with trades.
- **low** — thin sample (typical on **1 hour** with short history).

## Contract (this run)

- **fee_bps_per_leg:** `4.0`
- **fee_bps_source:** `cli_override`
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
| BTC_USD | `BTC-PERP-INTX` | **15 min** | **`rsi_reversion`** | -126.2580 | 33 | -1273.3558 | -614.6571 | high |
| SOL_USD | `SOL-PERP-INTX` | **15 min** | **`supertrend`** | -156.5907 | 28 | -1435.8903 | -602.5857 | high |
| XRP_USD | `XRP-PERP-INTX` | **15 min** | **`daviddtech_scalp`** | -129.9899 | 36 | -1384.3308 | -680.3367 | high |

† Mean of window `score_exp_sqrt_n` where that window had trades.
‡ Backtester-internal PnL, summed over early+mid+late for the chosen triple.

---

## BTC_USD (`BTC-PERP-INTX`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | -41.9723 | 2/3 | 8 | -133.3562 | -98.7162 |
| 15 min | `rsi_reversion` | -126.2580 | 3/3 | 33 | -1273.3558 | -614.6571 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 5 min | `daviddtech_scalp` | -41.9723 | 8 | -133.3562 | -98.7162 |
| 2 | 15 min | `rsi_reversion` | -126.2580 | 33 | -1273.3558 | -614.6571 |
| 3 | 5 min | `rsi_reversion` | -135.3584 | 13 | -1198.4364 | -1312.9944 |
| 4 | 15 min | `supertrend` | -145.6684 | 29 | -1392.6028 | -702.3909 |
| 5 | 15 min | `daviddtech_scalp` | -164.6370 | 46 | -1995.0938 | -1183.3220 |
| 6 | 5 min | `hull_suite` | -198.3967 | 25 | -1715.4945 | -663.7425 |
| 7 | 5 min | `ema_scalp` | -207.8169 | 38 | -2253.2869 | -1863.0465 |
| 8 | 5 min | `qqe_mod` | -209.8889 | 17 | -1393.3426 | -750.9831 |
| 9 | 5 min | `utbot_alert` | -225.3340 | 41 | -2479.0334 | -1173.7812 |
| 10 | 15 min | `qqe_mod` | -225.3731 | 84 | -3570.9913 | -1321.9714 |
| 11 | 5 min | `supertrend` | -227.0151 | 19 | -1714.3935 | -881.9220 |
| 12 | 5 min | `squeeze_momentum` | -282.5881 | 30 | -2607.3896 | -1183.7885 |

---

## SOL_USD (`SOL-PERP-INTX`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 0.4865 | 3/3 | 7 | 2.4284 | 0.3049 |
| 15 min | `supertrend` | -156.5907 | 3/3 | 28 | -1435.8903 | -602.5857 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 5 min | `daviddtech_scalp` | 0.4865 | 7 | 2.4284 | 0.3049 |
| 2 | 5 min | `hull_suite` | 0.0558 | 24 | 0.3666 | -0.6800 |
| 3 | 5 min | `rsi_reversion` | -0.0627 | 8 | -0.3223 | -0.9089 |
| 4 | 5 min | `supertrend` | -0.0855 | 18 | -0.4705 | -0.8170 |
| 5 | 5 min | `ema_scalp` | -0.1323 | 36 | -1.5171 | -1.7238 |
| 6 | 5 min | `utbot_alert` | -0.2159 | 43 | -2.3706 | -1.7534 |
| 7 | 5 min | `squeeze_momentum` | -0.2265 | 29 | -2.3014 | -2.0946 |
| 8 | 5 min | `qqe_mod` | -0.4366 | 20 | -3.2997 | -1.8246 |
| 9 | 5 min | `ema_momentum` | -0.4424 | 61 | -5.9344 | -2.7981 |
| 10 | 15 min | `supertrend` | -156.5907 | 28 | -1435.8903 | -602.5857 |
| 11 | 15 min | `rsi_reversion` | -161.0041 | 42 | -1824.6815 | -843.2578 |
| 12 | 15 min | `daviddtech_scalp` | -163.5403 | 45 | -1960.7299 | -1103.2210 |

---

## XRP_USD (`XRP-PERP-INTX`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | -0.0005 | 3/3 | 9 | -0.0018 | -0.0129 |
| 15 min | `daviddtech_scalp` | -129.9899 | 3/3 | 36 | -1384.3308 | -680.3367 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 5 min | `daviddtech_scalp` | -0.0005 | 9 | -0.0018 | -0.0129 |
| 2 | 5 min | `supertrend` | -0.0042 | 22 | -0.0366 | -0.0250 |
| 3 | 5 min | `ema_scalp` | -0.0043 | 43 | -0.0479 | -0.0208 |
| 4 | 5 min | `rsi_reversion` | -0.0045 | 8 | -0.0240 | -0.0180 |
| 5 | 5 min | `qqe_mod` | -0.0057 | 19 | -0.0435 | -0.0251 |
| 6 | 5 min | `ema_momentum` | -0.0067 | 78 | -0.0982 | -0.0636 |
| 7 | 5 min | `hull_suite` | -0.0069 | 26 | -0.0615 | -0.0290 |
| 8 | 5 min | `utbot_alert` | -0.0073 | 54 | -0.0931 | -0.0355 |
| 9 | 5 min | `squeeze_momentum` | -0.0084 | 36 | -0.0877 | -0.0365 |
| 10 | 15 min | `daviddtech_scalp` | -129.9899 | 36 | -1384.3308 | -680.3367 |
| 11 | 15 min | `rsi_reversion` | -138.4388 | 35 | -1451.1210 | -662.9752 |
| 12 | 15 min | `supertrend` | -208.1762 | 32 | -2173.7003 | -1077.3076 |

---
