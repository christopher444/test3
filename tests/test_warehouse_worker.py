"""Unit tests for durable WMS worker semantics, recovery and duplicate prevention.

These tests isolate worker behavior with filesystem/in-memory collaborators.
They cover partial record rejection, duplicate redelivery, interrupted SENDING
claims, safe retry, ambiguous outcomes, permanent request failures and final run
status.  LocalEmu S3/SQS/DynamoDB integration is intentionally tested separately.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from app.clients.warehouse_api import (
    AmbiguousWarehouseOutcome,
    PermanentWarehouseError,
    SafeRetryWarehouseError,
)
from app.config import settings
from app.messaging import ReceivedMessage
from app.services.transform import product_version_key
from app.services.warehouse_worker import drain_queue, process_message
from app.state import MemoryStateStore


class PartialSuccessWms:
    def __init__(self):
        self.calls = 0

    async def send_batch(self, products):
        self.calls += 1
        accepted = [p["sku"] for p in products if not str(p["sku"]).endswith("999")]
        rejected = [
            {"sku": p["sku"], "reason": "Invalid warehouse product"}
            for p in products
            if str(p["sku"]).endswith("999")
        ]
        return {"accepted": accepted, "rejected": rejected}


class SafeRetryWms:
    async def send_batch(self, products):
        raise SafeRetryWarehouseError("request known not to have committed")


class AmbiguousWms:
    async def send_batch(self, products):
        raise AmbiguousWarehouseOutcome("response lost after request transmission")


class PermanentFailureWms:
    async def send_batch(self, products):
        raise PermanentWarehouseError("401 invalid API key")


class AcceptingWms:
    async def send_batch(self, products):
        return {"accepted": [product["sku"] for product in products], "rejected": []}

class SlowAcceptingWms:
    async def send_batch(self, products):
        await asyncio.sleep(0.04)
        return {"accepted": [product["sku"] for product in products], "rejected": []}

class OmittedDispositionWms:
    async def send_batch(self, products):
        return {"accepted": [], "rejected": []}


def _product(sku):
    return {
        "sku": sku,
        "description": f"Product {sku}",
        "selling_price": 1.0,
        "currency": "INR",
        "category_code": "TEST",
        "source_updated_at": "2026-09-25T08:30:00Z",
    }


def _prepare_message(store, state, run_id, batch_id, products, receipt_handle="r1"):
    key = f"catalogue-sync/work/{run_id}/batches/{batch_id}.json"
    store.put_json(key, products)
    state.create_run(run_id)
    state.set_run_fields(run_id, total_batches=1)
    state.create_batch(run_id, batch_id, len(products), key)
    return ReceivedMessage(
        body={"run_id": run_id, "batch_id": batch_id, "s3_key": key, "product_count": len(products)},
        receipt_handle=receipt_handle,
        message_id=receipt_handle,
    )


def test_partial_success_is_recorded_and_redelivery_does_not_resend(filesystem_store, recording_queue):
    state = MemoryStateStore()
    wms = PartialSuccessWms()
    products = [_product("P0000001"), _product("P0000999")]
    message = _prepare_message(filesystem_store, state, "run-1", "run-1-00000001", products)

    first = asyncio.run(process_message(message, filesystem_store, state, recording_queue, wms))

    assert first == {
        "accepted": 1,
        "rejected": 1,
        "skipped_duplicate": 0,
        "ambiguous": 0,
        "request_failed": 0,
    }
    assert wms.calls == 1
    assert state.get_product(product_version_key(products[0]))["status"] == "ACCEPTED"
    assert state.get_product(product_version_key(products[1]))["status"] == "REJECTED"
    assert recording_queue.extended[0] == (message, settings.sqs_visibility_timeout_seconds)
    second = asyncio.run(process_message(message, filesystem_store, state, recording_queue, wms))

    assert second["skipped_duplicate"] == 2
    assert wms.calls == 1
    run = state.get_run("run-1")
    assert run["completed_batches"] == 1
    assert run["accepted"] == 1
    assert run["rejected"] == 1

def test_long_running_batch_renews_sqs_visibility_while_processing(filesystem_store, recording_queue):
    state = MemoryStateStore()
    product = _product("P0000011")
    message = _prepare_message(filesystem_store, state, "run-heartbeat", "run-heartbeat-00000001", [product], "heartbeat-rh")
    cfg = replace(
        settings,
        sqs_visibility_timeout_seconds=5,
        sqs_visibility_heartbeat_seconds=0.01,
    )

    result = asyncio.run(
        process_message(
            message,
            filesystem_store,
            state,
            recording_queue,
            SlowAcceptingWms(),
            cfg,
        )
    )

    assert result["accepted"] == 1
    assert len(recording_queue.extended) >= 2
    assert all(timeout == 5 for _, timeout in recording_queue.extended)
    assert recording_queue.deleted == ["heartbeat-rh"]

def test_inflight_redelivery_is_quarantined_not_resent(filesystem_store, recording_queue):
    state = MemoryStateStore()
    product = _product("P0000002")
    message = _prepare_message(filesystem_store, state, "run-2", "run-2-00000001", [product], "r2")
    state.claim_product(product_version_key(product), "run-2", product["sku"])
    wms = PartialSuccessWms()

    result = asyncio.run(process_message(message, filesystem_store, state, recording_queue, wms))

    assert result["ambiguous"] == 1
    assert wms.calls == 0
    assert state.get_product(product_version_key(product))["status"] == "AMBIGUOUS"


def test_safe_retry_releases_claim_and_requeues_without_completing_batch(filesystem_store, recording_queue):
    state = MemoryStateStore()
    product = _product("P0000003")
    message = _prepare_message(filesystem_store, state, "run-3", "run-3-00000001", [product], "r3")

    result = asyncio.run(process_message(message, filesystem_store, state, recording_queue, SafeRetryWms()))

    assert result == {"retry_deferred": 1}
    assert state.get_product(product_version_key(product)) is None
    assert recording_queue.deleted == []
    assert recording_queue.released == [(message, 5)]
    assert state.get_run("run-3").get("completed_batches", 0) == 0


def test_ambiguous_request_marks_claims_ambiguous_and_completes_for_operator_recovery(filesystem_store, recording_queue):
    state = MemoryStateStore()
    products = [_product("P0000004"), _product("P0000005")]
    message = _prepare_message(filesystem_store, state, "run-4", "run-4-00000001", products, "r4")

    result = asyncio.run(process_message(message, filesystem_store, state, recording_queue, AmbiguousWms()))

    assert result["ambiguous"] == 2
    assert all(state.get_product(product_version_key(p))["status"] == "AMBIGUOUS" for p in products)
    assert recording_queue.deleted == ["r4"]
    assert state.get_run("run-4")["completed_batches"] == 1


def test_success_response_omitting_product_disposition_is_marked_ambiguous(filesystem_store, recording_queue):
    state = MemoryStateStore()
    product = _product("P0000006")
    message = _prepare_message(filesystem_store, state, "run-5", "run-5-00000001", [product], "r5")

    result = asyncio.run(process_message(message, filesystem_store, state, recording_queue, OmittedDispositionWms()))

    assert result["ambiguous"] == 1
    record = state.get_product(product_version_key(product))
    assert record["status"] == "AMBIGUOUS"
    assert "omitted product disposition" in record["reason"]


def test_request_level_4xx_releases_claim_for_corrected_future_run(filesystem_store, recording_queue):
    state = MemoryStateStore()
    product = _product("P0000007")
    message = _prepare_message(filesystem_store, state, "run-6", "run-6-00000001", [product], "r6")

    result = asyncio.run(process_message(message, filesystem_store, state, recording_queue, PermanentFailureWms()))

    assert result["request_failed"] == 1
    assert result["rejected"] == 0
    assert state.get_product(product_version_key(product)) is None
    assert state.get_run("run-6")["request_failed"] == 1


def test_drain_queue_completes_run_when_all_batches_have_terminal_non_ambiguous_outcomes(filesystem_store, recording_queue, tmp_path):
    state = MemoryStateStore()
    product = _product("P0000008")
    message = _prepare_message(filesystem_store, state, "run-7", "run-7-00000001", [product], "r7")
    assert state.acquire_run_lock("run-7") is True
    recording_queue.messages.append(message)
    cfg = replace(settings, wms_max_in_flight=2, worker_max_idle_seconds=1, read_timeout_seconds=0.1)

    run = asyncio.run(drain_queue("run-7", filesystem_store, state, recording_queue, cfg, AcceptingWms()))

    assert run["status"] == "COMPLETED"
    assert run["completed_batches"] == 1
    assert run["accepted"] == 1
    assert run["worker_status"] == "COMPLETE"
    assert state.acquire_run_lock("next-run") is True


def test_drain_queue_fails_run_when_any_outcome_is_ambiguous(filesystem_store, recording_queue):
    state = MemoryStateStore()
    product = _product("P0000009")
    message = _prepare_message(filesystem_store, state, "run-8", "run-8-00000001", [product], "r8")
    assert state.acquire_run_lock("run-8") is True
    recording_queue.messages.append(message)
    cfg = replace(settings, wms_max_in_flight=2, worker_max_idle_seconds=1, read_timeout_seconds=0.1)

    run = asyncio.run(drain_queue("run-8", filesystem_store, state, recording_queue, cfg, AmbiguousWms()))

    assert run["status"] == "FAILED"
    assert run["ambiguous"] == 1
    assert run["completed_batches"] == 1


def test_empty_catalogue_completes_without_waiting_for_idle_timeout(filesystem_store, recording_queue):
    state = MemoryStateStore()
    state.create_run("empty-run")
    state.set_run_fields("empty-run", enqueue_status="COMPLETE", total_batches=0, queued_products=0)
    cfg = replace(settings, wms_max_in_flight=2, worker_max_idle_seconds=60, read_timeout_seconds=0.1)

    run = asyncio.run(drain_queue("empty-run", filesystem_store, state, recording_queue, cfg, AcceptingWms()))

    assert run["status"] == "COMPLETED"
    assert int(run.get("completed_batches", 0)) == 0
    assert run["worker_status"] == "COMPLETE"


def test_worker_deletes_messages_from_terminal_previous_run(filesystem_store, recording_queue):
    state = MemoryStateStore()
    state.create_run("old-run")
    state.set_run_fields("old-run", status="FAILED")

    stale = ReceivedMessage(
        body={"run_id": "old-run", "batch_id": "old-batch", "s3_key": "unused", "product_count": 1},
        receipt_handle="stale-rh",
        message_id="stale-msg",
    )
    recording_queue.messages.append(stale)

    product = _product("P0000010")
    current = _prepare_message(filesystem_store, state, "new-run", "new-run-00000001", [product], "current-rh")
    recording_queue.messages.append(current)
    cfg = replace(settings, wms_max_in_flight=2, worker_max_idle_seconds=1, read_timeout_seconds=0.1)

    run = asyncio.run(drain_queue("new-run", filesystem_store, state, recording_queue, cfg, AcceptingWms()))

    assert run["status"] == "COMPLETED"
    assert "stale-rh" in recording_queue.deleted
    assert "current-rh" in recording_queue.deleted

