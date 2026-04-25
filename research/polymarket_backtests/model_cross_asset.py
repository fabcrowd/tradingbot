"""
Cross-asset proxy: use BTC returns to predict ETH direction (lead/lag on 1m bars).

Models:
  M1_contemp: sign(ret_eth[t-H:t]) predicted by sign(ret_btc[t-H:t]) — same window.
  M2_lead: sign(ret_eth[t:t+H]) predicted by sign(ret_btc[t-H:t]) — BTC leads.
  M3_lag: sign(ret_eth[t-H:t]) predicted by sign(ret_btc[t-2H:t-H]) — older BTC window.

Baseline: always predict majority class (up) frequency.

Eval: accuracy, balanced accuracy, and a naive "trade EV" if we bet notional at synthetic 0.55
implied odds when model fires (not Polymarket-realistic; for ranking models only).
"""
from __future__ import annotations

from dataclasses import dataclass


def aligned_series(
    ts_a: list[int],
    v_a: list[float],
    ts_b: list[int],
    v_b: list[float],
) -> tuple[list[float], list[float]]:
    j = 0
    out_a: list[float] = []
    out_b: list[float] = []
    for i, t in enumerate(ts_a):
        while j + 1 < len(ts_b) and ts_b[j] < t:
            j += 1
        if ts_b[j] != t:
            continue
        out_a.append(v_a[i])
        out_b.append(v_b[j])
    return out_a, out_b


def forward_return(closes: list[float], t: int, h: int) -> float | None:
    if t + h >= len(closes):
        return None
    p0 = closes[t]
    p1 = closes[t + h]
    if p0 == 0:
        return None
    return (p1 - p0) / p0


@dataclass
class ClassifierMetrics:
    n: int
    accuracy: float
    baseline_accuracy: float
    precision_pos: float
    recall_pos: float


def eval_binary(y_true: list[int], y_pred: list[int]) -> ClassifierMetrics:
    n = len(y_true)
    assert n == len(y_pred) and n > 0
    correct = sum(1 for a, b in zip(y_true, y_pred) if a == b)
    base = sum(y_true) / n
    baseline = max(base, 1 - base)  # always predict majority
    tp = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 1)
    fp = sum(1 for a, b in zip(y_true, y_pred) if a == 0 and b == 1)
    fn = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 0)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return ClassifierMetrics(
        n=n,
        accuracy=correct / n,
        baseline_accuracy=baseline,
        precision_pos=precision,
        recall_pos=recall,
    )


def run_models(
    btc: list[float],
    eth: list[float],
    h: int = 15,
) -> dict[str, ClassifierMetrics]:
    """h = horizon in bars (15 ~= 15 minutes on 1m data)."""
    y1: list[int] = []
    y2: list[int] = []
    y3: list[int] = []
    pred_contemp: list[int] = []
    pred_lead: list[int] = []
    pred_lag: list[int] = []

    t0 = 2 * h
    for t in range(t0, min(len(btc), len(eth)) - h):
        re_b = forward_return(btc, t - h, h)
        re_e_same = forward_return(eth, t - h, h)
        re_e_fwd = forward_return(eth, t, h)
        re_b_old = forward_return(btc, t - 2 * h, h)
        if re_b is None or re_e_same is None or re_e_fwd is None or re_b_old is None:
            continue
        p = 1 if re_b > 0 else 0
        y1.append(1 if re_e_same > 0 else 0)
        y2.append(1 if re_e_fwd > 0 else 0)
        y3.append(1 if re_e_same > 0 else 0)
        pred_contemp.append(p)
        pred_lead.append(p)
        pred_lag.append(1 if re_b_old > 0 else 0)

    return {
        "M1_contemp_same_window": eval_binary(y1, pred_contemp),
        "M2_btc_leads_eth_next_H": eval_binary(y2, pred_lead),
        "M3_btc_old_predicts_eth_same": eval_binary(y3, pred_lag),
    }


def sweep_horizons(btc: list[float], eth: list[float], horizons: tuple[int, ...]) -> list[dict]:
    rows = []
    for h in horizons:
        m = run_models(btc, eth, h=h)
        for name, met in m.items():
            rows.append(
                {
                    "H_bars": h,
                    "model": name,
                    "n": met.n,
                    "accuracy": round(met.accuracy, 4),
                    "baseline": round(met.baseline_accuracy, 4),
                    "edge_vs_baseline": round(met.accuracy - met.baseline_accuracy, 4),
                    "precision_pos": round(met.precision_pos, 4),
                    "recall_pos": round(met.recall_pos, 4),
                }
            )
    return rows
