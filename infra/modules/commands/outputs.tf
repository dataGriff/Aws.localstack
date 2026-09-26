output "handlers" {
  description = "Per command type: queue/DLQ URLs, function name and result table name."
  value = { for k, v in local.handlers : k => {
    QueueUrl     = module.queue[k].queue_url
    DlqUrl       = module.queue[k].dlq_url
    FunctionName = module.function[k].name
    TableName    = v.result_table ? aws_dynamodb_table.results[k].name : ""
  } }
}
