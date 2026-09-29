from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

import boto3
from app.aws import boto_config
from app.config import Settings, settings
from app.models import BatchPointer


@dataclass
class ReceivedMessage:
    body: dict[str, Any]
    receipt_handle: str
    message_id: str


class Queue(Protocol):
    def send(self, pointer: BatchPointer) -> None: ...
    def receive(self, max_messages: int = 10, wait_seconds: int = 10) -> list[ReceivedMessage]: ...
    def delete(self, receipt_handle: str) -> None: ...
    def extend_visibility(self, message: ReceivedMessage, timeout_seconds: int) -> None: ...    
    def release(self, message: ReceivedMessage, delay_seconds: int = 5) -> None: ...


class MemoryQueue:
    def __init__(self):
        self._items: deque[ReceivedMessage] = deque()
        self._counter = 0

    def send(self, pointer: BatchPointer) -> None:
        self._counter += 1
        message_id = str(self._counter)
        self._items.append(ReceivedMessage(pointer.asdict(), message_id, message_id))

    def receive(self, max_messages: int = 10, wait_seconds: int = 10) -> list[ReceivedMessage]:
        result: list[ReceivedMessage] = []
        while self._items and len(result) < max_messages:
            result.append(self._items.popleft())
        return result

    def delete(self, receipt_handle: str) -> None:
        return None

    def extend_visibility(self, message: ReceivedMessage, timeout_seconds: int) -> None:
        return None

    def release(self, message: ReceivedMessage, delay_seconds: int = 5) -> None:
        self._items.append(message)


class SQSQueue:
    def __init__(self, cfg: Settings = settings):
        if not cfg.queue_url:
            raise ValueError("QUEUE_URL is required when QUEUE_BACKEND=sqs")
        self.cfg = cfg
        self.client = boto3.client("sqs", region_name=cfg.aws_region, endpoint_url=cfg.aws_endpoint_url, config=boto_config(cfg))

    def send(self, pointer: BatchPointer) -> None:
        body = json.dumps(pointer.asdict(), separators=(",", ":"))
        kwargs = {
            "QueueUrl": self.cfg.queue_url,
            "MessageBody": body,
            "MessageGroupId": pointer.batch_id,
            "MessageDeduplicationId": pointer.batch_id,
        }
        self.client.send_message(**kwargs)

    def receive(self, max_messages: int = 10, wait_seconds: int = 10) -> list[ReceivedMessage]:
        response = self.client.receive_message(
            QueueUrl=self.cfg.queue_url,
            MaxNumberOfMessages=min(10, max_messages),
            WaitTimeSeconds=wait_seconds,
            VisibilityTimeout=self.cfg.sqs_visibility_timeout_seconds,
        )
        result = []
        for item in response.get("Messages", []):
            result.append(
                ReceivedMessage(
                    body=json.loads(item["Body"]),
                    receipt_handle=item["ReceiptHandle"],
                    message_id=item["MessageId"],
                )
            )
        return result

    def delete(self, receipt_handle: str) -> None:
        self.client.delete_message(QueueUrl=self.cfg.queue_url, ReceiptHandle=receipt_handle)

    def extend_visibility(self, message: ReceivedMessage, timeout_seconds: int) -> None:
        self.client.change_message_visibility(
            QueueUrl=self.cfg.queue_url,
            ReceiptHandle=message.receipt_handle,
            VisibilityTimeout=max(1, int(timeout_seconds)),
        )

    def release(self, message: ReceivedMessage, delay_seconds: int = 5) -> None:
        self.client.change_message_visibility(
            QueueUrl=self.cfg.queue_url,
            ReceiptHandle=message.receipt_handle,
            VisibilityTimeout=max(0, delay_seconds),
        )


def queue(cfg: Settings = settings) -> Queue:
    if cfg.queue_backend == "sqs":
        return SQSQueue(cfg)
    return MemoryQueue()
