Review package: ema_scalp
==============================

Chart / Pine
  strategy.pine — paste into TradingView Strategy Editor (5-minute chart).

Bot entry logic (numpy)
  bot_excerpt_scalp_vec_backtest.py — helpers + detect_signals_* for this mode only
  (line-number markers inside file).

Warmup / first-bar masking
  ../_shared/indicator_warmup.py — search mode string "ema_scalp".

Human-readable spec
  ../_shared/strategies.md — section starting "## 3." for this mode.

Exit simulation (WFO / vec)
  ../_shared/simulate_trades_bidir_review_bundle.py
  (ATR stop / TP / time / optional counter-exit path)

Full module (if you need full context)
  ../../../backend/server/scalp_bot/scalp_vec_backtest.py

Multi-mode Pine (dropdown)
  ../../TradingBotScalp_AllModes.pine

Global reviewer briefing
  ../_shared/REVIEW_HANDOFF_FOR_LLM.txt
