output "ingress_bus_name" { value = aws_cloudwatch_event_bus.ingress.name }
output "ingress_bus_arn" { value = aws_cloudwatch_event_bus.ingress.arn }
output "domain_bus_name" { value = aws_cloudwatch_event_bus.domain.name }
output "domain_bus_arn" { value = aws_cloudwatch_event_bus.domain.arn }
output "archive_queue_arn" { value = module.archive_queue.queue_arn }
output "archive_queue_url" { value = module.archive_queue.queue_url }
output "archive_dlq_url" { value = module.archive_queue.dlq_url }
output "alarm_topic_arn" { value = aws_sns_topic.alarms.arn }
