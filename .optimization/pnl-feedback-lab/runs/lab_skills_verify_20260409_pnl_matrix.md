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
| early | 0.00 | 111.19 | -403.48 | -31.62 | -351.42 | 85.96 | 14.21 | -66.21 | -408.00 |
| mid | -160.88 | 288.40 | -326.32 | -200.87 | 164.52 | -358.93 | -684.15 | 208.54 | -636.02 |
| late | -30.60 | -1,461.47 | 44.65 | 44.55 | -402.82 | -406.23 | 164.79 | 345.74 | -120.92 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | no trades | 1.28 | 0.42 | 0.00 | 0.07 | n/a | 1.04 | 0.00 | 0.45 |
| mid | 0.29 | 1.25 | 0.46 | 0.50 | 1.51 | 0.00 | 0.00 | 2.53 | 0.35 |
| late | 0.00 | 0.17 | 1.12 | 1.17 | 0.00 | 0.17 | 2.06 | n/a | 0.66 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0 | 9 | 6 | 1 | 3 | 1 | 5 | 1 | 7 |
| mid | 2 | 10 | 6 | 4 | 4 | 2 | 4 | 2 | 7 |
| late | 1 | 9 | 4 | 2 | 3 | 3 | 5 | 2 | 6 |

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
| early | 0.00 | -0.82 | 0.57 | -0.29 | -0.14 | -0.37 | 0.70 | 0.44 | -0.16 |
| mid | 0.00 | -0.89 | 0.02 | -0.35 | 0.19 | 0.00 | 1.23 | -1.18 | -0.58 |
| late | -0.01 | -1.69 | -0.63 | 0.61 | -0.27 | -0.01 | -0.85 | 0.75 | -0.03 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | no trades | 0.34 | 3.78 | 0.32 | 0.75 | 0.00 | n/a | 4.45 | 0.87 |
| mid | no trades | 0.69 | 1.01 | 0.58 | 1.42 | no trades | 3.01 | 0.29 | 0.66 |
| late | 0.00 | 0.41 | 0.61 | n/a | 0.34 | 0.96 | 0.21 | n/a | 0.97 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0 | 9 | 6 | 3 | 3 | 1 | 6 | 2 | 8 |
| mid | 0 | 11 | 5 | 3 | 3 | 0 | 5 | 5 | 8 |
| late | 1 | 10 | 6 | 1 | 3 | 3 | 4 | 2 | 6 |

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
| early | 0.00 | -0.01 | -0.00 | -0.01 | 0.00 | 0.00 | -0.00 | 0.00 | -0.01 |
| mid | 0.00 | -0.02 | -0.00 | -0.02 | -0.01 | -0.00 | -0.02 | -0.00 | 0.01 |
| late | -0.00 | 0.01 | -0.02 | -0.01 | 0.02 | 0.00 | -0.01 | -0.01 | -0.00 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | no trades | 0.55 | 0.90 | 0.00 | 4.04 | no trades | 0.86 | 1.14 | 0.53 |
| mid | no trades | 0.44 | 0.60 | 0.00 | 0.47 | 0.00 | 0.00 | 0.74 | 1.43 |
| late | 0.48 | 1.38 | 0.29 | 0.56 | 5.65 | 1.41 | 0.53 | 0.00 | 0.82 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0 | 13 | 8 | 3 | 2 | 0 | 6 | 3 | 9 |
| mid | 0 | 12 | 7 | 4 | 5 | 1 | 5 | 4 | 9 |
| late | 2 | 12 | 6 | 4 | 5 | 5 | 7 | 1 | 9 |

---
