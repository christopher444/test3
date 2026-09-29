from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    product_api_url: str = os.getenv("PRODUCT_API_URL", "http://localhost:8001")
    warehouse_api_url: str = os.getenv("WAREHOUSE_API_URL", "http://localhost:8002")
    product_api_key: str = os.getenv("PRODUCT_API_KEY", "challenge-product-key")
    warehouse_api_key: str = os.getenv("WAREHOUSE_API_KEY", "challenge-warehouse-key")

    page_size: int = int(os.getenv("PAGE_SIZE", "500"))
    batch_size: int = int(os.getenv("BATCH_SIZE", "100"))
    pim_rate_per_second: float = float(os.getenv("PIM_RATE_PER_SECOND", "10"))
    wms_rate_per_second: float = float(os.getenv("WMS_RATE_PER_SECOND", "20"))
    pim_max_in_flight: int = int(os.getenv("PIM_MAX_IN_FLIGHT", "32"))
    wms_max_in_flight: int = int(os.getenv("WMS_MAX_IN_FLIGHT", "80"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "6"))
    worker_max_idle_seconds: int = int(os.getenv("WORKER_MAX_IDLE_SECONDS", "240"))
    run_state_retention_days: int = int(os.getenv("RUN_STATE_RETENTION_DAYS", "400"))
    batch_state_retention_days: int = int(os.getenv("BATCH_STATE_RETENTION_DAYS", "30"))
    # Application-level distributed lease.  A short renewable lease recovers from
    # hard task/process termination without allowing overlapping catalogue runs.
    run_lock_seconds: int = int(os.getenv("RUN_LOCK_SECONDS", "120"))
    run_lock_heartbeat_seconds: int = int(os.getenv("RUN_LOCK_HEARTBEAT_SECONDS", "30"))
    connect_timeout_seconds: float = float(os.getenv("CONNECT_TIMEOUT_SECONDS", "5"))
    read_timeout_seconds: float = float(os.getenv("READ_TIMEOUT_SECONDS", "15"))

    # boto3/botocore HTTP transport settings.
    # Keep the pool comfortably above worker concurrency because one WMS batch
    # can trigger several AWS operations across SQS, DynamoDB and S3.
    aws_max_pool_connections: int = int(
        os.getenv("AWS_MAX_POOL_CONNECTIONS", "100")
    )
    aws_connect_timeout_seconds: float = float(
        os.getenv("AWS_CONNECT_TIMEOUT_SECONDS", "5")
    )
    aws_read_timeout_seconds: float = float(
        os.getenv("AWS_READ_TIMEOUT_SECONDS", "30")
    )
    runtime_dir: str = os.getenv("RUNTIME_DIR", "./runtime")
    storage_backend: str = os.getenv("STORAGE_BACKEND", "filesystem")
    s3_bucket: str = os.getenv("S3_BUCKET", "catalogue-sync-local")
    s3_prefix: str = os.getenv("S3_PREFIX", "catalogue-sync")
    aws_region: str = os.getenv("AWS_REGION", "eu-west-1")
    aws_endpoint_url: str | None = os.getenv("AWS_ENDPOINT_URL") or None
    kms_key_id: str | None = os.getenv("KMS_KEY_ID") or None
    sqs_visibility_timeout_seconds: int = int(
        os.getenv("SQS_VISIBILITY_TIMEOUT_SECONDS", "300")
    )
    sqs_visibility_heartbeat_seconds: float = float(
        os.getenv("SQS_VISIBILITY_HEARTBEAT_SECONDS", "60")
    )
    queue_url: str | None = os.getenv("QUEUE_URL") or None
    runs_table: str = os.getenv("RUNS_TABLE", "catalogue-sync-runs")
    batches_table: str = os.getenv("BATCHES_TABLE", "catalogue-sync-batches")
    idempotency_table: str = os.getenv("IDEMPOTENCY_TABLE", "catalogue-sync-idempotency")
    state_backend: str = os.getenv("STATE_BACKEND", "memory")
    queue_backend: str = os.getenv("QUEUE_BACKEND", "memory")

    # Conservative by default: a WMS 5xx/timeout after request transmission is ambiguous.
    retry_wms_5xx: bool = _bool("RETRY_WMS_5XX", False)
    # Full-catalogue daily export remains the source-of-truth artifact; WMS writes are deduped by product version.
    idempotency_scope: str = os.getenv("IDEMPOTENCY_SCOPE", "product-version")
    def __post_init__(self) -> None:
        # Hard source/target contracts from the assessment.  Fail fast rather
        # than allowing an environment variable to violate an external limit.
        if not 1 <= self.page_size <= 500:
            raise ValueError("PAGE_SIZE must be between 1 and the PIM maximum of 500")
        if not 1 <= self.batch_size <= 100:
            raise ValueError("BATCH_SIZE must be between 1 and the WMS maximum of 100")
        if self.pim_rate_per_second <= 0:
            raise ValueError("PIM_RATE_PER_SECOND must be > 0")
        if self.wms_rate_per_second <= 0:
            raise ValueError("WMS_RATE_PER_SECOND must be > 0")
        if self.run_lock_seconds <= 0:
            raise ValueError("RUN_LOCK_SECONDS must be positive")
        if not 0 < self.run_lock_heartbeat_seconds < self.run_lock_seconds:
            raise ValueError("RUN_LOCK_HEARTBEAT_SECONDS must be > 0 and less than RUN_LOCK_SECONDS")
        if self.aws_max_pool_connections < 1:
            raise ValueError("AWS_MAX_POOL_CONNECTIONS must be positive")
        if self.sqs_visibility_timeout_seconds < 1:
            raise ValueError("SQS_VISIBILITY_TIMEOUT_SECONDS must be positive")
        if not 0 < self.sqs_visibility_heartbeat_seconds < self.sqs_visibility_timeout_seconds:
            raise ValueError(
                "SQS_VISIBILITY_HEARTBEAT_SECONDS must be > 0 and less than "
                "SQS_VISIBILITY_TIMEOUT_SECONDS"
            )            
    @property
    def http_timeout(self):
        import httpx

        return httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.read_timeout_seconds,
            pool=self.connect_timeout_seconds,
        )


settings = Settings()
