from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from threading import Lock
from typing import Any, Protocol

import boto3
from app.aws import boto_config
from botocore.exceptions import ClientError

from app.config import Settings, settings


class StateStore(Protocol):
    def acquire_run_lock(self, run_id: str, lease_seconds: int | None = None) -> bool: ...
    def renew_run_lock(self, run_id: str, lease_seconds: int | None = None) -> bool: ...
    def release_run_lock(self, run_id: str) -> None: ...
    def create_run(self, run_id: str) -> None: ...
    def set_run_fields(self, run_id: str, **fields: Any) -> None: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    def create_batch(self, run_id: str, batch_id: str, product_count: int, s3_key: str) -> None: ...
    def claim_product(self, product_key: str, run_id: str, sku: str) -> str: ...
    def release_product_claim(self, product_key: str, run_id: str) -> None: ...
    def mark_product(self, product_key: str, status: str, reason: str | None = None) -> None: ...
    def get_product(self, product_key: str) -> dict[str, Any] | None: ...
    def complete_batch(self, run_id: str, batch_id: str, counts: dict[str, int]) -> bool: ...


@dataclass
class MemoryStateStore:
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    batches: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    products: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def acquire_run_lock(self, run_id: str, lease_seconds: int | None = None) -> bool:        
        with self._lock:
            now = int(time.time())
            lease = int(lease_seconds or settings.run_lock_seconds)            
            lock = self.runs.get("__ACTIVE_RUN__")
            if lock and int(lock.get("lease_expires_at", lock.get("expires_at", 0))) > now and lock.get("owner") != run_id:                
                return False

            stale_owner = None
            if lock and lock.get("owner") != run_id and int(lock.get("lease_expires_at", lock.get("expires_at", 0))) <= now:
                stale_owner = str(lock.get("owner"))


            self.runs["__ACTIVE_RUN__"] = {
                "run_id": "__ACTIVE_RUN__",
                "owner": run_id,
                "heartbeat_at": now,
                "lease_expires_at": now + lease,
             }

            if stale_owner:
                previous = self.runs.get(stale_owner)
                if previous and previous.get("status") == "RUNNING":
                    previous.update({
                        "status": "FAILED",
                        "failure_type": "INTERRUPTED",
                        "failure_reason": "run lease expired before completion",
                        "interrupted_at": now,
                        "finished_at": now,
                        "recovered_by_run_id": run_id,
                    })
            return True

    def renew_run_lock(self, run_id: str, lease_seconds: int | None = None) -> bool:
        with self._lock:
            now = int(time.time())
            lease = int(lease_seconds or settings.run_lock_seconds)
            lock = self.runs.get("__ACTIVE_RUN__")
            if not lock or lock.get("owner") != run_id:
                return False
            lock["heartbeat_at"] = now
            lock["lease_expires_at"] = now + lease
            run = self.runs.get(run_id)
            if run is not None:
                run["last_heartbeat_at"] = now

            return True

    def release_run_lock(self, run_id: str) -> None:
        with self._lock:
            lock = self.runs.get("__ACTIVE_RUN__")
            if lock and lock.get("owner") == run_id:
                del self.runs["__ACTIVE_RUN__"]

    def create_run(self, run_id: str) -> None:
        with self._lock:
            self.runs.setdefault(run_id, {"run_id": run_id, "status": "RUNNING", "started_at": int(time.time()), "last_heartbeat_at": int(time.time()), "expires_at": int(time.time()) + settings.run_state_retention_days * 86400})            

    def set_run_fields(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            self.runs.setdefault(run_id, {"run_id": run_id}).update(fields)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self.runs.get(run_id)
            return dict(item) if item else None

    def create_batch(self, run_id: str, batch_id: str, product_count: int, s3_key: str) -> None:
        with self._lock:
            self.batches.setdefault((run_id, batch_id), {
                "run_id": run_id,
                "batch_id": batch_id,
                "status": "PENDING",
                "product_count": product_count,
                "s3_key": s3_key,
                "expires_at": int(time.time()) + settings.batch_state_retention_days * 86400,
            })

    def claim_product(self, product_key: str, run_id: str, sku: str) -> str:
        with self._lock:
            existing = self.products.get(product_key)
            if existing is None:
                self.products[product_key] = {
                    "product_key": product_key,
                    "status": "SENDING",
                    "first_run_id": run_id,
                    "sku": sku,
                    "claimed_at": int(time.time()),
                }
                return "CLAIMED"
            if existing["status"] in {"ACCEPTED", "REJECTED"}:
                return "TERMINAL"
            if existing["status"] == "AMBIGUOUS":
                return "AMBIGUOUS"
            return "IN_FLIGHT"

    def release_product_claim(self, product_key: str, run_id: str) -> None:
        with self._lock:
            item = self.products.get(product_key)
            if item and item.get("status") == "SENDING" and item.get("first_run_id") == run_id:
                del self.products[product_key]

    def mark_product(self, product_key: str, status: str, reason: str | None = None) -> None:
        with self._lock:
            item = self.products[product_key]
            item["status"] = status
            item["updated_at"] = int(time.time())
            if reason:
                item["reason"] = reason

    def get_product(self, product_key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self.products.get(product_key)
            return dict(item) if item else None

    def complete_batch(self, run_id: str, batch_id: str, counts: dict[str, int]) -> bool:
        with self._lock:
            batch = self.batches[(run_id, batch_id)]
            if batch["status"] == "COMPLETE":
                return False
            batch.update({"status": "COMPLETE", **counts})
            run = self.runs.setdefault(run_id, {"run_id": run_id})
            for key, value in counts.items():
                run[key] = int(run.get(key, 0)) + int(value)
            run["completed_batches"] = int(run.get("completed_batches", 0)) + 1
            return True


class DynamoStateStore:
    """DynamoDB implementation using the low-level client (safe to share across worker threads)."""

    def __init__(self, cfg: Settings = settings):
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

        self.cfg = cfg
        self.client = boto3.client(
            "dynamodb",
            region_name=cfg.aws_region,
            endpoint_url=cfg.aws_endpoint_url,
            config=boto_config(cfg),
        )
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def _item(self, values: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {key: self._serializer.serialize(_normalise(value)) for key, value in values.items()}

    def _decode(self, item: dict[str, dict[str, Any]] | None) -> dict[str, Any] | None:
        if not item:
            return None
        return {key: self._deserializer.deserialize(value) for key, value in item.items()}

    def acquire_run_lock(self, run_id: str, lease_seconds: int | None = None) -> bool:        
        now = int(time.time())
        lease = int(lease_seconds or self.cfg.run_lock_seconds)
        previous_lock = self.get_run("__ACTIVE_RUN__")

        try:
            self.client.put_item(
                TableName=self.cfg.runs_table,
                Item=self._item({
                    "run_id": "__ACTIVE_RUN__",
                    "owner": run_id,
                    "heartbeat_at": now,
                    "lease_expires_at": now + lease,
                }),
                ConditionExpression=(
                    "attribute_not_exists(run_id) OR lease_expires_at <= :now "
                    "OR (attribute_not_exists(lease_expires_at) AND expires_at <= :now) "
                    "OR #owner = :owner"
                ),
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={
                    ":now": self._serializer.serialize(now),
                    ":owner": self._serializer.serialize(run_id),
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

        if (
            previous_lock
            and previous_lock.get("owner") != run_id
            and int(previous_lock.get("lease_expires_at", previous_lock.get("expires_at", 0))) <= now
        ):
            previous_run_id = str(previous_lock.get("owner"))
            previous_run = self.get_run(previous_run_id)
            if previous_run and previous_run.get("status") == "RUNNING":
                self.set_run_fields(
                    previous_run_id,
                    status="FAILED",
                    failure_type="INTERRUPTED",
                    failure_reason="run lease expired before completion",
                    interrupted_at=now,
                    finished_at=now,
                    recovered_by_run_id=run_id,
                )
        return True

    def renew_run_lock(self, run_id: str, lease_seconds: int | None = None) -> bool:
        now = int(time.time())
        lease = int(lease_seconds or self.cfg.run_lock_seconds)
        try:
            self.client.update_item(
                TableName=self.cfg.runs_table,
                Key=self._item({"run_id": "__ACTIVE_RUN__"}),
                UpdateExpression="SET heartbeat_at = :now, lease_expires_at = :expires REMOVE expires_at",
                ConditionExpression="#owner = :owner",
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={
                    ":now": self._serializer.serialize(now),
                    ":expires": self._serializer.serialize(now + lease),                    
                    ":owner": self._serializer.serialize(run_id),
                },
            )
            self.set_run_fields(run_id, last_heartbeat_at=now)            
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def release_run_lock(self, run_id: str) -> None:
        try:
            self.client.delete_item(
                TableName=self.cfg.runs_table,
                Key=self._item({"run_id": "__ACTIVE_RUN__"}),
                ConditionExpression="#owner = :owner",
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={":owner": self._serializer.serialize(run_id)},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def create_run(self, run_id: str) -> None:
        try:
            self.client.put_item(
                TableName=self.cfg.runs_table,
                Item=self._item({"run_id": run_id, "status": "RUNNING", "started_at": int(time.time()), "last_heartbeat_at": int(time.time()), "expires_at": int(time.time()) + self.cfg.run_state_retention_days * 86400}),                
                ConditionExpression="attribute_not_exists(run_id)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def set_run_fields(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        names = {f"#k{i}": key for i, key in enumerate(fields)}
        values = {f":v{i}": self._serializer.serialize(_normalise(value)) for i, value in enumerate(fields.values())}
        expression = "SET " + ", ".join(f"#k{i} = :v{i}" for i in range(len(fields)))
        self.client.update_item(
            TableName=self.cfg.runs_table,
            Key=self._item({"run_id": run_id}),
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        response = self.client.get_item(
            TableName=self.cfg.runs_table,
            Key=self._item({"run_id": run_id}),
            ConsistentRead=True,
        )
        return self._decode(response.get("Item"))

    def create_batch(self, run_id: str, batch_id: str, product_count: int, s3_key: str) -> None:
        try:
            self.client.put_item(
                TableName=self.cfg.batches_table,
                Item=self._item({
                    "run_id": run_id,
                    "batch_id": batch_id,
                    "status": "PENDING",
                    "product_count": product_count,
                    "s3_key": s3_key,
                    "expires_at": int(time.time()) + self.cfg.batch_state_retention_days * 86400,
                }),
                ConditionExpression="attribute_not_exists(run_id) AND attribute_not_exists(batch_id)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def claim_product(self, product_key: str, run_id: str, sku: str) -> str:
        try:
            self.client.put_item(
                TableName=self.cfg.idempotency_table,
                Item=self._item({
                    "product_key": product_key,
                    "status": "SENDING",
                    "first_run_id": run_id,
                    "sku": sku,
                    "claimed_at": int(time.time()),
                }),
                ConditionExpression="attribute_not_exists(product_key)",
            )
            return "CLAIMED"
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        existing = self.get_product(product_key) or {}
        status = existing.get("status")
        if status in {"ACCEPTED", "REJECTED"}:
            return "TERMINAL"
        if status == "AMBIGUOUS":
            return "AMBIGUOUS"
        return "IN_FLIGHT"

    def release_product_claim(self, product_key: str, run_id: str) -> None:
        """Delete a SENDING marker only when the downstream outcome is provably not committed."""
        try:
            self.client.delete_item(
                TableName=self.cfg.idempotency_table,
                Key=self._item({"product_key": product_key}),
                ConditionExpression="#s = :sending AND first_run_id = :run",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":sending": self._serializer.serialize("SENDING"),
                    ":run": self._serializer.serialize(run_id),
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def mark_product(self, product_key: str, status: str, reason: str | None = None) -> None:
        names = {"#status": "status"}
        values = {
            ":status": self._serializer.serialize(status),
            ":now": self._serializer.serialize(int(time.time())),
        }
        expr = "SET #status = :status, updated_at = :now"
        if reason:
            values[":reason"] = self._serializer.serialize(reason[:1000])
            expr += ", reason = :reason"
        self.client.update_item(
            TableName=self.cfg.idempotency_table,
            Key=self._item({"product_key": product_key}),
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def get_product(self, product_key: str) -> dict[str, Any] | None:
        response = self.client.get_item(
            TableName=self.cfg.idempotency_table,
            Key=self._item({"product_key": product_key}),
            ConsistentRead=True,
        )
        return self._decode(response.get("Item"))

    def complete_batch(self, run_id: str, batch_id: str, counts: dict[str, int]) -> bool:
        # Atomically make batch completion and run counters exactly-once relative to message redelivery.
        update_expr = "SET #status = :complete, completed_at = :now"
        batch_names = {"#status": "status"}
        batch_values: dict[str, Any] = {
            ":complete": {"S": "COMPLETE"},
            ":now": {"N": str(int(time.time()))},
        }
        for index, (key, value) in enumerate(counts.items()):
            nk, vk = f"#c{index}", f":c{index}"
            batch_names[nk] = key
            batch_values[vk] = {"N": str(int(value))}
            update_expr += f", {nk} = {vk}"

        run_names = {"#completed_batches": "completed_batches"}
        run_values: dict[str, Any] = {":one": {"N": "1"}}
        add_parts = ["#completed_batches :one"]
        for index, (key, value) in enumerate(counts.items()):
            nk, vk = f"#r{index}", f":r{index}"
            run_names[nk] = key
            run_values[vk] = {"N": str(int(value))}
            add_parts.append(f"{nk} {vk}")

        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.cfg.batches_table,
                            "Key": self._item({"run_id": run_id, "batch_id": batch_id}),
                            "UpdateExpression": update_expr,
                            "ConditionExpression": "#status <> :complete",
                            "ExpressionAttributeNames": batch_names,
                            "ExpressionAttributeValues": batch_values,
                        }
                    },
                    {
                        "Update": {
                            "TableName": self.cfg.runs_table,
                            "Key": self._item({"run_id": run_id}),
                            "UpdateExpression": "ADD " + ", ".join(add_parts),
                            "ExpressionAttributeNames": run_names,
                            "ExpressionAttributeValues": run_values,
                        }
                    },
                ]
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
                return False
            raise

def _normalise(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


def state_store(cfg: Settings = settings) -> StateStore:
    if cfg.state_backend == "dynamodb":
        return DynamoStateStore(cfg)
    return MemoryStateStore()
