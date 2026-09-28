from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator

import httpx

from app.config import Settings, settings
from app.util.rate_limit import AsyncRateLimiter
from app.util.retry import sleep_before_retry


class ProductApiClient:
    def __init__(self, cfg: Settings = settings, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self._external_client = client
        self._limiter = AsyncRateLimiter(cfg.pim_rate_per_second)
        self._sem = asyncio.Semaphore(cfg.pim_max_in_flight)

    async def _request_page(self, client: httpx.AsyncClient, page: int) -> dict:
        for attempt in range(self.cfg.max_retries + 1):
            await self._limiter.acquire()
            async with self._sem:
                try:
                    response = await client.get(
                        f"{self.cfg.product_api_url}/products",
                        params={"page": page, "page_size": self.cfg.page_size},
                        headers={"X-API-Key": self.cfg.product_api_key},
                    )
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt >= self.cfg.max_retries:
                        raise
                    await sleep_before_retry(attempt)
                    continue

            if response.status_code in {429, 500, 502, 503, 504}:
                if attempt >= self.cfg.max_retries:
                    response.raise_for_status()
                await sleep_before_retry(attempt, response.headers.get("Retry-After"))
                continue

            response.raise_for_status()
            return response.json()
        raise RuntimeError("unreachable")

    async def iter_pages(self) -> AsyncIterator[tuple[int, list[dict]]]:
        """Yield catalogue pages without accumulating the catalogue in memory.

        The production fast path assumes stable numeric page addressing and a total count,
        as exhibited by the supplied mock. If total is absent, it safely falls back to
        following has_next sequentially; this protects correctness at the cost of throughput.
        """
        owns_client = self._external_client is None
        client = self._external_client or httpx.AsyncClient(timeout=self.cfg.http_timeout)
        try:
            first = await self._request_page(client, 1)
            yield 1, first.get("products", [])

            total = first.get("total")
            if isinstance(total, int) and total >= 0:
                page_count = max(1, math.ceil(total / self.cfg.page_size))
                async def tagged(page_no: int) -> tuple[int, dict]:
                    return page_no, await self._request_page(client, page_no)

                # Keep control-plane memory bounded too: never create one asyncio Task per
                # catalogue page. A small sliding window is enough because the rate limiter,
                # not task creation, determines request throughput.
                next_page = 2
                window = max(1, self.cfg.pim_max_in_flight * 2)
                pending: set[asyncio.Task[tuple[int, dict]]] = set()
                while next_page <= page_count or pending:
                    while next_page <= page_count and len(pending) < window:
                        pending.add(asyncio.create_task(tagged(next_page)))
                        next_page += 1
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        page_no, result = await task
                        # Catalogue ordering has no business meaning, so completed pages can be streamed immediately.
                        yield page_no, result.get("products", [])
                return

            current = first
            page = 1
            while current.get("has_next"):
                page = int(current.get("next_page") or page + 1)
                current = await self._request_page(client, page)
                yield page, current.get("products", [])
        finally:
            if owns_client:
                await client.aclose()


# Backwards-compatible helper retained for simple callers/tests. It is intentionally not
# used by the production export path because it materializes the whole catalogue.
async def fetch_all_products(cfg: Settings = settings) -> list[dict]:
    products: list[dict] = []
    async for _, page in ProductApiClient(cfg).iter_pages():
        products.extend(page)
    return products


PAGE_SIZE = settings.page_size
