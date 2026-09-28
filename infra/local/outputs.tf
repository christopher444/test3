output "bucket" {
  value = aws_s3_bucket.catalogue.bucket
}

output "queue_url" {
  value = aws_sqs_queue.work.url
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "kms_key_arn" {
  value = aws_kms_key.data.arn
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.shape.arn
}

output "scheduler_name" {
  value = aws_scheduler_schedule.daily.name
}

output "ecs_cluster_arn" {
  value = aws_ecs_cluster.main.arn
}

output "ecs_task_definition_arn" {
  value = aws_ecs_task_definition.app.arn
}

output "vpc_id" {
  value = aws_vpc.local.id
}

output "subnet_id" {
  value = aws_subnet.local.id
}

output "security_group_id" {
  value = aws_security_group.local_tasks.id
}

output "local_app_access_key_id" {
  value = aws_iam_access_key.local_app.id
}

output "local_app_secret_access_key" {
  value     = aws_iam_access_key.local_app.secret
  sensitive = true
}
