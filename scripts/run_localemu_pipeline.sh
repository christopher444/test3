#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export AWS_REGION="${AWS_REGION:-eu-west-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-$(terraform -chdir=infra/local output -raw local_app_access_key_id)}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-$(terraform -chdir=infra/local output -raw local_app_secret_access_key)}"
export AWS_ENDPOINT_URL="${AWS_ENDPOINT_URL:-http://127.0.0.1:4566}"
export PRODUCT_API_URL="${PRODUCT_API_URL:-http://127.0.0.1:8001}"
export WAREHOUSE_API_URL="${WAREHOUSE_API_URL:-http://127.0.0.1:8002}"
export PRODUCT_API_KEY="${PRODUCT_API_KEY:-challenge-product-key}"
export WAREHOUSE_API_KEY="${WAREHOUSE_API_KEY:-challenge-warehouse-key}"
export STORAGE_BACKEND=s3
export STATE_BACKEND=dynamodb
export QUEUE_BACKEND=sqs
export S3_BUCKET=catalogue-sync-local
export RUNS_TABLE=catalogue-sync-runs
export BATCHES_TABLE=catalogue-sync-batches
export IDEMPOTENCY_TABLE=catalogue-sync-idempotency
export QUEUE_URL="${QUEUE_URL:-$(terraform -chdir=infra/local output -raw queue_url)}"
export KMS_KEY_ID="${KMS_KEY_ID:-$(terraform -chdir=infra/local output -raw kms_key_arn)}"
export AWS_MAX_POOL_CONNECTIONS="${AWS_MAX_POOL_CONNECTIONS:-100}"
export SQS_VISIBILITY_HEARTBEAT_SECONDS="${SQS_VISIBILITY_HEARTBEAT_SECONDS:-60}"

# The supplied WMS mock's intended transient 429/503 branches currently surface as 500s
# because of a fixture bug. The production default remains false; this local mock run opts
# into retrying those known pre-processing failures so the fixture can complete.
export RETRY_WMS_5XX="${RETRY_WMS_5XX:-true}"

# LocalEmu is substantially slower than real AWS DynamoDB under highly
# concurrent product-level writes. Keep local concurrency bounded so the
# emulator remains responsive while preserving the production rate limit.
export WMS_MAX_IN_FLIGHT="${WMS_MAX_IN_FLIGHT:-10}"

# LocalEmu recovery-test settings. Give the worker enough lease headroom while
# thousands of emulated DynamoDB operations are being performed.
export RUN_LOCK_SECONDS="${RUN_LOCK_SECONDS:-900}"
export RUN_LOCK_HEARTBEAT_SECONDS="${RUN_LOCK_HEARTBEAT_SECONDS:-30}"

# Must agree with the LocalEmu SQS queue visibility configured by Terraform.
export SQS_VISIBILITY_TIMEOUT_SECONDS="${SQS_VISIBILITY_TIMEOUT_SECONDS:-900}"

RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-localemu}"
echo "LocalEmu pipeline run_id=$RUN_ID"

python -m app.cli export --run-id "$RUN_ID"
python -m app.cli enqueue --run-id "$RUN_ID"
python -m app.cli worker --run-id "$RUN_ID"
python -m app.cli status --run-id "$RUN_ID"
