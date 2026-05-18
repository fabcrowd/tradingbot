from pathlib import Path

src_path = Path(__file__).with_name("TradingBotScalp_AllModes.pine")
src = src_path.read_text(encoding="utf-8")
modes = [
    "daviddtech_scalp",
    "ema_momentum",
    "ema_scalp",
    "macd_scalp",
    "rsi_reversion",
    "supertrend",
    "squeeze_momentum",
    "qqe_mod",
    "utbot_alert",
    "hull_suite",
    "sar_chop",
]
old_head = """// === Mode (matches WFO_REGISTERED_STRATEGY_MODES) =============================
MODE_OPT = input.string(\"daviddtech_scalp\", \"strategy_mode\",
     options = array.from(
         \"daviddtech_scalp\", \"ema_momentum\", \"ema_scalp\", \"macd_scalp\", \"rsi_reversion\",
         \"supertrend\", \"squeeze_momentum\", \"qqe_mod\", \"utbot_alert\", \"hull_suite\", \"sar_chop\"))"""
out_dir = Path(__file__).with_name("by_mode")
out_dir.mkdir(parents=True, exist_ok=True)
for m in modes:
    block = f'// === Mode (fixed) ============================================================\nMODE_OPT = "{m}"'
    text = src.replace(
        'strategy("Trading Bot Scalp — All registered modes (5m)",',
        f'strategy("TB scalp: {m} (5m)",',
        1,
    )
    if old_head not in text:
        raise SystemExit("header block not found — regenerate script")
    text = text.replace(old_head, block, 1)
    (out_dir / f"TradingBotScalp__{m}.pine").write_text(text, encoding="utf-8")
print("wrote", len(modes), "files to", out_dir)
