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
| fill_model | `next_open` |
| intervals_swept | `[5, 15, 60]` |
| slippage_bps | `1.0` |
| venue | `coinbase_perps` |
| windows | `thirds_of_series_bar_index` |

---

## BTC_USD — `BIP-20DEC30-CDE` @ **5 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | -423.07 | -1,671.31 | -671.33 | 225.35 | 129.84 |
| mid | 667.67 | -1,108.10 | -356.62 | -1,534.63 | -1,042.24 |
| late | -81.91 | 1,545.33 | -353.98 | -719.19 | -2,964.22 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.10 | 0.47 | 0.69 | 1.12 | n/a |
| mid | 2.19 | 0.77 | 0.80 | 0.66 | 0.37 |
| late | 0.94 | 1.25 | 0.83 | 0.86 | 0.25 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 5 | 39 | 27 | 33 | 3 |
| mid | 8 | 43 | 21 | 39 | 8 |
| late | 5 | 45 | 20 | 35 | 9 |

---

## BTC_USD — `BIP-20DEC30-CDE` @ **15 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 1,301.28 | 1,683.57 | -555.61 | -1,713.11 | -1,215.25 |
| mid | 3.13 | 425.91 | 135.57 | 1,768.79 | 726.66 |
| late | 271.89 | 3,802.63 | -396.58 | -1,047.39 | -1,100.14 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 28.69 | 1.78 | 0.68 | 0.49 | 0.18 |
| mid | n/a | 1.17 | 1.13 | 2.05 | n/a |
| late | 1.25 | 2.43 | 0.59 | 0.73 | 0.46 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 3 | 21 | 10 | 17 | 5 |
| mid | 1 | 22 | 10 | 19 | 2 |
| late | 3 | 24 | 10 | 17 | 6 |

---

## BTC_USD — `BIP-20DEC30-CDE` @ **1 hour**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.00 | 431.60 | -451.69 | -258.39 | 0.00 |
| mid | 0.00 | 186.52 | 702.68 | -682.40 | -961.25 |
| late | 0.00 | -320.70 | 1,529.28 | -958.71 | -707.19 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | no trades | n/a | 0.00 | 0.21 | no trades |
| mid | no trades | 1.22 | n/a | 0.43 | 0.00 |
| late | no trades | 0.77 | n/a | 0.60 | 0.00 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0 | 2 | 1 | 2 | 0 |
| mid | 0 | 3 | 1 | 3 | 2 |
| late | 0 | 2 | 1 | 3 | 1 |

---

## SOL_USD — `SLP-20DEC30-CDE` @ **5 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.71 | -5.40 | -3.99 | 1.18 | -0.41 |
| mid | 1.86 | -3.97 | 0.60 | -3.30 | -0.52 |
| late | 2.35 | 5.21 | -0.71 | -6.55 | -5.74 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | n/a | 0.36 | 0.33 | 1.34 | 0.35 |
| mid | 39.46 | 0.58 | 1.18 | 0.59 | 0.36 |
| late | 3.88 | 1.92 | 0.82 | 0.35 | 0.10 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 3 | 43 | 27 | 32 | 4 |
| mid | 4 | 39 | 21 | 35 | 3 |
| late | 6 | 40 | 21 | 32 | 9 |

---

## SOL_USD — `SLP-20DEC30-CDE` @ **15 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.92 | 2.08 | 0.57 | -2.21 | -0.66 |
| mid | 0.29 | 0.12 | -1.58 | 2.22 | -0.33 |
| late | 0.53 | 3.84 | -0.23 | -5.23 | -5.56 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 20.00 | 1.55 | 1.27 | 0.62 | 0.40 |
| mid | n/a | 1.03 | 0.34 | 2.20 | 0.00 |
| late | 1.65 | 1.97 | 0.88 | 0.36 | 0.10 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 2 | 24 | 11 | 20 | 2 |
| mid | 1 | 22 | 10 | 19 | 1 |
| late | 2 | 21 | 12 | 19 | 8 |

---

## SOL_USD — `SLP-20DEC30-CDE` @ **1 hour**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.00 | 1.47 | -1.34 | -1.33 | -1.49 |
| mid | 0.00 | -1.42 | 1.30 | -1.52 | -0.51 |
| late | 0.00 | -1.65 | 2.08 | 0.33 | 1.02 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | no trades | n/a | 0.00 | 0.01 | 0.00 |
| mid | no trades | 0.00 | n/a | 0.18 | 0.00 |
| late | no trades | 0.60 | n/a | 1.20 | n/a |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0 | 3 | 1 | 2 | 1 |
| mid | 0 | 2 | 1 | 3 | 1 |
| late | 0 | 4 | 1 | 3 | 1 |

---

## XRP_USD — `XPP-20DEC30-CDE` @ **5 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.00 | -0.03 | -0.02 | -0.02 | -0.00 |
| mid | 0.02 | 0.02 | -0.05 | -0.00 | -0.01 |
| late | 0.01 | 0.01 | 0.02 | -0.11 | -0.02 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | no trades | 0.67 | 0.68 | 0.71 | 0.84 |
| mid | 2.95 | 1.26 | 0.26 | 0.95 | 0.67 |
| late | 2.14 | 1.09 | 1.49 | 0.35 | 0.71 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0 | 41 | 25 | 32 | 3 |
| mid | 5 | 45 | 22 | 38 | 8 |
| late | 5 | 42 | 22 | 37 | 8 |

---

## XRP_USD — `XPP-20DEC30-CDE` @ **15 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.02 | 0.03 | -0.03 | 0.01 | -0.01 |
| mid | 0.00 | -0.02 | -0.00 | -0.01 | 0.01 |
| late | 0.02 | -0.01 | 0.00 | -0.04 | -0.02 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | n/a | 1.47 | 0.49 | 1.23 | 0.28 |
| mid | 1.28 | 0.71 | 0.87 | 0.74 | n/a |
| late | n/a | 0.91 | 1.26 | 0.61 | 0.50 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 2 | 25 | 13 | 21 | 3 |
| mid | 2 | 25 | 12 | 21 | 1 |
| late | 1 | 24 | 10 | 21 | 5 |

---

## XRP_USD — `XPP-20DEC30-CDE` @ **1 hour**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0.00 | -0.02 | 0.02 | -0.03 | 0.00 |
| mid | 0.00 | -0.00 | 0.02 | -0.01 | -0.01 |
| late | 0.00 | -0.01 | 0.03 | -0.01 | -0.00 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | no trades | 0.18 | 4.20 | 0.16 | no trades |
| mid | no trades | 1.00 | n/a | 0.65 | 0.00 |
| late | no trades | 0.59 | n/a | 0.64 | 0.00 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | macd_scalp | rsi_reversion |
|--------|-----:|-----:|-----:|-----:|-----:|
| early | 0 | 3 | 2 | 3 | 0 |
| mid | 0 | 3 | 1 | 4 | 1 |
| late | 0 | 3 | 1 | 3 | 1 |

---
