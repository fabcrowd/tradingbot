# AUTO-GENERATED review excerpt from scalp_vec_backtest.py
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ----- simulate_trades_rsi -----

# ----- scalp_vec_backtest.py lines 853–1065 -----
def detect_signals_rsi(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    rsi_period: int,
    atr_period: int,
    rsi_buy_threshold: float,
    rsi_sell_threshold: float,
    *,
    rsi_short_threshold: float = 70.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """RSI mean-reversion: long on oversold, short on overbought (perps).

    Returns (long_mask, short_mask, atr_vals, rsi_vals).
    rsi_sell_threshold is used by the long-exit simulator (RSI recovery).
    rsi_short_threshold gates short entries (default 70 = overbought).
    """
    rsi_vals = rsi(close, rsi_period)
    atr_vals = atr(high, low, close, atr_period)

    long_mask = (~np.isnan(rsi_vals)) & (rsi_vals <= rsi_buy_threshold)
    long_mask &= ~np.isnan(atr_vals) & (atr_vals > 0)

    short_mask = (~np.isnan(rsi_vals)) & (rsi_vals >= rsi_short_threshold)
    short_mask &= ~np.isnan(atr_vals) & (atr_vals > 0)

    warmup = vec_warmup_prefix_len(
        "rsi_reversion",
        SimpleNamespace(rsi_period=rsi_period, atr_period=atr_period),
    )
    long_mask[:warmup] = False
    short_mask[:warmup] = False

    return long_mask, short_mask, atr_vals, rsi_vals


def simulate_trades_rsi(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    long_mask: np.ndarray,
    short_mask: np.ndarray,
    atr_vals: np.ndarray,
    rsi_vals: np.ndarray,
    *,
    open_prices: np.ndarray | None = None,
    rsi_sell_threshold: float = 50.0,
    rsi_short_cover_threshold: float = 30.0,
    atr_stop_mult: float = 1.5,
    atr_tp_mult: float = 1.5,
    max_hold_bars: int = 15,
    fee_pct: float = 0.0,
    contract_size: float = 1.0,
    fee_usd_per_contract_per_leg: float = 0.0,
    slippage_pct: float = 0.0001,
    cooldown_bars: int = 1,
    fill_model: str = "close_slip",
) -> list[TradeResult]:
    """RSI mean-reversion: long and short entries for perps.

    Long exits use ATR TP (``entry + atr * atr_tp_mult``) like live ``SignalEngine``.
    Same-bar ambiguity when both stop and TP print resolves with `_intrabar_stop_first`
    if ``open_prices`` exists; otherwise **stop first** — matches ``simulate_trades_bidir``
    behavior when OHLC lacks opens.
    """
    n = len(close)
    trades: list[TradeResult] = []
    next_allowed = 0
    has_open = open_prices is not None
    use_next_open = fill_model == "next_open" and has_open

    for i in range(n):
        if i < next_allowed:
            continue

        if long_mask[i] and short_mask[i]:
            # Defensive — only reachable with mis/overlapping buy vs short RSI thresholds.
            continue

        a = atr_vals[i]
        if np.isnan(a) or a <= 0:
            continue

        if long_mask[i]:
            if fill_model == "next_open":
                if not has_open:
                    if i + 1 >= n:
                        continue
                    entry_price = close[i + 1] * (1.0 + slippage_pct)
                elif i + 1 >= n:
                    continue
                else:
                    entry_price = open_prices[i + 1] * (1.0 + slippage_pct)
            else:
                entry_price = close[i] * (1.0 + slippage_pct)
            stop_price = entry_price - a * atr_stop_mult
            tp_price = entry_price + a * atr_tp_mult

            if stop_price >= entry_price:
                continue
            if tp_price <= entry_price:
                continue

            exit_bar = min(i + max_hold_bars, n - 1)
            exit_price = close[exit_bar]
            exit_reason = "time_stop"

            slip = float(slippage_pct)
            for j in range(i + 1, min(i + max_hold_bars + 1, n)):
                stop_hit = low[j] <= stop_price
                tp_hit = high[j] >= tp_price

                if stop_hit and tp_hit:
                    if has_open:
                        o = float(open_prices[j])
                        stop_first = _intrabar_stop_first(o, float(high[j]), float(low[j]), 1)
                    else:
                        stop_first = True
                    if stop_first:
                        exit_price = stop_price * (1.0 - slip)
                        exit_reason = "stop"
                        exit_bar = j
                    else:
                        exit_price = tp_price * (1.0 - slip)
                        exit_reason = "tp"
                        exit_bar = j
                    break
                if stop_hit:
                    exit_price = stop_price * (1.0 - slip)
                    exit_reason = "stop"
                    exit_bar = j
                    break
                if tp_hit:
                    exit_price = tp_price * (1.0 - slip)
                    exit_reason = "tp"
                    exit_bar = j
                    break
                if not np.isnan(rsi_vals[j]) and rsi_vals[j] >= rsi_sell_threshold:
                    exit_price = close[j] * (1.0 - slip)
                    exit_reason = "rsi_exit"
                    exit_bar = j
                    break

            _g, _fc, net_pnl = _roundtrip_gross_fee_net(
                entry_price,
                exit_price,
                1,
                fee_pct,
                contract_size,
                fee_usd_per_contract_per_leg,
            )

            trades.append(TradeResult(
                entry_bar=i,
                exit_bar=exit_bar,
                entry_price=entry_price,
                exit_price=exit_price,
                stop_price=stop_price,
                tp_price=tp_price,
                pnl=net_pnl,
                is_win=net_pnl > 0,
                exit_reason=exit_reason,
                hold_bars=exit_bar - i,
            ))
            next_allowed = exit_bar + cooldown_bars
            continue

        if short_mask[i]:
            if fill_model == "next_open":
                if not has_open:
                    if i + 1 >= n:
                        continue
                    entry_price = close[i + 1] * (1.0 - slippage_pct)
                elif i + 1 >= n:
                    continue
                else:
                    entry_price = open_prices[i + 1] * (1.0 - slippage_pct)
            else:
                entry_price = close[i] * (1.0 - slippage_pct)
            stop_price = entry_price + a * atr_stop_mult
            tp_price = entry_price - a * atr_tp_mult

            if tp_price >= entry_price:
                continue

            exit_bar = min(i + max_hold_bars, n - 1)
            exit_price = close[exit_bar]
            exit_reason = "time_stop"

            slip = float(slippage_pct)
            for j in range(i + 1, min(i + max_hold_bars + 1, n)):
                stop_hit = high[j] >= stop_price
                tp_hit = low[j] <= tp_price

                if stop_hit and tp_hit:
                    if has_open:
                        o = float(open_prices[j])
                        stop_first = _intrabar_stop_first(o, float(high[j]), float(low[j]), -1)
                    else:
                        stop_first = True
                    if stop_first:
                        exit_price = stop_price * (1.0 + slip)
                        exit_reason = "stop"
                        exit_bar = j
                    else:
                        exit_price = tp_price * (1.0 + slip)
                        exit_reason = "tp"
                        exit_bar = j
                    break
                if stop_hit:
                    exit_price = stop_price * (1.0 + slip)
                    exit_reason = "stop"
                    exit_bar = j
