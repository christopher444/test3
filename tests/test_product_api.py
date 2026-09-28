"""Unit tests for paginated PIM retrieval, authentication and transient recovery.

The tests cover API-key/header propagation, bounded page size, numeric-page
parallel fetching, sequential fallback when total-count metadata is absent,
HTTP 429/5xx recovery, transport failure recovery and non-retryable 4xx errors.
No LocalEmu or network service is required.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from app.clients.product_api import ProductApiClient
from app.config import settings


def _cfg(**overrides):
    values = {
        "product_api_url": "http://pim.test",
        "page_size": 2,
        "pim_rate_per_second": 10000,
        "pim_max_in_flight": 3,
        "max_retries": 2,
    }
    values.update(overrides)
    return replace(settings, **values)


def _collect_pages(cfg, transport):
    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return [item async for item in ProductApiClient(cfg, client).iter_pages()]

    return asyncio.run(run())


def test_request_uses_api_key_and_configured_page_size():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={"products": [], "page": 1, "page_size": 2, "total": 0, "has_next": False},
        )

    _collect_pages(_cfg(product_api_key="secret-pim-key"), httpx.MockTransport(handler))

    assert len(seen) == 1
    assert seen[0].headers["X-API-Key"] == "secret-pim-key"
    assert seen[0].url.params["page"] == "1"
    assert seen[0].url.params["page_size"] == "2"


def test_numeric_pagination_streams_every_page_and_retries_503():
    calls: dict[int, int] = {}

    async def handler(request):
        page = int(request.url.params["page"])
        calls[page] = calls.get(page, 0) + 1
        if page == 2 and calls[page] == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, json={"detail": "temporary"})
        return httpx.Response(
            200,
            json={
                "products": [{"id": f"P{page}"}],
                "page": page,
                "page_size": 1,
                "total": 4,
                "has_next": page < 4,
                "next_page": page + 1 if page < 4 else None,
            },
        )

    pages = _collect_pages(_cfg(page_size=1), httpx.MockTransport(handler))

    assert sorted(page for page, _ in pages) == [1, 2, 3, 4]
    assert sorted(products[0]["id"] for _, products in pages) == ["P1", "P2", "P3", "P4"]
    assert calls[2] == 2


def test_429_is_retried_using_same_page_request():
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "slow down"})
        return httpx.Response(
            200,
            json={"products": [{"id": "P1"}], "page": 1, "page_size": 2, "total": 1},
        )

    pages = _collect_pages(_cfg(), httpx.MockTransport(handler))

    assert attempts == 2
    assert pages == [(1, [{"id": "P1"}])]


def test_transport_failure_is_retried(monkeypatch):
    attempts = 0

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr("app.clients.product_api.sleep_before_retry", no_sleep)

    async def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary connect failure", request=request)
        return httpx.Response(
            200,
            json={"products": [{"id": "P1"}], "page": 1, "page_size": 2, "total": 1},
        )

    pages = _collect_pages(_cfg(), httpx.MockTransport(handler))

    assert attempts == 2
    assert pages[0][1] == [{"id": "P1"}]


def test_missing_total_falls_back_to_next_page_sequence():
    requested_pages = []

    async def handler(request):
        page = int(request.url.params["page"])
        requested_pages.append(page)
        return httpx.Response(
            200,
            json={
                "products": [{"id": f"P{page}"}],
                "page": page,
                "page_size": 2,
                "has_next": page < 3,
                "next_page": page + 1 if page < 3 else None,
            },
        )

    pages = _collect_pages(_cfg(), httpx.MockTransport(handler))

    assert requested_pages == [1, 2, 3]
    assert [page for page, _ in pages] == [1, 2, 3]


def test_non_retryable_4xx_fails_immediately():
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, request=request, json={"detail": "bad API key"})

    with pytest.raises(httpx.HTTPStatusError):
        _collect_pages(_cfg(), httpx.MockTransport(handler))

    assert attempts == 1
