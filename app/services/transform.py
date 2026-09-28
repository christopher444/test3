from __future__ import annotations

import hashlib
import json


def transform(row: dict[str, str]) -> dict:
    return {
        "sku": row["id"],
        "description": row["name"],
        "selling_price": float(row["price"]),
        "currency": row["currency"],
        "category_code": row["category"],
        "source_updated_at": row["updated_at"],
    }


def product_version_key(product: dict) -> str:
    canonical = json.dumps(product, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{product['sku']}#{digest}"
