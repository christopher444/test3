"""Unit tests for PIM-to-WMS mapping and product-version idempotency keys.

The mapping test protects the externally visible WMS payload contract.  The
version-key tests ensure equivalent product content generates a stable key while
an actual product change generates a new key that may legitimately be sent.
"""

from app.services.transform import product_version_key, transform


def test_transform_maps_all_required_wms_fields():
    row = {
        "id": "P0000001",
        "name": "Test Product",
        "category": "SPICES",
        "price": "249.50",
        "currency": "INR",
        "updated_at": "2026-09-25T08:30:00Z",
    }

    assert transform(row) == {
        "sku": "P0000001",
        "description": "Test Product",
        "selling_price": 249.50,
        "currency": "INR",
        "category_code": "SPICES",
        "source_updated_at": "2026-09-25T08:30:00Z",
    }


def test_product_version_key_is_stable_for_equivalent_content():
    first = {
        "sku": "P1",
        "description": "One",
        "selling_price": 1.0,
        "currency": "INR",
        "category_code": "TEST",
        "source_updated_at": "2026-01-01T00:00:00Z",
    }
    same_content_different_order = dict(reversed(list(first.items())))

    assert product_version_key(first) == product_version_key(same_content_different_order)
    assert product_version_key(first).startswith("P1#")


def test_product_version_key_changes_when_product_version_changes():
    original = {
        "sku": "P1",
        "description": "One",
        "selling_price": 1.0,
        "currency": "INR",
        "category_code": "TEST",
        "source_updated_at": "2026-01-01T00:00:00Z",
    }
    changed = {**original, "selling_price": 2.0, "source_updated_at": "2026-01-02T00:00:00Z"}

    assert product_version_key(original) != product_version_key(changed)
