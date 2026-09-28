from __future__ import annotations

import argparse
import csv
import os
import resource
import time
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.transform import transform

FIELDS = ["id", "name", "category", "price", "currency", "updated_at"]
CATEGORIES = ["ELECTRONICS", "HOME", "SPICES", "GROCERY", "SPORTS"]


def generate(path: Path, records: int) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    updated = datetime(2026, 9, 25, 8, 30, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for i in range(1, records + 1):
            writer.writerow(
                {
                    "id": f"P{i:07d}",
                    "name": f"Product {i}",
                    "category": CATEGORIES[(i - 1) % len(CATEGORIES)],
                    "price": f"{100 + ((i * 1.25) % 900):.2f}",
                    "currency": "INR",
                    "updated_at": updated,
                }
            )
    return time.perf_counter() - started


def scan(path: Path, batch_size: int) -> tuple[int, int, float]:
    started = time.perf_counter()
    count = 0
    batches = 0
    batch = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            batch.append(transform(row))
            count += 1
            if len(batch) == batch_size:
                batches += 1
                batch.clear()
        if batch:
            batches += 1
    return count, batches, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description="Streaming 2M-record local capacity test")
    parser.add_argument("--records", type=int, default=2_000_000)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--path", default="./runtime/loadtest/catalogue-2m.csv")
    args = parser.parse_args()

    path = Path(args.path)
    gen_seconds = generate(path, args.records)
    count, batches, scan_seconds = scan(path, args.batch_size)
    rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(
        {
            "records": count,
            "batches": batches,
            "file_bytes": os.path.getsize(path),
            "generate_seconds": round(gen_seconds, 3),
            "stream_transform_seconds": round(scan_seconds, 3),
            "max_rss_kib": rss_kib,
            "note": "This validates O(batch) memory. Full WMS soak is bounded by its 20 req/s contract (~16.7 min for 2M at 100/batch).",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
