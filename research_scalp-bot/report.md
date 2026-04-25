# Scalp Bot: How It Works in Practice
**Research date:** 2026-04-09  
**Verified against:** config.toml, scalp_runtime.py, scalp_wfo.py, scalp_trader.py, param_tuner.py

---

## Answer (3-sentence summary)

The scalp bot is a three-layer adaptive system: a Walk-Forward Optimizer (WFO) runs every **15 minutes** (config: `wfo_interval_sec = 900`) to select the best strategy mode + params across 9 candidate strategies using rolling **24h train / 8h holdout** windows, writing a champion JSON that is hot-reloaded into the live process with zero downtime. A param tuner runs every **2 minutes** (`_TUNER_INTERVAL_SEC = 120.0`) to locally refine indicator settings within the active mode — but *only applies changes when its own internal best-mode ranking matches the currently active mode*; otherwise the cycle is a no-op for execution. A regime detector watches for volume spikes and ATR moves; when triggered it accelerates WFO by 2.85× (floor 300s), compresses the bootstrap window, and relaxes Nemesis quality gates — so the bot self-tunes faster under volatile conditions.

---

## Architecture Overview

```mermaid
flowchart TD
    subgraph FEED["Candle Feed (coinbase_candle_feed.py)"]
        WS["WebSocket\n(live candles)"]
        REST["REST backfill\n(Coinbase public candles)"]
        BS["BarStore\n(Parquet on disk)"]
    end

    subgraph INDICATORS["IndicatorSet (indicators.py)"]
        EMA["EMA fast/slow\n(Hexital or numpy-matched)"]
        RSI["RSI Wilder\n(9-period)"]
        VWAP["VWAP\n(daily session reset)"]
        VOL["Volume MA\n(20-period SMA)"]
        MACD["MACD\n(Ehlers super-smoother)"]
        STRATS["Strategy bundles\n(DaviddTech, Supertrend,\nSqueeze, QQE, UT Bot,\nHull Suite)"]
        IV["→ IndicatorValues\n(30+ flags, crosses,\nconfluence checks)"]
    end

    subgraph SIGNAL["SignalEngine (signal_engine.py)"]
        GATE["Entry gate checks\n(cooldown, hours, ADX min)"]
        DISPATCH["Strategy dispatcher\n(9 modes)"]
        SIG["→ ScalpSignal\n(dir, entry, stop, TP, ATR,\nconfidence 0–1)"]
    end

    subgraph TRADER["ScalpTrader (scalp_trader.py)"]
        FILTERS["Entry filters\n(standby, risk_halt,\npositions, daily loss,\ncorrelation scaling)"]
        SIZE["Position sizing\n(risk_pct × capital /\nstop_distance)"]
        ENTRY["Order placement\n(limit or market)"]
        POS["ScalpPosition\n(pending → open)"]
        EXITS["Exit management\n(OCO stop+TP,\nbreakeven, trail,\ntime-stop, RSI-exit,\ncounter-reversal)"]
    end

    subgraph EXEC["CoinbaseOrderManager (coinbase_order_manager.py)"]
        PLACE["create_order → INTX perps"]
        FILLS["Fill polling (3 paths)\n2s fill poll\npending order poll\nprotective order poll 10s"]
        RECON["INTX reconciliation\n(30s position sync)"]
    end

    WS -->|closed candle| BS
    REST -->|backfill| BS
    BS -->|OHLCV arrays| INDICATORS
    WS -->|live candle| INDICATORS
    EMA & RSI & VWAP & VOL & MACD & STRATS --> IV
    IV --> GATE
    GATE --> DISPATCH
    DISPATCH --> SIG
    SIG --> FILTERS
    FILTERS --> SIZE
    SIZE --> ENTRY
    ENTRY --> POS
    POS --> EXITS
    ENTRY -->|add_order| PLACE
    EXITS -->|cancel/replace| PLACE
    FILLS -->|on_fill callback| POS
    RECON -->|adopt/reconcile| POS
```

---

## Self-Adjusting Layer 1: Walk-Forward Optimizer

**Config (config.toml):** `wfo_interval_sec = 900`, `wfo_train_hours = 24`, `wfo_holdout_hours = 8`, `wfo_step_hours = 4`  
**Bar load span:** `total_hours = 24 + 8 + (4×3) = 44h` → `load_days ≈ 2.3 days`

```mermaid
flowchart TD
    subgraph WFO["ScalpWalkForwardOptimizer — every 15 min (risk_off) / ~5 min (risk_on, floor 300s)"]
        LOAD["Load ~2.3 days of bars\nfrom BarStore"]
        WINDOWS["Generate rolling windows\nWindow 1: Train h0–h24 → Holdout h24–h32\nWindow 2: Train h4–h28 → Holdout h28–h36\nWindow 3: Train h8–h32 → Holdout h32–h40\n(steps by 4h, needs ≥50 train / ≥20 holdout bars)"]
        GRID["Build parameter grid\n~1000 combinations × 9 modes\n(atr mults, hold bars, EMA periods,\nRSI, MACD, indicator-specific params)"]
        TRAIN["Evaluate each combo on TRAIN\nwith recency half-life = n_bars / 3\n(newer trades weighted more)"]
        GATES["Hard gates per window\nPF ≥ 0.8 · WR ≥ 20% · DD ≤ 30% · n ≥ 3"]
        TOP50["Top-K=50 survivors\nvalidated on HOLDOUT split"]
        AGG["Aggregate across windows\nStability filter: mean/std ≥ 0.15\nCoverage: must appear in ≥50% of windows\nPick highest mean Sharpe"]
        SAFETY["Safety gate\n|Δhold_bars| ≤ 24\n|Δstop_mult| ≤ 0.5×\n|Δtp_mult| ≤ 1.0×\nNew score > baseline on latest holdout"]
        WRITE["Write scalp_champion.json\n(atomic temp-file swap)"]
        MTIME["ScalpRuntime sees new mtime\non next tick"]
        APPLY["_try_load_champion()\nsetattr() params onto pair_cfg\nswitch _active_mode if changed\nno restart needed"]
    end

    LOAD --> WINDOWS --> GRID --> TRAIN --> GATES --> TOP50 --> AGG --> SAFETY --> WRITE --> MTIME --> APPLY
```

**What gets written to champion.json per symbol:**
- `mode` — one of 9 strategy names
- `params` — 25–45 indicator/risk settings
- `holdout_metrics` — trade count, win rate, PnL, Sharpe, PF, expectancy, max DD
- `score`, `stability`, `windows_passed` — audit trail

---

## Self-Adjusting Layer 2: Param Tuner

**Cadence:** every **2 minutes** (`_TUNER_INTERVAL_SEC = 120.0`, `scalp_runtime.py:70`)  
**Critical gate:** `apply_tuner_result` only fires when `active_mode == result.best_mode` (`scalp_runtime.py:467–481`).  
If the tuner's internal grid picks a *different* mode than the one currently executing, the cycle produces no param changes for that round — even if improvements were found. This is logged as `skip apply_tuner_result — active_mode=X tuner_cycle_best=Y`.

```mermaid
flowchart TD
    subgraph TUNER["ParamTuner — every 2 min (_TUNER_INTERVAL_SEC = 120.0)"]
        LOAD2["Load last 24h bars"]
        EVAL9["Evaluate all 9 modes\non recent data\n(recency half-life applied)"]
        BESTMODE["Select tuner best_mode\nby expectancy ≥ 2 trades\ntiebreak: PF → WR → PnL"]
        MATCH{"active_mode ==\ntuner best_mode?"}
        SKIP["Log skip apply_tuner_result\n(mode mismatch)\nNo param changes this cycle"]
        PF["Compute Profit Factor\nof active mode"]
        AGG2{"Aggressiveness\n(profit factor)"}
        FROZEN["FROZEN\nPF ≥ 3.0 AND n ≥ 10\n→ continue (no param changes)"]
        SLOW["SLOW\nPF ≥ 1.5\n→ 1 param, 0.5× step"]
        MOD["MODERATE\nPF ≥ 0.8\n→ 3 params, 1.0× step"]
        AGG3["AGGRESSIVE\nPF < 0.8\n→ 6 params, 2.0× step"]
        PERTURB["Perturb params (one at a time)\nPriority: stop_mult, tp_mult →\nhold_bars → indicators\nTry +Δ and −Δ each\nAccept if PnL improves\n(PF as tiebreaker)"]
        CONSTRAINTS["Enforce constraints\nema_fast < ema_slow\nmacd_fast < macd_slow\nhlc_low ≤ hlc_high"]
        APPLY2["apply_tuner_result()\nsetattr() onto pair_cfg\nlog adjustments_made"]
        SAVE["Save tuner state JSON"]
    end

    LOAD2 --> EVAL9 --> BESTMODE --> MATCH
    MATCH -->|no| SKIP
    MATCH -->|yes| PF --> AGG2
    AGG2 --> FROZEN
    AGG2 --> SLOW
    AGG2 --> MOD
    AGG2 --> AGG3
    FROZEN --> SAVE
    SLOW & MOD & AGG3 --> PERTURB --> CONSTRAINTS --> APPLY2 --> SAVE
    SKIP --> SAVE
```

---

## Mode Authority: Who Controls active_mode

This is the most nuanced part of the system. Two separate paths set `_active_mode`:

```mermaid
flowchart TD
    TICK["Every market tick\n(ScalpRuntime)"]
    CHAMPION_EXISTS{"scalp_champion.json\nexists for this symbol?"}

    subgraph CHAMPION_PATH["Champion path (WFO owns mode)"]
        RELOAD["_try_load_champion()\nmtime check"]
        SET_MODE["_active_mode = champion.mode\n_mode_source = 'wfo_champion'"]
        TUNER_GATE["Tuner runs every 2 min\napplies params ONLY IF\nactive_mode == tuner best_mode\notherwise: skip apply"]
    end

    subgraph NO_CHAMPION_PATH["No-champion path (Nemesis owns mode)"]
        BOOT["best_mode_bootstrap_no_champion()\n2h return% window"]
        TUNER2["Tuner selects best_mode\nby expectancy"]
        NEMESIS["nemesis_resolve_bootstrap_vs_tuner()\nCompares bootstrap vs tuner mode"]
        NEM_AGREE["Both agree → bootstrap\n_mode_source = 'bootstrap'"]
        NEM_TUNER["Tuner wins dual gate\n(better expectancy + PF ≥ 0.95)\n_mode_source = 'nemesis_tuner'"]
        NEM_BOOT["Bootstrap holds\n_mode_source = 'bootstrap'"]
    end

    TICK --> CHAMPION_EXISTS
    CHAMPION_EXISTS -->|yes| RELOAD --> SET_MODE --> TUNER_GATE
    CHAMPION_EXISTS -->|no| BOOT & TUNER2 --> NEMESIS
    NEMESIS --> NEM_AGREE & NEM_TUNER & NEM_BOOT
```

**Key distinction:** "Tuner never switches mode" is only true when a WFO champion exists. Without a champion, Nemesis uses tuner output as one of two inputs to set `_active_mode`.

---

## Self-Adjusting Layer 3: Regime Detection & Acceleration

```mermaid
stateDiagram-v2
    [*] --> RISK_OFF: startup

    RISK_OFF --> RISK_ON: volume ≥ 2.5× vol_MA\nOR |close − prev| ≥ 1.75× ATR\n(checked on every closed candle\nand live intra-bar velocity)

    RISK_ON --> RISK_OFF: now >= risk_on_until\n(default 900s window)

    state RISK_OFF {
        [*] --> normal
        normal: WFO interval: 900s\nBootstrap window: 2h\nNemesis gates: PF ≥ 1.0
    }

    state RISK_ON {
        [*] --> accelerated
        accelerated: WFO interval: 900 × 0.35 = ~315s (floor 300s)\nBootstrap window: capped 1h\nNemesis gates: PF ≥ 0.95 + expectancy slack
    }
```

**What regime does NOT do:** It does not gate entries or change position size directly. It is purely a WFO/bootstrap acceleration lever.

---

## Warmup & Operator State Machines

```mermaid
stateDiagram-v2
    direction LR

    state "Operator Phase" as OP {
        [*] --> STANDBY
        STANDBY --> WARMING_UP: begin_warmup()
        WARMING_UP --> PRIMED: WFO done\n+ bars ≥ warmup_min
        PRIMED --> LIVE: go_live()
        LIVE --> PRIMED: enter_standby()
    }

    state "Warmup Phase" as WP {
        [*] --> COLLECTING: startup
        COLLECTING --> OPTIMIZING: bars ≥ warmup_min_bars (default 500)\nper pair
        OPTIMIZING --> READY: WFO completes\n+ champion validated
        READY --> COLLECTING: re-warmup triggered
    }
```

- **Entries blocked** until operator phase = LIVE AND warmup phase = READY
- **WFO triggered once** bar threshold met during COLLECTING
- **Forced graduation** at `warmup_max_hours` timeout (if set)

---

## End-to-End Signal → Fill Flow

```mermaid
sequenceDiagram
    participant CF as CandleFeed
    participant RT as ScalpRuntime
    participant IND as IndicatorSet
    participant SE as SignalEngine
    participant ST as ScalpTrader
    participant OM as CoinbaseOrderManager
    participant EX as Coinbase INTX

    CF->>RT: closed_candle(pair_key, candle)
    RT->>IND: update(candle) → IndicatorValues
    RT->>RT: _touch_regime_risk_on(iv)
    RT->>SE: evaluate(pair_key, iv, mode) → ScalpSignal
    SE-->>RT: signal(LONG, entry=X, stop=Y, tp=Z, atr=A)
    RT->>ST: try_open(signal, pair_cfg, capital)
    ST->>ST: filters: standby? risk_halt? position exists?
    ST->>ST: size = risk_pct × capital / stop_distance
    ST->>OM: add_order(entry_limit, qty, cl_ord_id)
    OM->>EX: POST create_order
    EX-->>OM: order_id confirmed

    loop Every 2s
        OM->>EX: get_fills()
        EX-->>OM: fill(trade_id, price, qty)
        OM->>RT: on_fill(cl_ord_id, px, sz)
        RT->>ST: on_entry_filled → place OCO
        ST->>OM: add_order(stop_limit) + add_order(tp_limit)
    end

    Note over ST: Per closed candle also checks:<br/>time_stop (max_hold_bars)<br/>breakeven trigger<br/>trailing stop ratchet<br/>counter-reversal score (0-4)

    EX-->>OM: fill(stop OR tp order)
    OM->>RT: on_fill(exit cl_ord_id, px)
    RT->>ST: on_exit_filled → close position
    ST->>ST: record_loss/win → set cooldown
    ST->>OM: cancel sibling order
```

---

## Exit Decision Tree

```mermaid
flowchart TD
    BAR["Closed bar event"]
    BAR --> TIME{"age ≥ max_hold_bars?"}
    TIME -->|yes| CLOSE_TIME["Market close\n(time-stop)"]
    TIME -->|no| RSI_EXIT{"RSI mode AND\nRSI crossed above 50?"}
    RSI_EXIT -->|yes| CLOSE_RSI["Market close\n(RSI reversion exit)"]
    RSI_EXIT -->|no| BE{"unrealised PnL ≥\nbreakeven_atr_trigger × ATR?"}
    BE -->|yes, not yet hit| MOVE_BE["Move stop to entry\n± breakeven_buffer_bps"]
    BE -->|no| TRAIL{"unrealised PnL ≥\ntrail_atr_trigger × ATR?"}
    TRAIL -->|yes| RATCHET["Ratchet trail stop\nnever backward"]
    TRAIL -->|no| COUNTER{"Counter-signal\nexists AND\nbreakeven_hit?"}
    COUNTER -->|score 2| EXIT_ONLY["Exit at mark\n(no re-entry)"]
    COUNTER -->|score 3-4| FULL_REV["Close + re-enter\nopposite direction"]
    COUNTER -->|score 0-1| HOLD["Hold, let OCO work"]

    subgraph ALWAYS["Always active (via OCO orders on exchange)"]
        SL["Stop-loss limit\n(entry - ATR × stop_mult)"]
        TP["Take-profit limit\n(entry + ATR × tp_mult)"]
    end
```

---

## The Closed Learning Loop

```mermaid
flowchart LR
    LIVE["Live trading\n(pair_cfg params active)"]
    BARS["BarStore\n(rolling Parquet)"]
    WFO2["WFO\n(every 15 min, config: 900s)"]
    CHAMPION["scalp_champion.json"]
    TUNER2["Param Tuner\n(every 2 min)"]
    REGIME2["Regime detector\n(every bar)"]
    BOOTSTRAP2["Nemesis / Bootstrap\n(no-champion fallback)"]

    LIVE -->|fills candles| BARS
    BARS -->|~2.3-day window| WFO2
    WFO2 -->|writes| CHAMPION
    CHAMPION -->|hot-reloads via mtime| LIVE
    BARS -->|24h window| TUNER2
    TUNER2 -->|setattr() IF mode matches| LIVE
    LIVE -->|indicators| REGIME2
    REGIME2 -->|WFO interval × 0.35, floor 300s| WFO2
    BARS -->|2h lookback| BOOTSTRAP2
    BOOTSTRAP2 -->|sets active_mode when no champion| LIVE

    style LIVE fill:#1a6b3a,color:#fff
    style WFO2 fill:#1a3b6b,color:#fff
    style TUNER2 fill:#6b3b1a,color:#fff
    style REGIME2 fill:#6b1a3b,color:#fff
```

---

## Non-Obvious Design Decisions (verified from code)

| Decision | Why it matters |
|---|---|
| **EMA cross required, not trend** (`signal_engine.py:826`) | Pure trend fires every bar in mild drift → whipsaws. Cross = timing edge, once per shift |
| **MACD scaled ×1e7** (`indicators.py:510`) | Raw Ehlers MACD is ~0.00001; scaling prevents float precision loss in cross detection |
| **Recency half-life = n_bars/3** (both WFO and tuner) | Older trades decay geometrically; implicit regime adaptation without explicit detection |
| **Stability filter mean/std ≥ 0.15** (loose) | Crypto is volatile; tighter thresholds reject ALL strategies in choppy periods |
| **Tuner uses PF, not win rate** | PF(2.0) = $2 won per $1 lost regardless of WR; magnitude > frequency in crypto |
| **Reversal only if breakeven_hit** (`scalp_trader.py:905`) | Never chase a counter-signal at a loss; cost-basis must be protected first |
| **Regime accelerates WFO, not entries** | Entry gating stays clean; faster re-optimization is the response to volatility |
| **Bootstrap ranks by return%, not expectancy** | Bootstrap is regime-aware; recent absolute return > statistical edge on small samples |
| **Tuner apply gate: active_mode == tuner best_mode** (`scalp_runtime.py:467`) | Prevents param desync if Nemesis holds a mode the tuner grid doesn't favour; logs all mismatches |
| **3 independent fill detection paths** | Coinbase get_fills() can miss fills at >100 concurrent; safety net prevents orphan positions |

---

## Open Questions (grounded in code)

1. **Tuner apply gap when modes diverge:** When `active_mode ≠ tuner best_mode`, the tuner produces no param improvements that cycle — including potential improvements for the *active* mode. If logs show frequent `skip apply_tuner_result` while a champion is active, this is worth evaluating: either document "apply only when winner matches active mode" as an intentional product rule, or consider always scoring the active mode separately and applying its improvements regardless of the global grid winner.

2. **WFO floor 300s in risk-on** (`config.toml:320`): Regime scales interval to ~315s but floor is 300s — effectively no acceleration at the boundary. At 15-min candles, 300s = 1/3 of a bar. Real question is whether 300s is fast enough to catch a regime shift before the next significant move.

3. **Daily loss limit resets on restart** (`scalp_trader.py:113, 1283`): `_daily_pnl` is in-memory only. `reset_session()` zeroes it. A crash-and-restart mid-day resets the accumulator, allowing the loss limit to be exceeded across the session boundary. If this is a hard safety property, it needs persistence + rehydration on startup.

4. **Correlation group scaling** (unverified this pass): Not traced in detail. Noted as a candidate for future verification before drawing conclusions.
