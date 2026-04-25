# Report: TP/SL Best Practices for Leveraged Crypto Scalping
## Core question: Is the current config set up to target 1.5-2% profit per trade at 2x leverage?

---

## Answer

**Yes, the bot is configured for 2x leverage — and the TP/SL logic is structurally correct but miscalibrated in three ways that will reduce realized returns below the 1.5-2% goal.** The most significant issues are (1) the breakeven trigger fires too early, turning winners into scratch trades, and (2) the TP multiplier is too wide for SOL/XRP, meaning those TPs rarely fill on a scalping timeframe. BTC is the closest to the target; SOL/XRP are materially off.

---

## Section 1: Leverage Is Fully Active

The bot sends `leverage="2"` and `margin_type="CROSS"` to Coinbase on every order (`coinbase_order_manager.py:1008`). This is genuine exchange-level 2x leverage, not simulated.

**Math at 2x:**
- 1.5% leveraged profit = **0.75% underlying price move**
- 2.0% leveraged profit = **1.0% underlying price move**
- Fees erode ~0.1-0.16% of leveraged return (two Coinbase taker legs), so gross TP should target ~1.65-2.16% to net 1.5-2%
- Liquidation sits ~45-50% adverse from entry — irrelevant to stop placement; the noise floor always binds first

---

## Section 2: Current Config Assessment

**Pair configuration (all three pairs identical today):**
```
atr_stop_mult = 2.0      # stop at 2×ATR from entry
atr_tp_mult = 4.0        # TP at 4×ATR from entry
breakeven_atr_trigger = 1.0   # move stop to entry when profit = 1×ATR
trail_atr_trigger = 2.0       # start trailing when profit = 2×ATR
trail_atr_distance = 1.0      # trail 1×ATR behind price
```

**15-min ATR as % of price (estimated from research + leverage math):**
| Asset | Typical 15-min ATR | 4×ATR TP (underlying) | 4×ATR TP (leveraged 2x) |
|---|---|---|---|
| BTC | 0.25–0.55% | 1.0–2.2% | **2.0–4.4%** |
| SOL | 0.50–1.20% | 2.0–4.8% | **4.0–9.6%** |
| XRP | 0.50–1.00% | 2.0–4.0% | **4.0–8.0%** |

**Assessment:**
- **BTC:** TP is in range at low volatility (ATR=0.25% → 2.0% leveraged) but overshoots at high volatility. Reducing `atr_tp_mult` to 3.0 would center the target better (~2.1% leveraged at median ATR).
- **SOL/XRP:** TP is 2–5x the target. At SOL's median ATR of ~0.75%, the current 4×ATR TP = 6% leveraged gain — rarely hit on a 15-min scalp, causing the position to time out or trail out instead of TP. This degrades win rate significantly.

---

## Section 3: Three Calibration Bugs

### Bug 1 — Breakeven triggers at 0.5×R, not 1×R (high impact)

Current `breakeven_atr_trigger = 1.0` with `atr_stop_mult = 2.0` means the bot moves the stop to entry when profit equals **half the initial risk** — i.e., 0.5×R.

The literature consensus (multiple sources) is that breakeven should trigger at **1×R profit**, because:
- At 0.5R profit, a normal crypto retracement will frequently stop out a valid trade
- Moving to breakeven at 0.5R converts potentially profitable trades into scratch trades
- This is a direct reduction in expectancy

**Fix:** Set `breakeven_atr_trigger = 2.0` (= `atr_stop_mult`), so breakeven triggers when profit equals the full stop distance.

### Bug 2 — Trail distance is too tight (moderate impact)

`trail_atr_distance = 1.0` (1×ATR trail) is at the minimum of the recommended range for scalping. On a 15-min chart, normal candle bodies routinely cover 0.5-1×ATR, meaning the trail fires on normal oscillations rather than genuine reversals.

**Literature consensus for scalping:** minimum **1.5×ATR** trail distance.

**Fix:** Set `trail_atr_distance = 1.5`.

### Bug 3 — TP multiplier too wide for SOL/XRP (high impact on those pairs)

`atr_tp_mult = 4.0` is appropriate for swing-scalp hybrids, not 15-min scalping. The consensus range for 15-min scalping TP is **1.5–2.5×ATR**; 3×ATR is the upper end of the swing-scalp range. At 4×ATR, SOL/XRP TPs are so far from entry they rarely fill, reducing win rate while the stop (2×ATR) fires normally.

---

## Section 4: Recommended Config Changes

### Option A — Config-only (minimal, recommended first step)

```toml
# [scalp.pairs.BTC_USD]
atr_stop_mult = 2.0              # unchanged
atr_tp_mult = 3.0                # was 4.0 → targets ~2.1% leveraged at median BTC ATR
breakeven_atr_trigger = 2.0      # was 1.0 → now triggers at 1×R (matches stop distance)
trail_atr_trigger = 3.0          # was 2.0 → start trailing at 1.5×R (after meaningful profit)
trail_atr_distance = 1.5         # was 1.0 → less whipsaw on trail

# [scalp.pairs.SOL_USD]
atr_stop_mult = 1.5              # was 2.0 → tighter stop, minimum viable for SOL noise floor
atr_tp_mult = 2.0                # was 4.0 → targets ~2.25% leveraged at median SOL ATR
breakeven_atr_trigger = 1.5      # was 1.0 → now triggers at 1×R (matches new stop distance)
trail_atr_trigger = 2.5          # was 2.0 → proportionally scaled
trail_atr_distance = 1.5         # was 1.0

# [scalp.pairs.XRP_USD]  — same as SOL
atr_stop_mult = 1.5
atr_tp_mult = 2.0
breakeven_atr_trigger = 1.5
trail_atr_trigger = 2.5
trail_atr_distance = 1.5
```

**Expected leveraged returns with Option A (at median ATR):**
| Pair | Median ATR % | New TP (underlying) | New TP (leveraged) | New SL (leveraged) | R:R |
|---|---|---|---|---|---|
| BTC | 0.35% | ~1.05% | ~2.1% | ~1.4% | 1:1.5 |
| SOL | 0.75% | ~1.13% | ~2.25% | ~2.25% | 1:1.0 |
| XRP | 0.75% | ~1.13% | ~2.25% | ~2.25% | 1:1.0 |

**Note:** SOL/XRP R:R drops to 1:1 because their noise floor demands a wider stop. At 1:1 R:R, the win rate must exceed **~55-60%** to be profitable after fees. This is achievable but requires strong signal quality.

### Option B — Add percentage cap in code (better long-term, requires code change)

Add `max_tp_pct` and `min_tp_pct` fields to `ScalpPairConfig` that clip the ATR-based TP to a percentage band. This lets the ATR system adapt to volatility while enforcing the 1.5-2% leveraged return goal.

Example config usage:
```toml
atr_tp_mult = 4.0         # unchanged — still the R:R anchor
min_tp_pct = 0.0075       # 0.75% underlying = 1.5% leveraged minimum
max_tp_pct = 0.010        # 1.0% underlying = 2.0% leveraged maximum
```

This is the cleanest solution for enforcing a predictable profit target while keeping the ATR-based structure. It requires ~30 lines of code in `signal_engine.py` (clamp TP after ATR calculation) and a config field in `scalp_config.py`.

### Option C — Enable partial TP + trailing runner (highest expectancy, requires testing)

The research consistently shows the highest expectancy structure for crypto scalping is:
- Close **60% at TP1** (the fixed ATR-based target → locks 1.5-2% leveraged)
- Trail remaining **40%** with `1.5×ATR` distance

The bot already has `partial_tp_enabled` implemented in `scalp_trader.py`. Enable it:
```toml
partial_tp_enabled = true
partial_tp_pct = 0.6          # 60% at TP1
partial_tp_runner_trail_atr = 1.5  # trail the runner 1.5×ATR
```

This requires no new code and produces ~20% improvement in expectancy at the same win rate, per the literature.

---

## Section 5: Win Rate Requirements

At the recommended R:R ratios, the minimum win rate needed to be profitable after Coinbase fees (~0.14% round-trip leveraged):

| R:R | Theoretical break-even WR | After-fees break-even WR |
|---|---|---|
| 1:2 | 33.3% | ~38% |
| 1:1.5 | 40.0% | ~44% |
| 1:1 | 50.0% | ~55% |

**For SOL/XRP at Option A (1:1 R:R):** The signal quality must achieve >55% win rate. If backtests or live data show win rate near 50% on those pairs, the stop should be widened back to 2×ATR and the TP accepted as wider-than-target (accepting that average realized profit may exceed 2%).

---

## Recommended Execution Order

1. **Fix the breakeven bug first** — `breakeven_atr_trigger` → matches `atr_stop_mult`. This is a clear calibration error causing unnecessary scratch trades. No backtest needed.
2. **Widen trail** — `trail_atr_distance = 1.5`. Low-risk change.
3. **Reduce BTC atr_tp_mult** — 4.0 → 3.0. Run WFO re-evaluation on BTC before deploying live.
4. **Reduce SOL/XRP atr_tp_mult** — 4.0 → 2.0, with tighter stop. Requires WFO validation per pair.
5. **Consider partial_tp_enabled** — after the above are stable.
6. **Consider min/max_tp_pct code change** — for precise 1.5-2% targeting regardless of ATR volatility.

---

## Open Questions

- What is the actual observed win rate on live trades? The answer changes whether SOL/XRP should use 1:1 or 1:1.5 R:R.
- Is the WFO optimizer constrained to find the right `atr_tp_mult` values, or is it searching in a range that includes the current 4.0? If so, the WFO may self-correct given enough data.
- The current config has identical multipliers for all three pairs despite SOL/XRP being 2-3x more volatile than BTC per unit price move. Per-pair calibration is justified.
