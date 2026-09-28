from __future__ import annotations

import json
import os
import time
import uuid
from urllib.request import urlopen

import boto3

ENDPOINT = os.getenv("LOCALEMU_ENDPOINT", "http://127.0.0.1:4566")
REGION = os.getenv("AWS_REGION", "eu-west-1")
ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
SECRET_KEY = os.getenv(
    "AWS_SECRET_ACCESS_KEY",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
)


def client(service: str):
    return boto3.client(
        service,
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )


def main() -> int:
    results: dict[str, object] = {}

    with urlopen(f"{ENDPOINT}/_localemu/health", timeout=5) as response:
        results["localemu_health"] = json.loads(response.read().decode("utf-8"))

    results["sts_account"] = client("sts").get_caller_identity()["Account"]

    s3 = client("s3")
    bucket = "catalogue-sync-local"
    results["s3_versioning"] = s3.get_bucket_versioning(Bucket=bucket).get("Status")
    smoke_key = f"catalogue-sync/smoke/{uuid.uuid4().hex}.txt"
    s3.put_object(Bucket=bucket, Key=smoke_key, Body=b"localemu-smoke")
    results["s3_round_trip"] = s3.get_object(Bucket=bucket, Key=smoke_key)["Body"].read().decode()
    s3.delete_object(Bucket=bucket, Key=smoke_key)

    kms = client("kms")
    aliases = kms.list_aliases()["Aliases"]
    key_id = next(item["TargetKeyId"] for item in aliases if item.get("AliasName") == "alias/catalogue-sync-local")
    encrypted = kms.encrypt(KeyId=key_id, Plaintext=b"catalogue-sync-smoke")["CiphertextBlob"]
    results["kms_round_trip"] = kms.decrypt(CiphertextBlob=encrypted)["Plaintext"].decode()

    ddb = client("dynamodb")
    token = uuid.uuid4().hex
    run_id = f"smoke-{token}"
    batch_id = "000001"
    ddb.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": "catalogue-sync-runs",
                    "Item": {"run_id": {"S": run_id}, "status": {"S": "SMOKE"}},
                    "ConditionExpression": "attribute_not_exists(run_id)",
                }
            },
            {
                "Put": {
                    "TableName": "catalogue-sync-batches",
                    "Item": {"run_id": {"S": run_id}, "batch_id": {"S": batch_id}, "status": {"S": "SMOKE"}},
                    "ConditionExpression": "attribute_not_exists(run_id)",
                }
            },
        ]
    )
    results["dynamodb_transaction"] = ddb.get_item(
        TableName="catalogue-sync-runs", Key={"run_id": {"S": run_id}}, ConsistentRead=True
    )["Item"]["status"]["S"]
    ddb.delete_item(TableName="catalogue-sync-runs", Key={"run_id": {"S": run_id}})
    ddb.delete_item(
        TableName="catalogue-sync-batches",
        Key={"run_id": {"S": run_id}, "batch_id": {"S": batch_id}},
    )

    sqs = client("sqs")
    queue_url = sqs.get_queue_url(QueueName="catalogue-sync-smoke.fifo")["QueueUrl"]
    message_id = sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({"smoke": token}),
        MessageGroupId="localemu-smoke",
        MessageDeduplicationId=token,
    )["MessageId"]
    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=1).get("Messages", [])
    if not messages:
        raise RuntimeError("SQS smoke message was not received")
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=messages[0]["ReceiptHandle"])
    results["sqs_fifo_message_id"] = message_id

    secrets = client("secretsmanager")
    product_secret = secrets.get_secret_value(SecretId="catalogue-sync/product-api-key")["SecretString"]
    warehouse_secret = secrets.get_secret_value(SecretId="catalogue-sync/warehouse-api-key")["SecretString"]
    results["secrets_retrieved"] = bool(product_secret and warehouse_secret)

    results["log_group"] = client("logs").describe_log_groups(
        logGroupNamePrefix="/catalogue-sync/local"
    )["logGroups"][0]["logGroupName"]
    results["cloudwatch_alarm_count"] = len(client("cloudwatch").describe_alarms()["MetricAlarms"])
    results["sns_topic_count"] = len(client("sns").list_topics()["Topics"])
    results["ecr_repository"] = client("ecr").describe_repositories(
        repositoryNames=["catalogue-sync-local"]
    )["repositories"][0]["repositoryName"]
    results["ecs_cluster"] = client("ecs").describe_clusters(
        clusters=["catalogue-sync-local"]
    )["clusters"][0]["clusterName"]
    results["vpc_count"] = len(client("ec2").describe_vpcs()["Vpcs"])
    results["iam_role"] = client("iam").get_role(RoleName="catalogue-sync-local-task")["Role"]["RoleName"]
    results["scheduler"] = client("scheduler").get_schedule(
        Name="catalogue-sync-local-daily"
    )["State"]

    sfn = client("stepfunctions")
    machines = sfn.list_state_machines()["stateMachines"]
    state_machine_arn = next(
        item["stateMachineArn"] for item in machines if item["name"] == "catalogue-sync-local-shape"
    )
    execution_name = f"smoke-{uuid.uuid4().hex[:20]}"
    execution = sfn.start_execution(
        stateMachineArn=state_machine_arn,
        name=execution_name,
        input="{}",
    )
    execution_arn = execution["executionArn"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = sfn.describe_execution(executionArn=execution_arn)["status"]
        if status in {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}:
            break
        time.sleep(0.2)
    else:
        raise TimeoutError("LocalEmu Step Functions smoke execution did not finish")
    if status != "SUCCEEDED":
        raise RuntimeError(f"LocalEmu Step Functions smoke execution ended as {status}")
    results["stepfunctions_execution"] = status
    ddb.delete_item(TableName="catalogue-sync-runs", Key={"run_id": {"S": execution_name}})

    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
