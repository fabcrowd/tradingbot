# Champion vs lab P&L / score comparison — methodology and peer review

This document describes how the **multi-timeframe champion vs lab** measurement is conducted in this repository (including **14×24h rolling windows**), so humans and other LLMs can audit or reproduce it.

---

## 1. Purpose

**Question:** On historical bars, how does the **saved WFO champion** (one `ParamSet` per symbol from `data/scalp_champion.json`) compare to **fixed lab presets** (five strategy modes aligned with `run_multiwindow_lab.py`), when both are evaluated on **identical calendar-aligned** 5m / 15m / 60m data?

**Modes:**

1. **Single window** — optional clip to the last *H* hours after alignment (e.g. `--last-hours 24`).
2. **Rolling windows** — *N* disjoint wall-clock windows of length *H* (e.g. `--roll-windows 14 --window-hours 24`), anchored at the **newest** end of the aligned range; metrics are **averaged** over windows that pass bar-count gates.

---

## 2. Code locations

| Piece | Path |
|--------|------|
| Main script | `.optimization/pnl-feedback-lab/scripts/compare_champion_multi_timeframe.py` |
| Bar load | `backend/server/scalp_bot/bar_store.py` (`load_bars`) |
| Backtest | `scalp_bot.scalp_vec_backtest.evaluate_params` |
| Champion load | `scalp_bot.scalp_wfo.load_champion_for_symbol`, `CHAMPION_PATH` → `data/scalp_champion.json` |
| Lab param base | `scalp_wfo._params_from_config` |

Venue (`coinbase_perps` vs `kraken_spot`) follows `config.toml` and `bar_store.set_bar_store_venue`.

---

## 3. Operator inputs (CLI / config)

| Input | Role |
|--------|------|
| `config.toml` (+ `--config`) | Venue, pairs, fees, slippage, fill model, symbols, `contract_size` |
| `--lookback` | `full` \| `wfo` \| `days:FLOAT` — applied **before** cross-TF alignment |
| `--min-bars` | Minimum bars per TF after alignment (single path); rolling slices use `max(20, min-bars)` |
| `--last-hours` | After alignment, keep only last *H* hours (**mutually exclusive** with `--roll-windows`) |
| `--roll-windows` | Number *N* of disjoint windows |
| `--window-hours` | Length *H* of each window (default 24) |
| `--champion-path` | Override path to champion JSON |

Fees, slippage, and fill model are applied to **both** champion and lab via `dataclasses.replace` on `ParamSet`.

---

## 4. Data loading and calendar alignment

**4.1** For each configured pair in `BTC_USD`, `SOL_USD`, `XRP_USD`, load Parquet for intervals **5, 15, 60** minutes with the same `last_n_days` (or full file).

**4.2** Compute the **intersection** of time coverage:

- `ts0 = max(first timestamp of each loaded series)`
- `ts1 = min(last timestamp of each loaded series)`

**4.3** Mask each series to `[ts0, ts1]`. If any interval has fewer than `min-bars` bars, skip the pair.

**4.4** If `--last-hours` is set: clip to the last *H* hours from `t_end`, then **re-intersect** `ts0/ts1` across all TFs (same pattern as rolling slice).

---

## 5. Rolling windows (no overlap)

**5.1 Planning** (`_plan_contiguous_windows`):

- `span = ts1 - ts0`, `n_max = floor(span / window_sec)`, `n = min(N_requested, n_max)`.
- Windows end at `ts1`: `t_first = ts1 - n * window_sec`; if `t_first < ts0`, clamp to `ts0` and recompute `n`.

**5.2** Each window is **half-open** `[w_lo, w_hi)` in Unix seconds, length `window_hours * 3600`.

**5.3 Slicing** (`_slice_aligned_range`): subset each TF to that range, then **re-intersect** timestamps so 5m/15m/60m stay on the same calendar slice.

**5.4 Gate:** Each slice must have at least `max(20, min-bars)` bars **on every** TF or that window is skipped for that pair.

**5.5** `windows_ok` counts slices where evaluation succeeds for all three TFs.

---

## 6. One evaluation slice (champion + lab)

**6.1 Champion:** Build `ParamSet` from champion JSON fields that exist on the dataclass; merge fee/slip/fill from config.

**6.2 Lab base:** `_params_from_config(pair, bot)` with same fee/slip/fill.

**6.3 Per timeframe** `iv ∈ {5, 15, 60}`:

- Run `evaluate_params(bars, params, recency_half_life_bars=0.0, bars_per_year=...)`.
- Champion: one run.
- Lab: five runs with `mode ∈ STRATEGIES` (same tuple as `run_multiwindow_lab`).

**6.4 Scores**

- `score_exp_sqrt_n = expectancy * sqrt(max(1, trade_count))`
- `span_seconds` = wall time from first bar open through **end of last bar** (includes one bar length).
- `score_1d_eq = score_exp_sqrt_n * sqrt(86400 / span_seconds)`

**6.5 Best lab (per slice, per TF):** the lab mode with **highest `score_1d_eq`**.

**6.6 Per-window winner:** compare champion vs that best lab on **`score_1d_eq`** (strict inequality; else tie).

**6.7 USD (approx):** `pnl_USD = total_pnl * contract_size` (backtest units × configured contract size).

---

## 7. Rolling aggregation

Over successful windows, for each (pair, TF):

- **Means:** arithmetic mean of pnl_USD, win%, `score_1d_eq`, trades, bars (where collected).
- **champ_win_% / lab_win_% / tie_%:** fraction of windows by **per-window** `score_1d_eq` winner.

**Comparison chart table** (same columns as single-window run):

- **Winner** cell: higher **mean** `score_1d_eq` (champion vs best lab) — **not** the same as majority of daily winners.
- **Best lab** label: mode with **maximum count** as “best lab” across windows; trailing `*` if not unanimous across windows.
- **span_h** displayed as `window_hours` (e.g. 24).
- **trades / bars:** rounded means where applicable.

**JSON:** `<!-- JSON_SUMMARY -->` (detail rows; rolling rows may include `roll_window_idx`, `roll_window_utc`). `<!-- JSON_ROLL_AGGREGATE -->` holds per-(pair, TF) aggregates including `best_lab_mode_counts`.

---

## 8. Limitations (for reviewers)

1. **Frozen champion** — same params every window; not re-WFO’d inside this script.
2. **Oracle lab** — “lab” is the **best of five** modes **per window and TF**, not a single pre-declared mode.
3. **`score_1d_eq`** assumes roughly stable trade intensity; sparse windows (especially 60m) are noisy.
4. **`pnl_USD`** is a defined scaling of vector backtest output, not exchange-reconciled P&L.
5. **Calendar intersection** drops data outside the common `[ts0, ts1]`.
6. **Mean-score winner vs vote winner** can disagree; both views appear in output (means table vs `champ_win_%`).

---

## 9. Reproducibility checklist

- [ ] Record git commit, `config.toml`, `.env` not needed for bars-only run but venue must match data.
- [ ] Record `--lookback`, `--roll-windows`, `--window-hours`, `--min-bars`, `--champion-path`.
- [ ] Record Parquet provenance (symbols, date range on disk).
- [ ] Record `scalp_champion.json` (or hash) per symbol.

**Example command (14 × 24h rolling):**

```bash
python .optimization/pnl-feedback-lab/scripts/compare_champion_multi_timeframe.py --lookback full --roll-windows 14 --window-hours 24 --min-bars 20
```

**Sanity compile:**

```bash
python -m compileall backend/server
```

---

## 10. LLM reproduction prompt (copy-paste)

Use the block below as **system or user instructions** for another LLM that must verify or re-implement the same measurement **in this repository**.

```text
You are auditing or reproducing the "champion vs PnL lab" multi-timeframe comparison in the tradingbot-1 repo.

GOAL
- Explain and/or re-run the same experiment: saved WFO champion (scalp_champion.json ParamSet) vs five lab strategy modes on aligned 5m/15m/60m bars, with optional single last-H clip OR N disjoint H-hour rolling windows with averaged metrics.

AUTHORITATIVE CODE
- Read and trace: .optimization/pnl-feedback-lab/scripts/compare_champion_multi_timeframe.py
- Dependencies: backend/server/scalp_bot/bar_store.py (load_bars), scalp_vec_backtest.evaluate_params, scalp_wfo.load_champion_for_symbol and _params_from_config
- Lab modes must match STRATEGIES tuple in that script (same as run_multiwindow_lab.py)

REQUIRED UNDERSTANDING
1) Load 5/15/60 bars per pair; align to intersection [ts0,ts1] = [max(first_ts), min(last_ts)] across intervals; mask each series; enforce min-bars per TF.
2) Single-window mode: optional --last-hours clips from end; re-intersect all TFs. Rolling mode: --roll-windows N and --window-hours H; plan half-open windows [t, t+H) ending at ts1; slice with _slice_aligned_range; skip window if any TF below max(20, min-bars).
3) For each evaluated slice and each TF: run champion ParamSet once; run lab base with each of five modes; recency_half_life_bars=0 always.
4) score_exp_sqrt_n = expectancy * sqrt(max(1, trades)); span_seconds = first open to end of last bar; score_1d_eq = score_exp_sqrt_n * sqrt(86400/span_seconds).
5) Best lab = argmax score_1d_eq among five modes. Per-window winner = compare champion vs best lab on score_1d_eq.
6) pnl_USD = total_pnl * contract_size from pair config.
7) Rolling aggregates: arithmetic means of numeric series over successful windows; champ_win_% = fraction of windows where champion wins on score_1d_eq. The markdown "comparison chart" row winner uses HIGHER MEAN score_1d_eq (not majority vote). Best lab column = most frequent best-lab mode across windows, * if not unanimous.

REPRODUCTION STEPS
- From repo root: pip install -r backend/requirements.txt if needed.
- Run: python .optimization/pnl-feedback-lab/scripts/compare_champion_multi_timeframe.py --lookback full --roll-windows 14 --window-hours 24 --min-bars 20
- Confirm output contains: per-pair rolling stats, Winner summary table, JSON_SUMMARY and JSON_ROLL_AGGREGATE blocks.

DELIVERABLE
- Summarize whether your trace matches the steps above; list any divergence or ambiguity found in code.
- If reproducing numerically, state exact CLI args, champion JSON snapshot, and bar file date range; paste or reference the aggregate table and note limitations (oracle lab, mean-score vs vote, 60m sparsity).
```

---

## 11. Human-readable summary (one paragraph)

The script loads aligned multi-timeframe OHLCV, optionally cuts the last *H* hours or *N* non-overlapping *H*-hour windows from the end of that aligned range, and for each (pair, timeframe) runs the **frozen champion** and **five lab modes** through the same vector backtest with identical fees, slippage, fill model, and `recency_half_life_bars=0`. Strategies are ranked by a **1-day-equivalent score** derived from expectancy and trade count, scaled by sample span. The lab side is the **best of the five modes per slice** (oracle). Rolling output **averages** P&L, win rate, scores, and trades across valid windows and reports both **per-window win rates** (champion vs lab by score) and a **summary table** where the declared winner uses **mean** score — which reviewers should not conflate with majority-vote across windows.

---

*Document path: `tools/P&L audit/PEER_REVIEW_METHODOLOGY.md`*
