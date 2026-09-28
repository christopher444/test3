"""Unit tests for one logical PIM export run and its immutable CSV source artifact.

These tests verify streaming page-to-CSV behavior, run metadata, concurrent-run
exclusion, and failure recovery.  They use a filesystem ObjectStore test double
rather than LocalEmu S3; the S3-compatible adapter is integration-tested elsewhere.
"""

from __future__ import annotations

import asyncio
import csv
from dataclasses import replace

import pytest

from app.config import settings
from app.services.catalogue_export import FIELDS, export_catalogue
from app.state import MemoryStateStore


class FakePagedPim:
    def __init__(self, pages):
        self.pages = pages

    async def iter_pages(self):
        for item in self.pages:
            yield item


class FailingPim:
    async def iter_pages(self):
        yield 1, [{"id": "P1", "name": "One", "category": "C", "price": "1.00", "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"}]
        raise RuntimeError("PIM unavailable")


def _product(product_id):
    return {
        "id": product_id,
        "name": f"Product {product_id}",
        "category": "TEST",
        "price": "12.50",
        "currency": "INR",
        "updated_at": "2026-09-25T08:30:00Z",
    }


def test_export_streams_pages_to_complete_csv_and_updates_run(filesystem_store, tmp_path):
    cfg = replace(settings, runtime_dir=str(tmp_path / "runtime"), s3_prefix="catalogue-sync")
    state = MemoryStateStore()
    pim = FakePagedPim([(1, [_product("P1"), _product("P2")]), (2, [_product("P3")])])

    key, count = asyncio.run(export_catalogue("run-1", filesystem_store, state, cfg, pim))

    assert key == "catalogue-sync/exports/run-1/catalogue.csv"
    assert count == 3
    run = state.get_run("run-1")
    assert run["status"] == "RUNNING"
    assert run["export_status"] == "COMPLETE"
    assert run["exported_products"] == 3
    assert run["export_key"] == key

    exported_path = filesystem_store.root / key
    with exported_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == FIELDS
    assert [row["id"] for row in rows] == ["P1", "P2", "P3"]


def test_export_rejects_overlapping_business_run(filesystem_store, tmp_path):
    cfg = replace(settings, runtime_dir=str(tmp_path / "runtime"))
    state = MemoryStateStore()
    assert state.acquire_run_lock("existing-run") is True

    with pytest.raises(RuntimeError, match="another catalogue synchronisation run is active"):
        asyncio.run(export_catalogue("new-run", filesystem_store, state, cfg, FakePagedPim([])))

    assert state.get_run("new-run") is None


def test_export_failure_marks_run_failed_and_releases_lock(filesystem_store, tmp_path):
    cfg = replace(settings, runtime_dir=str(tmp_path / "runtime"))
    state = MemoryStateStore()

    with pytest.raises(RuntimeError, match="PIM unavailable"):
        asyncio.run(export_catalogue("failed-run", filesystem_store, state, cfg, FailingPim()))

    run = state.get_run("failed-run")
    assert run["status"] == "FAILED"
    assert run["export_status"] == "FAILED"
    assert "finished_at" in run
    assert state.acquire_run_lock("next-run") is True


def test_empty_catalogue_still_produces_header_only_export(filesystem_store, tmp_path):
    cfg = replace(settings, runtime_dir=str(tmp_path / "runtime"), s3_prefix="catalogue-sync")
    state = MemoryStateStore()

    key, count = asyncio.run(export_catalogue("empty-run", filesystem_store, state, cfg, FakePagedPim([])))

    assert count == 0
    exported_path = filesystem_store.root / key
    assert exported_path.read_text(encoding="utf-8").strip() == ",".join(FIELDS)
