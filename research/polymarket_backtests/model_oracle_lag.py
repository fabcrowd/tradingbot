"""
Proxy backtest: oracle-lag style entries on 15m windows using 1m Binance closes.

Not Polymarket historical replay — structural simulation:
- "Oracle" = current minute close vs window open return.
- "Book" = stale return from `lag_min` minutes ago mapped to a synthetic YES price around 0.5.
- Entry: first minute where |r_now|>=delta, time_left>=min_left, synthetic YES/NO ask <= max_entry.
- Settlement: spot close at window end vs window open (same as directional resolution).
- PnL: notional * (1/entry - 1) on win else -notional, minus taker fee on entry shares.

This isolates sensitivity to lag/delta/fees given a liquid spot path.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from fees import round_fee, taker_fee_usdc


def _ret(p0: float, p: float) -> float:
    return (p - p0) / p0 if p0 else 0.0


def synthetic_yes_ask(ret_stale: float, gamma: float = 120.0) -> float:
    """Map stale return to YES ask in (0,1); gamma controls sensitivity."""
    x = 0.5 + gamma * ret_stale
    return min(max(x, 0.05), 0.95)


@dataclass
class OracleLagResult:
    trades: int
    wins: int
    pnl_usdc: float
    avg_fee_usdc: float


def run_oracle_lag(
    closes: list[float],
    bar_sec: int,
    window_sec: int = 900,
    lag_min: int = 1,
    min_delta: float = 0.0007,
    min_left_sec: int = 300,
    max_entry: float = 0.62,
    notional: float = 5.0,
    gamma: float = 120.0,
    half_spread: float = 0.01,
) -> OracleLagResult:
    """
    `closes` evenly spaced `bar_sec` bars. Windows are non-overlapping [0,W), [W,2W)...
    lag_min in units of bars (if bar_sec=60, lag_min=1 ~= 1 minute lag).
    """
    bars_per_win = max(1, window_sec // bar_sec)
    lag_bars = max(0, lag_min * 60 // bar_sec)

    trades = wins = 0
    pnl = 0.0
    fee_sum = 0.0

    n = len(closes)
    w = 0
    while (w + 1) * bars_per_win <= n:
        start = w * bars_per_win
        end = start + bars_per_win
        p0 = closes[start]
        settled_up = closes[end - 1] >= p0
        traded = False
        for i in range(start + 1, end):
            if traded:
                break
            left_sec = (end - i) * bar_sec
            if left_sec < min_left_sec:
                break
            r_now = _ret(p0, closes[i])
            idx_stale = max(start, i - lag_bars)
            r_stale = _ret(p0, closes[idx_stale])
            yes_ask = synthetic_yes_ask(r_stale, gamma) + half_spread
            no_ask = synthetic_yes_ask(-r_stale, gamma) + half_spread  # NO maps like opposite return

            if abs(r_now) < min_delta:
                continue

            if r_now > 0:
                entry = yes_ask
                side_win = settled_up
            else:
                entry = no_ask
                side_win = not settled_up

            if entry > max_entry:
                continue

            shares = notional / entry
            fee = round_fee(taker_fee_usdc(shares, entry))
            fee_sum += fee
            trades += 1
            traded = True
            if side_win:
                wins += 1
                pnl += shares * (1.0 - entry) - fee
            else:
                pnl += -notional - fee

        w += 1

    avg_fee = fee_sum / trades if trades else 0.0
    return OracleLagResult(trades=trades, wins=wins, pnl_usdc=pnl, avg_fee_usdc=avg_fee)


def run_oracle_lag_window_slice(
    closes: list[float],
    bar_sec: int,
    window_sec: int,
    win_start: int,
    win_end: int,
    **kwargs,
) -> OracleLagResult:
    """Run only on 15m windows with index in [win_start, win_end)."""
    bars_per_win = max(1, window_sec // bar_sec)
    n = len(closes)
    max_w = (n // bars_per_win) if bars_per_win else 0
    win_end = min(win_end, max_w)
    trades = wins = 0
    pnl = 0.0
    fee_sum = 0.0
    for w in range(win_start, win_end):
        start = w * bars_per_win
        end = start + bars_per_win
        p0 = closes[start]
        settled_up = closes[end - 1] >= p0
        traded = False
        lag_min = kwargs.get("lag_min", 1)
        min_delta = kwargs.get("min_delta", 0.0007)
        min_left_sec = kwargs.get("min_left_sec", 300)
        max_entry = kwargs.get("max_entry", 0.62)
        notional = kwargs.get("notional", 5.0)
        gamma = kwargs.get("gamma", 120.0)
        half_spread = kwargs.get("half_spread", 0.01)
        lag_bars = max(0, lag_min * 60 // bar_sec)
        for i in range(start + 1, end):
            if traded:
                break
            left_sec = (end - i) * bar_sec
            if left_sec < min_left_sec:
                break
            r_now = _ret(p0, closes[i])
            idx_stale = max(start, i - lag_bars)
            r_stale = _ret(p0, closes[idx_stale])
            yes_ask = synthetic_yes_ask(r_stale, gamma) + half_spread
            no_ask = synthetic_yes_ask(-r_stale, gamma) + half_spread
            if abs(r_now) < min_delta:
                continue
            if r_now > 0:
                entry = yes_ask
                side_win = settled_up
            else:
                entry = no_ask
                side_win = not settled_up
            if entry > max_entry:
                continue
            shares = notional / entry
            fee = round_fee(taker_fee_usdc(shares, entry))
            fee_sum += fee
            trades += 1
            traded = True
            if side_win:
                wins += 1
                pnl += shares * (1.0 - entry) - fee
            else:
                pnl += -notional - fee
    avg_fee = fee_sum / trades if trades else 0.0
    return OracleLagResult(trades=trades, wins=wins, pnl_usdc=pnl, avg_fee_usdc=avg_fee)


def random_entry_baseline(
    closes: list[float],
    bar_sec: int,
    window_sec: int = 900,
    min_left_sec: int = 300,
    notional: float = 5.0,
    seed: int = 0,
) -> OracleLagResult:
    """Pick random minute in each window (with enough time left), random YES/NO, entry ~0.55."""
    rng = random.Random(seed)
    bars_per_win = max(1, window_sec // bar_sec)
    trades = wins = 0
    pnl = 0.0
    fee_sum = 0.0
    n = len(closes)
    w = 0
    while (w + 1) * bars_per_win <= n:
        start = w * bars_per_win
        end = start + bars_per_win
        p0 = closes[start]
        settled_up = closes[end - 1] >= p0
        candidates = [i for i in range(start + 1, end) if (end - i) * bar_sec >= min_left_sec]
        if not candidates:
            w += 1
            continue
        i = rng.choice(candidates)
        buy_yes = rng.random() < 0.5
        entry = 0.52 + rng.random() * 0.10
        side_win = settled_up if buy_yes else not settled_up
        shares = notional / entry
        fee = round_fee(taker_fee_usdc(shares, entry))
        fee_sum += fee
        trades += 1
        if side_win:
            wins += 1
            pnl += shares * (1.0 - entry) - fee
        else:
            pnl += -notional - fee
        w += 1
    avg_fee = fee_sum / trades if trades else 0.0
    return OracleLagResult(trades=trades, wins=wins, pnl_usdc=pnl, avg_fee_usdc=avg_fee)


def sweep(
    closes: list[float],
    bar_sec: int,
) -> list[dict]:
    rows = []
    for lag_min in (0, 1, 2, 3):
        for delta in (0.0003, 0.0007, 0.0012):
            for max_entry in (0.58, 0.62, 0.66):
                r = run_oracle_lag(
                    closes,
                    bar_sec,
                    lag_min=lag_min,
                    min_delta=delta,
                    max_entry=max_entry,
                )
                wr = r.wins / r.trades if r.trades else 0.0
                rows.append(
                    {
                        "lag_min": lag_min,
                        "min_delta": delta,
                        "max_entry": max_entry,
                        "trades": r.trades,
                        "win_rate": round(wr, 4),
                        "pnl_usdc": round(r.pnl_usdc, 2),
                        "avg_fee": round(r.avg_fee_usdc, 4),
                    }
                )
    return rows
