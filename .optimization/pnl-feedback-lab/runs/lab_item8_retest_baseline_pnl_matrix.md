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

## BTC_USD — `BTC-PERP-INTX` @ **5 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.00 | -967.85 | 773.82 | -63.51 | -537.05 | 124.09 | -288.13 | 111.12 | -6.73 |
| mid | 8.94 | -1,117.54 | 175.72 | -76.25 | 109.66 | 312.92 | 44.17 | -558.94 | 202.07 |
| late | 290.66 | 763.51 | -1,164.55 | -233.25 | -52.11 | -932.12 | -753.74 | -246.85 | -475.21 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | no trades | 0.40 | 3.05 | 0.76 | 0.00 | n/a | 0.67 | 1.50 | 0.99 |
| mid | 1.19 | 0.24 | 1.43 | 0.83 | 1.39 | n/a | 1.09 | 0.05 | 1.39 |
| late | 1.38 | 1.44 | 0.27 | 0.69 | 0.92 | 0.29 | 0.20 | 0.57 | 0.58 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0 | 18 | 12 | 8 | 4 | 1 | 11 | 6 | 13 |
| mid | 2 | 22 | 13 | 9 | 7 | 5 | 11 | 6 | 15 |
| late | 6 | 23 | 13 | 8 | 6 | 7 | 8 | 7 | 13 |

---

## BTC_USD — `BTC-PERP-INTX` @ **15 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | -426.53 | -1,084.46 | 48.95 | 108.70 | -123.86 | 126.47 | 204.94 | -224.67 | 90.41 |
| mid | -35.13 | -148.53 | -888.32 | -481.38 | 128.82 | 4.85 | -709.36 | 107.53 | -540.49 |
| late | 282.61 | -1,056.79 | -115.17 | -432.51 | -260.47 | -102.96 | -281.86 | -129.72 | -399.08 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.34 | 0.51 | 1.05 | 1.19 | 0.78 | 2.32 | 1.29 | 0.46 | 1.13 |
| mid | 0.91 | 0.92 | 0.38 | 0.55 | 1.22 | 1.03 | 0.41 | 1.85 | 0.53 |
| late | 3.42 | 0.52 | 0.90 | 0.61 | 0.65 | 0.60 | 0.75 | 0.50 | 0.60 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 19 | 77 | 49 | 38 | 28 | 10 | 44 | 12 | 40 |
| mid | 13 | 77 | 41 | 33 | 29 | 10 | 38 | 8 | 35 |
| late | 14 | 76 | 47 | 39 | 27 | 13 | 41 | 9 | 37 |

---

## SOL_USD — `SOL-PERP-INTX` @ **5 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.37 | -0.29 | -0.89 | -0.10 | -0.17 | 0.11 | -1.39 | -0.56 | -0.08 |
| mid | 0.58 | -1.57 | 0.71 | 0.58 | -0.41 | 0.80 | -0.03 | 0.64 | 1.32 |
| late | 1.93 | -0.16 | 0.97 | 1.43 | -1.44 | -0.71 | 0.98 | 0.60 | -0.86 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | n/a | 0.88 | 0.57 | 0.89 | 0.85 | n/a | 0.42 | 0.40 | 0.96 |
| mid | n/a | 0.44 | 1.92 | 2.19 | 0.38 | n/a | 0.96 | 3.91 | 2.58 |
| late | 5.33 | 0.96 | 2.33 | 2.74 | 0.08 | 0.32 | 2.29 | 1.43 | 0.64 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 1 | 19 | 13 | 9 | 8 | 2 | 11 | 4 | 13 |
| mid | 2 | 19 | 14 | 8 | 6 | 3 | 10 | 5 | 16 |
| late | 4 | 23 | 9 | 7 | 6 | 3 | 8 | 9 | 14 |

---

## SOL_USD — `SOL-PERP-INTX` @ **15 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | -381.39 | 121.01 | -57.00 | -179.99 | 200.95 | -107.85 | -215.78 | -18.96 | -121.20 |
| mid | 129.23 | -1,258.81 | 90.03 | -90.99 | -114.30 | 173.05 | -4.72 | -239.47 | 79.32 |
| late | 104.75 | -339.90 | -953.41 | -924.83 | -0.11 | -194.68 | -602.85 | -50.02 | -473.30 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.40 | 1.08 | 0.95 | 0.82 | 1.55 | 0.60 | 0.81 | 0.86 | 0.88 |
| mid | 1.79 | 0.46 | 1.08 | 0.85 | 0.82 | 1.74 | 0.99 | 0.22 | 1.12 |
| late | 1.32 | 0.82 | 0.41 | 0.34 | 1.00 | 0.55 | 0.49 | 0.85 | 0.59 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 18 | 76 | 46 | 41 | 24 | 13 | 43 | 8 | 41 |
| mid | 11 | 76 | 56 | 39 | 28 | 13 | 49 | 9 | 41 |
| late | 16 | 77 | 48 | 38 | 31 | 16 | 40 | 11 | 38 |

---

## XRP_USD — `XRP-PERP-INTX` @ **5 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | -0.01 | -0.04 | 0.01 | -0.01 | -0.00 | -0.01 | -0.02 | -0.01 | -0.02 |
| mid | -0.01 | -0.01 | -0.01 | -0.00 | -0.00 | 0.00 | -0.01 | 0.00 | -0.01 |
| late | 0.02 | 0.03 | -0.00 | -0.02 | -0.02 | -0.01 | -0.02 | -0.00 | -0.01 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.00 | 0.41 | 1.48 | 0.36 | 0.60 | 0.00 | 0.28 | 0.52 | 0.60 |
| mid | 0.00 | 0.74 | 0.64 | 0.71 | 0.54 | n/a | 0.49 | 1.78 | 0.57 |
| late | n/a | 1.70 | 0.79 | 0.37 | 0.30 | 0.49 | 0.39 | 0.85 | 0.78 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 2 | 24 | 16 | 9 | 5 | 1 | 13 | 10 | 18 |
| mid | 4 | 24 | 14 | 8 | 7 | 1 | 11 | 5 | 16 |
| late | 3 | 30 | 13 | 9 | 7 | 6 | 12 | 7 | 20 |

---

## XRP_USD — `XRP-PERP-INTX` @ **15 min**

### total_pnl

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 37.49 | -992.91 | -621.42 | -310.64 | -733.48 | -48.04 | -70.69 | -406.55 | -128.85 |
| mid | -39.24 | -621.68 | -695.27 | -428.91 | -351.17 | 64.23 | -437.01 | -556.80 | -271.13 |
| late | 62.66 | -1,081.79 | -630.38 | -580.39 | -197.96 | -62.44 | -631.70 | 73.97 | -170.19 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 1.24 | 0.59 | 0.56 | 0.70 | 0.25 | 0.75 | 0.94 | 0.14 | 0.86 |
| mid | 0.91 | 0.70 | 0.51 | 0.61 | 0.61 | 1.63 | 0.60 | 0.12 | 0.70 |
| late | 1.83 | 0.59 | 0.50 | 0.47 | 0.68 | 0.79 | 0.47 | 2.21 | 0.79 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 12 | 100 | 60 | 47 | 33 | 12 | 58 | 13 | 44 |
| mid | 16 | 99 | 54 | 42 | 34 | 8 | 49 | 13 | 41 |
| late | 8 | 99 | 56 | 44 | 28 | 15 | 49 | 6 | 46 |

---
