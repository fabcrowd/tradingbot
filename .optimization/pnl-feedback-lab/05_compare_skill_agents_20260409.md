# Compare — run `skill_agents_20260409`

**Git:** `203c2d1`  
**Lens B:** `research/H-LIVE-NEG-20260408/report.md` (verdict: **mixed**)

---

## 1. What we tested

| Field | Value |
|-------|--------|
| **Command** | `python .optimization/pnl-feedback-lab/scripts/run_multiwindow_lab.py --intervals 5,15,60` |
| **Artifacts** | `runs/skill_agents_20260409/lab.jsonl`, `runs/skill_agents_20260409/lab.stderr.txt` |
| **Compared** | Five strategy modes on **shared params from `[scalp]`** via vec backtest: `daviddtech_scalp`, `ema_momentum`, `rsi_reversion`, `ema_scalp`, `macd_scalp` — evaluated on **each** of **BTC_USD**, **SOL_USD**, **XRP_USD** (CDE symbols per config). |
| **Time windows** | **Early / mid / late** = first, middle, and last **third** of the loaded bar series (bar index ranges in JSONL per pair). |
| **Intervals** | **15m:** full run for all three pairs. **5m and 60m:** **not run** (missing Parquet — stderr lines `# skip … insufficient or missing parquet`). |
| **Simulation contract** | `coinbase_perps`, fill **`next_open`**, **0** fee bps/leg, **1** bps slippage (from `lab.jsonl` header). |

**Not compared:** live fills, funding, WFO grid variants, param tuner paths — this is **one-shot historical vec** on frozen bars.

---

## 2. PnL impact during the test windows

**Units:** `total_pnl` here is the **backtest engine’s internal PnL** for the sim (same units the vec backtest uses for the contract — **not** labeled as live USD). Use for **relative** comparison across modes/windows, not as guaranteed dollar PnL.

### BTC_USD (BIP-20DEC30-CDE), 15m

| Window | Highest score mode (`score_exp_sqrt_n`) | total_pnl | trades | profit factor |
|--------|----------------------------------------|-----------|--------|----------------|
| early | daviddtech_scalp | **+1301.28** | 3 | 28.69 |
| mid | rsi_reversion * | **+725.69** | 2 | — |
| late | ema_momentum | **+2544.90** | 22 | 1.94 |

\*Mid window: rsi_reversion edges daviddtech on score with **n=2** (unstable). **macd_scalp** mid: **+1332.53**, n=19, PF 1.65 — stronger sample than rsi at n=2.

**daviddtech_scalp alone (operator-relevant default mode):**

| Window | total_pnl | trades | profit factor |
|--------|-----------|--------|----------------|
| early | +1301.28 | 3 | 28.69 |
| mid | +3.13 | 1 | — |
| late | **−22.71** | 2 | **0.98** |

**Worst slice for daviddtech:** **late** (negative net, PF &lt; 1, **only 2 trades**).  
**Thinnest samples:** daviddtech **mid n=1**, several alt rows **n=1–2**.

### SOL_USD (SLP-20DEC30-CDE), 15m — best mode by window (from stderr SUMMARY)

| Window | Best mode | total_pnl (that mode) | trades |
|--------|-----------|------------------------|--------|
| early | daviddtech_scalp | +0.92 | 2 |
| mid | daviddtech_scalp | +0.29 | 1 |
| late | ema_momentum | +4.48 | 19 |

All SOL `total_pnl` magnitudes are **small** vs BTC in these units.

### XRP_USD (XPP-20DEC30-CDE), 15m — best mode by window

| Window | Best mode | total_pnl (that mode) | trades |
|--------|-----------|------------------------|--------|
| early | ema_momentum | +0.025 | 25 |
| mid | rsi_reversion | +0.006 | 1 |
| late | daviddtech_scalp | +0.020 | 2 |

XRP is **near-zero** net across modes; statistical noise dominates.

---

## 3. How we validated

| Check | Result |
|-------|--------|
| **Multi-window tape (RULE B)** | **Yes** — three disjoint slices per pair on 15m. |
| **G1 (min trades ≥5 for “steady” claims)** | **Fail for daviddtech on BTC** — only **one** window (early) has n≥3; mid n=1, late n=2. Cannot call daviddtech “steady PnL” on this test. |
| **G3 (PF ≥ 1 when finite)** | **Fail** for daviddtech **late** BTC (PF 0.98). |
| **RULE C-style “positive in ≥2/3 windows” for daviddtech BTC** | **Fail** — late window negative; mid essentially flat. |
| **Dual lens (H-LIVE-NEG)** | **CORROBORATED (exploratory):** Lens B allowed “small-n + late-window weakness”; Lens A shows **daviddtech late BTC negative** with **n=2**, matching the story of **a few bad trades** in a weak slice. Mode-mismatch / WFO story stays **open** until session + `scalp_champion.json` are mapped. |

**What this run does not prove:** live execution quality; that WFO’s **train/holdout** splits match these **thirds**; profitability after fees if promo ends; any 5m/60m behavior (no data).

---

## 4. Recommended optimizations

1. **WFO / champion alignment** — If live should track **best OOS mode**, compare **champion `mode`** to these per-window winners; tighten **promotion gates** so saved champions do not carry **negative latest holdout** when your definition of “winning” requires positive OOS (code change + new lab run).

2. **Do not treat daviddtech as validated on BTC** from this tape alone — **late window loss + n=2** fails sensible guards; either **accept regime risk**, **switch mode by window** (only if product supports it), or **require more trades** before trading in “late-like” regimes.

3. **Raise sample standards for CONFIRMED** — e.g. require **min trades per window ≥5** (or stricter) on the operating interval before labeling a strategy production-ready; document in `03_experiment_plan.md`.

4. **Add 5m / 60m Parquet** (or drop interval flags) so `--intervals 5,15,60` is a real comparison, not repeated skips.

5. **Fix / quiet `scalp_vec_backtest` empty-slice warnings** — reduces stderr noise so **impact tables** stay the focus.

6. **Optional: single “operator summary” row** — For each pair, print **daviddtech-only** 3-window PnL row in `05_compare` by default so config-default mode is visible without reading full JSONL.
