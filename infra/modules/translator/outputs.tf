output "queue_url" { value = module.queue.queue_url }
output "dlq_url" { value = module.queue.dlq_url }
output "function_name" { value = module.function.name }
output "quarantine_bucket_name" { value = aws_s3_bucket.quarantine.bucket }
output "quarantine_bucket_arn" { value = aws_s3_bucket.quarantine.arn }
output "idempotency_table_name" { value = aws_dynamodb_table.idempotency.name }
