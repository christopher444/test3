output "bucket" { value = aws_s3_bucket.catalogue.bucket }
output "work_queue_url" { value = aws_sqs_queue.work.url }
output "state_machine_arn" { value = aws_sfn_state_machine.sync.arn }
output "alarm_topic_arn" { value = aws_sns_topic.alarms.arn }
output "product_api_secret_arn" { value = aws_secretsmanager_secret.product_api_key.arn }
output "warehouse_api_secret_arn" { value = aws_secretsmanager_secret.warehouse_api_key.arn }

output "ecr_repository_url" { value = aws_ecr_repository.app.repository_url }
