from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.logging_utils import configure_logging
from app.messaging import MemoryQueue, queue
from app.services.batch_producer import enqueue_export
from app.services.catalogue_export import export_catalogue
from app.services.warehouse_worker import drain_queue
from app.state import MemoryStateStore, state_store
from app.storage.s3 import object_store


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


async def _run_local(run_id: str) -> dict:
    # Same process so in-memory queue/state remain available; filesystem is the durable artifact boundary.
    store = object_store(settings)
    state = MemoryStateStore()
    q = MemoryQueue()
    export_key, _ = await export_catalogue(run_id, store, state, settings)
    enqueue_export(run_id, export_key, store, state, q, settings)
    return await drain_queue(run_id, store, state, q, settings)


async def _main_async(args: argparse.Namespace) -> int:
    configure_logging(args.log_level)

    if args.command == "run-local":
        result = await _run_local(args.run_id or _run_id())
        print(json.dumps(result, default=str, indent=2))
        return 0 if result.get("status") == "COMPLETED" else 2

    store = object_store(settings)
    state = state_store(settings)

    if args.command == "export":
        run_id = args.run_id or _run_id()
        key, count = await export_catalogue(run_id, store, state, settings)
        print(json.dumps({"run_id": run_id, "export_key": key, "count": count}))
        return 0

    if args.command == "enqueue":
        run = state.get_run(args.run_id) or {}
        export_key = args.export_key or run.get("export_key")
        if not export_key:
            raise SystemExit("export key not found; run export first or pass --export-key")
        q = queue(settings)
        products, batches = enqueue_export(args.run_id, str(export_key), store, state, q, settings)
        print(json.dumps({"run_id": args.run_id, "products": products, "batches": batches}))
        return 0

    if args.command == "worker":
        q = queue(settings)
        result = await drain_queue(args.run_id, store, state, q, settings)
        print(json.dumps(result, default=str, indent=2))
        return 0 if result.get("status") == "COMPLETED" else 2

    if args.command == "status":
        run = state.get_run(args.run_id)
        print(json.dumps(run or {"run_id": args.run_id, "status": "NOT_FOUND"}, default=str, indent=2))
        return 0 if run else 1

    raise SystemExit(f"unknown command {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Product catalogue synchronisation")
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    run_local = sub.add_parser("run-local", help="Run the full pipeline in one process")
    run_local.add_argument("--run-id")

    export = sub.add_parser("export", help="Export PIM catalogue to immutable CSV object")
    export.add_argument("--run-id")

    enqueue = sub.add_parser("enqueue", help="Transform the CSV into WMS-sized durable batches")
    enqueue.add_argument("--run-id", required=True)
    enqueue.add_argument("--export-key")

    worker = sub.add_parser("worker", help="Drain durable batches into the WMS")
    worker.add_argument("--run-id", required=True)

    status = sub.add_parser("status", help="Show durable run status")
    status.add_argument("--run-id", required=True)
    return parser


def main() -> int:
    return asyncio.run(_main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
