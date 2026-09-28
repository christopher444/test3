.PHONY: test mocks-up mocks-down localemu-install localemu-up localemu-up-iam localemu-down localemu-status local-infra local-infra-destroy localemu-smoke localemu-iam-smoke localemu-recovery-smoke localemu-pipeline local-run loadtest-2m tf-validate

LOCALEMU_BIN ?= .localemu-venv/bin/localemu
LOCALEMU_PYTHON ?= python3.13

# Fast unit/integration-semantic tests that do not require external infrastructure.
test:
	python -m pytest -q

mocks-up:
	docker compose up -d --build product-api warehouse-api

mocks-down:
	docker compose down

# Keep the emulator isolated from application dependencies and pin the reviewed release.
localemu-install:
	python3 -m venv .localemu-venv
	@command -v "$(LOCALEMU_PYTHON)" >/dev/null || (echo "$(LOCALEMU_PYTHON) is required for LocalEmu 1.2.0 on this project; set LOCALEMU_PYTHON=/path/to/python3.13 if needed" >&2; exit 2)
	$(LOCALEMU_PYTHON) -m venv .localemu-venv

	.localemu-venv/bin/pip install --upgrade pip
	.localemu-venv/bin/pip install -r requirements-local.txt

# Bind only to loopback on the VPS. Persistence is useful for iterative Terraform work.
localemu-up:
	@test -x "$(LOCALEMU_BIN)" || (echo "Run 'make localemu-install' first" >&2; exit 2)
	PERSISTENCE=1 $(LOCALEMU_BIN) start -d --host 127.0.0.1 --port 4566

# Strict IAM mode is safe for Terraform because the canonical provider credential is LocalEmu root.
# The application pipeline uses a separate least-privilege IAM user created by infra/local.
localemu-up-iam:
	@test -x "$(LOCALEMU_BIN)" || (echo "Run 'make localemu-install' first" >&2; exit 2)
	PERSISTENCE=1 IAM_ENFORCEMENT=1 $(LOCALEMU_BIN) start -d --host 127.0.0.1 --port 4566

localemu-down:
	@test -x "$(LOCALEMU_BIN)" || exit 0
	$(LOCALEMU_BIN) stop

localemu-status:
	@test -x "$(LOCALEMU_BIN)" || (echo "Run 'make localemu-install' first" >&2; exit 2)
	$(LOCALEMU_BIN) status

local-infra:
	terraform -chdir=infra/local init
	terraform -chdir=infra/local apply -auto-approve

local-infra-destroy:
	terraform -chdir=infra/local destroy -auto-approve

localemu-smoke:
	python scripts/localemu_smoke.py

localemu-iam-smoke:
	AWS_ACCESS_KEY_ID="$$(terraform -chdir=infra/local output -raw local_app_access_key_id)" \
	AWS_SECRET_ACCESS_KEY="$$(terraform -chdir=infra/local output -raw local_app_secret_access_key)" \
	QUEUE_URL="$$(terraform -chdir=infra/local output -raw queue_url)" \
	AWS_REGION=eu-west-1 LOCALEMU_ENDPOINT=http://127.0.0.1:4566 \
	python scripts/localemu_iam_smoke.py
localemu-recovery-smoke:
	AWS_ACCESS_KEY_ID="$$(terraform -chdir=infra/local output -raw local_app_access_key_id)" \
	AWS_SECRET_ACCESS_KEY="$$(terraform -chdir=infra/local output -raw local_app_secret_access_key)" \
	AWS_REGION=eu-west-1 AWS_ENDPOINT_URL=http://127.0.0.1:4566 \
	STATE_BACKEND=dynamodb RUNS_TABLE=catalogue-sync-runs \
	$(PYTHON) scripts/localemu_recovery_smoke.py
	

# Production-shaped application stages, but executed as host processes against LocalEmu's
# S3/SQS/DynamoDB/KMS endpoints. Start LocalEmu + Terraform + mocks first.
localemu-pipeline:
	./scripts/run_localemu_pipeline.sh

# Smallest dependency path: mocks + filesystem/memory state, useful for rapid development.
local-run:
	python -m app.cli run-local

loadtest-2m:
	python scripts/loadtest.py --records 2000000

tf-validate:
	terraform -chdir=infra/local init -backend=false
	terraform -chdir=infra/local fmt -check
	terraform -chdir=infra/local validate
	terraform -chdir=infra/aws init -backend=false
	terraform -chdir=infra/aws fmt -check
	terraform -chdir=infra/aws validate
