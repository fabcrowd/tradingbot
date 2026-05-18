Pine exports mirroring backend/server/scalp_bot/scalp_vec_backtest.py entry logic.

Trading timeframe (required):
- **5-minute** chart on TradingView for every mode — this matches live scalp trading (`ScalpPairConfig.interval` default **5**).
- `max_hold_bars` is bar-count: default 15 → **75 minutes** wall time at 5m (same semantics as `simulate_trades_bidir` time stop).

Sharing for parity review (one mode per thread works best):
- Paste the Pine script for that mode (pine/by_mode/TradingBotScalp__<mode>.pine).
- Paste detect_signals_* (and any helpers it relies on) from scalp_vec_backtest.py.
- Shared numeric helpers (ema, atr, rsi, …) live at the top of scalp_vec_backtest.py — reference once, then mode-specific code.

Files:
- REVIEW_HANDOFF_FOR_LLM.txt — paste or attach for another model doing parity review (paths + checklist).
- review_packages/ — one folder per strategy + _shared/ for LLM bundles (run python pine/package_review_folders.py).
- package_review_folders.py — builds review_packages/ from by_mode + scalp_vec_backtest excerpts.
- TradingBotScalp_AllModes.pine — paste into TradingView; use input “strategy_mode” to pick any registered mode.
- pine/by_mode/TradingBotScalp__<mode>.pine — same script with MODE_OPT fixed (eleven files).
- _gen_by_mode.py — regenerate by_mode/ after editing TradingBotScalp_AllModes.pine (python _gen_by_mode.py).

Defaults align with backend/server/scalp_bot/scalp_config.py where applicable.

Parity notes:
- ATR uses the bot’s SMA-seeded smoothing (not TradingView ta.atr RMA).
- daviddtech ADX uses ta.dmi (Wilder-style); may differ slightly from numpy adx_wilder.
- Exits: ATR stop / ATR TP / max_hold_bars; optional counter_signal_exit input.
- RSI mode follows simulate_trades_rsi (long exits omit unused +10% TP from Python).
