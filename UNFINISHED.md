# Unfinished / intentionally not claimed as complete

The assignment explicitly says not to hide unfinished work. These are the remaining items or validation gaps.


## 2. Supplied mock transient-response fixture defect

Both supplied mocks construct their intended 429/503 `JSONResponse` values with positional arguments reversed. On the installed FastAPI/Starlette version those branches raise `TypeError` and surface as HTTP 500 instead of the intended status. The mock files are byte-for-byte unchanged from the original repository.

Effect: live fixture E2E still exercises transient failures, but does not prove distinct 429 handling. Real 429 and 5xx semantics are covered independently with `httpx.MockTransport` tests. The fixture owner should correct those calls if exact mock-status contract testing is desired.


## 5. LocalEmu ECS has two relevant documented fidelity gaps

LocalEmu documents real Docker-backed ECS/Fargate execution and Step Functions ECS `.sync`, but its current ECS implementation does not resolve task-definition `secrets` into container values and does not stream ECS stdout/stderr through the `awslogs` driver into CloudWatch Logs.

Effect: `infra/local` registers ECS/Fargate/IAM/VPC metadata and the emulator smoke test covers the control-plane surface, while the default local business pipeline executes the three application stages as host processes against LocalEmu's S3/SQS/DynamoDB/KMS APIs. `infra/aws` remains authoritative for production ECS secret injection and CloudWatch logging.

If a future LocalEmu release closes those gaps, the local workflow can run the production-equivalent ECS state machine end-to-end without changing the application stages.

## 6. True exactly-once WMS application is impossible with the stated API contract

The repository implements a conservative no-resend ledger and quarantines ambiguous WMS writes. It cannot prove whether WMS committed a request whose response was lost. This is not fixable purely client-side without accepting either duplicate risk or omission risk.

Production resolution: Warehouse should supply a durable idempotency key, operation-status lookup, or staging/commit API. Until then, ambiguous product versions require reconciliation and cause the run to fail.

## 7. PIM snapshot consistency is assumed, not specified

The supplied mock supports stable numeric pages and a `total` field, enabling safe bounded parallel reads. The assessment prose does not explicitly state snapshot/token semantics. If the real PIM mutates page membership during an export, concurrent or sequential pagination can miss/duplicate records.

Production resolution: confirm stable logical-run pagination or add a PIM snapshot/export token. If not available, this is a source-system contract risk.

## 8. Production alarm delivery subscription is intentionally not invented

Terraform creates an SNS alarm topic and wires alarms to it, but does not invent an email/PagerDuty/Slack destination. The topic carries operational metadata only; if policy requires a customer-managed KMS key, add the required CloudWatch service-principal key policy. Add the organisation's real incident-management subscription in deployment configuration.

need to test IAM_ENFORCEMENT=1 localemu start
