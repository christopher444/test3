# Local development and AWS emulation

Read it with README

## 1. Scope

This guide explains how to run and test the attached repository on Ubuntu without a real AWS account. It distinguishes:

- fast application tests;
- supplied PIM/WMS mocks;
- LocalEmu-backed AWS integration tests;
- Terraform validation;
- the local 2M streaming/memory test;
- known emulator and current-code limitations.

Local emulation increases confidence in AWS API usage; it is not proof of identical production AWS behavior.

## 2. LocalEmu selection

The repository pins **LocalEmu 1.2.0** in `requirements-local.txt` and uses standard AWS SDK/Terraform endpoints against `http://127.0.0.1:4566`.

Relevant service surface exercised/provisioned by this repository includes S3, SQS/DLQ, DynamoDB, KMS, Secrets Manager, IAM enforcement, CloudWatch, SNS, ECR/ECS control-plane resources, Step Functions, Scheduler, STS and minimal EC2/VPC resources.

Upstream installation currently documents:

```bash
pip install localemu
```

Reference: https://github.com/localemu/localemu

The repository keeps LocalEmu in a dedicated environment so emulator dependencies never enter the production application image.

## 3. Known LocalEmu/fidelity boundaries

The design does not treat the emulator as a substitute for a sandbox AWS deployment.

Important boundaries in this repository:

1. The default business E2E runs application stages as **host Python processes** while using LocalEmu S3/SQS/DynamoDB/KMS endpoints.
2. `infra/local` also provisions ECS/Fargate/Step Functions/Scheduler/IAM/VPC control-plane resources for smoke coverage, but `infra/aws` remains authoritative for production runtime/networking.
3. Strict IAM must be explicitly enabled for least-privilege failure testing.
4. Local resource performance can be much slower than managed AWS, especially for thousands of fine-grained DynamoDB operations.
5. The current application has an SQS message-visibility lifecycle gap described in section 11 and `UNFINISHED.md`; a large static timeout is only a local workaround.

## 4. Ubuntu prerequisites

Recommended:

- Python 3.11+ for the application;
- Python 3.13 side-by-side for the repository's pinned LocalEmu environment where required by the reviewed setup;
- Docker Engine + Docker Compose plugin;
- Terraform >=1.5;
- disk space for Docker and the local 2M CSV stress artifact.

Check ports:

```bash
sudo ss -tulpn | grep -E ':(4566|8001|8002)\b' || true
```

Expected project bindings:

```text
127.0.0.1:4566   LocalEmu
127.0.0.1:8001   PIM mock
127.0.0.1:8002   WMS mock
```

## 5. Application environment and unit tests

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
python -m pytest -q
```

Review-time result for the attached ZIP:

```text
54 passed
```

The unit suite covers pagination/retry behavior, transformations, batching, run-lease recovery, partial WMS success, duplicate/redelivery behavior, ambiguous outcomes, rate-limit utilities and core state-store semantics.

## 6. Install and start LocalEmu

Install into the dedicated emulator environment:

```bash
make localemu-install
.localemu-venv/bin/python -V
.localemu-venv/bin/localemu --version
```

`make localemu-install` defaults `LOCALEMU_PYTHON=python3.13`. This is a repository-specific compatibility choice for the pinned emulator path, not advice to replace Ubuntu's global `python3`.

Recommended strict-IAM start:

```bash
make localemu-up-iam
make localemu-status
curl -fsS http://127.0.0.1:4566/_localemu/health
```

Permissive start:

```bash
make localemu-up
```

Stop:

```bash
make localemu-down
```

## 7. Provision local resources with Terraform

```bash
terraform -chdir=infra/local init
terraform -chdir=infra/local plan
terraform -chdir=infra/local apply -auto-approve
```

Or:

```bash
make local-infra
```

Inspect:

```bash
terraform -chdir=infra/local output
```

The local provider uses emulator-root style credentials for provisioning. `infra/local` creates a separate least-privilege application identity whose access key/secret are used by the host business pipeline when strict IAM enforcement is enabled.

Destroy only LocalEmu resources:

```bash
make local-infra-destroy
```

## 8. Smoke-test the AWS surface

General service smoke:

```bash
make localemu-smoke
```

Strict-IAM allow/deny smoke:

```bash
make localemu-iam-smoke
```

Renewable-lease recovery smoke:

```bash
make localemu-recovery-smoke
```

The recovery smoke exercises real LocalEmu DynamoDB calls and proves that an expired active-run lease can be reclaimed and the old run marked interrupted.

## 9. Start the supplied PIM/WMS mocks

```bash
make mocks-up
docker compose ps
curl -fsS http://127.0.0.1:8001/health
curl -fsS http://127.0.0.1:8002/health
```

Stop:

```bash
make mocks-down
```

### Mock transient-status defect

The supplied mock code constructs intended 429/503 `JSONResponse` objects with reversed positional arguments. With the pinned FastAPI/Starlette stack those branches surface as HTTP 500. The mock code is not changed in this documentation-only pass.

The application unit tests separately verify intended 429 and 5xx client behavior. For local live runs, `RETRY_WMS_5XX=true` is used only because these mock failures occur before the mock applies product business processing; production remains conservative by default.

## 10. Fast non-AWS mode

```bash
make mocks-up
RETRY_WMS_5XX=true make local-run
```

This is useful for quick application/client work but does not test S3/SQS/DynamoDB behavior.

## 11. Durable LocalEmu business pipeline

### 11.1 Current docs-only workaround

No application/script/Terraform code is changed in this pass. The current runner contains earlier defaults for `RUN_LOCK_SECONDS` and `SQS_VISIBILITY_TIMEOUT_SECONDS`, followed later by attempted LocalEmu-specific defaults. Because the variables are already set, the later `${VAR:-...}` assignments do not replace them.

Pass explicit environment values before invoking the script:

```bash
mkdir -p logs

RUN_LOCK_SECONDS=900 \
SQS_VISIBILITY_TIMEOUT_SECONDS=3600 \
WMS_MAX_IN_FLIGHT=10 \
./scripts/run_localemu_pipeline.sh 2>&1 \
  | tee "logs/localemu-pipeline-$(date +%Y%m%d-%H%M%S).log"
```

These values are **local-emulator headroom only**, not the final production fix.

### 11.2 Why the visibility workaround is temporary

`SQSQueue.receive()` supplies `VisibilityTimeout` directly on `ReceiveMessage`. The current worker can then buffer received messages in its internal work queue before a consumer starts useful work. The visibility clock is already running during that wait.

The code currently has no per-message `ChangeMessageVisibility` renewal. If processing plus local wait exceeds visibility, `DeleteMessage` can be attempted with an expired receipt handle. AWS supports changing a received message's visibility while it is being processed, up to the service maximum; production code should use that mechanism and keep prefetch bounded to real processing capacity.

Official API reference:

https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_ChangeMessageVisibility.html

### 11.3 Inspect logs and progress

Latest captured pipeline log:

```bash
LOG="$(ls -1t logs/localemu-pipeline-*.log | head -1)"
tail -f "$LOG"
```

List tables using the same region as Terraform:

```bash
.localemu-venv/bin/awsemu dynamodb list-tables --region eu-west-1
```

Queue state:

```bash
QUEUE_URL="$(terraform -chdir=infra/local output -raw queue_url)"

.localemu-venv/bin/awsemu sqs get-queue-attributes \
  --region eu-west-1 \
  --queue-url "$QUEUE_URL" \
  --attribute-names \
    ApproximateNumberOfMessages \
    ApproximateNumberOfMessagesNotVisible \
    VisibilityTimeout
```

Run state:

```bash
.localemu-venv/bin/awsemu dynamodb get-item \
  --region eu-west-1 \
  --table-name catalogue-sync-runs \
  --key '{"run_id":{"S":"<run-id>"}}'
```

DynamoDB run/batch state is the application's durable business progress source. SQS approximate queue metrics are operational hints and can lag.

### 11.4 Interrupted-run lock recovery

Current active locks use `lease_expires_at`; older persisted LocalEmu state may have used `expires_at`.

After verifying no pipeline process is alive, a one-time **LocalEmu-only** forced-expiry command that covers both formats is:

```bash
.localemu-venv/bin/awsemu dynamodb update-item \
  --region eu-west-1 \
  --table-name catalogue-sync-runs \
  --key '{"run_id":{"S":"__ACTIVE_RUN__"}}' \
  --update-expression 'SET lease_expires_at = :expired, expires_at = :expired' \
  --expression-attribute-values '{":expired":{"N":"0"}}'
```

Do not perform manual lock mutation in production without independent confirmation that the recorded owner is no longer executing.

## 12. 2M local streaming test

Run:

```bash
python scripts/loadtest.py --records 2000000
```

or:

```bash
make loadtest-2m
```

For system timing:

```bash
/usr/bin/time -v python scripts/loadtest.py --records 2000000
```

The script:

- generates 2,000,000 CSV rows locally;
- streams them through `transform()`;
- keeps only one 100-record batch at a time;
- reports generated file bytes, generation seconds, transform/scan seconds and peak RSS.

It does **not** call PIM, WMS or LocalEmu. It therefore validates streaming/O(batch)-style memory behavior, not the 30-minute end-to-end SLO.

At batch size 100, the script counts 20,000 logical WMS-sized batches.

### External-rate capacity floors

At the challenge's 1M horizon:

```text
PIM: 1,000,000 / 500 / 10  = 200s  = 3m20s
WMS: 1,000,000 / 100 / 20 = 500s  = 8m20s
sequential floor                    = 11m40s
```

At the optional 2M stress point:

```text
PIM floor = 6m40s
WMS floor = 16m40s
sequential floor = 23m20s
```

Those are only request-rate floors. Real duration also includes response latency, retries, task startup, S3/SQS/DynamoDB work and any reconciliation/failure handling.

## 13. Terraform validation

When Terraform is installed:

```bash
make tf-validate
```

This formats/checks and validates both `infra/local` and `infra/aws` without applying production infrastructure.

A production release should additionally run a plan in a sandbox AWS account and validate ECS secret injection, network egress, CloudWatch log delivery, Step Functions/ECS integration and real external API contracts.

## 14. Submission validation sequence

```bash
make test
make localemu-install
make localemu-up-iam
make local-infra
make localemu-smoke
make localemu-iam-smoke
make localemu-recovery-smoke
make mocks-up
make loadtest-2m
make tf-validate
```

Exercise the full LocalEmu business pipeline too, but do not call it a clean release gate until the SQS visibility heartbeat/bounded-prefetch fix in `UNFINISHED.md` is implemented and regression-tested.
