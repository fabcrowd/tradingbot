# Handoff for LLM review — tradingbot-1 (scalp / WFO)

Use this document to **review correctness, completeness, and risk** of recent work. Primary codebase: **Coinbase CDE scalp** under `backend/server/scalp_bot/`. Full operator context: `AGENTS.md`, `nextsession.md`.

---

## 1. Problem statement (what we fixed)

**Symptom:** Walk-forward optimization (WFO) always ran **full bar backtests** (signals + trade simulation + fees), but `evaluate_params` had a **final `else`** that routed **any unknown `ParamSet.mode`** through the **EMA-momentum** signal/simulation path **without error**.

**Risk:** A typo or stale `mode` string could be **scored as EMA momentum** while the champion row still advertised another strategy name — **label/logic mismatch**, misleading metrics and live-vs-backtest expectations.

**Non-problem (clarified):** Win rate in the vector backtest was already derived from **simulated `TradeResult` PnL**, not from “indicator direction” alone.

---

## 2. What was implemented (files & behavior)

| Area | File(s) | Change |
|------|---------|--------|
| Registry | `backend/server/scalp_bot/scalp_vec_backtest.py` | `WFO_REGISTERED_STRATEGY_MODES`: frozenset of **10** allowed `mode` strings aligned with explicit `evaluate_params` branches. |
| Backtest dispatch | same | `elif params.mode == "ema_momentum":` … (explicit). **Final `else`:** `ValueError` listing registered modes. Legacy `"auto"` still normalized earlier to fallback mode. |
| Champion I/O | `backend/server/scalp_bot/scalp_wfo.py` | Import registry; **`save_champion`** raises if `result["mode"]` not in registry (no silent bad writes). |
| Docs | `scalp_vec_backtest.py` module docstring | States that win rate = simulated trade outcomes; lists strategy modes; unknown modes do not silently fall back. |
| Tests | `backend/server/scalp_bot/test_registered_strategy_modes.py` | Unknown `evaluate_params` mode raises; invalid `save_champion` raises; `build_default_grid` modes ⊆ registry. |
| Handoff | `nextsession.md` | Refreshed with this work + WFO/tuner/win-rate semantics; **committed** in git as a docs-only commit (verify with `git log -1 -- nextsession.md`). |

**Registered modes (canonical):**  
`daviddtech_scalp`, `ema_momentum`, `ema_scalp`, `macd_scalp`, `rsi_reversion`, `supertrend`, `squeeze_momentum`, `qqe_mod`, `utbot_alert`, `hull_suite`.

---

## 3. What the reviewer should verify

1. **Completeness:** Every `if/elif` branch in `evaluate_params` for a `mode` appears in `WFO_REGISTERED_STRATEGY_MODES`, and vice versa (no orphan registry entries).
2. **`build_default_grid`:** Only emits `mode` values in the registry (test already asserts subset; reviewer can confirm no dynamic `mode` assignment elsewhere for WFO grid).
3. **Champion load path:** Manual or legacy `data/scalp_champion.json` with invalid `mode` — does runtime fail gracefully or only fail on **save**? (Reviewer: grep `load_champion`, `apply_param_dict_overrides`, mode resolution.)
4. **Imports / cycles:** `scalp_wfo` importing `WFO_REGISTERED_STRATEGY_MODES` from `scalp_vec_backtest` — acceptable or circular-risk? (Quick static read.)
5. **Tests run:** `python -m compileall backend/server` and `pytest backend/server/scalp_bot/test_registered_strategy_modes.py` (use `PYTHONPATH=backend/server` if project expects it).

---

## 4. Intentionally not changed (known gaps)

| Topic | Notes |
|-------|--------|
| **Live `SignalEngine`** | May still use a **final `else` → EMA momentum** for unknown modes. Backtester is **stricter** than live unless aligned. |
| **Param tuner** | `param_tuner.py` scores with `evaluate_params` but **optimizes nudges on PnL / PF**, not win rate; cross-mode ranking uses expectancy → PF → win rate → PnL tie-breaks. |
| **WFO objective** | WFO ranks by `WFOConfig.objective` (e.g. Sharpe, expectancy); **`min_win_rate`** is a **gate**, not the primary score. |
| **Grid vs tuner** | `build_default_grid` is a **finite** discrete set (~2886 rows); tuner can explore values **outside** that grid — separate coverage problem. |
| **Bar backtest vs tick entries** | Documented in `AGENTS.md` / `nextsession.md` — live tick path may diverge from bar-only `evaluate_params`. |

---

## 5. Recommended follow-ups (prioritized)

1. **Git hygiene:** Confirm `scalp_vec_backtest.py`, `scalp_wfo.py`, and `test_registered_strategy_modes.py` are **committed and pushed** with the rest of the operator’s intended changes (handoff noted a large dirty tree at one point).
2. **Optional parity:** Make **live** mode resolution **fail-closed** or log loudly for unknown `mode`, matching the backtester.
3. **Optional product:** If “tuner-only optima” matter, consider **denser WFO grid**, **post-grid local search inside WFO**, or a documented two-phase workflow.
4. **Regression suite:** Run full `backend/server/scalp_bot/` pytest after merging.

---

## 6. Commands (reviewer smoke test)

```powershell
cd C:\Users\daroo\Desktop\Repos\tradingbot-1
python -m compileall backend/server
$env:PYTHONPATH="backend/server"
python -m pytest backend/server/scalp_bot/test_registered_strategy_modes.py backend/server/scalp_bot/test_wfo_promotion_gates.py -q
```

---

## 7. Questions for the reviewing LLM

1. Should **loading** an invalid champion `mode` be rejected at startup with a dashboard alert, or is **save-time** validation enough?
2. Should `STRATEGY_MODES` / `TUNABLE_PARAMS` in `param_tuner.py` be **generated from or asserted against** `WFO_REGISTERED_STRATEGY_MODES` to prevent future drift?
3. Any **downstream JSON** consumers (dashboard, exports) that assume a larger mode vocabulary?

---

*Generated for cross-LLM review. Repo: Coinbase CDE scalp bot (`[scalp]` in `config.toml`).*
