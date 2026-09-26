# Lambda with a dedicated log group (retention set), least-privilege role (no managed policies:
# logs scoped to its own group, X-Ray's two write actions on "*" as the service requires),
# active tracing, JSON structured logs via Powertools, and an errors alarm.

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.function_name
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "base" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [aws_cloudwatch_log_group.this.arn, "${aws_cloudwatch_log_group.this.arn}:*"]
  }
  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
  dynamic "statement" {
    for_each = var.policy_statements
    content {
      sid       = statement.value.sid
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "${var.function_name}-policy"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.base.json
}

resource "aws_lambda_function" "this" {
  function_name = var.function_name
  role          = aws_iam_role.this.arn
  timeout       = var.timeout_seconds
  memory_size   = var.memory_mb
  architectures = ["x86_64"]

  # Zip or image packaging, decided by the caller.
  package_type     = var.image_uri == "" ? "Zip" : "Image"
  filename         = var.image_uri == "" ? var.zip_path : null
  source_code_hash = var.image_uri == "" ? var.zip_hash : null
  handler          = var.image_uri == "" ? var.handler : null
  runtime          = var.image_uri == "" ? "python3.12" : null
  image_uri        = var.image_uri == "" ? null : var.image_uri

  dynamic "image_config" {
    for_each = var.image_uri != "" && length(var.image_command) > 0 ? [1] : []
    content {
      command = var.image_command
    }
  }

  environment {
    variables = var.environment_variables
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.this.name
  }

  depends_on = [aws_iam_role_policy.this, aws_cloudwatch_log_group.this]
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${var.function_name}-errors"
  alarm_description   = "${var.function_name} reported errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = aws_lambda_function.this.function_name }
  alarm_actions       = [var.alarm_topic_arn]
}
