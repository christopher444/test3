"""Unit tests for streaming CSV transformation and durable WMS-sized batch fan-out.

The suite verifies exact batch boundaries, transformed payloads, queue pointers,
and run metadata without requiring S3/SQS/DynamoDB or LocalEmu.  A filesystem
ObjectStore and recording queue make the business behavior directly observable.
"""

from __future__ import annotations

import csv
from dataclasses import replace

from app.config import settings
from app.services.batch_producer import enqueue_export
from app.state import MemoryStateStore


FIELDS = ["id", "name", "category", "price", "currency", "updated_at"]


def _write_export(path, count):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for index in range(count):
            writer.writerow(
                {
                    "id": f"P{index:07d}",
                    "name": f"Product {index}",
                    "category": "TEST",
                    "price": "10.50",
                    "currency": "INR",
                    "updated_at": "2026-09-25T08:30:00Z",
                }
            )


def test_enqueue_splits_export_into_exact_wms_sized_batches(filesystem_store, recording_queue, tmp_path):
    cfg = replace(
        settings,
        runtime_dir=str(tmp_path / "runtime"),
        s3_prefix="catalogue-sync",
        batch_size=100,
        wms_max_in_flight=4,
    )
    export = tmp_path / "catalogue.csv"
    _write_export(export, 205)
    export_key = "catalogue-sync/exports/run-1/catalogue.csv"
    filesystem_store.put_file(export, export_key)
    state = MemoryStateStore()
    state.create_run("run-1")

    products, batches = enqueue_export(
        "run-1", export_key, filesystem_store, state, recording_queue, cfg
    )

    assert products == 205
    assert batches == 3
    assert sorted(len(payload) for _, payload in filesystem_store.json_puts) == [5, 100, 100]
    assert len(recording_queue.sent) == 3
    assert sorted(pointer.product_count for pointer in recording_queue.sent) == [5, 100, 100]
    assert all(pointer.s3_key.startswith("catalogue-sync/work/run-1/batches/") for pointer in recording_queue.sent)

    run = state.get_run("run-1")
    assert run["queued_products"] == 205
    assert run["total_batches"] == 3
    assert run["enqueue_status"] == "COMPLETE"


def test_enqueued_payload_is_transformed_to_wms_contract(filesystem_store, recording_queue, tmp_path):
    cfg = replace(settings, runtime_dir=str(tmp_path / "runtime"), batch_size=100, wms_max_in_flight=4)
    export = tmp_path / "catalogue.csv"
    _write_export(export, 1)
    export_key = "catalogue-sync/exports/run-2/catalogue.csv"
    filesystem_store.put_file(export, export_key)
    state = MemoryStateStore()
    state.create_run("run-2")

    enqueue_export("run-2", export_key, filesystem_store, state, recording_queue, cfg)

    payload = filesystem_store.json_puts[0][1]
    assert payload == [
        {
            "sku": "P0000000",
            "description": "Product 0",
            "selling_price": 10.5,
            "currency": "INR",
            "category_code": "TEST",
            "source_updated_at": "2026-09-25T08:30:00Z",
        }
    ]


def test_empty_export_creates_no_batches_but_completes_enqueue(filesystem_store, recording_queue, tmp_path):
    cfg = replace(settings, runtime_dir=str(tmp_path / "runtime"), batch_size=100, wms_max_in_flight=4)
    export = tmp_path / "catalogue.csv"
    _write_export(export, 0)
    export_key = "catalogue-sync/exports/run-empty/catalogue.csv"
    filesystem_store.put_file(export, export_key)
    state = MemoryStateStore()
    state.create_run("run-empty")

    products, batches = enqueue_export(
        "run-empty", export_key, filesystem_store, state, recording_queue, cfg
    )

    assert (products, batches) == (0, 0)
    assert recording_queue.sent == []
    assert filesystem_store.json_puts == []
    run = state.get_run("run-empty")
    assert run["queued_products"] == 0
    assert run["total_batches"] == 0
    assert run["enqueue_status"] == "COMPLETE"
