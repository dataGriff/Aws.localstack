# Output names match what tools/common.py, the integration tests, the query and smoke tools read.

output "IngressBusName" { value = module.buses.ingress_bus_name }
output "IngressBusArn" { value = module.buses.ingress_bus_arn }
output "DomainBusName" { value = module.buses.domain_bus_name }
output "DomainBusArn" { value = module.buses.domain_bus_arn }
output "ArchiveQueueUrl" { value = module.buses.archive_queue_url }
output "ArchiveDlqUrl" { value = module.buses.archive_dlq_url }
output "AlarmTopicArn" { value = module.buses.alarm_topic_arn }

output "TranslatorQueueUrl" { value = module.translator.queue_url }
output "TranslatorDlqUrl" { value = module.translator.dlq_url }
output "TranslatorFunctionName" { value = module.translator.function_name }
output "QuarantineBucketName" { value = module.translator.quarantine_bucket_name }
output "IdempotencyTableName" { value = module.translator.idempotency_table_name }

# One output per command handler, e.g. ReconcileInvoiceQueueUrl / ReconcileInvoiceDlqUrl /
# ReconcileInvoiceTableName / ReconcileInvoiceFunctionName, flattened for the tools.
output "Commands" {
  value = module.commands.handlers
}

output "TableBucketArn" { value = module.archive.table_bucket_arn }
output "TableBucketName" { value = module.archive.table_bucket_name }
output "IcebergRestUri" { value = module.archive.iceberg_rest_uri }
output "IcebergWarehouse" { value = module.archive.iceberg_warehouse }
output "IcebergNamespace" { value = module.archive.iceberg_namespace }
output "IcebergSigV4" { value = module.archive.iceberg_sigv4 }
output "IcebergS3Endpoint" { value = module.archive.iceberg_s3_endpoint }
output "ArchiveWriterFunctionName" { value = module.archive.writer_function_name }
