"""Unit tests for WMS batching, authentication, retry safety and ambiguous outcomes.

The WMS has a hard 100-product batch limit, 20 requests/second ceiling, partial
success responses and a no-duplicate business requirement.  These tests focus
on the HTTP-result semantics that decide whether retrying is safe or dangerous.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from app.clients.warehouse_api import (
    AmbiguousWarehouseOutcome,
    PermanentWarehouseError,
    SafeRetryWarehouseError,
    WarehouseApiClient,
)
from app.config import settings


def _cfg(**overrides):
    return replace(
        settings,
        warehouse_api_url="http://wms.test",
        batch_size=100,
        wms_rate_per_second=10000,
        max_retries=1,
        **overrides,
    )


def _send(cfg, transport, products=None):
    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return await WarehouseApiClient(cfg, client).send_batch(products or [{"sku": "P1"}])

    return asyncio.run(run())


def test_request_uses_api_key_and_expected_batch_envelope():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"accepted": ["P1"], "rejected": []})

    result = _send(_cfg(warehouse_api_key="secret-wms-key"), httpx.MockTransport(handler))

    assert result["accepted"] == ["P1"]
    assert seen[0].headers["X-API-Key"] == "secret-wms-key"
    assert seen[0].url.path == "/products/batch"
    assert b'"products"' in seen[0].content


def test_batch_larger_than_contract_is_rejected_before_http_call():
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"accepted": [], "rejected": []})

    with pytest.raises(ValueError, match="max is 100"):
        _send(_cfg(), httpx.MockTransport(handler), [{"sku": f"P{i}"} for i in range(101)])

    assert attempts == 0


def test_429_is_safely_retried_then_succeeds():
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "slow down"})
        return httpx.Response(200, json={"accepted": ["P1"], "rejected": []})

    result = _send(_cfg(), httpx.MockTransport(handler))

    assert result == {"accepted": ["P1"], "rejected": []}
    assert attempts == 2


def test_connect_failure_retries_and_becomes_safe_retry_error(monkeypatch):
    attempts = 0

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr("app.clients.warehouse_api.sleep_before_retry", no_sleep)

    async def handler(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(SafeRetryWarehouseError, match="connection could not be established"):
        _send(_cfg(), httpx.MockTransport(handler))

    assert attempts == 2


def test_timeout_after_possible_transmission_is_ambiguous_and_not_retried():
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("response lost", request=request)

    with pytest.raises(AmbiguousWarehouseOutcome):
        _send(_cfg(), httpx.MockTransport(handler))

    assert attempts == 1


def test_5xx_is_ambiguous_by_default_and_not_retried():
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"detail": "unavailable"})

    with pytest.raises(AmbiguousWarehouseOutcome):
        _send(_cfg(), httpx.MockTransport(handler))

    assert attempts == 1


def test_5xx_can_retry_only_when_safe_retry_contract_is_explicitly_enabled():
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, json={"detail": "temporary"})
        return httpx.Response(200, json={"accepted": ["P1"], "rejected": []})

    result = _send(_cfg(retry_wms_5xx=True), httpx.MockTransport(handler))

    assert result["accepted"] == ["P1"]
    assert attempts == 2


def test_request_level_4xx_is_permanent_integration_error():
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, text="invalid API key")

    with pytest.raises(PermanentWarehouseError, match="401"):
        _send(_cfg(), httpx.MockTransport(handler))

    assert attempts == 1


@pytest.mark.parametrize(
    "response",
    [
        lambda: httpx.Response(200, text="not-json"),
        lambda: httpx.Response(200, json={"accepted": ["P1"]}),
        lambda: httpx.Response(200, json={"accepted": "P1", "rejected": []}),
    ],
)
def test_unusable_2xx_disposition_is_ambiguous(response):
    async def handler(request):
        return response()

    with pytest.raises(AmbiguousWarehouseOutcome):
        _send(_cfg(), httpx.MockTransport(handler))
