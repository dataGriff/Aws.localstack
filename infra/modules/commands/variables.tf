variable "name_prefix" { type = string }
variable "service_name" { type = string }
variable "environment" { type = string }
variable "is_local" { type = bool }
variable "build_dir" { type = string }
variable "commands" {
  description = "Command type -> handler spec, exported from src/domain/registry.py."
  type = map(object({
    handler_name   = string
    handler_module = string
    result_table   = bool
  }))
}
variable "domain_bus_name" { type = string }
variable "alarm_topic_arn" { type = string }
variable "timeout_seconds" { type = number }
variable "max_receive_count" { type = number }
variable "rule_retry_attempts" { type = number }
variable "rule_max_event_age_seconds" { type = number }
variable "idempotency_ttl_seconds" { type = number }
variable "log_retention_days" { type = number }
