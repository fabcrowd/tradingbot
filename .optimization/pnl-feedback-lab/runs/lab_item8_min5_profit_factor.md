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

- **fee_bps_per_leg:** `0.0`
- **fee_bps_source:** `config`
- **fill_model:** `next_open`
- **intervals_swept:** `[5, 15, 60]`
- **min_trades_per_window:** `5`
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
| early | no trades | 0.40 | 3.05 | 0.76 | 0.00 | n/a | 0.67 | 1.50 | 0.99 |
| mid | 1.19 | 0.24 | 1.43 | 0.83 | 1.39 | n/a | 1.09 | 0.05 | 1.39 |
| late | 1.38 | 1.44 | 0.27 | 0.69 | 0.92 | 0.29 | 0.20 | 0.57 | 0.58 |

### SOL_USD (`SOL-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | n/a | 0.88 | 0.57 | 0.89 | 0.85 | n/a | 0.42 | 0.40 | 0.96 |
| mid | n/a | 0.44 | 1.92 | 2.19 | 0.38 | n/a | 0.96 | 3.91 | 2.58 |
| late | 5.33 | 0.96 | 2.33 | 2.74 | 0.08 | 0.32 | 2.29 | 1.43 | 0.64 |

### XRP_USD (`XRP-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.00 | 0.41 | 1.48 | 0.36 | 0.60 | 0.00 | 0.28 | 0.52 | 0.60 |
| mid | 0.00 | 0.74 | 0.64 | 0.71 | 0.54 | n/a | 0.49 | 1.78 | 0.57 |
| late | n/a | 1.70 | 0.79 | 0.37 | 0.30 | 0.49 | 0.39 | 0.85 | 0.78 |

---

## 15 min

### BTC_USD (`BTC-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.34 | 0.51 | 1.05 | 1.19 | 0.78 | 2.32 | 1.29 | 0.46 | 1.13 |
| mid | 0.91 | 0.92 | 0.38 | 0.55 | 1.22 | 1.03 | 0.41 | 1.85 | 0.53 |
| late | 3.42 | 0.52 | 0.90 | 0.61 | 0.65 | 0.60 | 0.75 | 0.50 | 0.60 |

### SOL_USD (`SOL-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.40 | 1.08 | 0.95 | 0.82 | 1.55 | 0.60 | 0.81 | 0.86 | 0.88 |
| mid | 1.79 | 0.46 | 1.08 | 0.85 | 0.82 | 1.74 | 0.99 | 0.22 | 1.12 |
| late | 1.32 | 0.82 | 0.41 | 0.34 | 1.00 | 0.55 | 0.49 | 0.85 | 0.59 |

### XRP_USD (`XRP-PERP-INTX`)

| Window | DDtech | EMA_mom | EMA_scp | hull_sui | qqe_mod | RSI_rev | squeeze_ | supertre | utbot_al |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 1.24 | 0.59 | 0.56 | 0.70 | 0.25 | 0.75 | 0.94 | 0.14 | 0.86 |
| mid | 0.91 | 0.70 | 0.51 | 0.61 | 0.61 | 1.63 | 0.60 | 0.12 | 0.70 |
| late | 1.83 | 0.59 | 0.50 | 0.47 | 0.68 | 0.79 | 0.47 | 2.21 | 0.79 |

---
