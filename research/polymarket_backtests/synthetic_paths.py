"""Correlated GBM 1m paths (pure stdlib) when live data fetch is unavailable."""
from __future__ import annotations

import json
import math
import random
from pathlib import Path


def correlated_paths(
    n: int,
    *,
    rho: float = 0.86,
    sigma_per_min: float = 0.00035,
    seed: int = 7,
) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    btc = [1.0]
    eth = [1.0]
    for _ in range(n - 1):
        z1 = rng.gauss(0, 1)
        z2 = rng.gauss(0, 1)
        rb = sigma_per_min * z1
        re = sigma_per_min * (rho * z1 + math.sqrt(max(0.0, 1.0 - rho * rho)) * z2)
        btc.append(btc[-1] * math.exp(rb))
        eth.append(eth[-1] * math.exp(re))
    return btc, eth


def write_synthetic(out_btc: Path, out_eth: Path, days: int = 30, seed: int = 7) -> int:
    n = days * 24 * 60
    btc, eth = correlated_paths(n, seed=seed)
    t0 = 1_700_000_000
    ts = [t0 + i * 60 for i in range(n)]
    klines_b = [[ts[i] * 1000, "0", "0", "0", str(btc[i]), "0"] for i in range(n)]
    klines_e = [[ts[i] * 1000, "0", "0", "0", str(eth[i]), "0"] for i in range(n)]
    out_btc.parent.mkdir(parents=True, exist_ok=True)
    out_btc.write_text(json.dumps({"symbol": "BTCUSDT", "interval": "1m", "klines": klines_b}))
    out_eth.write_text(json.dumps({"symbol": "ETHUSDT", "interval": "1m", "klines": klines_e}))
    return n
