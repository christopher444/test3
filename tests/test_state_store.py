"""Unit tests for run coordination, product claims and exactly-once batch accounting.

The in-memory StateStore mirrors the state transitions required from DynamoDB:
one active business run, product-version claim/terminal states, safe claim release,
and idempotent batch completion so queue redelivery cannot double-count results.
"""

from app.state import MemoryStateStore


def test_run_lock_allows_one_owner_and_can_be_released():
    state = MemoryStateStore()

    assert state.acquire_run_lock("run-1") is True
    assert state.acquire_run_lock("run-1") is True
    assert state.acquire_run_lock("run-2") is False

    state.release_run_lock("run-1")
    assert state.acquire_run_lock("run-2") is True


def test_product_claim_lifecycle_distinguishes_terminal_ambiguous_and_inflight():
    state = MemoryStateStore()

    assert state.claim_product("P1#v1", "run-1", "P1") == "CLAIMED"
    assert state.claim_product("P1#v1", "run-2", "P1") == "IN_FLIGHT"

    state.mark_product("P1#v1", "AMBIGUOUS", "unknown remote outcome")
    assert state.claim_product("P1#v1", "run-2", "P1") == "AMBIGUOUS"

    assert state.claim_product("P2#v1", "run-1", "P2") == "CLAIMED"
    state.mark_product("P2#v1", "ACCEPTED")
    assert state.claim_product("P2#v1", "run-2", "P2") == "TERMINAL"


def test_safe_claim_release_only_removes_matching_sending_owner():
    state = MemoryStateStore()
    state.claim_product("P1#v1", "run-1", "P1")

    state.release_product_claim("P1#v1", "another-run")
    assert state.get_product("P1#v1") is not None

    state.release_product_claim("P1#v1", "run-1")
    assert state.get_product("P1#v1") is None


def test_complete_batch_updates_run_counters_exactly_once():
    state = MemoryStateStore()
    state.create_run("run-1")
    state.create_batch("run-1", "batch-1", 2, "work/batch-1.json")
    counts = {
        "accepted": 1,
        "rejected": 1,
        "skipped_duplicate": 0,
        "ambiguous": 0,
        "request_failed": 0,
    }

    assert state.complete_batch("run-1", "batch-1", counts) is True
    assert state.complete_batch("run-1", "batch-1", counts) is False

    run = state.get_run("run-1")
    assert run["completed_batches"] == 1
    assert run["accepted"] == 1
    assert run["rejected"] == 1


def test_expired_run_lease_is_reclaimed_and_previous_run_is_marked_interrupted(monkeypatch):
    now = 1_000
    monkeypatch.setattr("app.state.store.time.time", lambda: now)
    state = MemoryStateStore()
    state.create_run("old-run")
    assert state.acquire_run_lock("old-run", lease_seconds=10) is True

    now = 1_011
    state.create_run("new-run")
    assert state.acquire_run_lock("new-run", lease_seconds=10) is True

    old = state.get_run("old-run")
    assert old["status"] == "FAILED"
    assert old["failure_type"] == "INTERRUPTED"
    assert old["recovered_by_run_id"] == "new-run"
    assert old["finished_at"] == 1_011


def test_run_lease_heartbeat_extends_expiry_and_only_owner_can_renew(monkeypatch):
    now = 2_000
    monkeypatch.setattr("app.state.store.time.time", lambda: now)
    state = MemoryStateStore()
    state.create_run("run-1")
    assert state.acquire_run_lock("run-1", lease_seconds=10) is True

    now = 2_005
    assert state.renew_run_lock("run-1", lease_seconds=10) is True
    assert state.renew_run_lock("other-run", lease_seconds=10) is False

    now = 2_011
    assert state.acquire_run_lock("run-2", lease_seconds=10) is False

    now = 2_016
    state.create_run("run-2")
    assert state.acquire_run_lock("run-2", lease_seconds=10) is True