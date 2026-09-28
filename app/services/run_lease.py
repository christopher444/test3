"""Renewable run lease used across export, enqueue and WMS-drain stages."""

from __future__ import annotations

import logging
import threading
import time

from app.config import Settings
from app.state import StateStore

log = logging.getLogger(__name__)


class RunLeaseLost(RuntimeError):
    """Raised when this stage no longer owns the active catalogue-run lease."""


class RunLease:
    """Maintain a short DynamoDB-backed lease while a stage is doing work.

    A daemon heartbeat renews the lease.  Stage loops call ``assert_owned`` at
    safe boundaries so a task that loses ownership stops before doing more work.
    Hard process/container termination naturally stops the heartbeat; another run
    can reclaim the lease after ``RUN_LOCK_SECONDS``.
    """

    def __init__(self, state: StateStore, run_id: str, cfg: Settings):
        self.state = state
        self.run_id = run_id
        self.lease_seconds = cfg.run_lock_seconds
        self.heartbeat_seconds = cfg.run_lock_heartbeat_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._last_success = time.monotonic()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "RunLease":
        if not self.state.renew_run_lock(self.run_id, self.lease_seconds):
            raise RunLeaseLost(f"run {self.run_id} no longer owns the active lease")
        self._last_success = time.monotonic()
        self._thread = threading.Thread(
            target=self._heartbeat,
            name=f"run-lease-{self.run_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, float(self.heartbeat_seconds) + 1.0))
        if exc_type is None:
            self.assert_owned()

    def assert_owned(self) -> None:
        if self._lost.is_set():
            raise RunLeaseLost(f"run {self.run_id} lost its active lease")

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            try:
                if self.state.renew_run_lock(self.run_id, self.lease_seconds):
                    self._last_success = time.monotonic()
                    continue
                self._lost.set()
                return
            except Exception:
                # A single transient DynamoDB/LocalEmu failure should not abort a
                # healthy stage immediately.  Stop only when we have been unable
                # to renew for an entire lease duration.
                log.exception("run lease heartbeat failed", extra={"run_id": self.run_id, "event": "run_lease_heartbeat_failed"})
                if time.monotonic() - self._last_success >= self.lease_seconds:
                    self._lost.set()
                    return