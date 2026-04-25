# Profit factor only

**profit_factor** = gross profits ÷ gross losses on closed trades in that window (vector backtester).

## Strategy codes

| Code | Full name |
|------|-----------|
| DDtech | `daviddtech_scalp` |
| EMA_mom | `ema_momentum` |
| EMA_scp | `ema_scalp` |
| hull_sui | `hull_suite` |
| qqe_mod | `qqe_mod` |
| RSI_rev | `rsi_reversion` |
| squeeze_ | `squeeze_momentum` |
| supertre | `supertrend` |
| utbot_al | `utbot_alert` |

| Cell | Meaning |
|------|---------|
| — | missing row |
| no trades | 0 fills in window |
| n/a | PF not defined |
| ∞ | no losing gross (divide-by-zero side) |

## Contract

- **fee_bps_per_leg:** `4.0`
- **fee_bps_source:** `cli_override`
- **fill_model:** `next_open`
- **intervals_swept:** `[5, 15, 60]`
- **min_trades_per_window:** `None`
- **slippage_bps:** `1.0`
- **venue:** `coinbase_perps`
- **windows:** `thirds_of_series_bar_index`

---

## Layout

Grouped by **timeframe** (5 min → 15 min → 1 hour). Under each, **every configured pair** has the same window × strategy grid.

---

## 5 min

### BTC_USD (`BTC-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | no trades | 0.17 | 1.18 | 0.13 | 0.00 | n/a | 0.27 | 0.52 | 0.55 |
| mid | 0.03 | 0.07 | 0.35 | 0.20 | 0.43 | 1.46 | 0.33 | 0.00 | 0.38 |
| late | 0.96 | 0.79 | 0.12 | 0.32 | 0.57 | 0.14 | 0.06 | 0.21 | 0.21 |

### SOL_USD (`SOL-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | n/a | 0.52 | 0.36 | 0.50 | 0.55 | 0.71 | 0.26 | 0.27 | 0.59 |
| mid | 22.30 | 0.23 | 0.85 | 1.09 | 0.17 | n/a | 0.44 | 1.95 | 1.23 |
| late | 4.29 | 0.68 | 1.42 | 2.03 | 0.00 | 0.23 | 1.49 | 1.02 | 0.40 |

### XRP_USD (`XRP-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.00 | 0.25 | 0.61 | 0.18 | 0.22 | 0.00 | 0.11 | 0.31 | 0.30 |
| mid | 0.00 | 0.23 | 0.20 | 0.31 | 0.17 | n/a | 0.16 | 0.61 | 0.22 |
| late | n/a | 0.97 | 0.38 | 0.24 | 0.18 | 0.31 | 0.23 | 0.49 | 0.43 |

---

## 15 min

### BTC_USD (`BTC-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.03 | 0.07 | 0.19 | 0.13 | 0.04 | 0.21 | 0.18 | 0.06 | 0.17 |
| mid | 0.22 | 0.14 | 0.06 | 0.09 | 0.19 | 0.07 | 0.05 | 0.13 | 0.11 |
| late | 0.23 | 0.07 | 0.16 | 0.09 | 0.09 | 0.02 | 0.14 | 0.10 | 0.09 |

### SOL_USD (`SOL-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.05 | 0.17 | 0.15 | 0.14 | 0.24 | 0.01 | 0.13 | 0.01 | 0.15 |
| mid | 0.22 | 0.05 | 0.13 | 0.06 | 0.12 | 0.26 | 0.07 | 0.00 | 0.13 |
| late | 0.26 | 0.10 | 0.05 | 0.05 | 0.20 | 0.08 | 0.07 | 0.08 | 0.13 |

### XRP_USD (`XRP-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.08 | 0.06 | 0.06 | 0.08 | 0.02 | 0.02 | 0.09 | 0.00 | 0.12 |
| mid | 0.20 | 0.08 | 0.09 | 0.09 | 0.09 | 0.13 | 0.08 | 0.03 | 0.10 |
| late | 0.01 | 0.08 | 0.04 | 0.05 | 0.10 | 0.05 | 0.04 | 0.25 | 0.05 |

---
