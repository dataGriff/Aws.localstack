# Ingress and domain event buses, the alarm topic, the shared archive queue and the catch-all
# archive rules (one per bus, each with its own retry policy and rule-target DLQ).

resource "aws_cloudwatch_event_bus" "ingress" {
  name = "${var.name_prefix}-ingress"
}

resource "aws_cloudwatch_event_bus" "domain" {
  name = "${var.name_prefix}-domain"
}

resource "aws_sns_topic" "alarms" {
  name = "${var.name_prefix}-alarms"
}

data "aws_iam_policy_document" "alarms" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]
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
  statement {
    sid       = "AllowCloudWatchAlarms"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "alarms" {
  arn    = aws_sns_topic.alarms.arn
  policy = data.aws_iam_policy_document.alarms.json
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  buses = {
    ingress = aws_cloudwatch_event_bus.ingress
    domain  = aws_cloudwatch_event_bus.domain
  }
}

# Shared archive queue: visibility timeout >= 6x the writer's timeout.
module "archive_queue" {
  source = "../queue_pair"

  name                       = "${var.name_prefix}-archive"
  visibility_timeout_seconds = 6 * var.archive_writer_timeout_seconds
  max_receive_count          = var.max_receive_count
  alarm_topic_arn            = aws_sns_topic.alarms.arn
  eventbridge_rule_arns      = [for r in aws_cloudwatch_event_rule.archive : r.arn]
}

resource "aws_cloudwatch_event_rule" "archive" {
  for_each = local.buses

  name           = "${var.name_prefix}-archive-${each.key}"
  description    = "Archive every event on the ${each.key} bus"
  event_bus_name = each.value.name
  event_pattern  = jsonencode({ source = [{ prefix = "" }] })
}

module "archive_rule_dlq" {
  source   = "../rule_dlq"
  for_each = local.buses

  name            = "${var.name_prefix}-archive-${each.key}"
  rule_arn        = aws_cloudwatch_event_rule.archive[each.key].arn
  alarm_topic_arn = aws_sns_topic.alarms.arn
}

resource "aws_cloudwatch_event_target" "archive" {
  for_each = local.buses

  rule           = aws_cloudwatch_event_rule.archive[each.key].name
  event_bus_name = each.value.name
  target_id      = "archive-queue"
  arn            = module.archive_queue.queue_arn

  # Wrap the whole event with its bus name so the writer never infers the bus from `source`.
  input_transformer {
    input_paths    = { event = "$" }
    input_template = "{\"bus\":\"${each.key}\",\"event\":<event>}"
  }

  dead_letter_config {
    arn = module.archive_rule_dlq[each.key].arn
  }

  retry_policy {
    maximum_retry_attempts       = var.rule_retry_attempts
    maximum_event_age_in_seconds = var.rule_max_event_age_seconds
  }
}
