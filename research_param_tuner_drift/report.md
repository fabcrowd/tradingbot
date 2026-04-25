# Param tuner: timed intervals vs frequent runs (drift research)

## Executive answer

**Yes — for most goals, the tuner should behave like a periodic refiner, not a high-frequency control loop.** Your codebase already **throttles** by `param_tuner_interval_sec` (minimum **30s** in code, **60s** in repo `config.toml`), but that floor is still **very fast** relative to a 5m (or slower) bar strategy: many tuner cycles can fire **before a single new bar closes**, each re-optimizing on **overlapping** history with **strong recency weighting**. That combination is exactly where **noise-driven parameter drift** (chasing the last few hours of tape) tends to appear.

**“Drift” here means bad drift:** creeping parameter changes that improve **in-sample / recent** vec score but do not represent durable edge. It is distinct from **good drift** (true regime change), which your stack already addresses better with **WFO** (mode + coarse params) than with a tuner firing every minute.

---

## What the repo actually does (verification)

| Mechanism | Location | Effect on drift |
|-----------|----------|-----------------|
| Wall-clock throttle | [`scalp_runtime._maybe_run_tuner`](backend/server/scalp_runtime.py) ~798–817 | `tuner_iv = max(30.0, param_tuner_interval_sec)`; optional **`volatility_armed_param_tuner_interval_mult`** slows or skips when vol filter armed. |
| Recency-weighted objective | [`param_tuner.run_tuner_cycle`](backend/server/scalp_bot/param_tuner.py) ~523–524, 536–542 | `half_life = max(10.0, n_bars / 3.0)` — recent **~third of the lookback** dominates `evaluate_params`. |
| Lookback span | same, ~819 in caller | Uses `wfo_train_hours + wfo_holdout_hours` worth of bars per cycle. |
| Perturbation discipline | [`param_tuner.tune_strategy_params`](backend/server/scalp_bot/param_tuner.py) ~339–347, 451–485 | **Frozen** at high PF + min trades; **slow** mode nudges **one** param; comment notes multi-param jumps caused large PnL swings. |
| Champion coupling | [`scalp_runtime._maybe_run_tuner`](backend/server/scalp_runtime.py) ~826–840 | With defaults, tuner runs only with WFO champion and often **single mode** — reduces mode churn vs grid. |

**Implication:** The tuner is **not** tick-by-tick, but **short** `param_tuner_interval_sec` + **recency half-life ≈ n_bars/3** is closer to an **online** optimizer than to a **monthly refit** mindset. Overlapping windows mean successive runs are **highly correlated**; small vec “improvements” can accumulate into **parameter random walk**.

---

## External research (adaptation vs overfitting)

- **Adaptation and overfitting share the same math** when the signal-to-noise ratio in the feedback signal is low: frequent updates fit **noise** as easily as structure. Practitioner framing: responsiveness vs stability tradeoff ([“Can a Strategy Evolve? The Math of Adaptation vs Overfitting”](https://kniyer.substack.com/p/can-a-strategy-evolve-the-math-of)).
- **Rolling / online** methods track moving optima but **oscillate** more than periodic refits; shorter effective windows increase variance of parameter estimates (same source; see bias–variance discussion there).
- **Walk-forward / periodic reoptimization** is standard for reducing **implicit** overfitting to the most recent window; exact optimal calendar depends on horizon and costs (see e.g. walk-forward methodology summaries such as [StratBase — Walk-forward analysis](https://stratbase.ai/en/blog/walk-forward-analysis-guide)).

These sources **support** using the tuner as a **slower** layer unless you have evidence that **true** microstructure or fee regime shifts faster than WFO cadence.

---

## Should you use only timed intervals?

**You already do (wall-clock).** The design question is **how long** the interval should be.

| If your priority is… | Suggested cadence (conceptual) |
|---------------------|--------------------------------|
| **Stability, interpretability, alignment with WFO champions** | **Longer** intervals: e.g. **many bars to many hours** for 5m (e.g. 15–60+ min minimum, or **once per session / daily** for refinement only). Treat WFO as the main response to regime change. |
| **Fast response to microstructure** (with monitoring) | Shorter intervals **only if** you log **`tuner_applied`** churn, compare **live forward** to vec, and accept higher noise risk. Use **`volatility_armed_param_tuner_interval_mult > 1`** when vol filter is on (already in config surface). |

**Not recommended:** Minimum **30s** interval as a default “set and forget” for a bar-based scalp — it is **misaligned** with the natural information arrival rate (new bars) and invites **redundant** re-scoring on nearly identical data.

---

## Gaps / possible future improvements (not required to accept conclusions)

- **Bar-based throttle:** Run tuner at most every **N closed candles** per pair, in addition to `param_tuner_interval_sec`, so frequency scales with timeframe.
- **Cooldown after apply:** Require **K bars or M minutes** since last successful `apply_tuner_result` before another apply (separate from run cycle).
- **Explicit drift metric:** Log L2 or max-abs delta of applied params over 24h; alert if churn exceeds threshold.

---

## Sources

- Fabcrowd Arceus (this repo): [`backend/server/scalp_bot/param_tuner.py`](backend/server/scalp_bot/param_tuner.py), [`backend/server/scalp_bot/scalp_runtime.py`](backend/server/scalp_bot/scalp_runtime.py), [`config.toml`](config.toml) `param_tuner_interval_sec`.
- External: [Kniyer — Adaptation vs overfitting](https://kniyer.substack.com/p/can-a-strategy-evolve-the-math-of); [StratBase — Walk-forward analysis](https://stratbase.ai/en/blog/walk-forward-analysis-guide).

---

## Bottom line

**Prefer longer timed intervals (or bar-count gates) for the param tuner** when the goal is to **avoid noise drift**; keep **WFO** as the primary tool for slower structural change. The existing **freeze** and **vol-armed slowdown** help, but they do not remove the risk from **frequent runs + strong recency weighting** on overlapping windows. Short minimum intervals (30–60s) are **aggressive** relative to typical bar periods and should be treated as an **explicit experiment**, not the default stability setting.
