# Unfinished work and next steps

The challenge explicitly says not to hide unfinished work. This file lists the remaining implementation or validation gaps found in the attached repository. Each item includes the fields requested by the assignment.

Priority scale:

- **P0** — correctness/reliability issue that should be fixed before production approval.
- **P1** — important production readiness or contract risk.
- **P2** — useful validation/fidelity/operational improvement that can follow the core correctness work.

## 1. SQS receipt visibility renewal and bounded prefetch

**What remains unfinished**  
The worker sets a visibility timeout when receiving an SQS message but does not renew that message's visibility while processing. It can also receive messages and buffer them in an internal `asyncio.Queue`, so a receipt's visibility clock can run before a consumer begins useful work. `DeleteMessage` happens only after product-state updates and atomic batch accounting.

**Proposed solution**  
Add a queue operation for per-message `ChangeMessageVisibility`, start a visibility-heartbeat task while each received message is owned, stop it immediately before/after successful delete, and bound receive/prefetch to available processing capacity. On redelivery of an already-complete batch, skip business processing and delete using the newest receipt handle after verifying durable batch state.

**How to validate**  
Add unit tests with a queue fake that expires receipt handles; add tests for long processing, redelivery after `complete_batch`, and heartbeat cancellation. Then run a LocalEmu E2E with processing deliberately longer than the initial visibility interval and verify all messages are deleted and the run reaches the expected terminal status.

**Why it was not completed**  
This pass was explicitly restricted to documentation/text changes; application code was not to be modified.

**Deliberate prioritisation or limitation**  
Implementation limitation for this docs-only pass; identified as the highest-priority code follow-up.

**Risk if left unfinished**  
A batch can be durably completed while its SQS receipt expires, causing delete failure/redelivery and potentially turning an otherwise processed run into an infrastructure failure. The product-version ledger prevents blind resend of terminal products, but operational completion/recovery is still incorrect.

**Priority**  
**P0**.

## 2. Worker child-task failure supervision

**What remains unfinished**  
`drain_queue()` starts one poller task and multiple consumer tasks, then awaits the poller and `work_queue.join()` before gathering consumer exceptions. A consumer that fails can therefore stop processing while its exception is not surfaced immediately.

**Proposed solution**  
Use structured concurrency (`asyncio.TaskGroup` on supported Python) or an explicit `asyncio.wait(..., return_when=FIRST_EXCEPTION)` supervisor. On any child failure, stop polling, cancel siblings, gather cancellations, persist a clear run failure reason, and exit promptly.

**How to validate**  
Inject queue-delete, S3-read, state-store and WMS exceptions into one consumer while other messages remain queued. Assert the run fails promptly, does not hang on `work_queue.join()`, and records the original failure.

**Why it was not completed**  
Application code changes are out of scope for the current requested pass.

**Deliberate prioritisation or limitation**  
Implementation limitation for this pass.

**Risk if left unfinished**  
Failures can look like stalled progress and delay operator feedback; queued work can remain unprocessed until another timeout/recovery path occurs.

**Priority**  
**P0**.

## 3. LocalEmu runner has shadowed timeout defaults

**What remains unfinished**  
`scripts/run_localemu_pipeline.sh` assigns `RUN_LOCK_SECONDS` and `SQS_VISIBILITY_TIMEOUT_SECONDS` early, then later attempts to set larger LocalEmu-specific defaults with the same `${VAR:-...}` pattern. Because the variables are already set by the first assignment, the later default values do not replace them.

**Proposed solution**  
Remove the duplicate earlier assignments or define LocalEmu defaults exactly once. Keep production defaults in `app/config.py`; keep LocalEmu-only tuning in the runner.

**How to validate**  
Launch the script, inspect `/proc/<worker-pid>/environ`, and assert the worker inherited the intended LocalEmu values. Add a shell-level regression test if the runner remains part of the submission.

**Why it was not completed**  
The user explicitly requested no code/script changes in this pass.

**Deliberate prioritisation or limitation**  
Deliberate scope limitation.

**Risk if left unfinished**  
Operators may believe a larger local visibility/lease value is active when the process is still using the smaller earlier default, making local failures confusing and non-reproducible.

**Priority**  
**P1**. A documented environment-variable workaround is provided in `LOCAL_DEVELOPMENT.md`.

## 4. True exactly-once WMS application is impossible with the stated API contract

**What remains unfinished**  
The client cannot prove whether WMS committed a POST if the request may have been transmitted but the response is lost. Client-side state alone cannot simultaneously guarantee no duplicates and no omissions in that case.

**Proposed solution**  
Obtain one of: a durable WMS idempotency key, an operation-status lookup, or a staging/commit API. Then make retries conditional on that server-side contract.

**How to validate**  
Contract tests against WMS that deliberately drop responses after commit, then retry/reconcile by idempotency key and prove one final application per product version.

**Why it was not completed**  
It requires a capability/contract from a system outside this team's ownership.

**Deliberate prioritisation or limitation**  
External-contract limitation, handled conservatively by design.

**Risk if left unfinished**  
Ambiguous product versions require operator reconciliation and cause the run to fail rather than risk a duplicate.

**Priority**  
**P1**.

## 5. PIM snapshot consistency is assumed, not specified

**What remains unfinished**  
The supplied mock exposes stable numeric pages plus `total`; the challenge does not explicitly guarantee a snapshot/export token. If real PIM page membership changes during a run, concurrent or sequential pagination can miss or duplicate rows.

**Proposed solution**  
Confirm stable logical-run pagination or obtain a snapshot/as-of/export token. If PIM only supports cursors, use cursor chaining and update the throughput model accordingly.

**How to validate**  
Run contract tests where products are inserted/updated during export and prove the snapshot contains each intended source version exactly once.

**Why it was not completed**  
The required source-system contract is not present in the supplied challenge.

**Deliberate prioritisation or limitation**  
External-contract limitation documented explicitly.

**Risk if left unfinished**  
A completed run can be operationally green while its source export is not a provably consistent catalogue snapshot.

**Priority**  
**P1**.

## 6. Production-scale timing and DynamoDB write-volume validation

**What remains unfinished**  
The included 2M script only generates/scans a local CSV. It does not exercise PIM/WMS latency, S3/SQS/DynamoDB round trips, product-ledger write volume, ECS startup, retries or the 30-minute SLO. The current worker performs fine-grained product-ledger operations per product.

**Proposed solution**  
Run production-like 250k and 1M tests in a sandbox AWS account against representative API simulators/contract environments. Measure DynamoDB latency/throttling/cost, SQS age/visibility, WMS p95/p99 latency, worker CPU/thread-pool behavior and total duration. Optimize product-ledger I/O only from measured bottlenecks.

**How to validate**  
Define a repeatable load profile, capture CloudWatch metrics and end-to-end durations, and demonstrate normal 1M completion with operational headroom below 30 minutes.

**Why it was not completed**  
The challenge does not require live AWS deployment, and the attached repository contains no sandbox benchmark evidence artifact.

**Deliberate prioritisation or limitation**  
Deliberate prioritisation: architecture/code demonstration first, full production performance qualification later.

**Risk if left unfinished**  
Theoretical external-rate math may understate internal state-management overhead; the 30-minute SLO at 1M is not yet empirically proven.

**Priority**  
**P1**.

## 7. Supplied mock transient-response fixture defect

**What remains unfinished**  
Both supplied mocks construct intended 429/503 `JSONResponse` values with reversed positional arguments. With the pinned FastAPI/Starlette stack these branches surface as HTTP 500 rather than the intended status.

**Proposed solution**  
Have the fixture owner correct the response construction, or test against a corrected external copy while preserving the supplied source if modification is forbidden.

**How to validate**  
Contract-test the mocks and assert actual 429 + `Retry-After` and 503 responses; keep the existing client unit tests for exact status-specific retry semantics.

**Why it was not completed**  
This repository intentionally does not change supplied mock behavior in the current pass.

**Deliberate prioritisation or limitation**  
Deliberate scope choice.

**Risk if left unfinished**  
Live fixture E2E does not prove distinct 429 behavior even though unit tests cover it.

**Priority**  
**P2**.

## 8. LocalEmu ECS/production-fidelity validation

**What remains unfinished**  
The default local business pipeline executes host processes against emulated data services rather than proving the complete production Step Functions -> ECS/Fargate -> Secrets Manager -> CloudWatch Logs runtime path.

**Proposed solution**  
Use future emulator support where sufficient, but more importantly run a sandbox AWS integration deployment with the production Terraform.

**How to validate**  
Terraform plan/apply in sandbox; run the scheduled/state-machine path; verify task role, secret injection, network egress, log delivery, alarms and terminal run state.

**Why it was not completed**  
Live AWS deployment is explicitly not required by the challenge; local emulation has known fidelity boundaries.

**Deliberate prioritisation or limitation**  
Deliberate prioritisation and emulator limitation.

**Risk if left unfinished**  
Local smoke tests can miss production IAM/network/service-integration differences.

**Priority**  
**P2**.

## 9. Production alarm delivery destination is deployment-specific

**What remains unfinished**  
Terraform creates/wires an SNS alarm topic but does not invent an organisation email/PagerDuty/Slack subscription.

**Proposed solution**  
Provide the actual incident-management integration as environment/deployment configuration and add any required topic encryption/key policy.

**How to validate**  
Trigger a test alarm in a non-production account and verify acknowledgement reaches the real on-call destination.

**Why it was not completed**  
No organisation-specific incident endpoint is supplied by the challenge.

**Deliberate prioritisation or limitation**  
Deliberate avoidance of fabricated operational configuration.

**Risk if left unfinished**  
Alarms exist but may not reach a human responder.

**Priority**  
**P2**.

