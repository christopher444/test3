# Local development and AWS emulation

## 1. Why LocalEmu is used

This repository uses **LocalEmu 1.2.0** as the local AWS emulator. The selection is based on the actual services and failure behaviors required by this challenge rather than on generic popularity.

For this workload the important documented capabilities are:

| Requirement from this project | LocalEmu capability used |
|---|---|
| Terraform-provisioned AWS resources | Standard HashiCorp AWS provider pointed at one local endpoint. |
| S3 source export + lifecycle/versioning | S3 emulation with persistence/versioning and normal SDK/Terraform APIs. |
| FIFO queue, visibility, DLQ/redrive | SQS implementation with FIFO, visibility tracking and DLQ/redrive behavior. |
| Durable run/idempotency state | DynamoDB including transactions/conditional operations. |
| Encryption API surface | KMS implementation; `infra/local` creates a CMK and uses it for local resource configuration. |
| API-key secret storage | Secrets Manager with persistent secret versions. |
| Orchestration | Step Functions Standard interpreter with DynamoDB integration and documented ECS `.sync` support. |
| Scheduled trigger API | EventBridge Scheduler implementation; the local schedule is provisioned but deliberately disabled. |
| Containers/control plane | ECS supports real Docker-backed FARGATE/EC2 tasks when Docker is available. |
| IAM failure testing | Optional strict IAM policy enforcement. |
| Monitoring surface | CloudWatch Logs, CloudWatch metrics/alarms and SNS APIs. |
| Production-like control plane | ECR, EC2/VPC, STS and IAM resources can be provisioned locally. |
| Failure drills | Optional API throttling and latency simulation. |

LocalEmu runs most services in-process. Docker is only required for services that need a real runtime such as ECS. The host-executed application pipeline therefore remains usable even when the ECS Docker backend is not being tested.

Reviewed upstream material:

- GitHub: https://github.com/localemu/localemu
- Installation: https://localemu.cloud/docs/installation/
- Terraform: https://localemu.cloud/docs/terraform
- Services/coverage: https://localemu.cloud/docs/services/
- Step Functions: https://localemu.cloud/docs/stepfunctions
- ECS: https://localemu.cloud/docs/ecs
- Scheduler: https://localemu.cloud/docs/scheduler
- IAM enforcement: https://localemu.cloud/docs/iam-enforcement/

## 2. Important LocalEmu limitations for this architecture

Local emulation is a confidence tool, not proof that AWS will behave identically.

The most relevant current differences are:

1. LocalEmu ECS can execute real Docker tasks, but its documentation states that ECS task-definition `secrets` are currently accepted without resolving/injecting the Secrets Manager value. Production uses ECS secret injection; local host-stage tests use the mock API keys directly instead.
2. LocalEmu ECS currently does not forward task stdout/stderr through the ECS `awslogs` driver into CloudWatch Logs. Local ECS container logs must therefore be inspected through Docker; production uses CloudWatch Logs.
3. The local Terraform creates one small VPC/subnet/security group to exercise the control-plane API. It does **not** pretend to reproduce the production two-AZ/private-subnet/two-NAT failure model.
4. The normal local business-pipeline test executes `export`, `enqueue` and `worker` as host Python processes against LocalEmu data services. `infra/local` still registers ECS/Fargate task metadata and a Step Functions workflow for control-plane smoke testing; `infra/aws` remains authoritative for the production Step Functions -> Fargate workflow.
5. LocalEmu IAM defaults can be permissive unless enforcement is explicitly enabled. `make localemu-up-iam` enables strict enforcement and the host pipeline uses a dedicated local IAM user with the same S3/SQS/DynamoDB/KMS permissions as the ECS task role.

These boundaries are intentional: the repository should not claim a local emulator proves behavior it does not implement.

## 3. Ubuntu/VPS ports

The supplied server already uses 22, 53, 80, 443, 5432 and several high loopback/Tailscale ports. This project uses only:

- `127.0.0.1:8001` — PIM mock
- `127.0.0.1:8002` — WMS mock
- `127.0.0.1:4566` — LocalEmu gateway

All three are bound to loopback for the documented workflow. Before starting:

```bash
sudo ss -tulpn | grep -E ':(4566|8001|8002)\b' || true
```

If any is occupied, change the mock Compose port or start LocalEmu with a different port and set `endpoint`/`AWS_ENDPOINT_URL` consistently.

## 4. Prerequisites

Recommended on Ubuntu:

- Python 3.11+ for the application; **Python 3.13 for the pinned LocalEmu 1.2.0 runtime used by this repository**
- Docker Engine + Docker Compose plugin for the supplied API mocks and optional ECS runtime tests
- Terraform >=1.5
- sufficient disk for Docker images and the ~130 MB 2M-row capacity artifact while the test is running

Application environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

Install LocalEmu separately:

```bash
make localemu-install
.localemu-venv/bin/python -V
.localemu-venv/bin/localemu --version
```

The separate environment is deliberate: the emulator is a development dependency and should not inflate or change the production application's dependency graph.

`make localemu-install` defaults to `LOCALEMU_PYTHON=python3.13`. On the Ubuntu VPS, LocalEmu 1.2.0's Step Functions validation path failed under Python 3.12 (`warnings.deprecated` is a Python 3.13 API). Keep Ubuntu `/usr/bin/python3` and the application venv on their normal versions; install/use Python 3.13 side-by-side only for `.localemu-venv`. Override with `LOCALEMU_PYTHON=/full/path/to/python3.13` when using pyenv.

## 5. Start LocalEmu

Recommended development mode with strict IAM enforcement:

```bash
make localemu-up-iam
make localemu-status
curl -fsS http://127.0.0.1:4566/_localemu/health
```

For a quicker permissive session:

```bash
make localemu-up
```

Both commands enable LocalEmu persistence and bind the gateway to `127.0.0.1:4566` rather than exposing it publicly from the VPS. The production S3 bucket policy rejects insecure transport; the local S3 endpoint deliberately uses HTTP on loopback, so `infra/local` does not copy that TLS-deny statement. Production `infra/aws` remains authoritative for that transport control.

Stop it with:

```bash
make localemu-down
```

## 6. Provision local AWS resources with Terraform

Yes: the local AWS resources are provisioned with normal Terraform.

```bash
terraform -chdir=infra/local init
terraform -chdir=infra/local plan
terraform -chdir=infra/local apply -auto-approve
```

Or:

```bash
make local-infra
```

The provider uses LocalEmu's canonical local root credential only for provisioning. The two API-key values created by `infra/local` are fixed mock credentials and therefore may exist in local Terraform state; real production API-key values are never declared in production Terraform state. Terraform creates a separate `catalogue-sync-local-app` IAM user mirroring the production application task-role permissions; `scripts/run_localemu_pipeline.sh` uses that identity for application calls. With `IAM_ENFORCEMENT=1`, missing application permissions therefore fail locally instead of being hidden by root credentials.

Local resources include:

- KMS key + alias
- S3 bucket with versioning, KMS configuration and 90-day source/7-day derived lifecycle rules
- FIFO work queue + FIFO DLQ/redrive policy
- DynamoDB run/batch/idempotency tables with TTL/PITR/encryption configuration
- Secrets Manager mock-key secrets
- CloudWatch Logs group
- CloudWatch DLQ, workflow-failure and >30-minute alarms + SNS topic
- ECR repository
- ECS cluster + application task definition
- Step Functions Standard smoke workflow using direct DynamoDB integrations
- disabled EventBridge Scheduler schedule targeting that workflow
- IAM roles + local application IAM identity
- STS-backed identity surface
- local VPC + subnet + security group

Destroy only local emulated resources with:

```bash
make local-infra-destroy
```

## 7. Verify the AWS service surface

After Terraform apply:

```bash
make localemu-smoke
```

`scripts/localemu_smoke.py` performs real SDK calls through LocalEmu and checks:

- LocalEmu health and STS caller identity
- S3 write/read/versioning
- KMS encrypt/decrypt
- DynamoDB transaction semantics
- SQS FIFO send/receive/delete
- Secrets Manager retrieval
- CloudWatch Logs and alarm resources
- SNS topic
- ECR repository
- ECS cluster
- VPC presence
- IAM role
- Scheduler state
- Step Functions execution reaching `SUCCEEDED`

The script fails fast if any expected integration is missing.

When LocalEmu was started with strict IAM enforcement, also prove that the application identity is constrained rather than merely valid:

```bash
make localemu-iam-smoke
```

That test verifies an allowed SQS operation succeeds and an intentionally unauthorized account-wide S3 listing returns `AccessDenied`.

## 7.1 Verify interrupted-run lease recovery

```bash
make localemu-recovery-smoke
```

This uses the least-privilege local application identity and the real LocalEmu DynamoDB API. It acquires a short lease for an old run, lets the heartbeat/lease expire, then proves a new run can reclaim the lock and that the old run becomes `FAILED` with `failure_type=INTERRUPTED`. This specifically covers the hard-stop/VPS-restart case where a `finally` block cannot release the lock.

The application defaults are `RUN_LOCK_SECONDS=120` and `RUN_LOCK_HEARTBEAT_SECONDS=30`; normal stages refresh the lease in the background. DynamoDB TTL is not used for correctness because TTL deletion is asynchronous. The active lock uses `lease_expires_at` and intentionally omits the table TTL attribute.

### One-time migration from the old two-hour lock

A lock created by an older build with `RUN_LOCK_SECONDS=7200` keeps its original `expires_at`; changing the code/default does not retroactively shorten that DynamoDB item. After confirming no old pipeline process is still running, expire the **LocalEmu-only** active lock once so the next run can exercise normal stale-lease takeover and mark the previous owner interrupted:

```bash
.localemu-venv/bin/awsemu dynamodb update-item \
  --table-name catalogue-sync-runs \
  --key '{"run_id":{"S":"__ACTIVE_RUN__"}}' \
  --update-expression 'SET expires_at = :expired' \
  --expression-attribute-values '{":expired":{"N":"0"}}'
```

Do not run this against production unless operations have independently verified that the recorded owner is no longer executing.

## 8. Run the application against LocalEmu

Start the unchanged mocks:

```bash
make mocks-up
```

Then run all three durable application stages against LocalEmu:

```bash
make localemu-pipeline
```

The script obtains these values from Terraform outputs:

- local least-privilege AWS access key/secret
- SQS queue URL
- KMS key ARN

and sets:

```text
STORAGE_BACKEND=s3
STATE_BACKEND=dynamodb
QUEUE_BACKEND=sqs
AWS_ENDPOINT_URL=http://127.0.0.1:4566
```

Therefore the real application adapters exercise S3, SQS, DynamoDB and KMS-aware writes rather than falling back to memory/filesystem state.

You can inspect a particular durable run afterwards:

```bash
AWS_ENDPOINT_URL=http://127.0.0.1:4566 \
AWS_REGION=eu-west-1 \
AWS_ACCESS_KEY_ID="$(terraform -chdir=infra/local output -raw local_app_access_key_id)" \
AWS_SECRET_ACCESS_KEY="$(terraform -chdir=infra/local output -raw local_app_secret_access_key)" \
STORAGE_BACKEND=s3 STATE_BACKEND=dynamodb QUEUE_BACKEND=sqs \
python -m app.cli status --run-id '<run-id>'
```

## 9. Fast mode without AWS emulation

For transformation/client work where AWS semantics are irrelevant:

```bash
make mocks-up
RETRY_WMS_5XX=true make local-run
```

This uses filesystem object storage plus in-memory queue/state. It is intentionally not the production-confidence path; use the LocalEmu path before submission/release.

## 10. Throttling and latency drills

LocalEmu supports opt-in throttling and latency simulation. Stop the current emulator and start it manually, still loopback-only:

```bash
make localemu-down
PERSISTENCE=1 \
IAM_ENFORCEMENT=1 \
SIMULATE_THROTTLING=1 \
THROTTLE_RATE=0.02 \
SIMULATE_LATENCY=1 \
.localemu-venv/bin/localemu start --host 127.0.0.1 --port 4566
```

Do **not** enable random AWS throttling while first provisioning Terraform; provision a clean baseline, then use fault injection for application/recovery drills. PIM/WMS 429/5xx behavior is tested separately because those are external systems, not AWS APIs.

## 11. Optional ECS/Fargate smoke testing

LocalEmu documents real Docker-backed ECS `FARGATE` task execution, `awsvpc` attachment and task-role credentials when Docker is available. `infra/local` registers the project task definition so this control plane can be inspected.

The repository deliberately does not call the registered task as the default end-to-end business test because two LocalEmu ECS differences are directly relevant here: Secrets Manager values are not injected through ECS task-definition `secrets`, and ECS container stdout/stderr is not streamed through `awslogs` to CloudWatch Logs. Those are explicitly production-only validations.

The production ECS/Step Functions definition in `infra/aws` is unchanged and remains the deployment contract.

## 12. Two-million-record local capacity test

```bash
python scripts/loadtest.py --records 2000000
```

The test generates then streams a 2,000,000-record CSV and validates 100-record batching without materialising the full catalogue in RAM.

The earlier submission environment measured approximately:

- 2,000,000 rows
- 20,000 WMS-sized batches
- ~130.5 MB CSV
- ~93 MB Python peak RSS

That is an application-memory test, not an excuse to bypass external API limits. At 20 WMS requests/s with 100 products/request, 2M products require at least **1,000 seconds (~16m40s)** of WMS request starts. PIM at 10 requests/s and 500/page adds at least **400 seconds (~6m40s)**. The combined hard floor is therefore about **23m20s** before latency/retries/overhead, leaving limited margin against the 30-minute expectation.

## 13. Validation sequence before submission/deployment

Run, in order:

```bash
make test
make localemu-install
make localemu-up-iam
make local-infra
make localemu-smoke
make localemu-iam-smoke
make mocks-up
make localemu-pipeline
make loadtest-2m
make tf-validate
```

Then review `UNFINISHED.md`. A green emulator test increases confidence in API usage and recovery semantics; it does not replace a Terraform plan in a sandbox AWS account or an external-system contract test.
