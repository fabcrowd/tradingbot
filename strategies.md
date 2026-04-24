# Scalp strategy reference

This document describes every **registered** Coinbase CDE scalp strategy mode: when each mode signals **long** vs **short**, and how **exits** are modeled in backtest / walk-forward optimization (WFO).

**Registry:** `WFO_REGISTERED_STRATEGY_MODES` in `backend/server/scalp_bot/scalp_vec_backtest.py`.

**Entries** come from each mode’s `detect_signals_*` (and live bundles that mirror them). **Exits** for most modes use `simulate_trades_bidir` in `scalp_vec_backtest.py`; `rsi_reversion` uses `simulate_trades_rsi`.

**Exit mechanics (bidirectional modes):** ATR-based stop, ATR-based take-profit, `max_hold_bars` time stop, optional break-even / trailing (when passed into `evaluate_params`), and optional **counter-signal exit** (close a long if the short *entry* mask is true on a bar, and vice versa). **WFO** from `ScalpRuntime` calls `optimize_pair(..., counter_signal_exit=True)`, so champion scoring includes counter exits unless that call is changed.

**Live:** Entry logic aligns with these detectors; fills and protective exits are enforced by order placement (stops, targets, flatten, etc.) in `coinbase_order_manager` / runtime—not always identical to every simulator branch.

---

## 1. `daviddtech_scalp`

DaviddTech-style confluence: T3, ADX, HLC trend lines, Waddah Attar Explosion (WAE), ATR.

| Action | Condition |
|--------|------------|
| **Enter long** | Valid ATR; ADX `> adx_threshold`; close `>` Tillson T3; HLC green `>` red and close `>` HLC mid; WAE histogram `> 0` and `>` explosion band `(upper − lower) / 2`; warmup cleared. |
| **Enter short** | Mirror: close `<` T3; HLC red `>` green; close `<` mid; WAE ` < 0` and ` < −e_band`. |

**Exits:** `simulate_trades_bidir` — stop / TP / time / BE / trail / counter-signal (when enabled).

---

## 2. `ema_momentum`

Fast vs slow EMA cross; RSI/volume params exist on `ParamSet` but **do not** gate entries in `detect_signals_ema`.

| Action | Condition |
|--------|------------|
| **Enter long** | Fast EMA crosses **above** slow (bullish cross), ATR `> 0`. |
| **Enter short** | Fast EMA crosses **below** slow. |

**Exits:** `simulate_trades_bidir`.

---

## 3. `ema_scalp` (“Tony’s EMA scalper”)

Price vs single EMA with bar direction; S/R from rolling high/low of close.

| Action | Condition |
|--------|------------|
| **Enter long** | Price crosses **above** EMA (prev vs current bar) **and** close rises vs prior close; ATR and S/R windows valid. |
| **Enter short** | Crosses **below** EMA **and** close falls vs prior close. |

**Exits:** `simulate_trades_bidir` (WFO `evaluate_params` path).

---

## 4. `macd_scalp` (“Scalp Pro” MACD)

Ehlers super-smoother MACD vs signal.

| Action | Condition |
|--------|------------|
| **Enter long** | Super-smoothed MACD line crosses **above** super-smoothed signal. |
| **Enter short** | MACD line crosses **below** signal. |

**Exits:** `simulate_trades_bidir`.

---

## 5. `rsi_reversion`

Mean reversion from RSI levels.

| Action | Condition |
|--------|------------|
| **Enter long** | RSI `≤ rsi_buy_threshold` (oversold), ATR ok. |
| **Enter short** | RSI `≥ rsi_short_threshold` (overbought), ATR ok. |

If both long and short masks are true on the same bar, **no** trade is opened in `simulate_trades_rsi`.

**Exits (dedicated simulator):**

- **Exit long:** ATR stop; or RSI `≥ rsi_sell_threshold` (recovery exit); or `max_hold_bars` time. *(A fixed +10% TP field exists in code but is not used in the long exit loop.)*
- **Exit short:** ATR stop; or price hits ATR TP (`atr_tp_mult`); or RSI `≤ rsi_short_cover_threshold` (WFO passes `rsi_buy_threshold` as cover); or time.

---

## 6. `supertrend`

ATR channel trend following with tightened bands.

| Action | Condition |
|--------|------------|
| **Enter long** | Internal direction flips from bearish to **bullish** (classic Supertrend flip after band updates). |
| **Enter short** | Flips from bullish to **bearish**. |

**Exits:** `simulate_trades_bidir`. The Supertrend flip defines **entries** here; simulator still uses stop/TP/time/counter unless you wire something else.

---

## 7. `squeeze_momentum` (TTM-style)

Bollinger vs Keltner squeeze plus linear-regression momentum of a midpoint offset.

| Action | Condition |
|--------|------------|
| **Enter long** | Prior bar: squeeze **on** (BB inside KC); this bar: momentum crosses **above** zero. |
| **Enter short** | Prior bar squeeze on; momentum crosses **below** zero. |

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

ATR chandelier-style trailing stop; entries on direction flips.

| Action | Condition |
|--------|------------|
| **Enter long** | UT direction flips to **bull** (price crosses above ratcheted trail per implementation). |
| **Enter short** | Direction flips to **bear**. |

**Exits:** `simulate_trades_bidir`. Trail drives **entries** in this mode; backtest exits remain standard stop/TP/time/counter.

---

## 10. `hull_suite`

Hull MA on close (TradingView-style HMA step).

| Action | Condition |
|--------|------------|
| **Enter long** | `HMA[i] > HMA[i-2]`. |
| **Enter short** | `HMA[i] < HMA[i-2]`. |

**Exits:** `simulate_trades_bidir`.

---

## 11. `sar_chop`

Parabolic SAR flip + choppiness regime + MA stack + MACD histogram; optional close-based “Lucid” SAR and UT Bot trail **agreement** on entries.

| Action | Condition |
|--------|------------|
| **Enter long** | PSAR (high/low) **bear→bull** flip; Choppiness `< chop_threshold`; close `>` fast MA and `>` long MA; MA(50) `≥` MA(200); MACD hist `> 0`; if `use_lucid_sar`, Lucid SAR bullish; if `use_utbot_trail`, UT direction **bull**. |
| **Enter short** | PSAR **bull→bear** flip; chop trending; close `<` fast and long MA; MA(50) `≤` MA(200); MACD hist `< 0`; optional Lucid bear; optional UT **bear**. |

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
