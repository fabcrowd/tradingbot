"""Async token bucket rate limiter for exchange / REST message pacing."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, rate_per_sec: float, burst: int) -> None:
        self._rate_per_sec = max(0.1, float(rate_per_sec))
        self._burst = max(1, int(burst))
        self._tokens = float(self._burst)
        self._last_refill = time.monotonic()
        self._penalty_until = 0.0
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_refill)
        self._last_refill = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate_per_sec)

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                self._refill()
                now = time.monotonic()
                if now < self._penalty_until:
                    sleep_for = self._penalty_until - now
                elif self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                else:
                    needed = 1.0 - self._tokens
                    sleep_for = needed / self._rate_per_sec
            await asyncio.sleep(max(0.005, sleep_for))

    def penalize(self, seconds: float = 30.0) -> None:
        """Apply a penalty cooldown. Safe to call from any coroutine on the same loop."""
        now = time.monotonic()
        until = now + max(1.0, seconds)
        self._penalty_until = max(self._penalty_until, until)

    def snapshot(self) -> dict[str, float]:
        return {
            "tokens": round(self._tokens, 3),
            "rate_per_sec": self._rate_per_sec,
            "burst": self._burst,
            "penalty_remaining_sec": max(0.0, self._penalty_until - time.monotonic()),
        }
