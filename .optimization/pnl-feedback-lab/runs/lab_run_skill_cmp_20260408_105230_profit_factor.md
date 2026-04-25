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
- **fill_model:** `next_open`
- **intervals_swept:** `[5, 15, 60]`
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
| early | 0.10 | 0.47 | 0.69 | 1.12 | n/a |
| mid | 2.19 | 0.77 | 0.80 | 0.66 | 0.37 |
| late | 0.94 | 1.25 | 0.83 | 0.86 | 0.25 |

### SOL_USD (`SLP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | n/a | 0.36 | 0.33 | 1.34 | 0.35 |
| mid | 39.46 | 0.58 | 1.18 | 0.59 | 0.36 |
| late | 3.88 | 1.92 | 0.82 | 0.35 | 0.10 |

### XRP_USD (`XPP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | no trades | 0.67 | 0.68 | 0.71 | 0.84 |
| mid | 2.95 | 1.26 | 0.26 | 0.95 | 0.67 |
| late | 2.14 | 1.09 | 1.49 | 0.35 | 0.71 |

---

## 15 min

### BTC_USD (`BIP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 28.69 | 1.77 | 0.68 | 0.48 | 0.18 |
| mid | n/a | 0.95 | 0.61 | 1.48 | n/a |
| late | 1.19 | 1.90 | 1.26 | 0.77 | 0.72 |

### SOL_USD (`SLP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 20.00 | 1.57 | 1.18 | 0.64 | 0.40 |
| mid | n/a | 0.87 | 0.33 | 0.94 | 0.00 |
| late | no trades | 2.02 | 1.41 | 0.31 | 0.10 |

### XRP_USD (`XPP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | n/a | 1.54 | 0.49 | 1.17 | 0.28 |
| mid | 1.28 | 0.89 | 1.70 | 0.55 | n/a |
| late | no trades | 1.02 | 1.99 | 0.51 | 1.45 |

---

## 1 hour

### BTC_USD (`BIP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | no trades | n/a | 0.00 | 0.21 | no trades |
| mid | no trades | 1.22 | n/a | 0.43 | 0.00 |
| late | no trades | 0.77 | n/a | 0.60 | 0.00 |

### SOL_USD (`SLP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | no trades | n/a | 0.00 | 0.01 | 0.00 |
| mid | no trades | 0.00 | n/a | 0.18 | 0.00 |
| late | no trades | 0.60 | n/a | 1.20 | n/a |

### XRP_USD (`XPP-20DEC30-CDE`)

| Window | DDtech | EMA_mom | EMA_scp | MACD | RSI_rev |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | no trades | 0.18 | 4.20 | 0.16 | no trades |
| mid | no trades | 1.00 | n/a | 0.65 | 0.00 |
| late | no trades | 0.59 | n/a | 0.64 | 0.00 |

---
