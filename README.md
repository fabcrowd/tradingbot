# Mitch Trading Bot

Spread-based market-making bot for Kraken Pro. Places limit orders on both sides of the bid-ask spread, captures the gap when both fill, repeats every few seconds.

## Quick Start

```bash
cd backend
pip install -r requirements.txt
cd ..
python -m backend.server.main
```

Open `http://localhost:8080` in your browser for the dashboard.

## Configuration

Edit `config.toml` for pair settings (spread, order size, max inventory, fees).
Copy `.env.example` to `.env` and add your Kraken API keys for live trading.

### Spread vs fees (read this if paper P&amp;L stays red)

In code, **`spread_bps` is measured from mid to each quote** (half-width). Buy is `mid` minus that distance, sell is `mid` plus it, so **gross edge per full round-trip ≈ `2 × spread_bps`** in basis points of price, before fees.

Paper and P&amp;L subtract **`fee_bps` on every fill** (buy leg and sell leg). At the default **25 bps per leg** (0.25% maker style), fees are **50 bps** on a completed buy+sell. You need **`spread_bps > fee_bps`** or you lose on every symmetric round-trip. Example: `fee_bps = 25` ⇒ use **`spread_bps` at least ~26–30+**, not 8–12.

The FAQ’s **XRP $2.3450** example (buy $2.3441 / sell $2.3459) is about **~77 bps** total width — much wider than “8 bps” if that was read as the same knob. Tight quotes + high `fee_bps` = **negative** simulated edge even when the *idea* (market-make the spread) is right.

Set **`fee_bps`** to your **actual Kraken maker tier** (and use **0** only where the pair is truly zero maker). High volume lowers fees; the chat note about stables and fee drops is about **your real schedule**, not the numbers in `config.toml` until you match them.

**Mapping:** `fee_bps` is **basis points per fill leg** (1 bps = 0.01%). A **0.20%** maker fee ⇒ **`fee_bps = 20`** per leg. A full buy+sell uses **two** legs ⇒ compare **`2 × spread_bps`** (gross) to **`2 × fee_bps`** (fees) when reasoning about break-even.

### Kraken fee tiers (reference — verify on your account)

Schedules can differ by **product** (spot vs stable pairs, promos, region). Use the **Fees** page in Kraken for authoritative numbers. One common **30-day USD volume** spot-style ladder (example):

| 30-day volume (USD) | Maker | Taker |
|---------------------|-------|-------|
| $0+ | 0.20% | 0.20% |
| $50,000+ | 0.16% | 0.16% |
| $100,000+ | 0.12% | 0.12% |
| $250,000+ | 0.08% | 0.08% |
| $500,000+ | 0.04% | 0.04% |
| $1,000,000+ | 0.02% | 0.02% |
| $10,000,000+ | 0.00% | 0.01% |
| $100,000,000+ | 0.00% | 0.001% |

**Logic to remember:** fees **fall as volume rises**; at the highest tiers, **maker** can hit **0%** while **taker** still pays a small fee — this bot uses **post-only limits** (`post_only` in live), so modeling **maker** `fee_bps` is what matters for limit fills. When your tier drops, **lower `fee_bps`** in `config.toml` so paper P&amp;L and spread math match reality; you can often **tighten `spread_bps`** safely after a tier upgrade.

Optional **`[bot] enabled_pairs`**: list of pair keys (e.g. `["XRP_USDT"]`) that receive **orders**. Omit it to trade every `[pairs.*]` block. The order book still streams all configured pairs for the dashboard.

### Adaptive spread (optional)

With **`adaptive_tuning = true`** in `[bot]` (or the **AUTO SPREAD** checkbox while connected), a background loop periodically adjusts **`spread_bps` per trading pair** using the **win rate on recent sell legs** (same definition as dashboard **WIN%**, but computed on the last *N* sells for that pair). If win% sits **below** `adaptive_target_win_pct` minus `adaptive_win_band_pct`, it **widens** spread by `adaptive_spread_step_bps` (up to `adaptive_spread_ceiling_bps`). If win% is **above** target plus band, it **narrows** slightly (down to at least `fee_bps + 1` and `adaptive_spread_floor_bps`). Tuning only runs while the **engine is running** and waits for at least `adaptive_min_sample_sells` in the lookback window.

This is **heuristic**, not machine learning: it can help paper/live exploration but does **not** guarantee profit or optimal parameters. Tune intervals and bands in `config.toml` under `[bot]`.

### Using your existing Kraken account

You can use the **same Kraken account you already fund and trade on**. Create **Spot** API keys on that account; the bot reads balances and places orders against that account’s holdings. A **separate** account is optional (only if you want isolation from manual trading or other bots).

When creating keys: enable **spot trading**; **do not** enable **withdrawal** on the API key. Hold enough **USDT** (and base asset where needed) for the pairs in `config.toml`.

## Modes

- **Paper** (default): Connects to real Kraken order book data but simulates fills locally. No API keys needed.
- **Live**: Places real orders on Kraken. Requires API keys in `.env`.

## Pairs

- XRP/USDT
- BTC/USDT
- ETH/USDT
- USDC/USDT (stablecoin fee schedule)
- USDG/USD — noted in the original spec as **0% maker**; `fee_bps = 0` in `config.toml`. Confirm pair availability and minimums for your region on Kraken.

**TEL/USD** has a trading fee rebate on Kraken — if you add this pair, set `fee_bps = 0` (or negative if Kraken credits you).

Optional per-pair `cycle_ms` in `config.toml` overrides `[bot] default_cycle_ms` for that pair only.

### Smart order management

Orders are **not** blindly cancelled and replaced every cycle. The engine evaluates each open order and only cancels when:

- **Price drift** — order price has moved more than **1.5× half-spread** from the current target price
- **Stale** — order has been sitting for over **120 seconds** without filling
- **Near-fill protection** — orders within **3 bps** of being filled are kept alive regardless of drift

The **cancel reason** is shown live in the dashboard status bar (CANCEL field).

### Smart defaults

Click **SMART DEFAULTS** in the config panel to auto-detect the best configuration for the selected trading pair. The system considers pair type (stablecoin vs crypto), fee schedule, current mid price, book spread, and realized volatility.

## Persistence and metrics

- Fills append to mode-specific files: `data/trades_paper.jsonl` (paper) and `data/trades_live.jsonl` (live). On restart, cumulative P&amp;L and the chart replay from the active mode file.
- **FILLS** = every logged execution (buy and sell legs). **SELLS** = completed sell legs used for **WIN%** (profitable sells / sells). Net P&amp;L uses average cost basis per pair so sell-side economics match inventory.

## Live mode from the dashboard

With API keys in `.env`, you can switch to **LIVE** in the UI; the bot creates the authenticated Kraken client on demand (`backend/server/runtime.py`).
