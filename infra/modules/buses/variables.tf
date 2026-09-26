variable "name_prefix" { type = string }
variable "archive_writer_timeout_seconds" { type = number }
variable "max_receive_count" { type = number }
variable "rule_retry_attempts" { type = number }
variable "rule_max_event_age_seconds" { type = number }
variable "alarm_email" {
  type    = string
  default = ""
}
