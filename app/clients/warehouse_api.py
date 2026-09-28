from __future__ import annotations

import asyncio

import httpx

from app.config import Settings, settings
from app.util.rate_limit import AsyncRateLimiter
from app.util.retry import sleep_before_retry


class AmbiguousWarehouseOutcome(RuntimeError):
    """The request may have been committed by the WMS; retrying could duplicate products."""


class SafeRetryWarehouseError(RuntimeError):
    """The WMS is known not to have processed the request; releasing claims is safe."""


class PermanentWarehouseError(RuntimeError):
    pass


class WarehouseApiClient:
    def __init__(self, cfg: Settings = settings, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=cfg.http_timeout)
        self._limiter = AsyncRateLimiter(cfg.wms_rate_per_second)
        self._sem = asyncio.Semaphore(cfg.wms_max_in_flight)

    async def send_batch(self, products: list[dict]) -> dict:
        if len(products) > self.cfg.batch_size:
            raise ValueError(f"batch contains {len(products)} products; max is {self.cfg.batch_size}")

        for attempt in range(self.cfg.max_retries + 1):
            await self._limiter.acquire()
            try:
                async with self._sem:
                    response = await self._client.post(
                        f"{self.cfg.warehouse_api_url}/products/batch",
                        json={"products": products},
                        headers={"X-API-Key": self.cfg.warehouse_api_key},
                    )
            except httpx.ConnectError:
                # Connection establishment failed before an HTTP response existed; safe to retry.
                if attempt >= self.cfg.max_retries:
                    raise SafeRetryWarehouseError("WMS connection could not be established")
                await sleep_before_retry(attempt)
                continue
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Once transmission may have started, the remote commit state is unknowable.
                raise AmbiguousWarehouseOutcome(str(exc)) from exc

            if response.status_code == 429:
                # Assumption documented in ARCHITECTURE.md: 429 means rejected before processing.
                if attempt >= self.cfg.max_retries:
                    raise SafeRetryWarehouseError("WMS rate limit retries exhausted; request was not processed")
                await sleep_before_retry(attempt, response.headers.get("Retry-After"))
                continue

            if response.status_code in {500, 502, 503, 504}:
                if self.cfg.retry_wms_5xx:
                    if attempt >= self.cfg.max_retries:
                        raise SafeRetryWarehouseError("WMS 5xx retries exhausted under configured safe-retry contract")
                    await sleep_before_retry(attempt, response.headers.get("Retry-After"))
                    continue
                raise AmbiguousWarehouseOutcome(
                    f"WMS returned {response.status_code}; response semantics do not prove no commit"
                )

            if 400 <= response.status_code < 500:
                raise PermanentWarehouseError(
                    f"WMS request rejected: {response.status_code} {response.text[:500]}"
                )

            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                # A 2xx means the WMS may already have committed the request. Without a
                # disposition body, replaying it would risk duplicates.
                raise AmbiguousWarehouseOutcome("WMS returned 2xx with an unreadable response body") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("accepted"), list) or not isinstance(payload.get("rejected"), list):
                raise AmbiguousWarehouseOutcome("WMS returned 2xx without accepted/rejected disposition lists")
            return payload
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


BATCH_SIZE = settings.batch_size
