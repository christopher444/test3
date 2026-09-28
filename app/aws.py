"""Shared boto3/botocore transport configuration.

The application uses the same AWS adapters against LocalEmu and real AWS.  A
single transport configuration keeps connection pooling, timeouts and SDK retry
behavior consistent across S3, SQS and DynamoDB clients.
"""

from botocore.config import Config

from app.config import Settings


def boto_config(cfg: Settings) -> Config:
    return Config(
        max_pool_connections=cfg.aws_max_pool_connections,
        connect_timeout=cfg.aws_connect_timeout_seconds,
        read_timeout=cfg.aws_read_timeout_seconds,
        tcp_keepalive=True,
        retries={"mode": "standard", "max_attempts": cfg.max_retries + 1},
    )