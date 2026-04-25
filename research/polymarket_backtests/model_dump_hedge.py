"""
Dump-hedge structural arb (YES+NO < 1) requires historical bid/ask for both legs.

Without Polymarket L2 history, we only report a toy sensitivity:
probability of random walk mids summing below threshold under iid noise (illustrative).
"""
from __future__ import annotations

import random


def toy_dump_frequency(
    n_rounds: int = 100_000,
    mid0: float = 0.5,
    sigma: float = 0.08,
    sum_target: float = 0.96,
    seed: int = 42,
) -> dict:
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_rounds):
        y = mid0 + rng.gauss(0, sigma)
        n = mid0 + rng.gauss(0, sigma)
        y = min(max(y, 0.05), 0.95)
        n = min(max(n, 0.05), 0.95)
        # illustrative "ask" = mid + half spread
        y_ask = y + 0.02
        n_ask = n + 0.02
        if y_ask + n_ask < sum_target:
            hits += 1
    return {
        "n_rounds": n_rounds,
        "p_hit": hits / n_rounds,
        "note": "Toy model only — not calibrated to Polymarket microstructure",
    }
