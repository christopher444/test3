from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ProductDisposition(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class BatchPointer:
    run_id: str
    batch_id: str
    s3_key: str
    product_count: int

    def asdict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "s3_key": self.s3_key,
            "product_count": self.product_count,
        }
