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
| BTC_USD | `BTC-PERP-INTX` | **15 min** | **`rsi_reversion`** | 4.3238 | 33 | 28.3616 | -102.9577 | high |
| SOL_USD | `SOL-PERP-INTX` | **15 min** | **`qqe_mod`** | 6.4661 | 83 | 86.5406 | -114.2999 | high |
| XRP_USD | `XRP-PERP-INTX` | **15 min** | **`daviddtech_scalp`** | 7.7230 | 36 | 60.9190 | -39.2395 | high |

† Mean of window `score_exp_sqrt_n` where that window had trades.
‡ Backtester-internal PnL, summed over early+mid+late for the chosen triple.

---

## BTC_USD (`BTC-PERP-INTX`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 62.4936 | 2/3 | 8 | 299.6074 | 0.0000 |
| 15 min | `rsi_reversion` | 4.3238 | 3/3 | 33 | 28.3616 | -102.9577 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 5 min | `daviddtech_scalp` | 62.4936 | 8 | 299.6074 | 0.0000 |
| 2 | 15 min | `rsi_reversion` | 4.3238 | 33 | 28.3616 | -102.9577 |
| 3 | 15 min | `daviddtech_scalp` | -10.6881 | 46 | -179.0460 | -426.5297 |
| 4 | 15 min | `qqe_mod` | -16.5371 | 84 | -255.4984 | -260.4673 |
| 5 | 5 min | `ema_scalp` | -16.9574 | 38 | -215.0191 | -1164.5535 |
| 6 | 15 min | `supertrend` | -23.3598 | 29 | -246.8616 | -224.6723 |
| 7 | 5 min | `utbot_alert` | -27.1636 | 41 | -279.8666 | -475.2070 |
| 8 | 5 min | `rsi_reversion` | -29.4239 | 13 | -495.1009 | -932.1161 |
| 9 | 15 min | `squeeze_momentum` | -42.7321 | 123 | -786.2772 | -709.3602 |
| 10 | 5 min | `hull_suite` | -43.4452 | 25 | -373.0061 | -233.2458 |
| 11 | 15 min | `hull_suite` | -45.1401 | 110 | -805.1856 | -481.3752 |
| 12 | 15 min | `utbot_alert` | -47.5575 | 112 | -849.1579 | -540.4930 |

---

## SOL_USD (`SOL-PERP-INTX`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 0.5814 | 3/3 | 7 | 2.8801 | 0.3690 |
| 15 min | `qqe_mod` | 6.4661 | 3/3 | 83 | 86.5406 | -114.2999 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 15 min | `qqe_mod` | 6.4661 | 83 | 86.5406 | -114.2999 |
| 2 | 5 min | `daviddtech_scalp` | 0.5814 | 7 | 2.8801 | 0.3690 |
| 3 | 5 min | `hull_suite` | 0.2370 | 24 | 1.9060 | -0.1033 |
| 4 | 5 min | `ema_scalp` | 0.0888 | 36 | 0.7908 | -0.8922 |
| 5 | 5 min | `supertrend` | 0.0689 | 18 | 0.6810 | -0.5610 |
| 6 | 5 min | `rsi_reversion` | 0.0424 | 8 | 0.1951 | -0.7136 |
| 7 | 5 min | `utbot_alert` | 0.0264 | 43 | 0.3844 | -0.8600 |
| 8 | 5 min | `squeeze_momentum` | -0.0276 | 29 | -0.4420 | -1.3902 |
| 9 | 5 min | `ema_momentum` | -0.1536 | 61 | -2.0229 | -1.5743 |
| 10 | 5 min | `qqe_mod` | -0.2718 | 20 | -2.0196 | -1.4420 |
| 11 | 15 min | `daviddtech_scalp` | -8.2478 | 45 | -147.4121 | -381.3884 |
| 12 | 15 min | `rsi_reversion` | -10.1955 | 42 | -129.4787 | -194.6791 |

---

## XRP_USD (`XRP-PERP-INTX`)

### Winner at each bar size (compare timeframes)

| Bar size | Best strategy | Mean score | Windows w/ trades | Total trades | Sum PnL | Worst window |
|----------|---------------|------------|-------------------|--------------|---------|--------------|
| 5 min | `daviddtech_scalp` | 0.0013 | 3/3 | 9 | 0.0076 | -0.0087 |
| 15 min | `daviddtech_scalp` | 7.7230 | 3/3 | 36 | 60.9190 | -39.2395 |

### Full ranking (all bar sizes × strategies for this pair)

| Rank | Bar size | Strategy | Mean score | Trades | Sum PnL | Worst window |
|------|----------|----------|------------|--------|---------|--------------|
| 1 | 15 min | `daviddtech_scalp` | 7.7230 | 36 | 60.9190 | -39.2395 |
| 2 | 5 min | `daviddtech_scalp` | 0.0013 | 9 | 0.0076 | -0.0087 |
| 3 | 5 min | `ema_scalp` | -0.0003 | 43 | -0.0028 | -0.0060 |
| 4 | 5 min | `ema_momentum` | -0.0013 | 78 | -0.0164 | -0.0383 |
| 5 | 5 min | `supertrend` | -0.0013 | 22 | -0.0135 | -0.0144 |
| 6 | 5 min | `utbot_alert` | -0.0029 | 54 | -0.0365 | -0.0165 |
| 7 | 5 min | `rsi_reversion` | -0.0029 | 8 | -0.0156 | -0.0117 |
| 8 | 5 min | `qqe_mod` | -0.0030 | 19 | -0.0236 | -0.0178 |
| 9 | 5 min | `hull_suite` | -0.0038 | 26 | -0.0342 | -0.0196 |
| 10 | 5 min | `squeeze_momentum` | -0.0048 | 36 | -0.0500 | -0.0228 |
| 11 | 15 min | `rsi_reversion` | -2.4282 | 35 | -46.2619 | -62.4445 |
| 12 | 15 min | `utbot_alert` | -28.9541 | 131 | -570.1750 | -271.1307 |

---
