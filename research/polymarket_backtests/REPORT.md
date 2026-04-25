# Polymarket strategy backtests — proxy models and results

**Generated:** automated research run in-repo.  
**Artifacts:** `results.json` (full tables), Python modules in this folder.  
**Follow-up desk research (Medium, blogs, Polymarket docs, 10-test backlog):** see [`REPORT_INTERNET_DEEP_DIVE.md`](./REPORT_INTERNET_DEEP_DIVE.md).  
**Resolution-rule diff tooling design (cross-venue arb):** see [`REPORT_RESOLUTION_DIFF.md`](./REPORT_RESOLUTION_DIFF.md).

## Executive summary

1. **Live Binance 1m data could not be downloaded** from this environment (`HTTP 451`), so **all quantitative paths use synthetic correlated GBM** for BTC/ETH unless you replace `data/*.json` with real klines. Numbers are **useful for comparing models to each other**, not for claiming Polymarket profitability.

2. **Oracle-lag proxy (Model A)** — a structural simulation of “fast oracle move + stale synthetic book + Polymarket crypto taker fee” — **beats a random-entry baseline by a wide margin** on synthetic data and stays **positive on a 70/30 walk-forward window split**. That only means the **simulator’s assumptions** create an edge; **it is not evidence** the same holds on real Polymarket L2 history.

3. **Cross-asset “BTC leads ETH” proxy (Model B)** — predicting **ETH’s next H minutes from BTC’s prior H minutes** — shows **~50% accuracy**, i.e. **no edge** vs a coin flip, on synthetic data at H = 5, 15, and 60 minutes. **Same-window** BTC/ETH direction matches (~83% at ρ=0.86) are **trivial correlation**, not a tradable lead signal.

4. **Dump-hedge (Model C)** — without historical YES/NO **asks**, only a **toy Monte Carlo** was run; **not** calibrated to Polymarket.

5. **Fees:** All trade PnL uses the **documented crypto taker fee** shape `fee = C × 0.072 × p × (1−p)` from [Polymarket Fees](https://docs.polymarket.com/trading/fees).

---

## Data and limitations

| Item | Detail |
|------|--------|
| Primary intent | 30 days × 1m bars, BTCUSDT + ETHUSDT |
| Actual source | `synthetic_fallback (HTTP Error 451)` from Binance REST |
| Synthetic model | Correlated GBM, ρ≈0.86, σ≈0.00035 per minute, seeds in code |
| Alignment | Timestamps matched 1:1 for cross-asset tests |

**What would be required for a production-grade Polymarket backtest**

- Historical **CLOB best bid/ask** (or full book) for the exact **token IDs**.
- Time-aligned **resolution oracle ticks** (Chainlink / RTDS) as used at settlement.
- **Fee schedule** per market (`feesEnabled`, `fee_rate_bps`) from the API at the time of each trade.
- **Fill model** (queue position, partial fills, FOK/GTC).

---

## Model A — Oracle-lag proxy

**Idea:** Within each 15m window, if spot has moved at least `min_delta` from the window open, take the side implied by the sign of that move. **Entry price** is a synthetic function of a **stale** return (lagged `lag_min` minutes) plus half-spread. **Settlement:** window close vs open (spot “up/down”). **One trade per window max.** **Fees** applied on entry.

**Parameter sweep (BTC path):** 4 lags × 3 deltas × 3 max-entry values = 36 configs per asset. See `results.json` → `oracle_lag_proxy_btc_sweep` / `oracle_lag_proxy_eth_sweep`.

**Representative config (used for walk-forward and multi-seed):** `lag_min=1`, `min_delta=0.0007`, `max_entry=0.62`.

### Walk-forward (70% train / 30% test windows)

| Split | Trades | Win rate | PnL (USDC, proxy) |
|-------|--------|----------|-------------------|
| Train (windows 0–2015) | 1633 | 0.8108 | 3473.70 |
| Test (windows 2015–2880) | 698 | 0.7923 | 1349.66 |

Test remains strongly positive **under this simulator** (not extrapolated to Polymarket).

### Random-entry baseline (5 seeds)

Random minute in window, random YES vs NO, entry uniform in [0.52, 0.62], same fee rule.

| Metric | Value |
|--------|-------|
| Avg PnL (USDC) | **−2283.42** |
| Avg win rate | 0.4963 |

So **on this synthetic world**, the structured oracle-lag proxy is **not** explained by random timing alone (random bleeds ~fees + noise).

### Multi-seed robustness (same config, different synthetic paths)

| Seed | Trades | Win rate | PnL (USDC) |
|------|--------|----------|------------|
| 11 | 2331 | 0.8052 | 4823.36 |
| 21 | 2325 | 0.7953 | 4587.47 |
| 31 | 2313 | 0.8102 | 4895.75 |
| 41 | 2385 | 0.8126 | 5113.74 |
| 51 | 2315 | 0.7909 | 4479.85 |

Stable **high** win rate across seeds — again **only** shows the **proxy** is self-consistent under GBM + stale quoting map, **not** market truth.

### Fee entry curve (BTC, same config, varying `max_entry`)

For `max_entry` ∈ {0.50, 0.55, 0.60, 0.62, 0.65, 0.70}, **trades and PnL were identical** in this run (2331 trades): the cap was **not binding** once the first qualifying minute was found. In a **tighter** book mapping, this curve would separate.

---

## Model B — Cross-asset BTC → ETH direction

Bars: **H ∈ {5, 15, 60}** minutes.

| Model | Definition |
|-------|------------|
| M1 | Predict **same-window** ETH sign from **same-window** BTC sign |
| M2 | Predict **ETH forward** return sign from **BTC past** return (lead) |
| M3 | Predict **ETH same-window** sign from **BTC previous** window sign |

**Results (synthetic, aligned 1m)**

- **M1** ≈ **83% accuracy** at H=5/15, **83%** at H=60 — expected from **high correlation**, not from timing advantage.
- **M2** ≈ **49.7–49.9%** accuracy; **edge vs baseline ≈ −0.01 to −0.02** (worse than majority-class baseline).
- **M3** ≈ same ballpark as M2 — **no evidence** of a simple lead–lag exploitable edge at these horizons **on this generator**.

**Implication for your “pair lag” bot thesis:** A **naive** “watch BTC, trade ETH Polymarket” rule does **not** show up here. Any real edge would need **nonlinear** signals, **different horizons**, or **actual Polymarket mispricing** relative to spot, not just correlated returns.

---

## Model C — Dump-hedge / YES+NO &lt; 1

**Toy only** (`dump_hedge_toy` in `results.json`): 100k rounds of iid mid noise → fraction of rounds where `ask_Y + ask_N < 0.96` ≈ **0.24** under arbitrary Gaussian mids. This is **not** Polymarket-calibrated; **do not** interpret as 24% arb rate.

---

## Polymarket platform context (from prior desk research)

- **Dynamic taker fees** on fee-enabled **crypto** markets peak near **50¢** probability per [Polymarket Fees](https://docs.polymarket.com/trading/fees) and were covered in trade press ([Finance Magnates](https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees-to-curb-latency-arbitrage-in-short-term-crypto-markets), [Unchained](https://unchainedcrypto.com/polymarket-introduces-taker-fees-in-15-minute-markets/)) as targeting **latency-style** takers.
- Open reference architectures: [oracle-lag-sniper](https://github.com/JonathanPetersonn/oracle-lag-sniper) (Python/asyncio), [apechurch dump-hedge](https://github.com/apechurch/polymarket-arbitrage-trading-bot) (TypeScript CLOB).

---

## How to re-run

```powershell
cd research\polymarket_backtests
python run_all.py
```

If Binance is reachable, delete `data/*.json` first to force a live download. Otherwise synthetic data is written automatically.

---

## Conclusions and recommended next steps

1. **Do not** treat synthetic PnL as expected live Polymarket PnL.
2. **Do** use this repo to **regression-test** any future **real-data** pipeline (same metrics: sweep, walk-forward, random baseline).
3. For a **pair** strategy, prioritize **building a dataset** of Polymarket **ETH and BTC 15m tokens** vs **oracle**, then rerun **Model A** separately per asset and a **conditional** model (e.g. signal only when BTC move exceeds threshold) — still **separate** from proving cross-market lead–lag.
4. **Compliance:** verify **your** jurisdiction and Polymarket **ToS** before automation.

---

## File map

| File | Role |
|------|------|
| `fetch_binance_klines.py` | Binance public kline downloader |
| `synthetic_paths.py` | Correlated GBM fallback |
| `fees.py` | Polymarket crypto taker fee helper |
| `model_oracle_lag.py` | Model A + baselines + walk-forward slice |
| `model_cross_asset.py` | Model B |
| `model_dump_hedge.py` | Toy Model C |
| `run_all.py` | Orchestration → `results.json` |
| `results.json` | Machine-readable full output |
