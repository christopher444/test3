#!/usr/bin/env python3
"""Exercise stale-run lease recovery against LocalEmu DynamoDB."""

from __future__ import annotations

import time
import uuid

from app.config import settings
from app.state import DynamoStateStore


def main() -> int:
    state = DynamoStateStore(settings)
    suffix = uuid.uuid4().hex[:8]
    old_run = f"recovery-old-{suffix}"
    new_run = f"recovery-new-{suffix}"

    try:
        state.create_run(old_run)
        assert state.acquire_run_lock(old_run, lease_seconds=1), "could not acquire old-run lease"
        time.sleep(1.2)

        state.create_run(new_run)
        assert state.acquire_run_lock(new_run, lease_seconds=30), "expired lease was not reclaimed"

        previous = state.get_run(old_run) or {}
        assert previous.get("status") == "FAILED", previous
        assert previous.get("failure_type") == "INTERRUPTED", previous
        assert previous.get("recovered_by_run_id") == new_run, previous

        print("LocalEmu stale-run recovery smoke: PASS")
        return 0
    finally:
        state.release_run_lock(new_run)
        for run_id in (old_run, new_run):
            state.client.delete_item(
                TableName=settings.runs_table,
                Key=state._item({"run_id": run_id}),
            )


if __name__ == "__main__":
    raise SystemExit(main())
