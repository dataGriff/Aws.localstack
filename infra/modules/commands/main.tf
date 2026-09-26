# Command routing generated from the domain registry (build/registry.json): per command type one
# rule (detail.kind=command, detail.type=X) -> SQS queue (+DLQ, rule-target DLQ) -> handler
# Lambda with a DynamoDB result table. Nothing payment-specific lives here.

resource "aws_dynamodb_table" "idempotency" {
  name         = "${var.name_prefix}-commands-idempotency"
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

locals {
  handlers = { for type, spec in var.commands : type => merge(spec, {
    function_name = "${var.name_prefix}-${spec.handler_name}"
  }) }
}

module "queue" {
  source   = "../queue_pair"
  for_each = local.handlers

  name                       = each.value.function_name
  visibility_timeout_seconds = 6 * var.timeout_seconds
  max_receive_count          = var.max_receive_count
  alarm_topic_arn            = var.alarm_topic_arn
  eventbridge_rule_arns      = [aws_cloudwatch_event_rule.command[each.key].arn]
}

resource "aws_cloudwatch_event_rule" "command" {
  for_each = local.handlers

  name           = "${var.name_prefix}-command-${each.value.handler_name}"
  description    = "Route ${each.key} commands to the ${each.value.handler_name} handler"
  event_bus_name = var.domain_bus_name
  event_pattern  = jsonencode({ detail = { kind = ["command"], type = [each.key] } })
}

module "rule_dlq" {
  source   = "../rule_dlq"
  for_each = local.handlers

  name            = each.value.function_name
  rule_arn        = aws_cloudwatch_event_rule.command[each.key].arn
  alarm_topic_arn = var.alarm_topic_arn
}

resource "aws_cloudwatch_event_target" "queue" {
  for_each = local.handlers

  rule           = aws_cloudwatch_event_rule.command[each.key].name
  event_bus_name = var.domain_bus_name
  target_id      = "handler-queue"
  arn            = module.queue[each.key].queue_arn

  dead_letter_config {
    arn = module.rule_dlq[each.key].arn
  }

  retry_policy {
    maximum_retry_attempts       = var.rule_retry_attempts
    maximum_event_age_in_seconds = var.rule_max_event_age_seconds
  }
}

resource "aws_dynamodb_table" "results" {
  for_each = { for k, v in local.handlers : k => v if v.result_table }

  name         = "${each.value.function_name}-results"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = !var.is_local
  }

  server_side_encryption {
    enabled = true
  }
}

data "archive_file" "code" {
  for_each = local.handlers

  type        = "zip"
  source_dir  = "${var.build_dir}/${each.value.handler_module}"
  output_path = "${var.build_dir}/${each.value.handler_module}.zip"
  excludes    = ["**/__pycache__/**", "**/*.pyc"]
}

module "function" {
  source   = "../lambda_function"
  for_each = local.handlers

  function_name      = each.value.function_name
  timeout_seconds    = var.timeout_seconds
  memory_mb          = 512
  log_retention_days = var.log_retention_days
  alarm_topic_arn    = var.alarm_topic_arn
  zip_path           = data.archive_file.code[each.key].output_path
  zip_hash           = data.archive_file.code[each.key].output_base64sha256
  handler            = "app.handler"

  environment_variables = merge(
    {
      SERVICE_NAME                       = var.service_name
      POWERTOOLS_SERVICE_NAME            = "${var.service_name}.${each.value.handler_name}"
      POWERTOOLS_LOG_LEVEL               = "INFO"
      POWERTOOLS_LOGGER_LOG_EVENT        = "false"
      POWERTOOLS_TRACER_CAPTURE_RESPONSE = "false"
      ENVIRONMENT                        = var.environment
      IDEMPOTENCY_TABLE                  = aws_dynamodb_table.idempotency.name
      IDEMPOTENCY_TTL_SECONDS            = tostring(var.idempotency_ttl_seconds)
    },
    each.value.result_table ? { RESULT_TABLE = aws_dynamodb_table.results[each.key].name } : {},
  )

  policy_statements = concat(
    [
      {
        sid       = "ConsumeQueue"
        actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
        resources = [module.queue[each.key].queue_arn]
      },
      {
        sid       = "Idempotency"
        actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
        resources = [aws_dynamodb_table.idempotency.arn]
      },
    ],
    each.value.result_table ? [{
      sid       = "WriteResults"
      actions   = ["dynamodb:PutItem"]
      resources = [aws_dynamodb_table.results[each.key].arn]
    }] : [],
  )
}

resource "aws_lambda_event_source_mapping" "queue" {
  for_each = local.handlers

  event_source_arn                   = module.queue[each.key].queue_arn
  function_name                      = module.function[each.key].arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 2
  function_response_types            = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 5
  }
}
