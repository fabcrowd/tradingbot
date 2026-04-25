# Profit factor only

**profit_factor** = gross profits ÷ gross losses on closed trades in that window (vector backtester).

## Strategy codes

| Code | Full name |
|------|-----------|
| DDtech | `daviddtech_scalp` |
| EMA_mom | `ema_momentum` |
| EMA_scp | `ema_scalp` |
| MACD | `macd_scalp` |
| RSI_rev | `rsi_reversion` |

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
- **min_trades_per_window:** `None`
- **slippage_bps:** `1.0`
- **venue:** `coinbase_perps`
- **windows:** `thirds_of_series_bar_index`

---

## Layout

Grouped by **timeframe** (5 min → 15 min → 1 hour). Under each, **every configured pair** has the same window × strategy grid.

---

## 5 min

### BTC_USD (`BIP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.81 | 0.87 | 1.21 | 0.83 | 1.21 |
| mid | 0.15 | 1.02 | 0.77 | 0.99 | 1.24 |
| late | 1.08 | 0.85 | 0.85 | 0.89 | 0.73 |

### SOL_USD (`SLP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.77 | 0.88 | 1.18 | 0.86 | 0.83 |
| mid | 0.40 | 0.72 | 0.90 | 1.03 | 0.80 |
| late | 4.90 | 0.89 | 0.92 | 0.62 | 0.45 |

### XRP_USD (`XPP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.76 | 0.93 | 1.09 | 0.99 | 1.42 |
| mid | 0.38 | 0.74 | 1.00 | 1.19 | 1.04 |
| late | 1.36 | 1.04 | 1.16 | 0.55 | 0.93 |

---

## 15 min

### BTC_USD (`BIP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 1.15 | 0.72 | 0.71 | 1.00 | 5.16 |
| mid | 0.65 | 0.80 | 0.85 | 0.96 | 4.31 |
| late | 1.52 | 1.21 | 1.04 | 0.84 | 0.71 |

### SOL_USD (`SLP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 2.08 | 0.80 | 1.02 | 1.05 | 1.97 |
| mid | 0.20 | 0.74 | 1.58 | 0.88 | 1.21 |
| late | 5.26 | 1.41 | 0.97 | 0.69 | 0.14 |

### XRP_USD (`XPP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 1.32 | 0.90 | 1.15 | 1.07 | 1.52 |
| mid | 0.58 | 1.32 | 0.88 | 0.83 | 1.04 |
| late | 1.42 | 0.91 | 0.82 | 0.47 | 0.57 |

---

## 1 hour

### BTC_USD (`BIP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 15.60 | 2.20 | 2.34 | 0.62 | 2.71 |
| mid | n/a | 1.13 | 9.41 | 0.91 | 3.68 |
| late | 1.60 | 0.52 | 0.38 | 0.47 | 2.37 |

### SOL_USD (`SLP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | n/a | 1.76 | 3.48 | 0.89 | 0.18 |
| mid | 0.00 | 1.40 | 0.86 | 1.79 | 1.55 |
| late | 0.94 | 0.45 | 3.59 | 4.28 | n/a |

### XRP_USD (`XPP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 1.12 | 1.16 | 1.29 | 0.39 | 0.20 |
| mid | n/a | 1.60 | 0.91 | 0.80 | n/a |
| late | n/a | 0.52 | 2.25 | 0.51 | 1.04 |

---
