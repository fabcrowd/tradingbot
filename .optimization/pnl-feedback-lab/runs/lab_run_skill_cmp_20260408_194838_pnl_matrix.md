# Lab PnL grid — every pair × interval × window × strategy

## How to read

- **total_pnl** — backtester-internal PnL for that slice (not asserted live USD). Comma-separated for readability.
- **profit_factor** — gross winning trades ÷ gross losing trades (same backtest). **> 1** means wins outweighed losses; **< 1** the opposite.
- **no trades** — zero fills in that window (PF not defined). **n/a** — PF undefined (e.g. one-sided gross). **∞** — no gross loss side.
- **trades** — round-trip count for that cell.

**Units:** `total_pnl` is the vector backtester internal PnL (not asserted live USD).

## Simulation contract

| Key | Value |
|-----|-------|
| fee_bps_per_leg | `0.0` |
| fee_bps_source | `config` |
| fill_model | `next_open` |
| intervals_swept | `[5, 15, 60]` |
| min_trades_per_window | `None` |
| slippage_bps | `1.0` |
| venue | `coinbase_perps` |
| windows | `thirds_of_series_bar_index` |

---

## BTC_USD — `BIP-20DEC30-CDE` @ **5 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | -1,122.07 | -4,896.98 | 3,234.67 | -5,861.70 | 1,268.94 |
| mid | -6,644.69 | 610.00 | -3,692.62 | -255.71 | 848.62 |
| late | 480.41 | -4,570.70 | -2,208.65 | -2,653.63 | -1,937.55 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.81 | 0.87 | 1.21 | 0.83 | 1.21 |
| mid | 0.15 | 1.02 | 0.77 | 0.99 | 1.24 |
| late | 1.08 | 0.85 | 0.85 | 0.89 | 0.73 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 37 | 220 | 116 | 187 | 39 |
| mid | 30 | 214 | 121 | 193 | 26 |
| late | 38 | 226 | 123 | 190 | 36 |

---

## BTC_USD — `BIP-20DEC30-CDE` @ **15 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 673.71 | -6,833.66 | -4,314.76 | 6.26 | 3,127.84 |
| mid | -1,213.85 | -4,788.91 | -1,707.08 | -693.11 | 1,700.09 |
| late | 1,200.27 | 2,450.27 | 222.00 | -1,899.28 | -1,006.34 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 1.15 | 0.72 | 0.71 | 1.00 | 5.16 |
| mid | 0.65 | 0.80 | 0.85 | 0.96 | 4.31 |
| late | 1.52 | 1.21 | 1.04 | 0.84 | 0.71 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 15 | 87 | 48 | 76 | 12 |
| mid | 14 | 87 | 45 | 74 | 9 |
| late | 14 | 91 | 45 | 73 | 15 |

---

## BTC_USD — `BIP-20DEC30-CDE` @ **1 hour**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 1,710.57 | 5,403.06 | 3,158.37 | -3,886.50 | 2,367.74 |
| mid | 373.37 | 568.22 | 3,017.92 | -698.10 | 835.81 |
| late | 751.06 | -4,937.60 | -3,902.18 | -2,518.72 | 1,278.33 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 15.60 | 2.20 | 2.34 | 0.62 | 2.71 |
| mid | n/a | 1.13 | 9.41 | 0.91 | 3.68 |
| late | 1.60 | 0.52 | 0.38 | 0.47 | 2.37 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 3 | 16 | 8 | 14 | 4 |
| mid | 1 | 14 | 7 | 15 | 2 |
| late | 4 | 17 | 10 | 13 | 3 |

---

## SOL_USD — `SLP-20DEC30-CDE` @ **5 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | -1.66 | -6.64 | 4.90 | -6.52 | -1.68 |
| mid | -5.23 | -14.78 | -2.40 | 0.94 | -1.09 |
| late | 9.22 | -5.46 | -1.90 | -18.38 | -6.66 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.77 | 0.88 | 1.18 | 0.86 | 0.83 |
| mid | 0.40 | 0.72 | 0.90 | 1.03 | 0.80 |
| late | 4.90 | 0.89 | 0.92 | 0.62 | 0.45 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 29 | 211 | 117 | 178 | 26 |
| mid | 25 | 216 | 110 | 177 | 20 |
| late | 25 | 221 | 119 | 184 | 31 |

---

## SOL_USD — `SLP-20DEC30-CDE` @ **15 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 4.99 | -7.65 | 0.40 | 1.34 | 2.70 |
| mid | -9.54 | -11.47 | 8.26 | -3.52 | 0.49 |
| late | 3.69 | 6.70 | -0.30 | -6.37 | -6.54 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 2.08 | 0.80 | 1.02 | 1.05 | 1.97 |
| mid | 0.20 | 0.74 | 1.58 | 0.88 | 1.21 |
| late | 5.26 | 1.41 | 0.97 | 0.69 | 0.14 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 16 | 87 | 47 | 70 | 9 |
| mid | 16 | 90 | 45 | 71 | 5 |
| late | 10 | 92 | 50 | 80 | 11 |

---

## SOL_USD — `SLP-20DEC30-CDE` @ **1 hour**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.73 | 5.83 | 6.64 | -1.26 | -7.48 |
| mid | -0.33 | 4.30 | -1.05 | 5.80 | 0.51 |
| late | -0.15 | -11.64 | 10.24 | 11.90 | 2.23 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | n/a | 1.76 | 3.48 | 0.89 | 0.18 |
| mid | 0.00 | 1.40 | 0.86 | 1.79 | 1.55 |
| late | 0.94 | 0.45 | 3.59 | 4.28 | n/a |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 1 | 15 | 8 | 13 | 4 |
| mid | 1 | 16 | 7 | 13 | 2 |
| late | 3 | 18 | 11 | 16 | 3 |

---

## XRP_USD — `XPP-20DEC30-CDE` @ **5 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | -0.04 | -0.06 | 0.03 | -0.00 | 0.05 |
| mid | -0.07 | -0.18 | 0.00 | 0.09 | 0.00 |
| late | 0.03 | 0.03 | 0.04 | -0.29 | -0.01 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.76 | 0.93 | 1.09 | 0.99 | 1.42 |
| mid | 0.38 | 0.74 | 1.00 | 1.19 | 1.04 |
| late | 1.36 | 1.04 | 1.16 | 0.55 | 0.93 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 37 | 250 | 122 | 208 | 39 |
| mid | 29 | 245 | 125 | 206 | 16 |
| late | 33 | 254 | 127 | 212 | 27 |

---

## XRP_USD — `XPP-20DEC30-CDE` @ **15 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.02 | -0.06 | 0.04 | 0.03 | 0.05 |
| mid | -0.05 | 0.12 | -0.03 | -0.07 | 0.00 |
| late | 0.02 | -0.03 | -0.02 | -0.20 | -0.03 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 1.32 | 0.90 | 1.15 | 1.07 | 1.52 |
| mid | 0.58 | 1.32 | 0.88 | 0.83 | 1.04 |
| late | 1.42 | 0.91 | 0.82 | 0.47 | 0.57 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 12 | 99 | 52 | 87 | 15 |
| mid | 13 | 98 | 52 | 85 | 6 |
| late | 14 | 107 | 53 | 91 | 10 |

---

## XRP_USD — `XPP-20DEC30-CDE` @ **1 hour**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.01 | 0.03 | 0.02 | -0.16 | -0.10 |
| mid | 0.01 | 0.07 | -0.01 | -0.03 | 0.05 |
| late | 0.03 | -0.12 | 0.07 | -0.10 | 0.00 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 1.12 | 1.16 | 1.29 | 0.39 | 0.20 |
| mid | n/a | 1.60 | 0.91 | 0.80 | n/a |
| late | n/a | 0.52 | 2.25 | 0.51 | 1.04 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 4 | 20 | 10 | 16 | 4 |
| mid | 1 | 18 | 11 | 17 | 2 |
| late | 2 | 21 | 11 | 19 | 3 |

---
