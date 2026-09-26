# Ingress rule -> SQS (+DLQ, rule-target DLQ) -> translator Lambda, idempotency table,
# quarantine bucket.

locals {
  function_name = "${var.name_prefix}-translator"
}

# --- quarantine bucket: encrypted, private, TLS-only, 90-day expiry
resource "aws_s3_bucket" "quarantine" {
  bucket_prefix = "${var.name_prefix}-quarantine-"
  force_destroy = var.is_local
}

resource "aws_s3_bucket_server_side_encryption_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "quarantine" {
  bucket                  = aws_s3_bucket.quarantine.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
  }
}

data "aws_iam_policy_document" "quarantine" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.quarantine.arn, "${aws_s3_bucket.quarantine.arn}/*"]
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "quarantine" {
  bucket     = aws_s3_bucket.quarantine.id
  policy     = data.aws_iam_policy_document.quarantine.json
  depends_on = [aws_s3_bucket_public_access_block.quarantine]
}

# --- idempotency (Powertools) table
resource "aws_dynamodb_table" "idempotency" {
  name         = "${var.name_prefix}-translator-idempotency"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  ttl {
    attribute_name = "expiration"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }
}

# --- queue, rule, target
module "queue" {
  source = "../queue_pair"

  name                       = local.function_name
  visibility_timeout_seconds = 6 * var.timeout_seconds
  max_receive_count          = var.max_receive_count
  alarm_topic_arn            = var.alarm_topic_arn
  eventbridge_rule_arns      = [aws_cloudwatch_event_rule.ingress.arn]
}

resource "aws_cloudwatch_event_rule" "ingress" {
  name           = "${var.name_prefix}-ingress-to-translator"
  description    = "Every third-party event on the ingress bus goes to the translator"
  event_bus_name = var.ingress_bus_name
  event_pattern  = jsonencode({ source = [{ prefix = "${var.service_name}.thirdparty." }] })
}

module "rule_dlq" {
  source = "../rule_dlq"

  name            = local.function_name
  rule_arn        = aws_cloudwatch_event_rule.ingress.arn
  alarm_topic_arn = var.alarm_topic_arn
}

resource "aws_cloudwatch_event_target" "queue" {
  rule           = aws_cloudwatch_event_rule.ingress.name
  event_bus_name = var.ingress_bus_name
  target_id      = "translator-queue"
  arn            = module.queue.queue_arn

  dead_letter_config {
    arn = module.rule_dlq.arn
  }

  retry_policy {
    maximum_retry_attempts       = var.rule_retry_attempts
    maximum_event_age_in_seconds = var.rule_max_event_age_seconds
  }
}

# --- function
data "archive_file" "code" {
  type        = "zip"
  source_dir  = "${var.build_dir}/translator"
  output_path = "${var.build_dir}/translator.zip"
  excludes    = ["**/__pycache__/**", "**/*.pyc"]
}

module "function" {
  source = "../lambda_function"

  function_name      = local.function_name
  timeout_seconds    = var.timeout_seconds
  memory_mb          = 512
  log_retention_days = var.log_retention_days
  alarm_topic_arn    = var.alarm_topic_arn
  zip_path           = data.archive_file.code.output_path
  zip_hash           = data.archive_file.code.output_base64sha256
  handler            = "app.handler"

  environment_variables = {
    SERVICE_NAME                       = var.service_name
    POWERTOOLS_SERVICE_NAME            = "${var.service_name}.translator"
    POWERTOOLS_LOG_LEVEL               = "INFO"
    POWERTOOLS_LOGGER_LOG_EVENT        = "false"
    POWERTOOLS_TRACER_CAPTURE_RESPONSE = "false"
    ENVIRONMENT                        = var.environment
    DOMAIN_BUS_NAME                    = var.domain_bus_name
    IDEMPOTENCY_TABLE                  = aws_dynamodb_table.idempotency.name
    IDEMPOTENCY_TTL_SECONDS            = tostring(var.idempotency_ttl_seconds)
    QUARANTINE_BUCKET                  = aws_s3_bucket.quarantine.bucket
  }

  policy_statements = [
    {
      sid       = "ConsumeQueue"
      actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
      resources = [module.queue.queue_arn]
    },
    {
      sid       = "PublishDomain"
      actions   = ["events:PutEvents"]
      resources = [var.domain_bus_arn]
    },
    {
      sid       = "Idempotency"
      actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
      resources = [aws_dynamodb_table.idempotency.arn]
    },
    {
      sid       = "Quarantine"
      actions   = ["s3:PutObject"]
      resources = ["${aws_s3_bucket.quarantine.arn}/translator/*"]
    },
  ]
}

resource "aws_lambda_event_source_mapping" "queue" {
  event_source_arn                   = module.queue.queue_arn
  function_name                      = module.function.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 2
  function_response_types            = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 5
  }
}
