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
| fee_bps_per_leg | `4.0` |
| fee_bps_source | `cli_override` |
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
| early | 0.00 | -1,930.68 | 132.32 | -491.47 | -750.98 | 70.48 | -876.44 | -209.93 | -701.73 |
| mid | -98.72 | -2,299.70 | -522.56 | -560.28 | -266.38 | 44.08 | -547.16 | -881.92 | -603.52 |
| late | -34.64 | -475.86 | -1,863.05 | -663.74 | -375.98 | -1,312.99 | -1,183.79 | -622.54 | -1,173.78 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | no trades | 0.17 | 1.18 | 0.13 | 0.00 | n/a | 0.27 | 0.52 | 0.55 |
| mid | 0.03 | 0.07 | 0.35 | 0.20 | 0.43 | 1.46 | 0.33 | 0.00 | 0.38 |
| late | 0.96 | 0.79 | 0.12 | 0.32 | 0.57 | 0.14 | 0.06 | 0.21 | 0.21 |

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
| early | -1,183.32 | -4,150.94 | -1,902.64 | -1,404.73 | -1,239.25 | -271.90 | -1,547.31 | -702.39 | -1,502.00 |
| mid | -543.48 | -3,169.20 | -2,496.53 | -1,775.29 | -1,009.77 | -386.80 | -2,199.70 | -206.32 | -1,913.37 |
| late | -268.29 | -4,044.87 | -1,963.05 | -1,966.07 | -1,321.97 | -614.66 | -1,893.81 | -483.89 | -1,853.93 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.03 | 0.07 | 0.19 | 0.13 | 0.04 | 0.21 | 0.18 | 0.06 | 0.17 |
| mid | 0.22 | 0.14 | 0.06 | 0.09 | 0.19 | 0.07 | 0.05 | 0.13 | 0.11 |
| late | 0.23 | 0.07 | 0.16 | 0.09 | 0.09 | 0.02 | 0.14 | 0.10 | 0.09 |

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
| early | 0.30 | -1.50 | -1.72 | -0.68 | -0.68 | -0.02 | -2.09 | -0.82 | -0.91 |
| mid | 0.45 | -2.80 | -0.19 | 0.06 | -0.80 | 0.60 | -0.67 | 0.32 | 0.29 |
| late | 1.68 | -1.64 | 0.39 | 0.99 | -1.82 | -0.91 | 0.47 | 0.03 | -1.75 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | n/a | 0.52 | 0.36 | 0.50 | 0.55 | 0.71 | 0.26 | 0.27 | 0.59 |
| mid | 22.30 | 0.23 | 0.85 | 1.09 | 0.17 | n/a | 0.44 | 1.95 | 1.23 |
| late | 4.29 | 0.68 | 1.42 | 2.03 | 0.00 | 0.23 | 1.49 | 1.02 | 0.40 |

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
| early | -1,103.22 | -2,925.52 | -1,900.73 | -1,823.39 | -761.79 | -629.61 | -1,939.39 | -339.33 | -1,764.46 |
| mid | -314.72 | -4,325.42 | -2,169.59 | -1,664.78 | -1,243.89 | -351.82 | -1,981.93 | -602.59 | -1,575.16 |
| late | -542.78 | -3,454.78 | -2,894.78 | -2,461.59 | -1,254.75 | -843.26 | -2,219.70 | -493.97 | -2,011.54 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.05 | 0.17 | 0.15 | 0.14 | 0.24 | 0.01 | 0.13 | 0.01 | 0.15 |
| mid | 0.22 | 0.05 | 0.13 | 0.06 | 0.12 | 0.26 | 0.07 | 0.00 | 0.13 |
| late | 0.26 | 0.10 | 0.05 | 0.05 | 0.20 | 0.08 | 0.07 | 0.08 | 0.13 |

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
| early | -0.01 | -0.06 | -0.01 | -0.02 | -0.01 | -0.01 | -0.04 | -0.02 | -0.04 |
| mid | -0.01 | -0.03 | -0.02 | -0.01 | -0.01 | 0.00 | -0.02 | -0.00 | -0.03 |
| late | 0.02 | -0.00 | -0.02 | -0.03 | -0.03 | -0.02 | -0.03 | -0.01 | -0.03 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.00 | 0.25 | 0.61 | 0.18 | 0.22 | 0.00 | 0.11 | 0.31 | 0.30 |
| mid | 0.00 | 0.23 | 0.20 | 0.31 | 0.17 | n/a | 0.16 | 0.61 | 0.22 |
| late | n/a | 0.97 | 0.38 | 0.24 | 0.18 | 0.31 | 0.23 | 0.49 | 0.43 |

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
| early | -447.20 | -5,026.27 | -3,042.19 | -2,206.87 | -2,063.92 | -531.99 | -2,410.40 | -930.98 | -1,903.82 |
| mid | -680.34 | -4,587.99 | -2,858.12 | -2,111.49 | -1,712.60 | -256.15 | -2,399.84 | -1,077.31 | -1,913.66 |
| late | -256.79 | -5,038.37 | -2,868.82 | -2,339.49 | -1,317.15 | -662.98 | -2,590.02 | -165.41 | -2,009.01 |

### profit_factor (gross win ÷ gross loss; see *How to read* above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 0.08 | 0.06 | 0.06 | 0.08 | 0.02 | 0.02 | 0.09 | 0.00 | 0.12 |
| mid | 0.20 | 0.08 | 0.09 | 0.09 | 0.09 | 0.13 | 0.08 | 0.03 | 0.10 |
| late | 0.01 | 0.08 | 0.04 | 0.05 | 0.10 | 0.05 | 0.04 | 0.25 | 0.05 |

### trades (count per cell above)

| Window | daviddtech_scalp | ema_momentum | ema_scalp | hull_suite | qqe_mod | rsi_reversion | squeeze_momentum | supertrend | utbot_alert |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|
| early | 12 | 100 | 60 | 47 | 33 | 12 | 58 | 13 | 44 |
| mid | 16 | 99 | 54 | 42 | 34 | 8 | 49 | 13 | 41 |
| late | 8 | 99 | 56 | 44 | 28 | 15 | 49 | 6 | 46 |

---
