from __future__ import annotations

import csv
import logging
import time
from pathlib import Path

from app.clients.product_api import ProductApiClient
from app.config import Settings, settings
from app.services.run_lease import RunLease, RunLeaseLost
from app.state import StateStore
from app.storage.s3 import ObjectStore

log = logging.getLogger(__name__)
FIELDS = ["id", "name", "category", "price", "currency", "updated_at"]


async def export_catalogue(
    run_id: str,
    store: ObjectStore,
    state: StateStore,
    cfg: Settings = settings,
    client: ProductApiClient | None = None,
) -> tuple[str, int]:
    """Stream one logical PIM export into an immutable CSV source artifact."""
    if not state.acquire_run_lock(run_id, cfg.run_lock_seconds):
        raise RuntimeError("another catalogue synchronisation run is active")
    state.create_run(run_id)
    state.set_run_fields(run_id, status="RUNNING", export_status="RUNNING")

    export_dir = Path(cfg.runtime_dir) / "exports" / run_id
    export_dir.mkdir(parents=True, exist_ok=True)
    local_path = export_dir / "catalogue.csv"
    count = 0
    pim = client or ProductApiClient(cfg)

    try:
         with RunLease(state, run_id, cfg) as lease:
            with local_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                async for page, products in pim.iter_pages():
                    lease.assert_owned()
                    writer.writerows(products)
                    count += len(products)
                    log.info(
                        "exported PIM page",
                        extra={"run_id": run_id, "event": "pim_page", "batch_id": str(page)},
                    )

            lease.assert_owned()
            key = f"{cfg.s3_prefix}/exports/{run_id}/catalogue.csv"
            store.put_file(local_path, key)
            lease.assert_owned()
            state.set_run_fields(
                run_id,
                export_key=key,
                exported_products=count,
                export_status="COMPLETE",
            )
            return key, count
    except RunLeaseLost as exc:
        state.set_run_fields(
            run_id,
            status="FAILED",
            export_status="FAILED",
            failure_type="INTERRUPTED",
            failure_reason=str(exc),
            finished_at=int(time.time()),
        )
        state.release_run_lock(run_id)
        raise
    except Exception as exc:
        state.set_run_fields(
            run_id,
            status="FAILED",
            export_status="FAILED",
            failure_type="EXPORT_FAILED",
            failure_reason=str(exc)[:1000],
            finished_at=int(time.time()),
        )

        state.release_run_lock(run_id)
        raise
