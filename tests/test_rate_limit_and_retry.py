"""Unit tests for process-local rate limiting and retry-delay policy.

The production design relies on one exporter and one WMS worker process, so the
client-side start-rate limiter is a critical guardrail for the PIM/WMS ceilings.
The retry tests also verify Retry-After handling and capped jitter behavior.
"""

from __future__ import annotations

import asyncio

import pytest

from app.util.rate_limit import AsyncRateLimiter
from app.util.retry import retry_delay


def test_rate_limiter_spaces_request_starts_at_configured_rate(monkeypatch):
    now = 100.0
    sleeps = []

    def fake_monotonic():
        return now

    async def fake_sleep(delay):
        nonlocal now
        sleeps.append(delay)
        now += delay

    monkeypatch.setattr("app.util.rate_limit.time.monotonic", fake_monotonic)
    monkeypatch.setattr("app.util.rate_limit.asyncio.sleep", fake_sleep)

    limiter = AsyncRateLimiter(10)

    async def run():
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()

    asyncio.run(run())

    assert sleeps == pytest.approx([0.1, 0.1])


def test_rate_limiter_rejects_non_positive_rate():
    with pytest.raises(ValueError, match="must be > 0"):
        AsyncRateLimiter(0)


def test_retry_after_is_honoured_but_capped():
    assert retry_delay(3, retry_after="2.5", cap_seconds=8) == 2.5
    assert retry_delay(3, retry_after="99", cap_seconds=8) == 8
    assert retry_delay(3, retry_after="-1", cap_seconds=8) == 0


def test_jitter_delay_never_exceeds_exponential_or_global_cap(monkeypatch):
    observed = []

    def fake_uniform(low, high):
        observed.append((low, high))
        return high

    monkeypatch.setattr("app.util.retry.random.uniform", fake_uniform)

    assert retry_delay(2, cap_seconds=8) == 4
    assert observed[-1] == (0.0, 4)
    assert retry_delay(10, cap_seconds=8) == 8
    assert observed[-1] == (0.0, 8)
