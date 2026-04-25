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
| BTC_USD | `BTC-PERP-INTX` | **15 min** | **`supertrend`** | 108.5735 | 5 | 488.0642 | -66.2124 | low |
| SOL_USD | `SOL-PERP-INTX` | **15 min** | **`squeeze_momentum`** | 0.1369 | 15 | 1.0800 | -0.8518 | high |
| XRP_USD | `XRP-PERP-INTX` | **15 min** | **`qqe_mod`** | 0.0024 | 12 | 0.0141 | -0.0081 | moderate |

† Mean of window `score_exp_sqrt_n` where that window had trades.
‡ Backtester-internal PnL, summed over early+mid+late for the chosen triple.

---

## BTC_USD (`BTC-PERP-INTX`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 62.4936 | 2/3 | 8 | 299.6074 | 0.0000 |
| 15 min | `supertrend` | 108.5735 | 3/3 | 5 | 488.0642 | -66.2124 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 15 min | `supertrend` | 108.5735 | 5 | 488.0642 | -66.2124 |
| 2 | 5 min | `daviddtech_scalp` | 62.4936 | 8 | 299.6074 | 0.0000 |
| 3 | 5 min | `ema_scalp` | -16.9574 | 38 | -215.0191 | -1164.5535 |
| 4 | 5 min | `utbot_alert` | -27.1636 | 41 | -279.8666 | -475.2070 |
| 5 | 5 min | `rsi_reversion` | -29.4239 | 13 | -495.1009 | -932.1161 |
| 6 | 15 min | `hull_suite` | -33.5200 | 7 | -187.9496 | -200.8744 |
| 7 | 5 min | `hull_suite` | -43.4452 | 25 | -373.0061 | -233.2458 |
| 8 | 15 min | `daviddtech_scalp` | -72.1778 | 3 | -191.4766 | -160.8813 |
| 9 | 5 min | `qqe_mod` | -82.7838 | 17 | -479.4996 | -537.0544 |
| 10 | 15 min | `squeeze_momentum` | -87.3430 | 14 | -505.1609 | -684.1547 |
| 11 | 15 min | `ema_scalp` | -91.8719 | 16 | -685.1531 | -403.4844 |
| 12 | 5 min | `supertrend` | -92.0407 | 19 | -694.6693 | -558.9428 |

---

## SOL_USD (`SOL-PERP-INTX`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 0.5814 | 3/3 | 7 | 2.8801 | 0.3690 |
| 15 min | `squeeze_momentum` | 0.1369 | 3/3 | 15 | 1.0800 | -0.8518 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 5 min | `daviddtech_scalp` | 0.5814 | 7 | 2.8801 | 0.3690 |
| 2 | 5 min | `hull_suite` | 0.2370 | 24 | 1.9060 | -0.1033 |
| 3 | 15 min | `squeeze_momentum` | 0.1369 | 15 | 1.0800 | -0.8518 |
| 4 | 15 min | `supertrend` | 0.1053 | 9 | 0.0129 | -1.1806 |
| 5 | 5 min | `ema_scalp` | 0.0888 | 36 | 0.7908 | -0.8922 |
| 6 | 15 min | `hull_suite` | 0.0788 | 7 | -0.0341 | -0.3467 |
| 7 | 5 min | `supertrend` | 0.0689 | 18 | 0.6810 | -0.5610 |
| 8 | 5 min | `rsi_reversion` | 0.0424 | 8 | 0.1951 | -0.7136 |
| 9 | 5 min | `utbot_alert` | 0.0264 | 43 | 0.3844 | -0.8600 |
| 10 | 15 min | `ema_scalp` | -0.0061 | 17 | -0.0460 | -0.6307 |
| 11 | 15 min | `daviddtech_scalp` | -0.0113 | 1 | -0.0113 | -0.0113 |
| 12 | 5 min | `squeeze_momentum` | -0.0276 | 29 | -0.4420 | -1.3902 |

---

## XRP_USD (`XRP-PERP-INTX`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 0.0013 | 3/3 | 9 | 0.0076 | -0.0087 |
| 15 min | `qqe_mod` | 0.0024 | 3/3 | 12 | 0.0141 | -0.0081 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 15 min | `qqe_mod` | 0.0024 | 12 | 0.0141 | -0.0081 |
| 2 | 5 min | `daviddtech_scalp` | 0.0013 | 9 | 0.0076 | -0.0087 |
| 3 | 15 min | `rsi_reversion` | 0.0010 | 6 | 0.0043 | -0.0000 |
| 4 | 5 min | `ema_scalp` | -0.0003 | 43 | -0.0028 | -0.0060 |
| 5 | 15 min | `utbot_alert` | -0.0008 | 27 | -0.0069 | -0.0090 |
| 6 | 15 min | `daviddtech_scalp` | -0.0011 | 2 | -0.0016 | -0.0016 |
| 7 | 5 min | `ema_momentum` | -0.0013 | 78 | -0.0164 | -0.0383 |
| 8 | 5 min | `supertrend` | -0.0013 | 22 | -0.0135 | -0.0144 |
| 9 | 15 min | `ema_momentum` | -0.0024 | 37 | -0.0258 | -0.0180 |
| 10 | 15 min | `supertrend` | -0.0026 | 8 | -0.0087 | -0.0071 |
| 11 | 5 min | `utbot_alert` | -0.0029 | 54 | -0.0365 | -0.0165 |
| 12 | 5 min | `rsi_reversion` | -0.0029 | 8 | -0.0156 | -0.0117 |

---
