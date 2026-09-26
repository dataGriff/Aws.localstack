output "table_bucket_arn" { value = aws_s3tables_table_bucket.this.arn }
output "table_bucket_name" { value = local.bucket_name }
output "iceberg_rest_uri" { value = local.rest_uri }
output "iceberg_warehouse" { value = local.warehouse }
output "iceberg_namespace" { value = local.namespace }
output "iceberg_sigv4" { value = var.iceberg_sigv4 ? "true" : "false" }
output "iceberg_s3_endpoint" { value = var.iceberg_s3_endpoint }
output "writer_function_name" { value = module.writer.name }
output "table_setup_result" {
  value     = aws_lambda_invocation.tables.result
  sensitive = false
}
