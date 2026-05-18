# Scalp strategy reference

This document describes every **registered** Coinbase CDE scalp strategy mode: when each mode signals **long** vs **short**, and how **exits** are modeled in backtest / walk-forward optimization (WFO).

**Registry:** `WFO_REGISTERED_STRATEGY_MODES` in `backend/server/scalp_bot/scalp_vec_backtest.py`.

**Entries** come from each mode’s `detect_signals_*` (and live bundles that mirror them). **Exits** for most modes use `simulate_trades_bidir` in `scalp_vec_backtest.py`; `rsi_reversion` uses `simulate_trades_rsi`.

**Exit mechanics (bidirectional modes):** ATR-based stop, ATR-based take-profit, `max_hold_bars` time stop, optional break-even / trailing (when passed into `evaluate_params`), and optional **counter-signal exit** (close a long if the short *entry* mask is true on a bar, and vice versa). **WFO** from `ScalpRuntime` calls `optimize_pair(..., counter_signal_exit=True)`, so champion scoring includes counter exits unless that call is changed.

**Live:** Entry logic aligns with these detectors; fills and protective exits are enforced by order placement (stops, targets, flatten, etc.) in `coinbase_order_manager` / runtime—not always identical to every simulator branch.

**Bar interval:** Live trading and TradingView parity checks use **5-minute** candles (`ScalpPairConfig.interval` defaults to **5** in `scalp_config.py`). Indicator periods and `max_hold_bars` are defined in bar units (wall-clock duration scales with interval).

---

## 1. `daviddtech_scalp`

DaviddTech-style confluence: T3, ADX, HLC trend lines, Waddah Attar Explosion (WAE), ATR.

| Action | Condition |
|--------|------------|
| **Enter long** | Valid ATR; ADX `> adx_threshold`; close `>` Tillson T3; HLC green `>` red and close `>` HLC mid; WAE histogram `> 0` and `>` explosion band `(upper − lower) / 2`; warmup cleared. |
| **Enter short** | Mirror: close `<` T3; HLC red `>` green; close `<` mid; WAE ` < 0` and ` < −e_band`. |

**Implementation note (WAE):** In `waddah_attar_explosion`, histogram smoothing uses `sig_period = max(3, min(21, wae_slow_len // 2))`. For **`wae_slow_len > 41`**, `sig_period` **plateaus at 21** — further increases to `wae_slow_len` (config, champion JSON, tuner) **do not** further smooth that signal unless the clamp is relaxed in code. Default WFO daviddtech grid keeps `40` (`ParamSet`), so this bites mainly override paths.

**Exits:** `simulate_trades_bidir` — stop / TP / time / BE / trail / counter-signal (when enabled).

---

## 2. `ema_momentum`

Fast vs slow EMA cross on **rising-only** semantics (prior bar required to have finite fast & slow EMA so the first seeded bar cannot fake a cross). RSI/volume/`min_signals` exist on `ParamSet` but **do not** gate entries in `detect_signals_ema` (WFO grid does not sweep them for this mode).

| Action | Condition |
|--------|------------|
| **Enter long** | Fast EMA crosses **above** slow (bullish cross), ATR `> 0`. |
| **Enter short** | Fast EMA crosses **below** slow. |

**Exits:** `simulate_trades_bidir`.

---

## 3. `ema_scalp` (“Tony’s EMA scalper”)

Price vs single EMA with bar-direction confirmation. Rolling high/low of close define S/R **levels** but **do not** block entries near resistance/support (only require finite S/R + ATR windows, matching Pine).

| Action | Condition |
|--------|------------|
| **Enter long** | Price crosses **above** EMA (prev vs current bar) **and** close **strictly** rises vs prior close; ATR `> 0` and S/R window finite. |
| **Enter short** | Crosses **below** EMA **and** close **strictly** falls vs prior close; same validity gates. |

**Exits:** WFO / vector backtest — `simulate_trades_bidir` (generic ATR stop/TP). Live `SignalEngine` places stop/TP using rolling `high_8` / `low_8` (support/resistance) with ATR floors — not identical to WFO scoring.

---

## 4. `macd_scalp` (“Scalp Pro” MACD)

Ehlers super-smoother MACD vs signal (`detect_signals_macd` / Pine `f_super_smooth`).

| Action | Condition |
|--------|------------|
| **Enter long** | `ta.crossover` semantics: prior bar MACD `≤` signal, current bar MACD `>` signal. |
| **Enter short** | `ta.crossunder` semantics: prior bar MACD `≥` signal, current bar MACD `<` signal. |

MACD line uses `(ss_fast - ss_slow) * 1e7` (Pine parity); scale does not change crossover bars.

Warmup: `vec_warmup_prefix_len("macd_scalp")` = `max(fast, slow, signal, atr_period) + 1`.

**Exits:** `simulate_trades_bidir` (ATR stop/TP at entry; same as live `SignalEngine._eval_macd_scalp`).

---

## 5. `rsi_reversion`

Mean reversion from RSI levels.

| Action | Condition |
|--------|------------|
| **Enter long** | RSI `≤ rsi_buy_threshold` (oversold), ATR ok. |
| **Enter short** | RSI `≥ rsi_short_threshold` (overbought), ATR ok. |

If both long and short masks are true on the same bar, **no** trade is opened in `simulate_trades_rsi`.

**Exits (dedicated simulator):**

- **Exit long:** ATR stop (`atr_stop_mult`); or **price hits ATR TP** (`entry + ATR × atr_tp_mult`), matching live `signal_engine`; or RSI `≥ rsi_sell_threshold` (recovery exit); or `max_hold_bars` time. When both stop and TP print one bar with known **open**, the simulator resolves order like `simulate_trades_bidir` (`_intrabar_stop_first`). Without opens it assumes **stop first** same-bar.
- **Exit short:** ATR stop; or price hits ATR TP (`atr_tp_mult`); or RSI `≤ rsi_short_cover_threshold` (`evaluate_params` passes `rsi_buy_threshold` here); same-bar stop+TP respects opens when supplied; otherwise stop first; or time.

Set **`SCALP_VEC_BT_DIAG`** (`1`, `true`, `yes`) to log throttled diagnostics for short series / warmup edge cases (`adx_wilder`, `detect_signals_daviddtech`) and plateaued WAE smoothing when `wae_slow_len > 41` (`scalp_vec_backtest`).

---

## 6. `supertrend`

ATR channel trend following with tightened bands (`detect_signals_supertrend` / Pine `stDir`).

| Action | Condition |
|--------|------------|
| **Enter long** | Internal direction flips from bearish to **bullish** (sparse edge; not every bull bar). |
| **Enter short** | Flips from bullish to **bearish**. |

**Initialization (TV parity):** at `loop_warm = max(atr_period, period)`, direction is seeded **bullish**. First flip in a backtest window is often bearish in uptrends; first bar after seed may short in downtrends (same as Pine). Warmup mask clears `[:vec_warmup_prefix_len]` = bars `0..loop_warm` (first possible flip at `loop_warm + 1` is allowed).

**Exits:** `simulate_trades_bidir` (ATR stop/TP at entry; same as live `SignalEngine._eval_supertrend`).

---

## 7. `squeeze_momentum` (TTM-style)

Bollinger vs Keltner squeeze plus linear-regression momentum of a midpoint offset.

| Action | Condition |
|--------|------------|
| **Enter long** | Prior bar: squeeze **on** (BB inside KC); this bar: momentum crosses **above** zero. |
| **Enter short** | Prior bar squeeze on; momentum crosses **below** zero. |

**Squeeze gate (design):** Requires `squeeze_on[i-1]` only — **not** `not squeeze_on[i]`. Momentum may cross zero while compression is still on (broader than a strict “release bar only” rule). Pine export uses the same `squeezeOn[1]` gate.

**Exits:** `simulate_trades_bidir`.

---

## 8. `qqe_mod`

Smoothed RSI with QQE-style trailing band.

| Action | Condition |
|--------|------------|
| **Enter long** | Smoothed RSI crosses **above** trail from at/below; smoothed RSI `> 50`. |
| **Enter short** | Crosses **below** trail from at/above; smoothed RSI `< 50`. |

**Exits:** `simulate_trades_bidir`.

---

## 9. `utbot_alert`

ATR chandelier-style trailing stop (`detect_signals_utbot` / Pine `utbot_alert` block). **Edge detector** — signals on direction flips only (sparse mask). **On default WFO grid** (restored 2026-05).

| Action | Condition |
|--------|------------|
| **Enter long** | Direction flips to **bull** (`udir[1] != 1` and `udir == 1` in Pine). |
| **Enter short** | Direction flips to **bear**. |

**Initialization (TV parity):** at `loop_warm = utbot_atr_period`, trail = close, direction = **bullish**. Same window-start behavior as `supertrend`. Warmup clears `[:vec_warmup_prefix_len]` (= bars `0..loop_warm`).

**Exits:** `simulate_trades_bidir` (ATR stop/TP at entry; live `SignalEngine._eval_utbot`).

---

## 10. `hull_suite`

Hull MA on close (TradingView-style HMA step).

| Action | Condition |
|--------|------------|
| **Enter long** | `HMA[i] > HMA[i-2]` (trend-up **state** — mask True on every bar while it holds, not only on flips). |
| **Enter short** | `HMA[i] < HMA[i-2]` (trend-down state, same semantics). |

**Entries in practice:** Pine only calls `strategy.entry` when **flat** (`longEntry` / `shortEntry` while `strategy.position_size == 0`). Live and WFO mirror that: one position at a time; `simulate_trades_bidir` uses `next_allowed` + `cooldown_bars` so dense masks do not stack overlapping trades. `counter_signal_exit` has little effect on this mode (opposite mask rarely True mid-hold).

**Exits:** `simulate_trades_bidir`.

---

## 11. `sar_chop`

Parabolic SAR flip + choppiness regime + MA stack + MACD histogram; optional close-based “Lucid” SAR and UT Bot trail **agreement** on entries.

| Action | Condition |
|--------|------------|
| **Enter long** | PSAR (high/low) **bear→bull** flip; **Choppiness `< sar_chop_chop_threshold`** (repo default **68** — use **38.2** in pair TOML for a strict fib trending gate); close `>` fast MA and `>` long MA; MA(50) `≥` MA(200); MACD hist `> 0`; if `use_lucid_sar`, Lucid SAR bullish; if `use_utbot_trail`, UT direction **bull**. |
| **Enter short** | PSAR **bull→bear** flip; same **CHOP `< threshold`** regime gate; close `<` fast MA, `<` MA(50), and `<` MA(200); MACD hist `< 0`; if `use_lucid_sar`, Lucid bear; if `use_utbot_trail`, UT **bear**. |

**Exits:** `simulate_trades_bidir`. UT trail here is mainly an **entry** filter; it is not a separate trail-exit inside the bidirectional simulator for this mode.

---

## Code map

| Concern | Location |
|---------|----------|
| Mode list | `scalp_vec_backtest.WFO_REGISTERED_STRATEGY_MODES` |
| Vector detectors | `detect_signals_*` in `scalp_vec_backtest.py` |
| Bidirectional trade sim | `simulate_trades_bidir` |
| RSI trade sim | `simulate_trades_rsi` |
| Live closed-bar dispatch | `backend/server/scalp_bot/signal_engine.py` |
| WFO counter-signal flag | `scalp_wfo.optimize_pair` / `ScalpRuntime` call site |

---

## `auto` mode

`strategy_mode = "auto"` resolves to the WFO champion’s `mode` for that symbol (or `auto_mode_fallback` from config when no champion). It is not a separate strategy; it selects one of the modes above.
