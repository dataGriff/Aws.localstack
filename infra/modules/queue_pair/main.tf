# Encrypted, TLS-only work queue with its DLQ, EventBridge-scoped send policy and DLQ depth alarm.

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-dlq"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 14 * 24 * 3600
}

resource "aws_sqs_queue" "this" {
  name                       = var.name
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = 4 * 24 * 3600
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

data "aws_iam_policy_document" "queue" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.this.arn]
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

  dynamic "statement" {
    for_each = length(var.eventbridge_rule_arns) > 0 ? [1] : []
    content {
      sid       = "AllowEventBridgeRules"
      effect    = "Allow"
      actions   = ["sqs:SendMessage"]
      resources = [aws_sqs_queue.this.arn]
      principals {
        type        = "Service"
        identifiers = ["events.amazonaws.com"]
      }
      condition {
        test     = "ArnEquals"
        variable = "aws:SourceArn"
        values   = var.eventbridge_rule_arns
      }
    }
  }
}

resource "aws_sqs_queue_policy" "this" {
  queue_url = aws_sqs_queue.this.id
  policy    = data.aws_iam_policy_document.queue.json
}

data "aws_iam_policy_document" "dlq" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.dlq.arn]
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

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.dlq.json
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "${var.name}-dlq-depth"
  alarm_description   = "Messages are stuck in ${aws_sqs_queue.dlq.name}"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  alarm_actions       = [var.alarm_topic_arn]
}
