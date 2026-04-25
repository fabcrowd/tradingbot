# Compare — run `<run_id>`

**Git:** `<sha>`  
**Lens B (H-xxx):** `<path to report.md or N/A>`

---

## 1. What we tested

| Field | Value |
|-------|--------|
| Command | `...` |
| Artifacts | `runs/<run_id>/lab.jsonl`, `lab.stderr.txt` |
| Strategies / variants compared | e.g. `daviddtech_scalp`, `ema_momentum`, … × each pair |
| Focus mode in §2–§3 | When `strategy_mode` is `auto` in config, reports use `[scalp] auto_mode_fallback` (default `ema_momentum`), not DaviddTech unless the champion or manual mode says so |
| Time windows | e.g. early / mid / late = first/middle/last third of bar index |
| Bar interval(s) | e.g. 15m operating; 5m/60m if attempted |
| Simulation contract | venue, `fill_model`, `fee_bps_per_leg`, `slippage_bps` (from JSON header) |

---

## 2. PnL impact during the test windows

**Units:** `<backtester internal / USD / contracts — state explicitly>`

### `<PAIR_KEY>` (symbol)

| Window | Best mode (by lab score) | total_pnl | trades | profit factor |
|--------|--------------------------|-----------|--------|---------------|
| early | | | | |
| mid | | | | |
| late | | | | |

**Worst slice:** …  
**Thinnest sample:** …  

*(Repeat per pair or attach summary table.)*

---

## 3. How we validated

| Check | Result |
|-------|--------|
| G1 min trades (≥ N per window where required) | pass / fail — detail |
| G3 profit factor ≥ 1 where required | … |
| RULE C (≥2/3 windows positive primary, etc.) | … |
| Dual lens (B vs A) | CORROBORATED / REFUTED / DEFERRED — one sentence |

**What this run does not prove:** …

---

## 4. Recommended optimizations

1. … (tied to §2 or §3)  
2. …  
3. …
