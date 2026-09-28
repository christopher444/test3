# Product Catalogue Integration

Production-oriented refactor of the daily PIM -> WMS catalogue synchronisation.

The solution is designed for the current ~250,000-product catalogue and the stated growth path toward roughly 2 million products over the next four years. The production design uses AWS managed services and Terraform; local development uses the supplied PIM/WMS mocks plus **LocalEmu** for AWS-compatible service emulation.

## Start here

Read these files alongside this README:

- `ARCHITECTURE.md` — architecture, assumptions, failure semantics, four-year scaling, security, observability and trade-offs.
- `REQUIREMENTS_TRACEABILITY.md` — every stated business/assessment requirement mapped to implementation/evidence.
- `LOCAL_DEVELOPMENT.md` — deeper LocalEmu/Terraform details, emulator limitations, IAM enforcement and chaos testing.
- `UNFINISHED.md` — explicit validation gaps and unresolved external-contract issues.

## Architecture in one paragraph

EventBridge Scheduler starts a Step Functions Standard execution. Fargate stage 1 streams paginated PIM data into a complete CSV retained in S3 for 90 days. Stage 2 streams that CSV into durable 100-record batch objects, DynamoDB batch metadata and SQS FIFO pointers. Stage 3 drains the queue asynchronously at the WMS's 20 requests/second ceiling, recording product-version idempotency and accepted/rejected/ambiguous outcomes in DynamoDB. CloudWatch/SNS provide operational visibility; Secrets Manager, KMS, IAM and private networking address security.

The design deliberately treats ambiguous WMS outcomes conservatively. A request that may have reached the WMS but whose result is unknown is not blindly retried, because the business requirement says the same product must never be sent twice. See `ARCHITECTURE.md` for the exact assumptions and what would change if the WMS provides an idempotency key or reconciliation API.

---

# Local Ubuntu setup and test guide

This section is the recommended sequence for a clean Ubuntu VPS or Ubuntu development machine.

## 1. Local ports used

The documented local workflow binds only to loopback:

- `127.0.0.1:4566` — LocalEmu AWS gateway
- `127.0.0.1:8001` — PIM mock
- `127.0.0.1:8002` — WMS mock

Before starting, check that these ports are free:

```bash
sudo ss -tulpn | grep -E ':(4566|8001|8002)\b' || true
```

Even if the supplied VPS already uses ports such as 22, 80, 443 and 5432; this project does not bind to them.

## 2. Extract the repository

```bash
unzip repository.zip
cd repository
```

If you cloned/copied the repository by another method, simply `cd` to the directory containing `Makefile`, `README.md`, `app/`, `infra/` and `scripts/`.

## 3. Install Ubuntu prerequisites

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  make unzip curl wget gnupg ca-certificates
```

Check Python:

```bash
python3 --version
```

Python 3.11+ is recommended for this repository. LocalEmu itself currently requires Python 3.10+.

## 4. Install/check Docker

Docker is required for the supplied PIM/WMS mock containers and for LocalEmu services that run a real container engine, including ECS/Fargate-style execution.

If Docker is already installed:

```bash
docker --version
docker compose version
docker info
```

If Docker is not installed, install Docker Engine using Docker's official Ubuntu repository/instructions:

https://docs.docker.com/engine/install/ubuntu/

The relevant current Docker packages are:

```text
docker-ce
docker-ce-cli
containerd.io
docker-buildx-plugin
docker-compose-plugin
```

After installation:

```bash
sudo systemctl enable --now docker
docker --version
docker compose version
```

Optional: allow the current user to run Docker without `sudo`:

```bash
sudo usermod -aG docker "$USER"
```

Log out and log back in after changing Docker group membership, then verify:

```bash
docker info
```

## 5. Install/check Terraform

First check whether Terraform is already installed:

```bash
terraform version
```

If it is missing, install it from HashiCorp's Ubuntu repository:

```bash
wget -O - https://apt.releases.hashicorp.com/gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(grep -oP '(?<=UBUNTU_CODENAME=).*' /etc/os-release || lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/hashicorp.list

sudo apt update
sudo apt install -y terraform
terraform version
```

HashiCorp installation reference:

https://developer.hashicorp.com/terraform/install

## 6. Create the application Python environment

Create a virtual environment for the application and its tests:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

Run the fast test suite before starting any infrastructure:

```bash
make test
```

Expected submission-build result:
Expected result after the recovery-hardening pass:

```text
54 passed
```

This test suite covers the important application semantics including retry behavior, partial WMS success, validation rejection, duplicate/redelivery handling and conservative treatment of ambiguous WMS outcomes.

---

# LocalEmu installation
#version 3.13 is needed
python3.13 -m venv ~/venv4
source ~/venv4/bin/activate
python -m pip install --upgrade pip


```bash
pip install localemu
```

Upstream references:

- https://github.com/localemu/localemu
- https://pypi.org/project/localemu/

LocalEmu currently exposes AWS-compatible APIs through the default endpoint:

```text
http://localhost:4566
```

and ships the `awsemu` CLI, which automatically targets the local emulator.

## Why this repository uses `make localemu-install`

Do **not** install the emulator into the application's production dependency environment. This repository intentionally keeps it isolated.

`requirements-local.txt` contains the reviewed/pinned emulator version:


Verify the installation:

```bash
.localemu-venv/bin/localemu --version
.localemu-venv/bin/localemu services | head
```

---

# Start LocalEmu

## 7. 
For a permissive development session without strict IAM enforcement:

```bash
	PERSISTENCE=1 \
	SNAPSHOT_SAVE_STRATEGY=SCHEDULED \
	SNAPSHOT_FLUSH_INTERVAL=15 \
	.localemu-venv/bin/localemu start
```

Stop LocalEmu with:

```bash
.localemu-venv/bin/localemu stop
```

---

# Provision local AWS resources with Terraform

## 8. Review the Terraform plan

```bash
terraform -chdir=infra/local init
terraform -chdir=infra/local plan
```

## 9. Provision the local AWS resources


```bash
terraform -chdir=infra/local apply -auto-approve
```

Inspect outputs:

```bash
terraform -chdir=infra/local output
```

`infra/local` provisions the AWS-compatible resources needed to exercise the design locally, including:

- KMS key/alias
- S3 bucket
  - versioning
  - KMS configuration
  - 90-day original export lifecycle
  - 7-day derived-work lifecycle
- SQS FIFO work queue
- SQS FIFO dead-letter queue and redrive policy
- DynamoDB run table
- DynamoDB batch table
- DynamoDB product-version idempotency table
- DynamoDB transaction/conditional-write surface used by the application
- Secrets Manager mock API-key secrets
- CloudWatch Logs group
- CloudWatch/SQS/Step Functions alarm resources
- SNS alarm topic
- IAM roles/policies
- dedicated least-privilege local application IAM identity
- STS identity surface
- ECR repository
- ECS cluster/task-definition control plane
- Step Functions Standard smoke workflow
- EventBridge Scheduler schedule, disabled locally by default
- minimal local VPC/subnet/security-group control-plane resources

The production Terraform under `infra/aws` remains authoritative for the full AWS networking topology and production Step Functions -> ECS/Fargate workflow.

---

# Test LocalEmu and the emulated AWS surface

## 10. Run the LocalEmu service smoke test

```bash
cd root folder
python scripts/localemu_smoke.py
```

This performs real SDK/API calls through LocalEmu rather than only testing that port 4566 is open. It exercises/validates the expected local AWS surface including:

- LocalEmu health
- STS caller identity
- S3 write/read/versioning
- KMS encrypt/decrypt
- DynamoDB transaction behavior
- SQS FIFO send/receive/delete
- Secrets Manager retrieval
- CloudWatch Logs/alarm resources
- SNS
- ECR
- ECS cluster metadata
- VPC resources
- IAM roles
- Scheduler state
- Step Functions execution

The script fails if an expected integration is unavailable.

## 11. Prove least-privilege IAM enforcement

When LocalEmu was started with 

IAM_ENFORCEMENT=1 .localemu-venv/bin/localemu start

, run:

```bash
	AWS_ACCESS_KEY_ID="$$(terraform -chdir=infra/local output -raw local_app_access_key_id)" \
	AWS_SECRET_ACCESS_KEY="$$(terraform -chdir=infra/local output -raw local_app_secret_access_key)" \
	QUEUE_URL="$$(terraform -chdir=infra/local output -raw queue_url)" \
	AWS_REGION=eu-west-1 LOCALEMU_ENDPOINT=http://127.0.0.1:4566 \
	python scripts/localemu_iam_smoke.py
```

This intentionally verifies both sides of IAM:

```text
allowed operation:
  application identity can inspect/use its permitted SQS resource

forbidden operation:
  application identity cannot perform account-wide s3:ListBuckets
  -> expected AccessDenied
```

This is important because a successful request using emulator administrator/root credentials would not prove that the production task policy is sufficient or least-privilege.

---

# Start the supplied PIM and WMS mocks

## 12. Start mocks

```bash
docker compose up -d --build product-api warehouse-api
```

Verify containers:

```bash
docker compose ps
```

The expected services are:

```text
product-api      PIM mock      127.0.0.1:8001
warehouse-api    WMS mock      127.0.0.1:8002
```

Optional connectivity checks:

```bash
curl -i http://127.0.0.1:8001/
curl -i http://127.0.0.1:8002/
```

A `404` at `/` is acceptable if that route is not implemented; `connection refused` is not.

Stop the mocks with:

```bash
docker compose down
```

---

# Run the business pipeline against LocalEmu

## 13. Run the durable LocalEmu pipeline

With all of the following running/provisioned:

1. application virtual environment active
2. LocalEmu running
3. `infra/local` applied
4. PIM/WMS mocks running

execute:

```bash

mkdir -p logs

./scripts/run_localemu_pipeline.sh 2>&1 | tee "logs/localemu-pipeline-$(date +%Y%m%d-%H%M%S).log"



```

The local flow is:

```text
PIM mock
  -> streaming PIM pagination/export
  -> LocalEmu S3 original CSV
  -> streaming transform/batch generation
  -> LocalEmu S3 batch payloads
  -> LocalEmu DynamoDB run/batch/idempotency state
  -> LocalEmu SQS FIFO pointers
  -> WMS worker
  -> WMS mock
```

The local application pipeline uses the dedicated least-privilege IAM credentials emitted by Terraform instead of LocalEmu root credentials.

The application adapters are therefore exercised against AWS-compatible:

- S3
- SQS
- DynamoDB
- KMS-aware resource configuration

rather than falling back to in-memory/file-only persistence.

## 14. Inspect local AWS state

LocalEmu ships `awsemu`, which automatically targets the emulator.

List S3 buckets:

```bash
.localemu-venv/bin/awsemu s3 ls
```

List SQS queues:

```bash
.localemu-venv/bin/awsemu sqs list-queues
```

List DynamoDB tables:

```bash
.localemu-venv/bin/awsemu dynamodb list-tables
```

Inspect Step Functions:

```bash
.localemu-venv/bin/awsemu stepfunctions list-state-machines
```

Inspect ECS clusters:

```bash
.localemu-venv/bin/awsemu ecs list-clusters
```

Inspect Scheduler resources:

```bash
.localemu-venv/bin/awsemu scheduler list-schedules
```

Check the emulator itself:

```bash
.localemu-venv/bin/localemu status
.localemu-venv/bin/localemu services
```

---

# Fast test without AWS emulation

For rapid application/client development where AWS semantics are not being tested:

```bash
make mocks-up
RETRY_WMS_5XX=true python -m app.cli run-local
```

or:

```bash
make local-run
```

`RETRY_WMS_5XX=true` is specifically useful with the supplied WMS mock fixture. Production defaults to conservative handling because an HTTP 5xx from a real WMS can represent an ambiguous side effect: the remote system may have committed the request before the response was lost/failed.

Use `make localemu-pipeline` for the stronger integration test because it exercises the AWS adapters and durable state.

---

# Interrupted-run recovery drill

The production lock is a renewable DynamoDB lease (`120s` lease / `30s` heartbeat by default), not a two-hour sticky lock. A hard process/container kill stops heartbeats; after lease expiry the next run can reclaim ownership and the abandoned run is recorded as failed/interrupted. DynamoDB TTL is cleanup only and is **not** used to decide whether the lock is stale; the active lock uses a separate `lease_expires_at` attribute and intentionally has no TTL attribute.

After LocalEmu + Terraform are running, exercise this behavior directly:

```bash
make localemu-recovery-smoke
```

The smoke creates an old run with a one-second lease, lets it expire, acquires the lease with a new run, and verifies that the old run is marked `FAILED` with `failure_type=INTERRUPTED`.

If this repository is upgrading an existing LocalEmu state created with the previous two-hour lock, that old `expires_at` value remains until it naturally expires. After verifying that no previous pipeline process is alive, force only the **local emulator** lock to be stale once:

```bash
.localemu-venv/bin/awsemu dynamodb update-item \
  --table-name catalogue-sync-runs \
  --key '{"run_id":{"S":"__ACTIVE_RUN__"}}' \
  --update-expression 'SET expires_at = :expired' \
  --expression-attribute-values '{":expired":{"N":"0"}}'
```

The next run will reclaim it and mark the previous run failed/interrupted.

# 2-million-product scalability test

## 15. Run the streaming capacity test

```bash
make loadtest-2m
```

Equivalent command:

```bash
python scripts/loadtest.py --records 2000000
```

This test validates that CSV generation and transformation are streaming and do not require all 2 million products to reside in memory at once.

The submission-build benchmark processed:

```text
2,000,000 records
20,000 WMS-sized batches
~130.5 MB CSV
~93 MB peak Python RSS
```

The capacity test is intentionally not a claim that a real 2-million-product end-to-end WMS transfer completes in the same few seconds. The external API contracts dominate production time:

- PIM: 2,000,000 / 500 / 10 req/s = ~6m40s theoretical request-rate floor
- WMS: 2,000,000 / 100 / 20 req/s = ~16m40s theoretical request-rate floor
- combined theoretical floor = ~23m20s before latency, transformation, retries and failures

That leaves limited headroom against the 30-minute business target at the four-year scale and is explicitly documented as a capacity risk in `ARCHITECTURE.md`.

---

