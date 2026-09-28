# Requirements traceability

This matrix maps the supplied challenge instructions to implementation, evidence, or an explicit unfinished item. It intentionally distinguishes **implemented**, **tested**, **assumed**, and **not yet complete** rather than treating design intent as proof.

## Business and external-system requirements

| Requirement / expectation | Implementation / evidence / status |
|---|---|
| Daily PIM -> WMS synchronisation | Production Terraform: EventBridge Scheduler -> Step Functions Standard -> ECS/Fargate export/enqueue/worker stages. |
| Current ~250k; expected ~1M in 18 months | Streaming export/transform, bounded concurrency, external-rate capacity analysis in `ARCHITECTURE.md`. |
| Retrieve all PIM products using pagination | `ProductApiClient.iter_pages()`; production export path does not use the materializing compatibility helper. |
| PIM max 10 requests/s | Shared `AsyncRateLimiter` using `PIM_RATE_PER_SECOND=10`. |
| PIM max page size 500 | `PAGE_SIZE=500`; `Settings.__post_init__` rejects values above 500. |
| PIM occasional 429 | Bounded retry, `Retry-After`, backoff/jitter. |
| PIM occasional 5xx | Safe GET retry with bounded attempts/backoff/jitter. |
| PIM response 100ms–3s | Bounded in-flight concurrency separate from request-start rate. |
| PIM API-key authentication | Header client implementation; Secrets Manager injection in production task definition. |
| Complete catalogue export is one logical business run | One run ID/Step Functions execution; full source CSV completed before downstream WMS side effects. |
| No whole-catalogue PIM endpoint | Numeric-page fast path plus documented sequential fallback if total count is unavailable. Snapshot consistency remains an explicit assumption in `UNFINISHED.md`. |
| Produce CSV | `catalogue_export.py` streams source fields to CSV. |
| Store original CSV in S3 | `S3ObjectStore`; deterministic run key. |
| Retain original export 90 days | `infra/aws` S3 lifecycle rule for `catalogue-sync/exports/`. |
| Read and transform catalogue | Streaming `csv.DictReader` + `transform()`; no full-catalogue materialization in the production path. |
| WMS max batch size 100 | `BATCH_SIZE=100`; settings validation and batch producer enforce the contract. |
| WMS max sustained 20 req/s | Shared WMS `AsyncRateLimiter` with default 20 request starts/s. |
| WMS occasional 429 | Retried under the documented assumption that 429 is pre-commit. |
| WMS occasional 5xx | Conservative default: ambiguous/no blind resend; optional retry only when an explicit safe-retry contract is configured. |
| WMS variable latency | Persistent async HTTP client + bounded concurrency. |
| WMS individual validation rejects | Successful response accepted/rejected lists are persisted per product; request-level 4xx is kept separate. |
| WMS can return accepted + rejected together | `warehouse_worker.py` partial-disposition handling; unit tests cover mixed outcomes. |
| “Same product must never be sent twice” | Product-version hash ledger + DynamoDB conditional put; ambiguity is quarantined. Technical interpretation and impossibility boundary are explicit in `ARCHITECTURE.md`. |

## Operational requirements

| Requirement / expectation | Implementation / evidence / status |
|---|---|
| Normally complete within 30 minutes | 1M external-rate sequential floor calculated at ~11m40s before overhead; 30-minute Step Functions duration alarm exists. Realistic 1M AWS/WMS load validation remains required. |
| Scheduled production workload | EventBridge Scheduler + Step Functions Standard + ECS/Fargate. |
| Balance reliability/scalability/complexity/cost | Managed durable services, one WMS drainer process with internal concurrency, on-demand DynamoDB, lifecycle-managed S3, explicit trade-offs. |
| Temporary failures recover automatically where practical | Safe PIM retry, SDK retries, Step Functions retries, SQS/DLQ, renewable run lease, safe WMS retries only when non-commit is known. |
| Operators can tell running/completed/failed | DynamoDB run record, CLI status command, Step Functions status and structured logs/alarms. |
| Small failure must not reprocess successful products | Product-version terminal state + atomic batch/run accounting prevents successful product resubmission/double-counting. |
| Sufficient logging/monitoring | JSON logs, CloudWatch log group, workflow failure/duration alarms, DLQ alarm and recommended dashboard/runbook metrics. |
| Recovery from interrupted runs | Renewable active-run lease, deterministic batch keys, durable queue/state, product-version ledger; lease-recovery smoke target exists. SQS visibility-renewal gap remains P0 unfinished. |
| Original export retained 90 days | Production S3 lifecycle configuration. |
| Growth support | Streaming/O(page)/O(batch) design, rate math for 1M and optional 2M stress analysis. |

## Architecture/data-processing assignment areas

| Assignment area | Evidence / status |
|---|---|
| Execution model / orchestration | Step Functions Standard + three ECS/Fargate stages. |
| Component boundaries | Export, enqueue, drain stages with S3/SQS/DynamoDB boundaries. |
| Failure boundaries / retry strategy | Dedicated architecture failure table; safe-vs-ambiguous remote-call semantics. |
| Concurrency | PIM/WMS semaphores and rate limiters; batch-producer thread pool. LocalEmu concurrency is lowered via explicit test overrides. |
| Recovery | Renewable run lease, deterministic durable work, redelivery-aware product ledger. |
| Scalability | External-rate model + internal DynamoDB-state-write risk documented. |
| Operational visibility | Durable run counters, JSON logs, Step Functions and alarms. |
| Large catalogue handling | Streaming source export and streaming transform/batching. |
| S3 storage | Source + derived batch objects, encryption/versioning/lifecycle in Terraform. |
| Partial processing | Accepted/rejected/ambiguous/request-failed counters and product states. |
| Authentication | API-key headers; Secrets Manager in production. |
| Ambiguous outcomes | No blind retry after possible WMS commit; operator reconciliation required without stronger WMS contract. |

## AWS / infrastructure / local-test requirements

| Requirement / expectation | Implementation / evidence / status |
|---|---|
| Use AWS services where appropriate | Scheduler, Step Functions, ECS/Fargate, ECR, S3, SQS/DLQ, DynamoDB, Secrets Manager, KMS, CloudWatch, SNS, VPC/NAT. |
| Infrastructure represented in Terraform | `infra/aws` production resources; `infra/local` LocalEmu-compatible resources using the HashiCorp AWS provider. |
| Reasonably operable in production | Durable state/queue, run statuses, alarms, DLQ, runbook guidance, encryption/IAM/networking and explicit unfinished work. |
| Runnable/testable locally | Fast mock mode plus LocalEmu/Terraform mode; Ubuntu steps in `README.md`/`LOCAL_DEVELOPMENT.md`. |
| Local IAM least-privilege validation | Strict IAM Makefile target + dedicated local application identity + allow/deny smoke test. |
| Local AWS service smoke | `scripts/localemu_smoke.py` exercises the configured emulated service surface. |
| Local interrupted-run recovery smoke | `scripts/localemu_recovery_smoke.py` targets DynamoDB lease takeover. |
| Local full business E2E | **Not claimed clean yet.** Current code can leave received SQS messages long enough for receipt expiry because it has no per-message visibility heartbeat/bounded prefetch. See `UNFINISHED.md`. |
| 2M local test | `scripts/loadtest.py` generates/scans a local 2M CSV and reports memory/timing. It does not call PIM/WMS/AWS and is not end-to-end SLO proof. |
| Run on Ubuntu | Python/Docker/Terraform/LocalEmu commands documented; local ports are loopback-only. |

## Expected deliverables

| Deliverable | Repository status |
|---|---|
| `ARCHITECTURE.md` with diagram | Present; contains component, execution/data/failure flows, scaling, rate limits, partial/duplicate handling, AWS choices, security, observability and trade-offs. |
| Working code/Terraform demonstrating important architecture | Present, with high-priority unfinished worker reliability items explicitly documented rather than hidden. |
| Meaningful tests | `python -m pytest -q` executed against the attached ZIP: **54 passed**. LocalEmu smoke targets are separate. |
| `UNFINISHED.md` | Present and structured per assignment: remaining work, proposed solution, validation, reason, prioritization/limitation, risk, priority. |
| Local run/test instructions | `README.md` + `LOCAL_DEVELOPMENT.md`. |

## Explicit assumptions / limitations to defend in follow-up

- product-version interpretation of the WMS no-duplicate requirement;
- 429 pre-commit assumption;
- PIM snapshot/stable-pagination assumption;
- no client-only solution can prove exactly-once WMS application after an ambiguous network outcome;
- Standard Step Functions exactly-once orchestration does not imply exactly-once external WMS side effects;
- SQS visibility must be actively managed for long-running received messages;
- the included 2M script validates local streaming memory, not the production 30-minute SLO.
