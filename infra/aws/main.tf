provider "aws" {
  region = var.region
  default_tags { tags = merge({ Service = var.name, ManagedBy = "Terraform" }, var.tags) }
}

data "aws_availability_zones" "available" { state = "available" }
data "aws_caller_identity" "current" {}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
}

resource "aws_internet_gateway" "main" { vpc_id = aws_vpc.main.id }

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 4, count.index)
  map_public_ip_on_launch = true
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 4, count.index + 8)
}

resource "aws_route_table" "public" { vpc_id = aws_vpc.main.id }
resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}
resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  count      = 2
  domain     = "vpc"
  depends_on = [aws_internet_gateway.main]
}
resource "aws_nat_gateway" "nat" {
  count         = 2
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
}
resource "aws_route_table" "private" {
  count  = 2
  vpc_id = aws_vpc.main.id
}
resource "aws_route" "private_internet" {
  count                  = 2
  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.nat[count.index].id
}
resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_security_group" "tasks" {
  name        = "${var.name}-tasks"
  description = "No ingress; outbound only to AWS services and external PIM/WMS"
  vpc_id      = aws_vpc.main.id
  egress {
    description = "HTTPS only: AWS APIs and production PIM/WMS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_kms_key" "data" {
  description             = "${var.name} data encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}
resource "aws_kms_alias" "data" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_s3_bucket" "catalogue" { bucket_prefix = "${var.name}-" }
resource "aws_s3_bucket_public_access_block" "catalogue" {
  bucket = aws_s3_bucket.catalogue.id
  block_public_acls = true
  block_public_policy = true
  ignore_public_acls = true
  restrict_public_buckets = true
}
data "aws_iam_policy_document" "catalogue_tls" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"
    actions = ["s3:*"]
    resources = [aws_s3_bucket.catalogue.arn, "${aws_s3_bucket.catalogue.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}
resource "aws_s3_bucket_policy" "catalogue_tls" {
  bucket = aws_s3_bucket.catalogue.id
  policy = data.aws_iam_policy_document.catalogue_tls.json
}
resource "aws_s3_bucket_versioning" "catalogue" {
  bucket = aws_s3_bucket.catalogue.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "catalogue" {
  bucket = aws_s3_bucket.catalogue.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "catalogue" {
  bucket = aws_s3_bucket.catalogue.id
  depends_on = [aws_s3_bucket_versioning.catalogue]
  rule {
    id     = "original-export-90-days"
    status = "Enabled"
    filter { prefix = "catalogue-sync/exports/" }
    expiration { days = 90 }
    noncurrent_version_expiration { noncurrent_days = 90 }
  }
  rule {
    id     = "derived-work-7-days"
    status = "Enabled"
    filter { prefix = "catalogue-sync/work/" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 7 }
  }
}

resource "aws_sqs_queue" "dlq" {
  name       = "${var.name}-dlq.fifo"
  fifo_queue = true
  message_retention_seconds = 1209600
  kms_master_key_id = aws_kms_key.data.arn
}
resource "aws_sqs_queue" "work" {
  name       = "${var.name}-work.fifo"
  fifo_queue = true
  visibility_timeout_seconds = 300
  message_retention_seconds = 1209600
  kms_master_key_id = aws_kms_key.data.arn
  redrive_policy = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dlq.arn, maxReceiveCount = 5 })
}

resource "aws_dynamodb_table" "runs" {
  name         = "${var.name}-runs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"
  attribute {
    name = "run_id"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.data.arn
  }
}
resource "aws_dynamodb_table" "batches" {
  name         = "${var.name}-batches"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"
  range_key    = "batch_id"
  attribute {
    name = "run_id"
    type = "S"
  }
  attribute {
    name = "batch_id"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.data.arn
  }
}
resource "aws_dynamodb_table" "idempotency" {
  name         = "${var.name}-idempotency"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "product_key"
  attribute {
    name = "product_key"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.data.arn
  }
}

resource "aws_secretsmanager_secret" "product_api_key" {
  name       = "${var.name}/product-api-key"
  kms_key_id = aws_kms_key.data.arn
}
resource "aws_secretsmanager_secret" "warehouse_api_key" {
  name       = "${var.name}/warehouse-api-key"
  kms_key_id = aws_kms_key.data.arn
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}"
  retention_in_days = 90
  # CloudWatch Logs encrypts data at rest by default. A customer-managed KMS key
  # requires a Logs service-principal key policy; use the service-managed default
  # here rather than ship an incomplete CMK policy.
}
resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
  # Alarm messages contain operational metadata only. If a customer-managed KMS
  # key is required for SNS, add the CloudWatch service-principal key policy too.
}


resource "aws_ecr_repository" "app" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecs_cluster" "main" { name = var.name }

resource "aws_iam_role" "ecs_execution" {
  name = "${var.name}-ecs-execution"
  assume_role_policy = jsonencode({ Version="2012-10-17", Statement=[{Effect="Allow", Principal={Service="ecs-tasks.amazonaws.com"}, Action="sts:AssumeRole"}] })
}
resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role_policy" "ecs_execution_secrets" {
  role = aws_iam_role.ecs_execution.id
  policy = jsonencode({ Version="2012-10-17", Statement=[
    {Effect="Allow", Action=["secretsmanager:GetSecretValue"], Resource=[aws_secretsmanager_secret.product_api_key.arn, aws_secretsmanager_secret.warehouse_api_key.arn]},
    {Effect="Allow", Action=["kms:Decrypt"], Resource=aws_kms_key.data.arn}
  ]})
}

resource "aws_iam_role" "task" {
  name = "${var.name}-task"
  assume_role_policy = jsonencode({ Version="2012-10-17", Statement=[{Effect="Allow", Principal={Service="ecs-tasks.amazonaws.com"}, Action="sts:AssumeRole"}] })
}
resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.id
  policy = jsonencode({ Version="2012-10-17", Statement=[
    {Effect="Allow", Action=["s3:GetObject","s3:PutObject","s3:AbortMultipartUpload"], Resource="${aws_s3_bucket.catalogue.arn}/catalogue-sync/*"},
    {Effect="Allow", Action=["sqs:SendMessage","sqs:ReceiveMessage","sqs:DeleteMessage","sqs:ChangeMessageVisibility","sqs:GetQueueAttributes"], Resource=aws_sqs_queue.work.arn},
    {Effect="Allow", Action=["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:DeleteItem","dynamodb:TransactWriteItems"], Resource=[aws_dynamodb_table.runs.arn, aws_dynamodb_table.batches.arn, aws_dynamodb_table.idempotency.arn]},
    {Effect="Allow", Action=["kms:Encrypt","kms:Decrypt","kms:GenerateDataKey"], Resource=aws_kms_key.data.arn}
  ]})
}

locals {
  env = [
    {name="PRODUCT_API_URL", value=var.product_api_url},
    {name="WAREHOUSE_API_URL", value=var.warehouse_api_url},
    {name="STORAGE_BACKEND", value="s3"},
    {name="STATE_BACKEND", value="dynamodb"},
    {name="QUEUE_BACKEND", value="sqs"},
    {name="S3_BUCKET", value=aws_s3_bucket.catalogue.bucket},
    {name="QUEUE_URL", value=aws_sqs_queue.work.url},
    {name="RUNS_TABLE", value=aws_dynamodb_table.runs.name},
    {name="BATCHES_TABLE", value=aws_dynamodb_table.batches.name},
    {name="IDEMPOTENCY_TABLE", value=aws_dynamodb_table.idempotency.name},
    {name="KMS_KEY_ID", value=aws_kms_key.data.arn},
    {name="PIM_RATE_PER_SECOND", value="10"},
    {name="WMS_RATE_PER_SECOND", value="20"},
    {name="PIM_MAX_IN_FLIGHT", value="32"},
    {name="WMS_MAX_IN_FLIGHT", value="80"},
    {name="RUN_LOCK_SECONDS", value="120"},
    {name="RUN_LOCK_HEARTBEAT_SECONDS", value="30"},
    {name="AWS_MAX_POOL_CONNECTIONS", value="100"},
    {name="SQS_VISIBILITY_TIMEOUT_SECONDS", value="300"}
  ]
  secrets = [
    {name="PRODUCT_API_KEY", valueFrom=aws_secretsmanager_secret.product_api_key.arn},
    {name="WAREHOUSE_API_KEY", valueFrom=aws_secretsmanager_secret.warehouse_api_key.arn}
  ]
}

resource "aws_ecs_task_definition" "app" {
  family = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode = "awsvpc"
  cpu = 1024
  memory = 2048
  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name = "app", image = var.container_image, essential = true,
    environment = local.env, secrets = local.secrets,
    logConfiguration = { logDriver="awslogs", options={"awslogs-group"=aws_cloudwatch_log_group.app.name,"awslogs-region"=var.region,"awslogs-stream-prefix"="app"} }
  }])
}

resource "aws_iam_role" "sfn" {
  name = "${var.name}-sfn"
  assume_role_policy = jsonencode({ Version="2012-10-17", Statement=[{Effect="Allow", Principal={Service="states.amazonaws.com"}, Action="sts:AssumeRole"}] })
}
resource "aws_iam_role_policy" "sfn" {
  role = aws_iam_role.sfn.id
  policy = jsonencode({ Version="2012-10-17", Statement=[
    {Effect="Allow", Action=["ecs:RunTask"], Resource=aws_ecs_task_definition.app.arn},
    {Effect="Allow", Action=["ecs:StopTask","ecs:DescribeTasks"], Resource="*"},
    {Effect="Allow", Action=["iam:PassRole"], Resource=[aws_iam_role.ecs_execution.arn,aws_iam_role.task.arn]},
    {Effect="Allow", Action=["events:PutTargets","events:PutRule","events:DescribeRule"], Resource="arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"},
    {Effect="Allow", Action=["dynamodb:GetItem","dynamodb:UpdateItem"], Resource=aws_dynamodb_table.runs.arn}
  ]})
}

resource "aws_sfn_state_machine" "sync" {
  name = var.name
  role_arn = aws_iam_role.sfn.arn
  type = "STANDARD"
  definition = jsonencode({
    StartAt = "Export"
    States = {
      Export = {
        Type="Task", Resource="arn:aws:states:::ecs:runTask.sync", TimeoutSeconds=900,
        Parameters={LaunchType="FARGATE",Cluster=aws_ecs_cluster.main.arn,TaskDefinition=aws_ecs_task_definition.app.arn,
          NetworkConfiguration={AwsvpcConfiguration={Subnets=aws_subnet.private[*].id,SecurityGroups=[aws_security_group.tasks.id],AssignPublicIp="DISABLED"}},
          Overrides={ContainerOverrides=[{Name="app","Command.$"="States.Array('export','--run-id', $$.Execution.Name)"}]}}
        ResultPath=null, Retry=[{ErrorEquals=["States.TaskFailed"],IntervalSeconds=10,MaxAttempts=2,BackoffRate=2.0}], Catch=[{ErrorEquals=["States.ALL"],Next="MarkFailed"}], Next="Enqueue"
      },
      Enqueue = {
        Type="Task", Resource="arn:aws:states:::ecs:runTask.sync", TimeoutSeconds=600,
        Parameters={LaunchType="FARGATE",Cluster=aws_ecs_cluster.main.arn,TaskDefinition=aws_ecs_task_definition.app.arn,
          NetworkConfiguration={AwsvpcConfiguration={Subnets=aws_subnet.private[*].id,SecurityGroups=[aws_security_group.tasks.id],AssignPublicIp="DISABLED"}},
          Overrides={ContainerOverrides=[{Name="app","Command.$"="States.Array('enqueue','--run-id', $$.Execution.Name)"}]}}
        ResultPath=null, Retry=[{ErrorEquals=["States.TaskFailed"],IntervalSeconds=10,MaxAttempts=2,BackoffRate=2.0}], Catch=[{ErrorEquals=["States.ALL"],Next="MarkFailed"}], Next="DrainWMS"
      },
      DrainWMS = {
        Type="Task", Resource="arn:aws:states:::ecs:runTask.sync",
        TimeoutSeconds=2400,
        Parameters={LaunchType="FARGATE",Cluster=aws_ecs_cluster.main.arn,TaskDefinition=aws_ecs_task_definition.app.arn,
          NetworkConfiguration={AwsvpcConfiguration={Subnets=aws_subnet.private[*].id,SecurityGroups=[aws_security_group.tasks.id],AssignPublicIp="DISABLED"}},
          Overrides={ContainerOverrides=[{Name="app","Command.$"="States.Array('worker','--run-id', $$.Execution.Name)"}]}}
        ResultPath=null, Retry=[{ErrorEquals=["States.TaskFailed"],IntervalSeconds=15,MaxAttempts=2,BackoffRate=2.0}], Catch=[{ErrorEquals=["States.ALL"],Next="MarkFailed"}], Next="ReadRunStatus"
      },
      ReadRunStatus = {
        Type="Task", Resource="arn:aws:states:::dynamodb:getItem",
        Parameters={TableName=aws_dynamodb_table.runs.name,Key={run_id={"S.$"="$$.Execution.Name"}},ConsistentRead=true},
        ResultPath="$.run", Next="RunSucceeded"
      },
      RunSucceeded = {
        Type="Choice",
        Choices=[{Variable="$.run.Item.status.S",StringEquals="COMPLETED",Next="Succeeded"}],
        Default="MarkFailed"
      },
      Succeeded = {Type="Succeed"},
      MarkFailed = {
        Type="Task", Resource="arn:aws:states:::dynamodb:updateItem",
        Parameters={TableName=aws_dynamodb_table.runs.name,Key={run_id={"S.$"="$$.Execution.Name"}},UpdateExpression="SET #s = :s",ExpressionAttributeNames={"#s"="status"},ExpressionAttributeValues={":s"={S="FAILED"}}}, Next="Failed"
      },
      Failed = {Type="Fail",Cause="Catalogue synchronisation failed"}
    }
  })
}

resource "aws_iam_role" "scheduler" {
  name = "${var.name}-scheduler"
  assume_role_policy = jsonencode({ Version="2012-10-17", Statement=[{Effect="Allow", Principal={Service="scheduler.amazonaws.com"}, Action="sts:AssumeRole"}] })
}
resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({ Version="2012-10-17", Statement=[{Effect="Allow",Action=["states:StartExecution"],Resource=aws_sfn_state_machine.sync.arn}] })
}
resource "aws_scheduler_schedule" "daily" {
  name = "${var.name}-daily"
  schedule_expression = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_sfn_state_machine.sync.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"
  }
}

resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name          = "${var.name}-dlq-not-empty"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
resource "aws_cloudwatch_metric_alarm" "workflow_failed" {
  alarm_name          = "${var.name}-workflow-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.sync.arn }
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
resource "aws_cloudwatch_metric_alarm" "duration" {
  alarm_name          = "${var.name}-over-30-minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionTime"
  namespace           = "AWS/States"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1800000
  treat_missing_data  = "notBreaching"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.sync.arn }
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
