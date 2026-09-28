from __future__ import annotations

import csv
import logging
import time
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

from app.config import Settings, settings
from app.messaging import Queue
from app.models import BatchPointer
from app.services.run_lease import RunLease
from app.services.transform import transform
from app.state import StateStore
from app.storage.s3 import ObjectStore

log = logging.getLogger(__name__)


def _emit_batch(
    run_id: str,
    batch_number: int,
    products: list[dict],
    store: ObjectStore,
    state: StateStore,
    out_queue: Queue,
    cfg: Settings,
) -> None:
    batch_id = f"{run_id}-{batch_number:08d}"
    key = f"{cfg.s3_prefix}/work/{run_id}/batches/{batch_id}.json"
    store.put_json(key, products)
    state.create_batch(run_id, batch_id, len(products), key)
    out_queue.send(BatchPointer(run_id=run_id, batch_id=batch_id, s3_key=key, product_count=len(products)))


def _raise_completed(pending: set[Future], block: bool = False) -> set[Future]:
    if not pending:
        return pending
    done, remaining = wait(pending, return_when=ALL_COMPLETED if block else FIRST_COMPLETED)
    for future in done:
        future.result()
    return set(remaining)


def enqueue_export(
    run_id: str,
    export_key: str,
    store: ObjectStore,
    state: StateStore,
    out_queue: Queue,
    cfg: Settings = settings,
) -> tuple[int, int]:
    """Stream the CSV into durable WMS-sized batches without materialising the catalogue."""
    if not state.acquire_run_lock(run_id, cfg.run_lock_seconds):
        raise RuntimeError("catalogue run no longer owns the active lease")
    state.set_run_fields(run_id, status="RUNNING", enqueue_status="RUNNING")
 
    local_path = Path(cfg.runtime_dir) / "downloads" / run_id / "catalogue.csv"

    total_products = 0
    batch_number = 0
    batch: list[dict] = []
    pending: set[Future] = set()
    workers = min(32, max(4, cfg.wms_max_in_flight // 2))

    try:
        with RunLease(state, run_id, cfg) as lease:
            store.download_file(export_key, local_path)
            lease.assert_owned()

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="batch-producer") as executor:
                with local_path.open(newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        batch.append(transform(row))
                        total_products += 1
                        if len(batch) == cfg.batch_size:
                            lease.assert_owned()
                            batch_number += 1
                            pending.add(
                                executor.submit(
                                    _emit_batch,
                                    run_id,
                                    batch_number,
                                    batch,
                                    store,
                                    state,
                                    out_queue,
                                    cfg,
                                )
                            )
                            batch = []
                            if len(pending) >= workers * 8:
                                pending = _raise_completed(pending)
                    if batch:
                        lease.assert_owned()
                        batch_number += 1
                        pending.add(
                            executor.submit(
                                _emit_batch,
                                run_id,
                                batch_number,
                                batch,
                                store,
                                state,
                                out_queue,
                                cfg,
                            )
                        )
                _raise_completed(pending, block=True)
                lease.assert_owned()

            state.set_run_fields(
                run_id,
                queued_products=total_products,
                total_batches=batch_number,
                enqueue_status="COMPLETE",
            )
            log.info("catalogue enqueued", extra={"run_id": run_id, "event": "enqueue_complete"})
            return total_products, batch_number
    except Exception as exc:
        state.set_run_fields(
            run_id,
            status="FAILED",
            enqueue_status="FAILED",
            failure_type="ENQUEUE_FAILED",
            failure_reason=str(exc)[:1000],
            finished_at=int(time.time()),
        )
        state.release_run_lock(run_id)
        raise
