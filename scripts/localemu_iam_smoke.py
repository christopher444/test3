from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError

ENDPOINT = os.getenv("LOCALEMU_ENDPOINT", "http://127.0.0.1:4566")
REGION = os.getenv("AWS_REGION", "eu-west-1")
ACCESS_KEY = os.environ["AWS_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
QUEUE_URL = os.environ["QUEUE_URL"]


def client(service: str):
    return boto3.client(
        service,
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )


def main() -> int:
    # Positive check: the application identity is allowed to inspect its work queue.
    client("sqs").get_queue_attributes(QueueUrl=QUEUE_URL, AttributeNames=["QueueArn"])

    # Negative check: the application policy intentionally has no account-wide S3 listing.
    try:
        client("s3").list_buckets()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"AccessDenied", "AccessDeniedException"}:
            raise
    else:
        raise RuntimeError(
            "IAM negative test unexpectedly succeeded. Start LocalEmu with IAM_ENFORCEMENT=1."
        )

    print("LocalEmu IAM smoke: allowed queue access succeeded; forbidden S3 list was denied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
