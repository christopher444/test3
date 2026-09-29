"""Tests that default client configuration respects the documented external API contracts.

The assignment defines PIM limits of 500 records/page and 10 requests/second,
and WMS limits of 100 products/batch and 20 requests/second.  These tests guard
against a future configuration default accidentally exceeding those ceilings.
"""

from app.config import settings


def test_default_pim_limits_do_not_exceed_contract():
    assert 1 <= settings.page_size <= 500
    assert 0 < settings.pim_rate_per_second <= 10
    assert settings.pim_max_in_flight >= 1


def test_default_wms_limits_do_not_exceed_contract():
    assert 1 <= settings.batch_size <= 100
    assert 0 < settings.wms_rate_per_second <= 20
    assert settings.wms_max_in_flight >= 1


def test_default_idempotency_scope_is_product_version():
    assert settings.idempotency_scope == "product-version"


def test_aws_connection_pool_exceeds_default_worker_concurrency():
    assert settings.aws_max_pool_connections >= settings.wms_max_in_flight


def test_run_lease_heartbeat_is_shorter_than_lease():
    assert 0 < settings.run_lock_heartbeat_seconds < settings.run_lock_seconds


def test_sqs_visibility_timeout_covers_long_batch_processing_window():
    assert settings.sqs_visibility_timeout_seconds >= 180
    assert 0 < settings.sqs_visibility_heartbeat_seconds < settings.sqs_visibility_timeout_seconds    