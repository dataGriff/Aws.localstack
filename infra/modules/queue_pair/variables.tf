variable "name" { type = string }
variable "visibility_timeout_seconds" { type = number }
variable "max_receive_count" { type = number }
variable "alarm_topic_arn" { type = string }
variable "eventbridge_rule_arns" {
  description = "Rule ARNs allowed to send to this queue (aws:SourceArn condition)."
  type        = list(string)
  default     = []
}
