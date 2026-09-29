from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter

from app.clients.warehouse_api import AmbiguousWarehouseOutcome, PermanentWarehouseError, SafeRetryWarehouseError, WarehouseApiClient
from app.config import Settings, settings
from app.messaging import Queue, ReceivedMessage
from app.services.run_lease import RunLease
from app.services.transform import product_version_key
from app.state import StateStore
from app.storage.s3 import ObjectStore

log = logging.getLogger(__name__)

async def _visibility_heartbeat(
    in_queue: Queue,
    message: ReceivedMessage,
    timeout_seconds: int,
    heartbeat_seconds: float,
) -> None:
    while True:
        await asyncio.sleep(heartbeat_seconds)
        await asyncio.to_thread(
            in_queue.extend_visibility,
            message,
            timeout_seconds,
        )


async def _stop_visibility_heartbeat(task: asyncio.Task | None) -> None:
    if task is None:
        return
    if task.done():
        task.result()
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def process_message(
    message: ReceivedMessage,
    store: ObjectStore,
    state: StateStore,
    in_queue: Queue,
    wms: WarehouseApiClient,
    cfg: Settings = settings,
) -> dict[str, int]:
    pointer = message.body
    run_id = pointer["run_id"]
    batch_id = pointer["batch_id"]
    heartbeat_task: asyncio.Task | None = None

    # Reset the SQS visibility window when useful work actually starts.  The
    # poller may have received the message earlier, so relying only on the
    # receive-time visibility timeout can leave too little time for processing.
    await asyncio.to_thread(
        in_queue.extend_visibility,
        message,
        cfg.sqs_visibility_timeout_seconds,
    )
    heartbeat_task = asyncio.create_task(
        _visibility_heartbeat(
            in_queue,
            message,
            cfg.sqs_visibility_timeout_seconds,
            cfg.sqs_visibility_heartbeat_seconds,
        )
    )

    try:
        products = await asyncio.to_thread(store.get_json, pointer["s3_key"])
        if not isinstance(products, list):
            raise ValueError("batch payload is not a list")

        counts: Counter[str] = Counter()
        to_send: list[dict] = []
        claimed_keys: dict[str, str] = {}

        for product in products:
            sku = str(product["sku"])
            key = product_version_key(product)
            claim = await asyncio.to_thread(state.claim_product, key, run_id, sku)
            if claim == "CLAIMED":
                to_send.append(product)
                claimed_keys[sku] = key
            elif claim == "TERMINAL":
                counts["skipped_duplicate"] += 1
            elif claim in {"AMBIGUOUS", "IN_FLIGHT"}:
                # Do not submit an item whose previous send may be in progress/unknown.
                counts["ambiguous"] += 1
                if claim == "IN_FLIGHT":
                    await asyncio.to_thread(state.mark_product, key, "AMBIGUOUS", "redelivered after a SENDING claim; remote outcome unknown")

        if to_send:
            try:
                response = await wms.send_batch(to_send)
                accepted = set(response.get("accepted") or [])
                rejected = {str(item.get("sku")): str(item.get("reason", "rejected")) for item in response.get("rejected") or []}

                for product in to_send:
                    sku = str(product["sku"])
                    key = claimed_keys[sku]
                    if sku in accepted:
                        await asyncio.to_thread(state.mark_product, key, "ACCEPTED")
                        counts["accepted"] += 1
                    elif sku in rejected:
                        await asyncio.to_thread(state.mark_product, key, "REJECTED", rejected[sku])
                        counts["rejected"] += 1
                    else:
                        await asyncio.to_thread(state.mark_product, key, "AMBIGUOUS", "WMS success response omitted product disposition")
                        counts["ambiguous"] += 1
            except SafeRetryWarehouseError:
                # The WMS contract tells us this request did not commit. Remove the claims and
                # make the queue message visible again after a short delay.
                for key in claimed_keys.values():
                    await asyncio.to_thread(state.release_product_claim, key, run_id)
                await _stop_visibility_heartbeat(heartbeat_task)
                heartbeat_task = None
                await asyncio.to_thread(in_queue.release, message, 5)
                log.warning("batch deferred for safe retry", extra={"run_id": run_id, "batch_id": batch_id, "event": "safe_retry"})
                return {"retry_deferred": 1}
            except AmbiguousWarehouseOutcome as exc:
                for key in claimed_keys.values():
                    await asyncio.to_thread(state.mark_product, key, "AMBIGUOUS", str(exc))
                counts["ambiguous"] += len(claimed_keys)
            except PermanentWarehouseError as exc:
                # Contract assumption: request-level 4xx means the batch was not accepted for
                # processing. These are integration/configuration failures, not per-record
                # business validation rejections. Release claims so a corrected future run can
                # send them, but fail this run instead of retrying blindly forever.
                for key in claimed_keys.values():
                    await asyncio.to_thread(state.release_product_claim, key, run_id)
                counts["request_failed"] += len(claimed_keys)
                log.error("WMS request rejected", extra={"run_id": run_id, "batch_id": batch_id, "event": "wms_request_failed"})

        normalized = {key: int(counts.get(key, 0)) for key in ("accepted", "rejected", "skipped_duplicate", "ambiguous", "request_failed")}
        await asyncio.to_thread(state.complete_batch, run_id, batch_id, normalized)
        await _stop_visibility_heartbeat(heartbeat_task)
        heartbeat_task = None
        await asyncio.to_thread(in_queue.delete, message.receipt_handle)
        log.info("batch complete", extra={"run_id": run_id, "batch_id": batch_id, "event": "batch_complete"})
        return normalized
    finally:
        await _stop_visibility_heartbeat(heartbeat_task)


async def drain_queue(
    run_id: str,
    store: ObjectStore,
    state: StateStore,
    in_queue: Queue,
    cfg: Settings = settings,
    wms: WarehouseApiClient | None = None,
    empty_polls_before_exit: int = 3,  # retained for API compatibility; idle timeout governs exit
) -> dict:
    """Drain run messages while maintaining the run lease and WMS rate contract."""
    if not state.acquire_run_lock(run_id, cfg.run_lock_seconds):
        raise RuntimeError("catalogue run no longer owns the active lease")

    client = wms or WarehouseApiClient(cfg)
    work_queue: asyncio.Queue[ReceivedMessage | None] = asyncio.Queue(maxsize=cfg.wms_max_in_flight)
    receive_slots = asyncio.Semaphore(cfg.wms_max_in_flight)
    
    stop = asyncio.Event()

    poll_task: asyncio.Task | None = None
    consumers: list[asyncio.Task] = []

    try:
        with RunLease(state, run_id, cfg) as lease:
            state.set_run_fields(run_id, status="RUNNING", worker_status="RUNNING")

            async def poller() -> None:
                idle_since: float | None = None
                while True:
                    lease.assert_owned()
                    await receive_slots.acquire()
                    try:
                        messages = await asyncio.to_thread(in_queue.receive, 1, 2)
                    except Exception:
                        receive_slots.release()
                        raise
                    if not messages:
                        receive_slots.release()

                    matching: list[ReceivedMessage] = []
                    for message in messages:
                        message_run_id = str(message.body.get("run_id", ""))
                        if message_run_id == run_id:
                            matching.append(message)
                            continue

                        # A failed/completed run will never be resumed by this worker.
                        # Its immutable export will be represented again by the next full
                        # catalogue run, while the product-version ledger prevents resends.
                        previous = await asyncio.to_thread(state.get_run, message_run_id)
                        if previous and previous.get("status") in {"FAILED", "COMPLETED"}:
                            await asyncio.to_thread(in_queue.delete, message.receipt_handle)
                            log.info(
                                "discarded terminal-run queue message",
                                extra={"run_id": message_run_id, "event": "stale_queue_message_deleted"},
                            )
                        else:
                            await asyncio.to_thread(in_queue.release, message, 5)
                        receive_slots.release()                            

                    if matching:
                        idle_since = None
                        for message in matching:
                            await work_queue.put(message)
                        continue

                    run_snapshot = await asyncio.to_thread(state.get_run, run_id) or {}
                    total_batches = int(run_snapshot.get("total_batches", 0))
                    completed_batches = int(run_snapshot.get("completed_batches", 0))
                    enqueue_complete = run_snapshot.get("enqueue_status") == "COMPLETE"
                    if completed_batches >= total_batches and (total_batches > 0 or enqueue_complete):
                        break

                    if idle_since is None:
                        idle_since = time.monotonic()
                    elif time.monotonic() - idle_since >= cfg.worker_max_idle_seconds:
                        break
                    await asyncio.sleep(0.25)
                stop.set()

            async def consumer() -> None:
                while True:
                    lease.assert_owned()
                    if stop.is_set() and work_queue.empty():
                        return
                    try:
                        message = await asyncio.wait_for(work_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        if message is not None:
                            await process_message(message, store, state, in_queue, client, cfg)                            
                            lease.assert_owned()
                    finally:
                        work_queue.task_done()
                        receive_slots.release()                        

            poll_task = asyncio.create_task(poller())
            consumers = [asyncio.create_task(consumer()) for _ in range(cfg.wms_max_in_flight)]
            pending: set[asyncio.Task] = {poll_task, *consumers}
            while poll_task in pending:
                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task is poll_task:
                        task.result()
                    else:
                        task.result()
                        if not stop.is_set():
                            raise RuntimeError("warehouse consumer exited before poller completed")

            join_task = asyncio.create_task(work_queue.join())
            pending = {join_task, *(task for task in consumers if not task.done())}
            while join_task in pending:
                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task is join_task:
                        task.result()
                    else:
                        task.result()
                        if not join_task.done():
                            raise RuntimeError("warehouse consumer exited before work queue drained")

            await asyncio.gather(*consumers)
            lease.assert_owned()

            run = await asyncio.to_thread(state.get_run, run_id) or {}
            ambiguous = int(run.get("ambiguous", 0))
            request_failed = int(run.get("request_failed", 0))
            total_batches = int(run.get("total_batches", 0))
            completed_batches = int(run.get("completed_batches", 0))
            enqueue_complete = run.get("enqueue_status") == "COMPLETE"

            # Empty catalogues are a valid successful business run once enqueue completed.
            all_batches_terminal = completed_batches >= total_batches and (total_batches > 0 or enqueue_complete)
            status = "COMPLETED" if ambiguous == 0 and request_failed == 0 and all_batches_terminal else "FAILED"
            await asyncio.to_thread(
                state.set_run_fields,
                run_id,
                status=status,
                worker_status="COMPLETE",
                finished_at=int(time.time()),
            )
            await asyncio.to_thread(state.release_run_lock, run_id)
            return await asyncio.to_thread(state.get_run, run_id) or {}
    except Exception as exc:
        for task in ([poll_task] if poll_task is not None else []) + consumers:
            if not task.done():
                task.cancel()
        if poll_task is not None or consumers:
            await asyncio.gather(
                *([poll_task] if poll_task is not None else []),
                *consumers,
                return_exceptions=True,
            )
        await asyncio.to_thread(
            state.set_run_fields,
            run_id,
            status="FAILED",
            worker_status="FAILED",
            failure_type="WORKER_FAILED",
            failure_reason=str(exc)[:1000],
            finished_at=int(time.time()),
        )
        await asyncio.to_thread(state.release_run_lock, run_id)
        raise
    finally:
        close = getattr(client, "aclose", None)
        if close is not None:
            await close()

