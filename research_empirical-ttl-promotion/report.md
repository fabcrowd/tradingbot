# Report: Empirical TTL-cancel promotion — literature + code-accurate behavior

## Conclusions (answer-first)

1. **Microstructure:** Unfilled limits are normal under **price–time priority**, **queuing uncertainty**, and **adverse selection** at the back of the queue; frequent **cancel/add** dynamics are consistent with equilibrium depth behavior ([Queuing Uncertainty of Limit Orders](https://doi.org/10.1287/mnsc.2023.03371)).
2. **Escalation:** Hybrid **limit-then-market** policies are standard in execution theory: balance **fee/price improvement** vs **non-fill risk** ([Reinforcement Learning for Trade Execution with Market and Limit Orders](https://arxiv.org/abs/2507.06345)).
3. **Fees:** **Taker** pays for **immediacy**; it is rational when **opportunity cost of waiting** exceeds **extra fee + spread** ([Market Makers vs. Market Takers](https://www.cmegroup.com/education/courses/trading-and-analysis/market-makers-vs-market-takers); structural background [SEC maker–taker memo PDF](https://www.sec.gov/spotlight/emsac/memo-maker-taker-fees-on-equities-exchanges.pdf)).
4. **This bot:** Two arms coexist: **pattern-based** promotion (evidence of favorable missed move + count window + cooldown) vs **TTL-direct** promotion (market slots on every TTL cancel). TTL-direct **skips** the empirical “was the move favorable?” filter and **stacks** with pattern arms—so it is the main candidate for **over-taker** behavior if TTL cancels are common but **not** informative.

**Low confidence:** Mapping equity/CME/SEC framing **directly** to Coinbase CDE fee tiers and fill dynamics is **indicative**, not proven—venue rules and toxicity differ.

---

## Code-accurate description (this repo — Fabcrowd Arceus)

### Config (current `config.toml` grep snapshot)

- `empirical_market_promotion_enabled = true`
- `empirical_market_ttl_cancel_arms_promotion = true`
- `empirical_market_ttl_cancel_promotion_entries = 1`
- `empirical_market_missed_move_bps = 12.0`
- `empirical_market_miss_eval_window_sec = 600.0`
- `empirical_market_min_pattern_in_window = 2`
- `empirical_market_pattern_window_sec = 86400.0`
- `empirical_market_promotion_entries = 2`
- `empirical_market_promotion_cooldown_sec = 3600.0`

(Operator may change these; cite live file for audits.)

### `resolve_order_type(pair_key)`

- If base `order_type` is `hybrid`, it is treated as **limit-first** for resolution (`hybrid` → `limit`).
- If `order_type` is `market`, returns `("market", False)` (no promotion flag).
- If empirical promotion is **disabled**, returns configured type and `used_promotion=False`.
- If `promotion_remaining[pair_key] > 0`, returns **`("market", True)`**; else limit.

```69:81:backend/server/scalp_bot/empirical_market_promotion.py
    def resolve_order_type(self, pair_key: str) -> tuple[str, bool]:
        """Return (order_type, used_promotion) for the next entry."""
        base = str(getattr(self._cfg, "order_type", "limit") or "limit").lower().strip()
        if base == "hybrid":
            # Prefer maker first; empirical promotion upgrades to market after TTL + missed-move pattern.
            base = "limit"
        if base == "market":
            return "market", False
        if not self._enabled():
            return base, False
        if self._promotion_remaining.get(pair_key, 0) > 0:
            return "market", True
        return "limit", False
```

### TTL cancel path: watch + optional TTL-direct arm

- `note_entry_ttl_cancel` always (when enabled) appends a **missed-move watch** with deadline `now + empirical_market_miss_eval_window_sec` and logs `entry_ttl_cancel` via session logger.
- Immediately after, `_maybe_arm_promotion_on_ttl_cancel` runs: if `empirical_market_ttl_cancel_arms_promotion`, it **increments** `promotion_remaining` by `empirical_market_ttl_cancel_promotion_entries` and logs `empirical_market_promotion_armed` with `arm_reason="ttl_cancel"` and `pattern_hits=0`.

```92:165:backend/server/scalp_bot/empirical_market_promotion.py
    def note_entry_ttl_cancel(
        ...
    ) -> None:
        """Start watching for favorable drift after a limit entry TTL cancel."""
        ...
        self._watches.append(
            _MissedMoveWatch(
                pair_key=pair_key,
                symbol=str(symbol or "").strip(),
                direction=str(direction or "long").lower(),
                limit_px=lp,
                deadline=deadline,
            )
        )
        ...
        self._maybe_arm_promotion_on_ttl_cancel(pair_key, session_log=session_log)

    def _maybe_arm_promotion_on_ttl_cancel(
        self, pair_key: str, *, session_log: Any | None = None
    ) -> None:
        """Arm market-entry burst immediately after TTL cancel (optional; see config)."""
        ...
        if not bool(getattr(self._cfg, "empirical_market_ttl_cancel_arms_promotion", False)):
            return
        add = max(1, int(getattr(self._cfg, "empirical_market_ttl_cancel_promotion_entries", 1)))
        self._promotion_remaining[pair_key] = self._promotion_remaining.get(pair_key, 0) + add
        ...
            session_log.log_scalp(
                "empirical_market_promotion_armed",
                pair_key=pair_key,
                promotion_entries=add,
                arm_reason="ttl_cancel",
                pattern_hits=0,
                pattern_window_sec=0.0,
            )
```

### Pattern path: favorable drift → rolling count → arm (cooldown)

- On each mark update, watches compute favorable **bps vs limit**; if ≥ `empirical_market_missed_move_bps` before deadline, append pattern event and call `_try_arm_promotion`.
- `_try_arm_promotion` counts events for that `pair_key` in `empirical_market_pattern_window_sec`; if count ≥ `empirical_market_min_pattern_in_window` and cooldown elapsed, adds `empirical_market_promotion_entries` to `promotion_remaining` and logs `empirical_market_promotion_armed` **without** `arm_reason` (pattern hits recorded).

### Live placement (`scalp_trader.py`)

- **Only when `use_exchange`** (live manager, not sim): `resolve_order_type` is used; sim path bypasses empirical resolution.
- Promoted market entries log `entry_market_promoted` and decrement burst via `after_promoted_market_entry`; all entries log `entry_placed` with `empirical_promoted` boolean.

```667:720:backend/server/scalp_bot/scalp_trader.py
        use_exchange = self._live_mgr is not None and not self._sim_mode
        if use_exchange:
            order_type, used_promotion = self._empirical.resolve_order_type(signal.pair_key)
        else:
            ot = str(self._cfg.order_type or "limit").lower().strip()
            order_type = "limit" if ot == "hybrid" else ot
            used_promotion = False
        ...
            if order_type == "market" and used_promotion:
                ...
                self._empirical.after_promoted_market_entry(signal.pair_key)
                if self._session_log is not None:
                    self._session_log.log_scalp(
                        "entry_market_promoted",
                        pair_key=signal.pair_key,
                        symbol=signal.symbol,
                        direction=signal.direction,
                        qty=round(qty, 8),
                    )