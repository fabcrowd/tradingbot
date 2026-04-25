# Findings: Win Rate vs R:R Tradeoffs for Crypto Scalping

**Summary:** At a 1:2 R:R ratio the mathematical break-even win rate is 33.3%, but real-world crypto scalping profitability demands 40-55%+ after accounting for fees and slippage. ATR-based TP targets are empirically superior to fixed-percentage targets in volatile crypto markets because they contract in quiet regimes and expand during trending moves. The bot's 2xATR stop / 4xATR TP configuration places the TP price level wider than needed to net 1.5-2% leveraged profit at 2x; reducing to 2.5-3xATR TP with a 1.5-2xATR stop would maintain the 1:1.5-2 R:R shape at distances where 15-min fill rates are considerably more reliable.

---

## 1. Break-Even Win Rate at 1:2 R:R

**Formula:** Break-even WR = 1 / (1 + 2) = **33.3%**

- After typical crypto futures fees (taker ~0.05-0.06% per leg at Coinbase), the effective break-even rises to approximately **36-40%**.
- Professional scalpers at 1:2 R:R typically achieve **40-50% win rates** and rely on the asymmetry for profitability.
- A 45% win rate at 1:2 R:R produces positive expected value of roughly +0.35R per trade — viable but thin.

---

## 2. ATR-Based TP vs Fixed-Percentage TP

**ATR-based is superior for crypto:**
- Adapts to current volatility: shrinks in quiet regimes, expands in trending moves.
- Prevents early exits in trends and near-zero fills in quiet markets.
- ATR+trend-indicator combination improves performance ~15% vs fixed stop methods in backtests.

**Consensus ATR multiplier ranges:**
| Regime | TP multiplier | SL multiplier |
|---|---|---|
| Scalping (tight) | 1.5–2.5x | 0.5–1.0x |
| Scalping (moderate) | 2.5–3.0x | 1.0–1.5x |
| Swing-scalp hybrid | 3.0–4.0x | 1.5–2.0x |

**Fixed-% TP niche:** Useful as a min/max clip on ATR-derived targets to prevent extreme behavior when ATR spikes.

**Verdict:** ATR-based TP is the better primary mechanism. A hybrid — ATR×multiplier, clipped to a min/max % band — captures both adaptability and outcome predictability.

---

## 3. Typical 15-min ATR as % of Price

| Asset | Typical 15-min ATR (% of price) | Note |
|---|---|---|
| BTC | 0.25–0.55% | Quieter asset; spikes during news |
| SOL | 0.50–1.20% | Significantly more volatile |
| XRP | 0.50–1.00% | Volatile, large wick events |

**BTC at 2x leverage — 4×ATR analysis:**
- Median ATR ~0.35% → 4×ATR = 1.4% underlying = **2.8% leveraged** (overshoots 2% target)
- Low-vol ATR ~0.25% → 4×ATR = 1.0% underlying = **2.0% leveraged** (hits target exactly)
- High-vol ATR ~0.55% → 4×ATR = 2.2% underlying = **4.4% leveraged** (significantly above target)

**For SOL at 2x leverage — 4×ATR analysis:**
- Median ATR ~0.75% → 4×ATR = 3.0% underlying = **6.0% leveraged** (3x the target)

**Calibration toward 1.5-2% leveraged at 2x:**
- BTC: 2.5–3.0×ATR TP → 0.6–1.0% underlying = 1.2–2.0% leveraged ✓
- SOL: 1.5–2.0×ATR TP → 0.75–1.5% underlying = 1.5–3.0% leveraged (still variable)
- XRP: 1.5–2.0×ATR TP → similar to SOL

---

## 4. Summary: ATR vs Fixed %

| Dimension | Fixed % TP | ATR-Based TP |
|---|---|---|
| Outcome predictability | High | Low (variable per regime) |
| Fill rate stability | Poor (regime-dependent) | Good |
| R:R maintenance | Breaks down with volatility | Maintains intended ratio |
| Trend capture | Exits too early | Captures more |
| Recommended use | Floor/cap on ATR target | Primary TP mechanism |

---

## Sources

- [Risk-Reward Ratio Explained — ChartMini](https://chartmini.com/blog/risk-reward-ratio-explained) — Break-even WR math, fee impact, scalper win rate norms.
- [Average True Range in Crypto — Mudrex Learn](https://mudrex.com/learn/average-true-range-crypto/) — ATR formula, multiplier recommendations, crypto-specific settings.
- [Scalping 15min ATR-based SL/TP — TradingView](https://www.tradingview.com/script/eESlTd8Q-Scalping-15min-EMA-MACD-RSI-ATR-based-SL-TP/) — Practical 15-min ATR TP/SL implementation.
- [ATR Scalping Strategy — OpoFinance Blog](https://blog.opofinance.com/en/mastering-atr-scalping/) — ATR multiplier benchmarks, performance vs fixed stop approaches.
- [Is Crypto Scalping Still Profitable in 2025? — CoinAPI](https://www.coinapi.io/blog/is-crypto-scalping-still-profitable-2025-coinapi-data-driven-insights) — Empirical data: 12% post-fee profitability rate.
- [Dynamic ATR Stop Loss and TP — FMZQuant](https://medium.com/@FMZQuant/dynamic-stop-loss-and-take-profit-strategy-based-on-atr-dual-tracking-stop-loss-bf85c1fa99b9) — ATR dual-tracking mechanics.
- [ATR Indicator Trading Strategy — Mind Math Money](https://www.mindmathmoney.com/articles/atr-indicator-trading-strategy-master-volatility-for-better-breakouts-and-risk-management) — 1.5-3×ATR TP targets.
