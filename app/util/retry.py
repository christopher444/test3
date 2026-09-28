from __future__ import annotations

import asyncio
import random


def retry_delay(attempt: int, retry_after: str | None = None, cap_seconds: float = 8.0) -> float:
    if retry_after:
        try:
            return min(cap_seconds, max(0.0, float(retry_after)))
        except ValueError:
            pass
    # Full jitter: random(0, min(cap, 2^attempt)).
    return random.uniform(0.0, min(cap_seconds, 2**attempt))


async def sleep_before_retry(attempt: int, retry_after: str | None = None) -> None:
    await asyncio.sleep(retry_delay(attempt, retry_after))
