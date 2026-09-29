provider "aws" {
  region                      = var.region
  access_key                  = "AKIAIOSFODNN7EXAMPLE"
  secret_key                  = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  # LocalEmu exposes all emulated AWS APIs through one gateway.
  endpoints {
    cloudwatch     = var.endpoint
    dynamodb       = var.endpoint
    ec2            = var.endpoint
    ecr            = var.endpoint
    ecs            = var.endpoint
    events         = var.endpoint
    iam            = var.endpoint
    kms            = var.endpoint
    logs           = var.endpoint
    s3             = var.endpoint
    scheduler      = var.endpoint
    secretsmanager = var.endpoint
    sns            = var.endpoint
    sqs            = var.endpoint
    stepfunctions  = var.endpoint
    sts            = var.endpoint
  }
}

data "aws_caller_identity" "current" {}

# Minimal VPC resources exercise LocalEmu's EC2/VPC control plane without pretending
# that the local developer path reproduces AWS NAT/availability-zone failure modes.
resource "aws_vpc" "local" {
  cidr_block           = "10.99.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "catalogue-sync-local" }
}

resource "aws_subnet" "local" {
  vpc_id     = aws_vpc.local.id
  cidr_block = "10.99.1.0/24"

  tags = { Name = "catalogue-sync-local" }
}

resource "aws_security_group" "local_tasks" {
  name        = "catalogue-sync-local-tasks"
  description = "LocalEmu ECS metadata/smoke-test security group"
  vpc_id      = aws_vpc.local.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_kms_key" "data" {
  description         = "catalogue-sync local-emulation data key"
  enable_key_rotation = true
}

resource "aws_kms_alias" "data" {
  name          = "alias/catalogue-sync-local"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_s3_bucket" "catalogue" {
  bucket = "catalogue-sync-local"
}

resource "aws_s3_bucket_public_access_block" "catalogue" {
  bucket = aws_s3_bucket.catalogue.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
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
  bucket     = aws_s3_bucket.catalogue.id
  depends_on = [aws_s3_bucket_versioning.catalogue]

  rule {
    id     = "retain-original-exports-90-days"
    status = "Enabled"
    filter { prefix = "${var.prefix}/exports/" }
    expiration { days = 90 }
    noncurrent_version_expiration { noncurrent_days = 90 }
  }

  rule {
    id     = "expire-derived-work-after-7-days"
    status = "Enabled"
    filter { prefix = "${var.prefix}/work/" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 7 }
  }
}

resource "aws_sqs_queue" "dlq" {
  name                        = "catalogue-sync-dlq.fifo"
  fifo_queue                  = true
  content_based_deduplication = false
  message_retention_seconds   = 1209600
  kms_master_key_id           = aws_kms_key.data.arn
}

# Dedicated local-only smoke queue so service verification can never consume business work.
resource "aws_sqs_queue" "smoke" {
  name                        = "catalogue-sync-smoke.fifo"
  fifo_queue                  = true
  content_based_deduplication = false
  message_retention_seconds   = 3600
}

resource "aws_sqs_queue" "work" {
  name                        = "catalogue-sync-work.fifo"
  fifo_queue                  = true
  content_based_deduplication = false
  visibility_timeout_seconds  = 900
  message_retention_seconds   = 1209600
  kms_master_key_id           = aws_kms_key.data.arn

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_dynamodb_table" "runs" {
  name         = "catalogue-sync-runs"
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
  name         = "catalogue-sync-batches"
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
  name         = "catalogue-sync-idempotency"
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
  name       = "catalogue-sync/product-api-key"
  kms_key_id = aws_kms_key.data.arn
}

resource "aws_secretsmanager_secret_version" "product_api_key" {
  secret_id     = aws_secretsmanager_secret.product_api_key.id
  secret_string = "challenge-product-key"
}

resource "aws_secretsmanager_secret" "warehouse_api_key" {
  name       = "catalogue-sync/warehouse-api-key"
  kms_key_id = aws_kms_key.data.arn
}

resource "aws_secretsmanager_secret_version" "warehouse_api_key" {
  secret_id     = aws_secretsmanager_secret.warehouse_api_key.id
  secret_string = "challenge-warehouse-key"
}

resource "aws_cloudwatch_log_group" "application" {
  name              = "/catalogue-sync/local"
  retention_in_days = 7
}

resource "aws_sns_topic" "alarms" {
  name = "catalogue-sync-local-alarms"
}

resource "aws_ecr_repository" "app" {
  name                 = "catalogue-sync-local"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecs_cluster" "main" {
  name = "catalogue-sync-local"
}

resource "aws_iam_role" "ecs_execution" {
  name = "catalogue-sync-local-ecs-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role" "task" {
  name = "catalogue-sync-local-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = "${aws_s3_bucket.catalogue.arn}/${var.prefix}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.work.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:TransactWriteItems"]
        Resource = [aws_dynamodb_table.runs.arn, aws_dynamodb_table.batches.arn, aws_dynamodb_table.idempotency.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.data.arn
      }
    ]
  })
}

# A local IAM user mirrors the application task-role permissions. Host-executed local
# pipeline stages use this identity, allowing IAM_ENFORCEMENT=1 to catch permission drift.
# These credentials are emulator-only and are intentionally stored only in local Terraform state.
resource "aws_iam_user" "local_app" {
  name = "catalogue-sync-local-app"
}

resource "aws_iam_user_policy" "local_app" {
  name = "catalogue-sync-local-app"
  user = aws_iam_user.local_app.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = "${aws_s3_bucket.catalogue.arn}/${var.prefix}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.work.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:TransactWriteItems"]
        Resource = [aws_dynamodb_table.runs.arn, aws_dynamodb_table.batches.arn, aws_dynamodb_table.idempotency.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.data.arn
      }
    ]
  })
}

resource "aws_iam_access_key" "local_app" {
  user = aws_iam_user.local_app.name
}

# LocalEmu can execute ECS tasks as real Docker containers. We register an application
# task definition so ECS/IAM/VPC integration can be inspected/smoke-tested locally.
# The normal local pipeline deliberately runs the three stages from the host: this avoids
# hiding LocalEmu's documented current gaps for ECS secret injection and awslogs streaming.
resource "aws_ecs_task_definition" "app" {
  family                   = "catalogue-sync-local"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "app"
    image     = var.local_container_image
    essential = true
    environment = [
      { name = "AWS_ENDPOINT_URL", value = var.endpoint },
      { name = "AWS_REGION", value = var.region },
      { name = "STORAGE_BACKEND", value = "s3" },
      { name = "STATE_BACKEND", value = "dynamodb" },
      { name = "QUEUE_BACKEND", value = "sqs" },
      { name = "S3_BUCKET", value = aws_s3_bucket.catalogue.bucket },
      { name = "QUEUE_URL", value = aws_sqs_queue.work.url },
      { name = "RUNS_TABLE", value = aws_dynamodb_table.runs.name },
      { name = "BATCHES_TABLE", value = aws_dynamodb_table.batches.name },
      { name = "IDEMPOTENCY_TABLE", value = aws_dynamodb_table.idempotency.name },
      { name = "RUN_LOCK_SECONDS", value = "120" },
      { name = "RUN_LOCK_HEARTBEAT_SECONDS", value = "30" },
      { name = "AWS_MAX_POOL_CONNECTIONS", value = "100" },
      { name = "SQS_VISIBILITY_TIMEOUT_SECONDS", value = "300" }
    ]
  }])
}

resource "aws_iam_role" "sfn" {
  name = "catalogue-sync-local-sfn"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sfn" {
  role = aws_iam_role.sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem"]
        Resource = aws_dynamodb_table.runs.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:RunTask", "ecs:StopTask", "ecs:DescribeTasks"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.ecs_execution.arn, aws_iam_role.task.arn]
      }
    ]
  })
}

# This state machine is intentionally side-effect-light but not a no-op: it exercises
# LocalEmu Step Functions plus direct DynamoDB service integrations. The production
# state machine in infra/aws remains the source of truth for the real ECS stage workflow.
resource "aws_sfn_state_machine" "shape" {
  name     = "catalogue-sync-local-shape"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = jsonencode({
    StartAt = "MarkRunning"
    States = {
      MarkRunning = {
        Type     = "Task"
        Resource = "arn:aws:states:::dynamodb:putItem"
        Parameters = {
          TableName = aws_dynamodb_table.runs.name
          Item = {
            run_id = { "S.$" = "$$.Execution.Name" }
            status = { S = "RUNNING" }
          }
        }
        ResultPath = null
        Next       = "Export"
      }
      Export = {
        Type = "Pass"
        Next = "Enqueue"
      }
      Enqueue = {
        Type = "Pass"
        Next = "DrainWMS"
      }
      DrainWMS = {
        Type = "Pass"
        Next = "MarkCompleted"
      }
      MarkCompleted = {
        Type     = "Task"
        Resource = "arn:aws:states:::dynamodb:updateItem"
        Parameters = {
          TableName = aws_dynamodb_table.runs.name
          Key = {
            run_id = { "S.$" = "$$.Execution.Name" }
          }
          UpdateExpression          = "SET #s = :s"
          ExpressionAttributeNames  = { "#s" = "status" }
          ExpressionAttributeValues = { ":s" = { S = "COMPLETED" } }
        }
        End = true
      }
    }
  })
}

resource "aws_iam_role" "scheduler" {
  name = "catalogue-sync-local-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = aws_sfn_state_machine.shape.arn
    }]
  })
}

# Provision Scheduler locally so its Terraform/API surface is covered, but keep it disabled:
# developers explicitly start test executions and do not want a background daily run.
resource "aws_scheduler_schedule" "daily" {
  name                         = "catalogue-sync-local-daily"
  schedule_expression          = "cron(0 4 * * ? *)"
  schedule_expression_timezone = "UTC"
  state                        = "DISABLED"

  flexible_time_window { mode = "OFF" }

  target {
    arn      = aws_sfn_state_machine.shape.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"
  }
}

resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name          = "catalogue-sync-local-dlq-not-empty"
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
  alarm_name          = "catalogue-sync-local-workflow-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.shape.arn }
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "duration" {
  alarm_name          = "catalogue-sync-local-over-30-minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionTime"
  namespace           = "AWS/States"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1800000
  treat_missing_data  = "notBreaching"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.shape.arn }
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

