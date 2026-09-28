# Requirements traceability

This matrix is intentionally explicit so a reviewer can trace every business/assessment expectation to a design decision, implementation, test, or documented limitation.

| Requirement / expectation | Implementation / evidence |
|---|---|
| Daily PIM -> WMS synchronisation | EventBridge Scheduler -> Step Functions Standard -> export/enqueue/worker Fargate stages (`infra/aws`). |
| Current ~250k; ~1M in 18 months; assume ~2M within four-year roadmap | Streaming/O(page) export, O(batch) transform, bounded async worker; 2M capacity math and roadmap in `ARCHITECTURE.md`; 2M streaming load test in `scripts/loadtest.py`. |
| Retrieve all PIM products through pagination | `ProductApiClient.iter_pages`; page size 500; no all-catalogue list in production path. |
| PIM max 10 requests/s | `AsyncRateLimiter` configured by `PIM_RATE_PER_SECOND=10`. |
| PIM page max 500 | `PAGE_SIZE=500`; Terraform/runtime env sets contract value. |
| PIM occasional 429 | Retried with `Retry-After`, backoff, jitter. |
| PIM occasional 5xx | Safe GET retry with bounded attempts/backoff/jitter. |
| PIM latency ~100ms–3s | Bounded in-flight concurrency (`PIM_MAX_IN_FLIGHT`) decoupled from 10 req/s start rate. |
| PIM API-key auth | Env locally; Secrets Manager injection in AWS. |
| Complete catalogue export is one logical business run | One Step Functions execution/run ID; source CSV finalized before downstream WMS stage. |
| PIM has no whole-catalogue endpoint | Pagination client; numeric page concurrency assumption documented. |
| Do not modify supplied mock behavior | Mock source files are byte-for-byte unchanged (hash-verified). Their malformed `JSONResponse` transient branches are documented, not silently fixed. |
| Produce CSV | `catalogue_export.py` streams CSV with original source columns. |
| Store original CSV in S3 | `S3ObjectStore`; production S3 bucket and key per run. |
| Retain original export 90 days | Terraform lifecycle on `catalogue-sync/exports/` = 90 days. |
| Read/transform catalogue | Streaming `csv.DictReader` + `transform()`; no full materialization. |
| WMS batch max 100 | `BATCH_SIZE=100`; batch producer groups <=100. |
| WMS max sustained 20 req/s | Shared WMS `AsyncRateLimiter`, default 20 request starts/s. |
| WMS occasional 429 | Retry when assumed pre-processing rejection; release claims/requeue on exhausted safe retry. |
| WMS occasional 5xx | Default is ambiguous/no resend; optional retry only after explicit safe-5xx contract via `RETRY_WMS_5XX=true`. |
| WMS response latency varies significantly | Persistent HTTP connection pool + up to 80 in-flight calls while respecting 20 starts/s. |
| WMS individual records may be validation rejected | Worker persists `REJECTED` only from successful WMS partial-disposition responses; request-level 4xx is kept separate as an integration failure. |
| WMS success may contain accepted + rejected | Explicit partial-response handling in `warehouse_worker.py`; tested in `test_warehouse_worker.py`. |
| “Same product must never be sent twice” | Product-version hash ledger + DynamoDB conditional put; SQS FIFO defense-in-depth; ambiguity and interpretation documented. |
| Requirement's technical meaning intentionally incomplete | `ARCHITECTURE.md` assumption table records interpretation, effect, and alternative if invalid. |
| Normally complete within 30 minutes | Hard API-limit calculation and 30-minute CloudWatch alarm; four-year boundary documented. |
| Scheduled production workload | EventBridge Scheduler + Step Functions Standard + Fargate. |
| Balance reliability/scalability/operational complexity/cost | Managed durable services; one async WMS task because horizontal scaling cannot beat external rate; explicit trade-offs. |
| Temporary failures recover automatically where practical | Safe PIM retries; AWS SDK/stage retries; SQS redelivery/DLQ; safe WMS retry only when non-commit is known. |
| Operators can determine running/completed/failed | DynamoDB run record + `python -m app.cli status`; Step Functions execution status; structured logs/alarms. |
| Temporary failures recover automatically where practical | Safe PIM retries; AWS SDK/stage retries; SQS redelivery/DLQ; 300s SQS visibility protects in-flight batches; renewable run lease recovers crashed stages; safe WMS retry only when non-commit is known. |
| Operators can determine running/completed/failed | DynamoDB run record + `python -m app.cli status`; Step Functions execution status; structured logs/alarms. Lease takeover marks abandoned runs `FAILED` with `failure_type=INTERRUPTED`, timestamps and `recovered_by_run_id`. |
| Small number of failures must not cause successful products to be reprocessed | Per-product-version terminal state + per-batch atomic accounting; accepted products skipped on redelivery. |
| Sufficient logging/monitoring | JSON stdout logs, CloudWatch log group, workflow-duration/failure and DLQ alarms, metrics/runbook recommendations. |
| Recovery from interrupted runs | Deterministic export/batch keys, SQS, DDB batch state, idempotency ledger, stage retries; conservative ambiguous state after uncertain WMS call. |
| Large catalogue handling | O(page) export, O(batch) transform/queueing; local 2M test completed. |
| Partial processing | Product-level accepted/rejected/ambiguous states and run counters. |
| Authentication/security | Secrets Manager, KMS, private Fargate tasks, no ingress, least-privilege roles, S3 public block, ECR scanning. |
| AWS where appropriate | Scheduler, Step Functions, ECS/Fargate, ECR, S3, SQS/DLQ, DynamoDB, Secrets Manager, KMS, CloudWatch, SNS, VPC/NAT. |
| Terraform | `infra/aws` production resources; `infra/local` LocalEmu resources using the standard HashiCorp AWS provider. |
| Reasonably operable in production | Durable state, statuses, alarms, DLQ, runbook, failure boundaries, lifecycle, HA NAT choice, no always-on worker. |
| Runnable/testable locally | Mocks + filesystem/memory fast mode; LocalEmu + Terraform durable AWS-emulation mode; Ubuntu commands in `LOCAL_DEVELOPMENT.md`. |
| Live local integration behavior | Process-level E2E against the unchanged supplied mocks: 12,000 export rows, 120 batches, 11,988 accepted, 12 validation rejects, final `COMPLETED`. Docker packaging itself remains to be checked on the VPS. |
| Local AWS framework with maximum relevant services | LocalEmu 1.2.0 selected for the specific required service surface: S3, SQS/DLQ, DynamoDB transactions, KMS, Secrets Manager, IAM enforcement, CloudWatch, SNS, ECR/ECS, Step Functions, Scheduler, STS and EC2/VPC; official research links documented. |
| Local AWS resources provisionable with Terraform | Yes; standard AWS-provider endpoint overrides in `infra/local`; local KMS/S3/SQS/DynamoDB/Secrets/IAM/CloudWatch/SNS/ECR/ECS/Step Functions/Scheduler/VPC resources are declared there. |
| Local IAM least-privilege validation | `make localemu-up-iam` enables LocalEmu strict IAM enforcement; `infra/local` creates a dedicated application identity matching task-role S3/SQS/DynamoDB/KMS permissions, and the host pipeline uses it rather than emulator-root credentials. |
| Local AWS failure injection | LocalEmu throttling/latency simulation commands documented separately from PIM/WMS failure tests. |
| Do not overclaim emulator fidelity | `LOCAL_DEVELOPMENT.md` records LocalEmu ECS limitations relevant here (task-definition secret resolution and ECS `awslogs` streaming); `infra/aws` remains authoritative for production. |
| Test 2M locally | `scripts/loadtest.py`; actual 2M generation/stream scan result documented. Full WMS soak remains contract-rate limited by design. |
| Run on Ubuntu VPS | Linux/Docker/Python/Terraform instructions; no platform-specific code. |
| Avoid supplied occupied ports | Mocks bind loopback 8001/8002 and LocalEmu binds loopback 4566; no 22/80/443/5432 conflict. |
| Architecture diagram | Mermaid in `ARCHITECTURE.md`. |
| Explain major components / execution / data / retry flows | Dedicated sections in `ARCHITECTURE.md`. |
| Scaling approach | External-rate capacity model and four-year roadmap in `ARCHITECTURE.md`. |
| Duplicate/ambiguous processing | Dedicated section explains impossibility boundary and required WMS contract improvement. |
| Security/IAM | Dedicated architecture section + Terraform roles/policies. |
| Observability | Dedicated architecture section + Terraform logs/alarms/SNS topic. |
| Important trade-offs | Explicit numbered trade-off section. |
| Working code demonstrates important architecture | Async API clients, streaming export, durable batches, SQS adapter, DDB idempotency/run state, CLI stages. |
| Meaningful tests | 54 passing unit tests cover API contracts, pagination/retry, transformation, batching, rate/retry utilities, run-lease ownership/reclaim, partial success, duplicate redelivery, interrupted claims, empty catalogues, stale-run queue cleanup, WMS ambiguity semantics, and repository hygiene. `make localemu-recovery-smoke` separately exercises stale-lease takeover against LocalEmu DynamoDB. |
| Do not hide unfinished work | `UNFINISHED.md`. |
| Include local running/testing instructions | `README.md` and `LOCAL_DEVELOPMENT.md`. |
| Research 3–4 engineering frameworks and condense into project | AWS Well-Architected, Serverless Lens, Amazon Builders' Library reliability patterns, Google SRE — mapped in `ARCHITECTURE.md`. |
