# Overnight Watch Log
Monitoring interval: 15 min | Started: 2026-04-13 ~03:14 UTC | Auto-expires: 2026-04-20

---

## [2026-04-13 ~03:14 UTC] — Cycle 1 (Initial Check)

### Bot Status
- **Phase**: LIVE (execution armed, entries enabled)
- **Risk halted**: NO
- **Standby**: NO
- **Daily PnL (scalp)**: $0.00 (no fills yet this session)
- **Futures account**: $1,012.46 total | $1,023.87 buying power | $6.15 daily realized PnL (pre-existing/prior fills)
- **Session uptime**: ~2.27 hours (session_sec=8165)
- **Fee tier**: Maker 6.5 bps / Taker 7.0 bps (verified via exchange, unchanged)

### Active Champions & Modes
| Pair | Mode | Source | Tuner PnL | Tuner PF | Tuner WR |
|------|------|--------|-----------|----------|----------|
| BTC_USD | `daviddtech_scalp` | wfo_champion | +$13.61 | 1.967 | 72.0% |
| SOL_USD | `utbot_alert` | wfo_champion | −$4.15 | 0.868 | 40.2% |
| XRP_USD | `daviddtech_scalp` | wfo_champion | −$3.91 | 0.816 | 42.7% |

### Open Positions
| Pair | Direction | Entry | Stop | TP | Status | Age |
|------|-----------|-------|------|-----|--------|-----|
| XRP_USD | LONG | 1.3332 | 1.3302 | 1.3372 | **PENDING** | ~80 min |
| XRP_USD | LONG | 1.3321 | 1.3291 | 1.3361 | **PENDING** | ~79 min |

> ⚠️ **Duplicate XRP pending entries** — two simultaneous long limit orders exist. Market price at time of snapshot was ~1.3272, which is **below both entries** — orders are resting, not yet filled. These duplicates were created before the `has_position()` guard fix deployed; they will resolve on the next restart or if one fills and the other gets cancelled.

### Trades This Session
- **Fills**: 0 (all pending, no executions)

### WFO Status
- **Last pass**: Champions saved for SOL_USD + XRP_USD; BTC_USD skipped (safety_gate:tp_delta=3.00)
- **BTC WFO stuck on safety gate**: BTC's best WFO candidate has a very wide TP (3.00× ATR delta), repeatedly blocked by the safety gate. Currently holding the prior `daviddtech_scalp` champion.
- **Mode history (from WFO log)**:
  - BTC: qqe_mod → **daviddtech_scalp** (changed mid-session)
  - SOL: utbot_alert (stable)
  - XRP: daviddtech_scalp (stable)

### Tuner Activity (last 5 adjustments)
```
SOL_USD  utbot_alert  atr_stop_mult 2.5→2.75   WR=41.3%  PnL=+15.13
SOL_USD  utbot_alert  atr_stop_mult 1.5→2.0 + atr_tp_mult 4.0→7.0 + max_hold_bars 20→30  WR=37.3%  PnL=-8.92
SOL_USD  utbot_alert  atr_tp_mult 7.0→7.5  WR=37.6%  PnL=-7.56
SOL_USD  utbot_alert  atr_tp_mult 7.5→8.0  WR=37.6%  PnL=-4.32
XRP_USD  daviddtech_scalp  atr_stop_mult 2.0→1.5  WR=42.7%  PnL=-3.91
```
> SOL tuner is aggressively widening TP (4.0 → 8.0 in 3 steps) looking for a better R/R, but PnL is still negative.

### Errors / Halts
- None detected

### Strategy Lookback Snapshot (24h)
All modes are negative for all 3 pairs — strongly bearish/choppy market environment.
- **Best relative performer (SOL)**: `macd_scalp` weighted_pnl=+$1.64 (only positive mode across entire board)
- **Bootstrap vs Champion misalignment**:
  - BTC: bootstrap=`macd_scalp` vs champion=`daviddtech_scalp`
  - SOL: bootstrap=`macd_scalp` vs champion=`utbot_alert`
  - XRP: bootstrap=`hull_suite` vs champion=`daviddtech_scalp`

### Fee Tier
- Last polled: in-session, success=true, no change (maker 6.5 / taker 7.0 bps)
- 30d volume: $0.00 (no fills yet counted toward volume tier)

---
**Observations:**
1. **No fills despite being live 2+ hours** — XRP pending longs are resting above current market price (1.3321–1.3332 vs market 1.3272). Market would need to rally ~40–60 pips for entries to fill. BTC and SOL show no active orders. The bot is effectively in a wait state while the market is ranging/drifting lower.
2. **SOL utbot_alert tuner is running a rapid TP expansion experiment** — widening max_hold_bars to 30 and TP to 8× ATR simultaneously is a significant parameter shift; this is concerning because the lookback shows utbot_alert at -4.6 PnL in 24h while macd_scalp is near breakeven. The two-gate demotion logic should eventually trigger a switch if macd_scalp pulls further ahead, but macd_scalp is at PF=0.88 — still not above the 1.0 gate threshold.
3. **BTC safety_gate:tp_delta=3.00 is an architectural friction point** — BTC's optimal config keeps being rejected, leaving it frozen on the last saved champion rather than advancing. Worth investigating whether the 3.00 delta threshold is calibrated correctly for BTC's ATR scale, or if this gate is being too conservative.

---

## [2026-04-13 03:19 UTC] — Cycle 2

### Bot Status
- **Phase**: LIVE — no change from Cycle 1
- **Risk halted**: NO | **Standby**: NO | **Errors**: 0 | **Halts**: 0
- **Daily PnL (scalp)**: $0.00 — still no fills
- **Futures account**: $1,012.46 total | daily_realized $6.15 (pre-session)
- **Session uptime**: ~2.34 hours
- **Session file**: last written 03:19:08 UTC (actively writing, 121 lines total)

### Active Champions & Modes
Unchanged from Cycle 1:
| Pair | Mode | Tuner PnL | Tuner PF |
|------|------|-----------|----------|
| BTC_USD | `daviddtech_scalp` | +$13.28 (-$0.33 vs C1) | 1.947 |
| SOL_USD | `utbot_alert` | −$4.14 | 0.868 |
| XRP_USD | `daviddtech_scalp` | −$3.91 | 0.816 |

> BTC tuner PnL ticked down slightly ($13.61 → $13.28) — likely a parameter perturbation that didn't improve.

### Open Positions
Same 2 XRP_USD pending longs as Cycle 1 — still not filled. Market at ~1.3272; entries at 1.3332 and 1.3321 are now ~80+ minutes old. Mark price has not moved up toward entries.

### WFO Activity Since Cycle 1
No new WFO pass completed. BTC still blocked by `safety_gate:tp_delta=3.00`.

**WFO history this session (full log):**
- 02:12 UTC — BTC saved as `qqe_mod`, SOL/XRP saved → all 3 champions 3/3
- 02:46 UTC — **BTC mode switched**: `qqe_mod` → `daviddtech_scalp` (promoted new champion)
- 03:25 UTC — BTC started failing safety gate (`tp_delta=3.00`) — holding `daviddtech_scalp` by default
- 03:10 UTC — BTC failing safety gate again (latest logged)

### Market Indicators (at last snapshot)
All pairs bearish/neutral — no long or short setups active:
| Pair | RSI | EMA Bullish | VWAP Bullish | Vol Confirmed |
|------|-----|-------------|--------------|---------------|
| BTC_USD | 49.06 | No | Yes | No |
| SOL_USD | 40.17 | No | No | No |
| XRP_USD | 44.49 | No | No | No |

Regime risk-on: **inactive**

### Tuner / WFO
No new tuner events since Cycle 1. Last tuner: XRP atr_stop_mult 2.0→1.5 at ~02:49 UTC.

### Errors / Halts
None.

### Fee Tier
Unchanged. Maker 6.5 / Taker 7.0 bps. Last polled 03:04 UTC.

---
**Observations:**
1. **BTC mode switch mid-session is notable** — at the 02:46 UTC WFO pass, the optimizer found `daviddtech_scalp` superior to `qqe_mod` on the same dataset. Both modes are now running with negative holdout expectancy per champion JSON, so neither is "good" — but daviddtech_scalp has a positive tuner sim PnL ($13.28) which suggests the live param-tuner has found better params than what WFO locked in during holdout.
2. **Two XRP longs have been pending >80 min** — they're limit orders placed above-market at the time of signal. The market has drifted lower, not higher. These will either expire via `max_hold_bars` or need a manual review on restart. If they fill into a continued downtrend, they're immediate losers.
3. **Zero fills in 2+ hours despite being in LIVE mode** — this could indicate the signal engine's `min_signals` threshold (2) and the current market (all indicators neutral/bearish) are working correctly to suppress entries. The two pending XRP longs were the exception and were likely placed during a brief bullish window early in the session.

---

## [2026-04-13 03:29 UTC] — Cycle 3

### Bot Status
- **Phase**: LIVE | **Standby**: NO | **Risk halted**: NO | **Errors**: 0 | **Halts**: 0
- **Daily PnL (scalp)**: $0.00 — still no fills
- **Futures account**: $1,012.46 | daily_realized $6.15 (unchanged)
- **Session uptime**: ~2.52 hours

### Active Champions & Modes
| Pair | Mode | Tuner PnL | Tuner PF | Tuner WR | Δ vs C2 |
|------|------|-----------|----------|----------|---------|
| BTC_USD | `daviddtech_scalp` | +$12.96 | **2.384** | 62.7% | PF ↑ (1.95→2.38), WR ↓ (72%→63%), PnL ↓ |
| SOL_USD | `utbot_alert` | **−$6.44** | 0.923 | 36.0% | PnL ↓ ($4.14→$6.44 loss), WR ↓ |
| XRP_USD | `daviddtech_scalp` | −$3.90 | 0.816 | 42.7% | Stable |

### Open Positions
Same 2 XRP_USD pending longs — **now 95+ minutes old**, still not filled. Mark ~1.3278 vs entries 1.3332 / 1.3321. Market has not moved up to fill them.

### WFO Activity Since Cycle 2 ⚠️
**New: SOL_USD now also hitting the safety gate** — at 03:19 UTC, SOL joined BTC in failing `safety_gate:tp_delta=3.00`. Last WFO pass completed 1/3 champions (XRP only).

WFO log since last cycle:
```
03:10 UTC  BTC_USD  no_champion  safety_gate:tp_delta=3.00
03:19 UTC  SOL_USD  no_champion  safety_gate:tp_delta=3.00  ← NEW
03:27 UTC  XRP_USD  champion_saved  daviddtech_scalp
03:27 UTC  Pass complete: 1/3 champions (2 skipped by safety gate)
```

XRP champion JSON also refreshed at 03:27 UTC — score degraded slightly (-1.638 → -1.889), same mode, same params.

Next WFO pass due: ~03:30 UTC (182s remaining at snapshot).

### Tuner Activity Since Cycle 2
```
03:28 UTC  XRP_USD  daviddtech_scalp  atr_stop_mult 2.0→1.5  (same change as 02:56 — tuner oscillating)
```
SOL tuner has been quiet since 02:48 UTC (last TP expansion). No new perturbations — possibly frozen or in cool-down.

### Market Indicators
All pairs still bearish/neutral — no long or short setups active. XRP volume_confirmed briefly turned True.
| Pair | RSI | EMA Bull | VWAP Bull | Vol Confirmed |
|------|-----|----------|-----------|---------------|
| BTC_USD | 45.66 | No | No | No |
| SOL_USD | 46.99 | No | No | No |
| XRP_USD | 45.03 | No | No | **Yes** |

Fee tier refreshed 03:20 UTC — unchanged (6.5/7.0 bps).

---
**Observations:**
1. **Safety gate escalation** — the `tp_delta=3.00` filter has now blocked 2/3 pairs on the latest WFO pass (BTC + SOL), up from 1/3. If this persists, the bot will run on stale champions for both pairs while the market continues to move. This filter may need recalibration or a per-symbol ATR-scaled threshold — BTC's raw TP delta will naturally be larger than SOL/XRP in absolute terms.
2. **SOL tuner PnL deteriorating** — -$4.14 → -$6.44 in one cycle. The rapid TP expansion (4→8× ATR) from Cycle 1 may have worsened the sim by allowing more losers to ride. The WFO lockout means the champion params won't refresh to correct this.
3. **XRP tuner oscillating on `atr_stop_mult`** — applied the same 2.0→1.5 change twice (02:56 and 03:28), suggesting the tuner is reverting and re-applying the same perturbation each cycle. This is a sign of a flat/noisy loss surface — the 1.5 and 2.0 values produce nearly identical sim results, so the tuner keeps switching.

---

## [2026-04-13 03:44 UTC] — Cycle 4

### Bot Status
- **Phase**: LIVE | **Standby**: NO | **Risk halted**: NO | **Errors**: 0 | **Halts**: 0
- **Daily PnL (scalp)**: $0.00 — no fills yet
- **Futures account**: $1,012.46 | daily_realized $6.15 (unchanged)
- **Session uptime**: ~2.77 hours

### Active Champions & Modes
| Pair | Mode | Tuner PnL | Tuner PF | Tuner WR | Δ vs C3 |
|------|------|-----------|----------|----------|---------|
| BTC_USD | `daviddtech_scalp` | **+$1.36** | 1.183 | 55.6% | ⚠️ PnL collapsed ($12.96→$1.36), PF halved |
| SOL_USD | `utbot_alert` | −$4.35 | 0.954 | 36.0% | Slightly improved (-$6.44→-$4.35) |
| XRP_USD | `daviddtech_scalp` | −$3.90 | 0.816 | 42.7% | Stable |

### Open Positions
Same 2 XRP_USD pending longs — **now ~110 minutes old**. Mark at 1.3261, drifting further below entries (1.3332/1.3321). Gap widening: ~54–71 pips below fill price.

### WFO Activity Since Cycle 3 ✅
**BTC cleared the safety gate** at 03:42 UTC — champion saved after failing 3 consecutive passes.

WFO log since last cycle:
```
03:32 UTC  WFO pass start
03:42 UTC  BTC_USD  champion_saved  daviddtech_scalp  ← UNBLOCKED
(SOL + XRP results pending — pass still in progress at snapshot time)
```

**BTC champion JSON significantly improved this pass:**
- Score: 1.751 → **3.669** (+109%)
- Stability: 0.228 → **0.527** (much more consistent across windows)
- Holdout mean PF: 1.348 → **1.652**
- Holdout mean WR: 57.9% → **71.2%**
- Candidates after filter: 14 → **29** (double — more of the grid is viable now)

Same mode (daviddtech_scalp) and same params — the improvement is from the rolling window advancing to include better recent data. SOL champion JSON unchanged (still at 02:45 timestamp — blocked last pass).

### Tuner Activity Since Cycle 3
No new tuner events — tuner quiet since 03:28 UTC (16+ min). BTC tuner sim took a sharp hit this cycle:
- PnL: +$12.96 → **+$1.36** — a tuner perturbation that was accepted but significantly hurt sim PnL
- PF: 2.384 → 1.183 — still above 1.0 but narrowly
- Aggressiveness shifted: slow → moderate

### Market Indicators
RSI drifting lower across all pairs (42–43). All indicators still bearish.
| Pair | RSI | EMA Bull | VWAP Bull | Vol Confirmed |
|------|-----|----------|-----------|---------------|
| BTC_USD | 42.69 | No | No | No |
| SOL_USD | 43.04 | No | No | No |
| XRP_USD | 42.46 | No | No | No |

Regime risk-on: **inactive**. Empirical market promotion: 0 active watches, 0 patterns.

Fee tier refreshed 03:36 UTC — no change (6.5/7.0 bps).

---
**Observations:**
1. **BTC WFO score doubling (1.75→3.67) with same params** signals the rolling window has shifted to capture a more favorable recent price regime. This is encouraging — BTC's daviddtech_scalp strategy is validating across more windows as data matures. The `relaxed_quarter` tier (5/21 windows passing) is still a low bar though; worth watching whether a future pass reaches the `primary` tier (7+).
2. **BTC tuner PnL collapse (+$12.96→+$1.36) is a red flag** — a single perturbation wiped 89% of the tuner's simulated edge. The tuner accepted a parameter change that looks bad in hindsight. With tuner aggressiveness shifting to "moderate" this may lead to increasingly unstable perturbations. If BTC tuner PnL goes negative next cycle, that's worth flagging for review on wake-up.
3. **XRP longs at 110 min with mark falling** — these orders are now ~54–71 pips out of the money and drifting further. If `max_hold_bars=16` at 15-min candles, the theoretical max hold is 240 min. They have ~130 min before bar-expiry kicks them. Given the bearish drift, the probability of fill before expiry is declining. These will likely be cancelled unfilled.

---

## [2026-04-13 04:01 UTC] — Cycle 5 ⚠️ XRP MODE CHANGE

### Bot Status
- **Phase**: LIVE | **Standby**: NO | **Risk halted**: NO | **Errors**: 0 | **Halts**: 0
- **Daily PnL (scalp)**: $0.00 — still no fills
- **Futures account**: $1,012.46 | daily_realized $6.15 (unchanged)
- **Session uptime**: ~3.05 hours

### Active Champions & Modes — ⚠️ XRP CHANGED
| Pair | Mode | Tuner PnL | Tuner PF | Tuner WR | Δ vs C4 |
|------|------|-----------|----------|----------|---------|
| BTC_USD | `daviddtech_scalp` | +$1.37 | 1.184 | 55.6% | Stable |
| SOL_USD | `utbot_alert` | −$5.99 | 0.930 | 36.0% | PnL worsened (-4.35→-5.99) |
| XRP_USD | **`supertrend`** ← NEW | −$22.30 | — | 29.7% | **Mode switched at 04:01 UTC** |

### XRP Champion Promotion — Details
WFO at 04:01 UTC promoted XRP from `daviddtech_scalp` → **`supertrend`**:
- Promotion tier: relaxed_quarter (6 windows) → **primary (7 windows)**
- New champion score: -1.889 → **-5.644** (worse mean PnL, but 7 windows now required)
- Stability: -0.438 → **-0.765** (less stable)
- Holdout mean PF: 0.636 → **0.815** (slightly better PF)
- Candidates after filter: 16 → **3** (very sparse — almost nothing passing in grid)
- New params: `supertrend_period=7, supertrend_factor=4.0, atr_stop_mult=1.5, atr_tp_mult=4.0, max_hold_bars=20`

> The WFO moved to `primary` tier (7 windows required), and the best primary-tier candidate for XRP is `supertrend` — even though it's worse than the prior `daviddtech_scalp` at the `relaxed_quarter` tier. This is a tier-selection tension: stricter tier = fewer candidates, and the best of those happens to be worse than the previous winner under looser criteria.

**First tuner cycle on new XRP champion (04:02 UTC) — immediate red flag:**
```
XRP_USD  supertrend  wr=29.7%  pnl=-$22.30
changes: atr_stop_mult 1.5→1.0, atr_tp_mult 4.0→5.0, max_hold_bars 20→40, supertrend_period 10→20
```
First perturbation produced -$22.30 in sim — catastrophic. The tuner is aggressively changing 4 params simultaneously on a brand-new champion, which is unusual.

### Open Positions
Still 2 XRP_USD pending longs (strategy_mode=`daviddtech_scalp` — placed before mode switch):
- Entry 1.3332 | Mark 1.3257 | Age **~125 min** | Gap: 75 pips below entry
- Entry 1.3321 | Mark 1.3257 | Age **~124 min** | Gap: 64 pips below entry

With `max_hold_bars=16` at 15min candles (max 240 min), these have ~115 min remaining before bar-expiry. Mark continues drifting lower. Very unlikely to fill.

### WFO Activity Since Cycle 4
```
03:51 UTC  SOL_USD  no_champion  safety_gate:tp_delta=3.00  (4th consecutive block)
04:01 UTC  XRP_USD  champion_saved  changed=True  ← mode switch to supertrend
(BTC passed at 03:42, in current pass BTC/SOL still pending)
```
SOL blocked 4 times now. SOL champion JSON still frozen at 02:45 UTC.

### Market Indicators
Slight RSI recovery (42-45 range) but still no setups.
| Pair | RSI | EMA Bull | VWAP Bull | Vol Confirmed |
|------|-----|----------|-----------|---------------|
| BTC_USD | 44.90 | No | No | No |
| SOL_USD | 45.55 | No | No | No |
| XRP_USD | 43.49 | No | No | No |

Fee tier refreshed 03:52 UTC — no change (6.5/7.0 bps).

---
**Observations:**
1. **XRP supertrend promotion looks like a tier-gate artifact** — the WFO moved from `relaxed_quarter` (5–6 windows) to `primary` (7 windows), forcing it to find the best candidate with 7+ passing windows. That winner (`supertrend`, score=-5.644) is objectively worse than the displaced champion (`daviddtech_scalp`, score=-1.889). This suggests the tier-selection logic should compare cross-tier champions before committing to a worse-scoring one, or at minimum require the new champion's score to beat the existing champion's score.
2. **Tuner's 4-param simultaneous perturbation on a new champion is aggressive** — standard practice would be to evaluate the champion at its WFO params before perturbing. A -$22.30 first read on `supertrend` may partly reflect an overconfident perturbation (halving the stop mult from 1.5→1.0, doubling max_hold_bars 20→40, changing period 10→20 all at once). Worth reviewing tuner behavior on champion transitions.
3. **SOL's 4-consecutive safety-gate blocks** leave it running a 5-hour-stale champion. If the market regime has shifted during that window, the bot is flying blind on SOL. Consider a fallback mechanism: if N consecutive WFO passes fail the safety gate, relax the gate threshold by a configurable step rather than holding indefinitely.

---

## [2026-04-13 04:15 UTC] — Cycle 6 ⚠️ BTC MODE CHANGE + SYSTEMIC WFO CONCERN

### Bot Status
- **Phase**: LIVE | **Standby**: NO | **Risk halted**: NO | **Errors**: 0 | **Halts**: 0
- **Daily PnL (scalp)**: $0.00 — still no fills (3.28 hours live)
- **Futures account**: $1,012.46 | daily_realized $6.15 (unchanged)

### Active Champions & Modes — ⚠️ BTC CHANGED
| Pair | Mode | Tuner PnL | Tuner PF | Tuner WR | Δ vs C5 |
|------|------|-----------|----------|----------|---------|
| BTC_USD | **`qqe_mod`** ← NEW | **−$22.22** | 0.727 | 34.2% | Mode switched 04:15, tuner in crisis |
| SOL_USD | `utbot_alert` | −$5.87 | 0.931 | 36.0% | Slight improvement |
| XRP_USD | `supertrend` | −$0.70 | **1.080** | 30.2% | Recovering — PF just crossed 1.0 |

### BTC Champion Change at 04:15 UTC — Details
WFO promoted BTC from `daviddtech_scalp` → **`qqe_mod`**:
- Score: +3.669 → **−6.140** (moved to primary tier, catastrophically worse)
- Stability: +0.527 → **−1.329** (most unstable reading of any champion so far)
- Holdout mean PF: 1.652 → **0.429**
- Windows passed: 5 → **7** (relaxed_quarter → primary tier)
- Candidates after filter: 29 → **1** (only 1 valid candidate in entire 2,886-param grid)
- Note: single holdout window shows WR=70%, PF=1.13 — misleadingly good; mean across 7 windows is −$6.14

**BTC tuner spiral** (4 adjustments in one cycle, aggressiveness=`aggressive`):
```
Started at pnl=−$82.06
atr_stop_mult 1.5→2.5   → pnl=−$65.86
atr_tp_mult 1.5→4.5     → pnl=−$35.01
atr_period 13→7         → pnl=−$24.22
qqe_factor 2.0→3.0      → pnl=−$22.22  ← current
```
Tuner has burned through 4 aggressive parameter swings and still can't get qqe_mod into positive territory.

### XRP Tuner Recovery (Positive Development)
Through systematic TP expansion over 5 tuner cycles, XRP supertrend is recovering:
```
04:02  pnl=−$22.30  (initial, 4 params changed at once)
04:03  pnl=−$13.80
04:04  pnl=−$7.43
04:05  pnl=−$1.23
04:06  pnl=−$0.70   ← current, PF=1.08
```
Still loss-making in sim but PF just crossed 1.0. TP at 7.0× ATR with WR at 30%.

### Open Positions
Same 2 XRP_USD pending longs (`daviddtech_scalp` mode from entry):
- Age: **~140 min** | Mark: 1.3256 | Entries: 1.3332/1.3321 | Gap: 65–76 pips
- Max hold (16 bars × 15min = 240 min) → **~100 min remaining**. Almost certainly unfilled.

### WFO Activity Since Cycle 5
```
04:01 UTC  XRP_USD  champion_saved  changed=True → supertrend (logged in C5)
04:06 UTC  WFO pass started (new pass)
04:15 UTC  BTC_USD  champion_saved  changed=True → qqe_mod  ← NEW
(SOL + XRP pending in current pass)
```
SOL blocked for 5th consecutive pass (safety_gate:tp_delta=3.00). Champion frozen ~95 min.

### Systemic WFO Pattern — ⚠️ FLAG FOR MORNING REVIEW
All 3 pairs are now running primary-tier champions with negative mean scores:
| Pair | Champion Mode | WFO Score | Stability | Candidates |
|------|--------------|-----------|-----------|------------|
| BTC_USD | `qqe_mod` | −6.14 | −1.329 | 1/2886 |
| SOL_USD | `utbot_alert` | −3.24 | −0.614 | 9/2886 |
| XRP_USD | `supertrend` | −5.64 | −0.765 | 3/2886 |

The WFO grid is finding almost no viable candidates. This isn't a strategy failure — it's a market regime problem. Nearly every mode in the lookback is negative, and the WFO holdout window is reflecting a sustained bearish/choppy period where no strategy is working well.

### Market Indicators
Bearish/neutral. XRP RSI lowest (41.4). No setups firing across any pair.
| Pair | RSI | EMA Bull | VWAP Bull |
|------|-----|----------|-----------|
| BTC_USD | 44.51 | No | No |
| SOL_USD | 45.92 | No | No |
| XRP_USD | 41.42 | No | No |

Fee tier refreshed 04:08 UTC — no change.

---
**Observations:**
1. **The WFO is degrading champions as it tightens tier requirements** — each time the rolling window advances and the primary tier (7 windows) becomes mandatory, the new "winner" is the least-bad primary-tier candidate, which is consistently worse than the displaced relaxed-quarter champion. This isn't the intended behavior. A straightforward fix: require `new_champion.score > current_champion.score` before replacing, regardless of tier. Don't demote a 3.67-score champion with a -6.14-score one.
2. **BTC tuner in aggressive mode with qqe_mod at −$22 after 4 param swings** — the tuner is burning capital (sim) trying to rescue a bad champion. Since the bot has no live fills yet, this is only a sim concern — but if the bot starts taking live entries under qqe_mod while the tuner is still in this recovery spiral, the live entries will use params that are still significantly loss-making.
3. **XRP supertrend TP expansion pattern is informative** — the tuner discovered that TP needs to be 7× ATR (vs the WFO-optimized 4×) to get to breakeven. This suggests the WFO holdout window is systematically underestimating the TP needed for this market phase. A potential enhancement: feed the tuner's discovered optimal params back into the next WFO search grid as warm-start seeds.

---

## [2026-04-13 10:28 UTC] — Cycle 7 (CATCHUP — covers 04:15→10:28 UTC)

> Note: large time gap between C6 and C7 — this entry covers ~6 hours of session activity.

### Bot Status
- **Phase**: LIVE | **Standby**: NO | **Risk halted**: NO | **Errors**: 0 | **Halts**: 0
- **Daily PnL (scalp)**: $0.00 — **ZERO fills entire session (9.5+ hours)**
- **Futures account**: $1,012.46 | daily_realized $6.15 (unchanged all night)
- **Session uptime**: ~9.5 hours

### Active Modes (10:28 UTC)
| Pair | Mode | Tuner PnL | Tuner PF | Tuner WR | Status |
|------|------|-----------|----------|----------|--------|
| BTC_USD | `qqe_mod` | −$21.12 | 0.750 | 34.4% | Stuck — safety_gate:stop_delta=1.50 blocking new champ |
| SOL_USD | `supertrend` | **+$2.75** | >1.0 | 31.8% | Changed 09:58 — tuner positive ✓ |
| XRP_USD | `daviddtech_scalp` | ~±$0.95 | ~1.0 | 52.4% | Oscillating near breakeven |

### Champion Churn Overnight — Full Summary
**18 champion changes across all 3 pairs since session start (01:24–09:58 UTC):**
```
01:24  BTC  → new (session init)     04:48  BTC  → changed (4th BTC change!)
01:35  SOL  → new (session init)     05:07  XRP  → changed
01:46  XRP  → new (session init)     05:21  BTC  → changed
01:56  BTC  → changed                05:30  SOL  → changed (first SOL change)
04:01  XRP  → supertrend             05:40  XRP  → changed
04:15  BTC  → qqe_mod                06:47  XRP  → changed
04:48  BTC  → changed (3rd in 33min) 07:35  BTC  → changed
05:07  XRP  → changed                07:53  XRP  → changed
                                     08:17  SOL  → changed
                                     08:27  XRP  → changed
                                     09:00  XRP  → daviddtech_scalp (returned)
                                     09:58  SOL  → supertrend
```
BTC changed **6 times**, XRP changed **7 times**, SOL changed **3 times**. This is extreme champion thrashing.

### Safety Gate Evolution
The gate type blocking BTC/SOL has shifted:
```
Early session:   safety_gate:tp_delta=3.00
Mid session:     safety_gate:tp_delta=5.00, tp_delta=6.00  (SOL requiring wider TP)
After ~09:00:    safety_gate:stop_delta=1.50 (BTC)
                 safety_gate:stop_delta=1.25 (SOL)
```
The gate shifted from "TP too wide" to "stop too tight" — meaning the WFO's best candidates now have very tight stops (1.25–1.50× ATR) that fail the minimum delta safety check. BTC has been blocked by stop_delta=1.50 since 09:15 with no successful pass.

### ⚠️ CRITICAL: XRP Pending Longs — STUCK 8.5+ Hours
```
Entry 1.3332  placed ~01:54 UTC  age=30,831s (~8.5 hrs)  mark=1.3263  status=PENDING
Entry 1.3321  placed ~01:54 UTC  age=30,759s (~8.5 hrs)  mark=1.3263  status=PENDING
```
**max_hold_bars=16 × 15min = 240min (4h) — these should have expired at ~05:54 UTC. They have not.**
These stale pending longs have been blocking all new XRP entries via the `has_position()` guard for ~4.5 extra hours beyond their theoretical max. This is likely responsible for XRP generating zero new entries all night.

### Strategy Lookback — Positive Shift (vs all-negative in C1-C6)
The 24h lookback has finally turned positive for all 3 pairs:
| Pair | Best Mode | Expectancy | PF | WR | Trades |
|------|-----------|-----------|-----|-----|--------|
| BTC_USD | `ema_scalp` | +0.204 | 1.192 | 66.7% | 12 |
| SOL_USD | `macd_scalp` | +0.553 | 1.889 | 66.7% | 9 |
| XRP_USD | `daviddtech_scalp` | +0.956 | 999 | 100% | 3 |

Market regime appears to have shifted positive in the last few hours. None of these best-in-lookback modes match the current WFO champions (qqe_mod / supertrend / daviddtech_scalp for XRP matches).

### SOL Tuner Positive Reads (Best Overnight)
```
08:19  SOL utbot_alert  pnl=+$6.44  (best single read of the night for any pair)
08:20  SOL utbot_alert  pnl=+$12.25 (peak sim performance overnight!)
10:33  SOL supertrend   pnl=+$2.75  (new champion tuner already positive)
```

### Errors / Halts
None. Feed connected continuously. No daily loss breach.

Fee tier polled 10:25 UTC — unchanged (6.5/7.0 bps).

---
**Observations:**
1. **Zero fills in 9.5 hours is a compounding problem** — the two XRP pending longs placed at session start have been stuck well past their max_hold_bars expiry. The `has_position()` guard (designed to prevent duplicates) is now working against the bot by treating these stale orders as "active." The pending order expiry logic needs to handle UNFILLED limit orders that exceed max_hold_bars × bar_duration in wall-clock time, not just bar-close count. These orders need to be cancelled on restart.
2. **Champion churn (18 changes, 6 for BTC alone) suggests the rolling WFO window is in a highly unstable region** — each 5-min pass is selecting a different mode as the rolling window advances through patchy historical data. No mode is stable enough to win consistently across windows. This instability combined with the safety gate oscillating between tp_delta and stop_delta types suggests the parameter space has very few robust candidates right now. A minimum tenure requirement (e.g., new champion must beat current champion by >X% score before replacing) would dampen this churn.
3. **SOL supertrend showing +$2.75 in tuner and macd_scalp at PF=1.889 in lookback** — these are the best positive signals of the entire night. If the two-gate demotion logic evaluates next WFO cycle with supertrend underperforming and macd_scalp clearly outperforming, a demotion+switch to macd_scalp should trigger. Watch for this on the next cycle.

---

## [2026-04-13 ~10:43 UTC] — Cycle 8

*Pending*

---
## Cycle — 2026-04-13 11:37 UTC
**Session:** session_20260413_005835.jsonl (same session, ~10h39m running)

### Bot State
- operator_standby: **False** (live)
- startup_phase: **live**
- risk_halted: None
- daily_pnl: None (session PnL tracking not yet populated — no closed trades)
- daily_loss_breached: None
- open_positions: 0
- regime_risk_on: active=False (live trigger enabled; calm market, not firing)

### Active Champions
| Pair | Mode | Score | Tier | Tuner PnL | Tuner Aggr |
|------|------|-------|------|-----------|------------|
| BTC_USD | qqe_mod | -6.343 | primary | -37.46 | aggressive |
| SOL_USD | supertrend | +1.095 | primary | +1.55 | moderate |
| XRP_USD | **rsi_reversion** (NEW) | **+2.325** | relaxed_quarter | +1.29 | moderate |

**Notable champion changes since last cycle:**
- XRP switched to  (score +2.325, 5/21 windows, relaxed_quarter tier). First time this mode has appeared. Tuner backing it with +.29 simulated PnL, 59.7% win rate, 17 trades.
- SOL improved: supertrend score +0.563 -> +1.095. Tuner PnL dropped from +3.60 to +1.55 (rebase on champion transition) but still positive and moderate aggression.
- BTC still stuck on qqe_mod -6.343. Most recent WFO pass at 11:27 UTC found a candidate (best_mean_score=+1.326) but was **blocked by safety_gate:stop_delta=1.50** — the old max_param_delta_stop=1.0 threshold. This exact case is unblocked by the fix applied this session (new threshold: 2.0). Will resolve on next WFO pass after restart.

### Trades
- No trades opened or closed this cycle.
- No entry_placed, entry_ttl_cancel, or fill events in the session tail.
- Bot is in live mode with no positions and no pending orders. Entry gates appear clear.

### WFO Activity (last pass: 11:27 UTC)
- BTC: no_champion — safety_gate:stop_delta=1.50 (8 candidates passed stability filter, best score +1.326 — will promote after restart with new delta threshold=2.0)
- SOL: no WFO result logged in recent tail (last champion write was 11:05 UTC per earlier analysis)
- XRP: champion_saved at 10:41 UTC (rsi_reversion, +2.325 score)

### Errors / Halts
- None. No error events, no risk halts, no feed disconnects.

### Fee Tier
- Last refresh: 11:29 UTC (auto_poll, success)
- maker=6.5 bps, taker=7.0 bps — unchanged
- 30d volume: /usr/bin/bash.00 (exchange not returning volume data — no fills yet this session)

---
**Observations:**
1. XRP rsi_reversion is the most encouraging development of the session. Score +2.325 with 65% win rate across 5 windows and 17 simulated trades is the first clearly positive signal on any pair since session start 10+ hours ago. The mode targets RSI oversold/overbought reversals — well-suited to the ranging overnight market that was choppy for trend modes.
2. BTC is a restartpending fix: the WFO found a +1.326 score champion at 11:27 but the old stop_delta gate (1.0) blocked it. After the bot is restarted with the new config (max_param_delta_stop=2.0, max_param_delta_tp=3.0), BTC should immediately promote a better champion on the first WFO pass. The current qqe_mod -6.343 is likely generating poor signals.
3. No entries despite being live for 10+ hours and having no position blocks. This points to signal generation being the remaining gate — min_signals was just lowered to 1, but the bot has not been restarted to pick up the new config yet. A restart is the primary action needed to activate all 8 fixes applied this session.

---
## Cycle — 2026-04-13 12:19 UTC
**Session:** session_20260413_114650.jsonl (NEW — restarted 11:46 UTC, ~33min running)

### Bot State
- startup_phase: **standby** — waiting for manual "Go Live"
- operator_standby: **True** (`require_manual_go_live = true`)
- risk_halted: None
- daily_pnl: $0.00 (fresh session, no closed trades)
- open_positions: 0
- exchange_open_orders: [] (confirmed clean, no phantom pending)
- regime_risk_on: **enabled=True, active=False** (NEW CONFIG CONFIRMED ACTIVE — calm market, not triggered yet)

### Warmup Status
- bars_collected: BTC_USD=14,459 / SOL_USD=14,235 / XRP_USD=14,141 (all >>100 minimum)
- progress_pct: 100.0% — bars fully loaded from cached bar store
- wfo_triggered: False — WFO validation step not yet started
- champion_found: False — pending WFO first-pass validation
- active_modes: all 3 pairs on "ema_momentum" (config fallback, pre-WFO)
- **BLOCKED: operator must click "Begin Warmup" then "Go Live" at http://localhost:8080**

### Active Champions (on disk — not yet active in this session)
| Pair | Mode | Score | Tier | Tuner PnL | Tuner Aggr |
|------|------|-------|------|-----------|------------|
| BTC_USD (BIP) | qqe_mod | -6.343 | primary (9/21 windows) | -37.45 | aggressive |
| SOL_USD (SLP) | supertrend | +1.220 | primary (7/21 windows) | +1.55 | moderate |
| XRP_USD (XPP) | rsi_reversion | +2.325 | relaxed_quarter (5/21 windows) | +1.29 | moderate |

### Startup Event Log
- 11:46:50 UTC — session started, Windows idle sleep prevention activated
- 11:46:51 UTC — 403 PERMISSION_DENIED errors began (transient, from fill/position polling at startup)
- 11:46:52 UTC — fill poll succeeded (99 historical fills retrieved), order manager registered all 3 pairs
- 11:46:57 UTC — fee tier refresh OK (maker=6.5 bps, taker=7.0 bps, unchanged)
- 11:46:58 UTC — bar backfill started; BIP page=1 rows=300 new=1 (all bars already cached from prior session)
- 11:47:15 UTC — STANDBY message: "waiting for operator to click Begin Warmup"
- 403 errors: 254 total, all resolved by ~11:53 UTC (no 403s in log after that)
- Session grew from 1.2KB → 76KB during analysis window (bot alive and writing)

### Trades
- No trades opened or closed.

### WFO Activity
- No WFO passes logged yet (warmup WFO not triggered — awaiting "Begin Warmup" click).
- WFO data fully loaded: 3,917 bars / 21 windows / 696.9h span for BTC.

### Errors / Halts
- 254x HTTP 403 PERMISSION_DENIED at startup (11:46-11:53 UTC) — transient, self-resolved.
- No risk halts, no feed disconnects, no ongoing errors.

### Fee Tier
- Refresh: 11:46:57 UTC — maker=6.5 bps, taker=7.0 bps, unchanged, success.

### New Config Confirmation
- regime_risk_on enabled: **True** (was False before restart)
- All 8 session fixes now loaded (min_signals=1, phantom-pending purge, WFO gates, tuner cap, safety gate deltas)

---
**Observations:**
1. **ACTION REQUIRED — manual Go Live needed.** The bot is healthy and fully loaded but `require_manual_go_live = true` means zero trades will occur until you open http://localhost:8080 and click "Begin Warmup" followed by "Go Live". The entire 33-minute window since restart has been idle for this reason. Consider setting `require_manual_go_live = false` in config.toml for future restarts to avoid this gate entirely — the warmup and WFO validation still run automatically, it just skips the manual click.
2. **Transient 403 errors on restart are normal for this setup.** 254 errors fired in the first ~7 minutes but all resolved. Likely caused by the CDE position-reconcile or fee-poll hitting a rate limit or brief auth delay on the CDP key at startup. No action needed — the bot retries and recovers cleanly.
3. **BTC champion (qqe_mod, -6.343) will face the new `require_holdout_beat_prior=true` gate on first WFO pass.** The last pre-restart WFO found a +1.326 candidate that was blocked by the old stop_delta=1.0 gate. With the new delta=2.0 threshold, that candidate should now pass and replace the current -6.343 champion immediately after "Go Live" triggers the first WFO run (~5 min after go-live).

---
## Cycle — 2026-04-13 12:24 UTC
**Session:** session_20260413_114650.jsonl (~38min running, restarted 11:46 UTC)

### Bot State
- startup_phase: **standby** — blocked on manual "Begin Warmup" click
- operator_standby: True | can_go_live: False
- daily_pnl: $0.00 | open_positions: 0 | exchange_open_orders: []
- regime_risk_on: enabled=True — **firing actively** (29 triggers in 38 min — see below)

### Warmup Pipeline (why can_go_live=False)
| Step | Status |
|------|--------|
| Candle Feed | done — 3 pairs subscribed |
| Bar Backfill | **pending** — waiting for "Begin Warmup" click |
| WFO | **pending** |
| Champion Validation | **pending** |

Bars ARE being collected (n=506 per pair at log tail), indicators are computing, but the formal warmup workflow requires a dashboard click at http://localhost:8080 to begin. Until that happens the bot will not trade.

### Active Champions (disk state — not yet loaded into session)
| Pair | Mode | Score | Tier | Tuner PnL | Tuner Aggr |
|------|------|-------|------|-----------|------------|
| BTC_USD | qqe_mod | -6.343 | primary | -37.45 | aggressive |
| SOL_USD | supertrend | +1.220 | primary | +1.55 | moderate |
| XRP_USD | rsi_reversion | +2.325 | relaxed_quarter | +1.29 | moderate |

Champions unchanged from prior cycle (WFO has not run this session).

### Trades
- None. Bot has not entered live mode.

### WFO / Tuner
- No WFO passes this session (backfill step pending).
- No tuner events this session.

### Regime Risk-On Activity (NEW CONFIG — first session confirming it works)
**29 triggers fired in 38 minutes across all 3 pairs:**
- Trigger types: live_velocity_bps (20+ events), live_volume_spike (12 events), live_range_atr (10 events)
- Pairs triggered: all 3 — BTC, SOL, XRP each fired multiple times
- Two full calm-relax cycles: 08:01 UTC and 08:16 UTC (regime calmed, then re-triggered)
- Most recent triggers: 08:19-08:20 UTC (BTC velocity, XRP velocity)
- **Market is active.** Had the bot been live, regime risk-on would have accelerated WFO refresh intervals and shortened the bootstrap window on every one of these events.

### Errors / Halts
- 254x HTTP 403 PERMISSION_DENIED at startup (11:46-11:53 UTC) — transient, resolved (confirmed by credential test: all CDE endpoints return 200 when tested directly).
- No errors since 11:53 UTC. Feed healthy.

### Fee Tier
- Last refresh: 11:46:57 UTC — maker=6.5 bps, taker=7.0 bps, unchanged.

---
**Observations:**
1. **The bot is missing an active market while sitting in standby.** Regime risk-on fired 29 times in 38 minutes — velocity, volume spikes, and ATR moves across all 3 pairs. This is the most active market window since the session started last night. Every trigger would have been a faster WFO refresh cycle if live. The manual go-live gate is costing real opportunity. Recommend opening the dashboard and clicking Begin Warmup now.
2. **`require_manual_go_live = true` should be reconsidered.** It was useful as a safety gate during initial bring-up, but the bot has run stably for 11+ hours and the warmup + WFO validation sequence is reliable. Setting it to `false` in config.toml would allow the bot to auto-resume on any restart without requiring manual intervention — the WFO still validates before going live, just without the human click gate.
3. **rsi_reversion on XRP is the best-positioned mode for this market.** XRP is ranging (RSI 42-53 across the last 10 bars, tight ATR=0.0015), exactly the chop regime where reversal strategies outperform trend-followers. The +2.325 mean score and 65% win rate in WFO history make this the highest-confidence champion of the three pairs right now.
