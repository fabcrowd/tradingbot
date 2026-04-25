# Champion atr_tp_mult validation summary

- Lookbacks: `wfo,full`
- Champion file: `C:\Users\daroo\Desktop\Repos\tradingbot-1\data\scalp_champion.json`

## `BTC_USD` (BIP-20DEC30-CDE)

### Lookback `wfo`

**No fully robust tp.** Best effort (>=2 segments with min trades, full window profitable): `atr_tp_mult=5.0` (full pnl_USD=53.49567884227043, full Sharpe=9.624957993508957). Still **not validated** on all early/mid/late segments.

- Disk champion **atr_tp_mult=5.0** matches or is consistent with validation pick.

### Lookback `full`

**Robust pick:** `atr_tp_mult=6.0` (min segment Sharpe > 0, min segment pnl_USD > 0, all segments meet min trades).

- Disk champion uses **atr_tp_mult=5.0** - validation suggests **6.0** on this tape.

---

## `SOL_USD` (SLP-20DEC30-CDE)

### Lookback `wfo`

**No fully robust tp.** Best effort (>=2 segments with min trades, full window profitable): `atr_tp_mult=5.0` (full pnl_USD=23.515543739784235, full Sharpe=6.142381095672814). Still **not validated** on all early/mid/late segments.

- Disk champion **atr_tp_mult=5.0** matches or is consistent with validation pick.

### Lookback `full`

**No robust tp and no profitable full-window candidate** in the grid (largest full Sharpe was `atr_tp_mult=6.0` with full pnl_USD=-4.092016614937889). Extend lookback, widen grid, or treat TP as unsettled.

- **No** profitable full-window `atr_tp_mult` in this grid; do not treat diagnostics as a promotion (disk **atr_tp_mult=5.0**).

---

## `XRP_USD` (XPP-20DEC30-CDE)

### Lookback `wfo`

**No fully robust tp.** Best effort (>=2 segments with min trades, full window profitable): `atr_tp_mult=3.0` (full pnl_USD=36.28676505731276, full Sharpe=8.11527689689216). Still **not validated** on all early/mid/late segments.

- Disk champion **atr_tp_mult=3.0** matches or is consistent with validation pick.

### Lookback `full`

**No fully robust tp.** Best effort (>=2 segments with min trades, full window profitable): `atr_tp_mult=4.0` (full pnl_USD=18.877144490469956, full Sharpe=0.4555991907918912). Still **not validated** on all early/mid/late segments.

- Disk champion uses **atr_tp_mult=3.0** - validation suggests **4.0** on this tape.

---
