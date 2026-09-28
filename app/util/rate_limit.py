from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    """Process-local start-rate limiter.

    A single exporter and a single WMS worker task are deliberate architecture choices,
    so a process-local limiter is sufficient and avoids a distributed-rate-limit service.
    """

    def __init__(self, rate_per_second: float):
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be > 0")
        self._interval = 1.0 / rate_per_second
        self._next_start = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_start - now)
            if delay:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._next_start = max(now, self._next_start) + self._interval
