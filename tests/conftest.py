"""Shared unit-test doubles and fixtures.

These helpers intentionally avoid LocalEmu and real AWS calls.  The unit suite
isolates application behavior with a clearly named filesystem object store and
in-memory queue/state collaborators; LocalEmu integration is exercised by the
separate smoke and pipeline commands in the repository.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.messaging import ReceivedMessage
from app.models import BatchPointer


class FileSystemObjectStore:
    """Small filesystem-backed ObjectStore test double; this is not S3."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.json_puts: list[tuple[str, object]] = []
        self.file_puts: list[tuple[Path, str]] = []

    def _path(self, key: str) -> Path:
        return self.root / key

    def put_file(self, source: str | Path, key: str) -> str:
        source = Path(source)
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        self.file_puts.append((source, key))
        return str(destination)

    def download_file(self, key: str, destination: str | Path) -> Path:
        source = self._path(key)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination

    def put_json(self, key: str, payload: object) -> str:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        self.json_puts.append((key, payload))
        return str(destination)

    def get_json(self, key: str) -> object:
        return json.loads(self._path(key).read_text(encoding="utf-8"))


class RecordingQueue:
    """Queue test double that records sends, deletes and visibility releases."""

    def __init__(self):
        self.sent: list[BatchPointer] = []
        self.messages: list[ReceivedMessage] = []
        self.deleted: list[str] = []
        self.released: list[tuple[ReceivedMessage, int]] = []
        self._counter = 0

    def send(self, pointer: BatchPointer) -> None:
        self.sent.append(pointer)
        self._counter += 1
        token = str(self._counter)
        self.messages.append(
            ReceivedMessage(body=pointer.asdict(), receipt_handle=token, message_id=token)
        )

    def receive(self, max_messages: int = 10, wait_seconds: int = 10) -> list[ReceivedMessage]:
        result = self.messages[:max_messages]
        del self.messages[:max_messages]
        return result

    def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)

    def release(self, message: ReceivedMessage, delay_seconds: int = 5) -> None:
        self.released.append((message, delay_seconds))
        self.messages.append(message)


@pytest.fixture
def filesystem_store(tmp_path):
    """Return a filesystem ObjectStore test double rooted in pytest temp space."""

    return FileSystemObjectStore(tmp_path / "object-store")


@pytest.fixture
def recording_queue():
    """Return a queue test double with observable delivery side effects."""

    return RecordingQueue()
