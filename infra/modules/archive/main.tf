# Iceberg archive: S3 table bucket, ECR repo + image build, writer Lambda (container image) with
# its event source mapping, and the table-setup Lambda invoked on apply (idempotent).

locals {
  namespace = "archive"
  tables    = { ingress = "ingress_events", domain = "domain_events" }

  bucket_name = "${var.name_prefix}-archive"

  # The only things that differ between AWS and LocalStack: endpoint, warehouse id, SigV4.
  rest_uri  = var.iceberg_rest_uri != "" ? var.iceberg_rest_uri : "https://s3tables.${var.region}.amazonaws.com/iceberg"
  warehouse = var.iceberg_warehouse != "" ? var.iceberg_warehouse : aws_s3tables_table_bucket.this.arn

  iceberg_env = merge(
    {
      ICEBERG_REST_URI      = local.rest_uri
      ICEBERG_WAREHOUSE     = local.warehouse
      ICEBERG_NAMESPACE     = local.namespace
      ICEBERG_SIGV4         = var.iceberg_sigv4 ? "true" : "false"
      ICEBERG_SIGNING_NAME  = "s3tables"
      ARCHIVE_TABLE_INGRESS = local.tables.ingress
      ARCHIVE_TABLE_DOMAIN  = local.tables.domain
    },
    var.iceberg_s3_endpoint != "" ? { ICEBERG_S3_ENDPOINT = var.iceberg_s3_endpoint } : {},
  )

  powertools_env = {
    SERVICE_NAME                       = var.service_name
    POWERTOOLS_LOG_LEVEL               = "INFO"
    POWERTOOLS_LOGGER_LOG_EVENT        = "false"
    POWERTOOLS_TRACER_CAPTURE_RESPONSE = "false"
    ENVIRONMENT                        = var.environment
  }

  # Image tag = content hash of everything that goes into the image, unless overridden.
  image_sources = concat(
    [for f in fileset("${path.root}/../src/shared", "**/*.py") : "${path.root}/../src/shared/${f}"],
    [for f in fileset("${path.root}/../src/iceberg", "**/*.py") : "${path.root}/../src/iceberg/${f}"],
    [for f in fileset("${path.root}/../src/functions/archive_writer", "**") : "${path.root}/../src/functions/archive_writer/${f}"],
    ["${path.root}/../src/functions/__init__.py", "${var.build_dir}/requirements-writer.txt"],
  )
  image_tag = var.image_tag != "" ? var.image_tag : substr(sha1(join("", [for f in sort(local.image_sources) : filesha1(f)])), 0, 16)
  image_uri = "${aws_ecr_repository.writer.repository_url}:${local.image_tag}"
}

# --- S3 Tables bucket. Maintenance (compaction, snapshot expiry, unreferenced file removal) is
# handled by the service; there is no compaction job in this repository.
resource "aws_s3tables_table_bucket" "this" {
  name = local.bucket_name

  encryption_configuration = {
    sse_algorithm = "AES256"
  }

  maintenance_configuration = {
    iceberg_unreferenced_file_removal = {
      status = "enabled"
      settings = {
        non_current_days  = 10
        unreferenced_days = 3
      }
    }
  }
}

# --- image
resource "aws_ecr_repository" "writer" {
  name                 = "${var.name_prefix}-archive-writer"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.is_local

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "terraform_data" "image" {
  input            = { uri = local.image_uri }
  triggers_replace = [local.image_uri]

  provisioner "local-exec" {
    command     = "${path.root}/../tools/build/image.sh ${aws_ecr_repository.writer.repository_url} ${local.image_tag} ${path.root}/.."
    interpreter = ["bash", "-c"]
  }
}

# --- writer
module "writer" {
  source = "../lambda_function"

  function_name      = "${var.name_prefix}-archive-writer"
  timeout_seconds    = var.writer_timeout_seconds
  memory_mb          = 2048
  log_retention_days = var.log_retention_days
  alarm_topic_arn    = var.alarm_topic_arn
  image_uri          = terraform_data.image.output.uri

  environment_variables = merge(local.powertools_env, local.iceberg_env, {
    POWERTOOLS_SERVICE_NAME = "${var.service_name}.archive-writer"
    QUARANTINE_BUCKET       = var.quarantine_bucket_name
  })

  policy_statements = [
    {
      sid       = "ConsumeQueue"
      actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
      resources = [var.archive_queue_arn]
    },
    {
      sid       = "Quarantine"
      actions   = ["s3:PutObject"]
      resources = ["${var.quarantine_bucket_arn}/archive-writer/*"]
    },
    {
      sid       = "TableBucketRead"
      actions   = ["s3tables:GetTableBucket", "s3tables:GetNamespace", "s3tables:ListNamespaces", "s3tables:ListTables", "s3tables:GetTable", "s3tables:GetTableMetadataLocation"]
      resources = [aws_s3tables_table_bucket.this.arn]
    },
    {
      sid       = "TablesWrite"
      actions   = ["s3tables:GetTable", "s3tables:GetTableMetadataLocation", "s3tables:GetTableData", "s3tables:PutTableData", "s3tables:UpdateTableMetadataLocation"]
      resources = ["${aws_s3tables_table_bucket.this.arn}/table/*"]
    },
  ]
}

resource "aws_lambda_event_source_mapping" "archive" {
  event_source_arn                   = var.archive_queue_arn
  function_name                      = module.writer.arn
  batch_size                         = var.batch_size
  maximum_batching_window_in_seconds = var.batching_window_seconds
  function_response_types            = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = var.max_concurrency
  }
}

# --- table setup: same image, different handler, invoked on every apply (idempotent).
module "table_setup" {
  source = "../lambda_function"

  function_name      = "${var.name_prefix}-archive-table-setup"
  timeout_seconds    = 300
  memory_mb          = 1024
  log_retention_days = var.log_retention_days
  alarm_topic_arn    = var.alarm_topic_arn
  image_uri          = terraform_data.image.output.uri
  image_command      = ["functions.archive_writer.table_setup.handler"]

  environment_variables = merge(local.powertools_env, local.iceberg_env, {
    POWERTOOLS_SERVICE_NAME = "${var.service_name}.table-setup"
  })

  policy_statements = [
    {
      sid       = "TableBucketCreate"
      actions   = ["s3tables:GetTableBucket", "s3tables:GetNamespace", "s3tables:ListNamespaces", "s3tables:ListTables", "s3tables:GetTable", "s3tables:GetTableMetadataLocation", "s3tables:CreateNamespace", "s3tables:CreateTable"]
      resources = [aws_s3tables_table_bucket.this.arn]
    },
    {
      sid       = "TablesInit"
      actions   = ["s3tables:GetTable", "s3tables:GetTableMetadataLocation", "s3tables:GetTableData", "s3tables:PutTableData", "s3tables:UpdateTableMetadataLocation"]
      resources = ["${aws_s3tables_table_bucket.this.arn}/table/*"]
    },
  ]
}

resource "aws_lambda_invocation" "tables" {
  function_name = module.table_setup.name
  input = jsonencode({
    RequestType = "Create"
    ResourceProperties = {
      Namespace     = local.namespace
      Tables        = sort(values(local.tables))
      SchemaVersion = "1"
    }
  })

  triggers = {
    schema_version = "1" # bump to re-run setup after a reviewed schema change
    tables         = join(",", sort(values(local.tables)))
    image          = local.image_tag
  }

  depends_on = [aws_s3tables_table_bucket.this]
}
