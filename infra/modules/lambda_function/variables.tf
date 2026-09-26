variable "function_name" { type = string }
variable "timeout_seconds" { type = number }
variable "memory_mb" {
  type    = number
  default = 512
}
variable "log_retention_days" { type = number }
variable "alarm_topic_arn" { type = string }
variable "environment_variables" { type = map(string) }

variable "handler" {
  type    = string
  default = "app.handler"
}
variable "zip_path" {
  type    = string
  default = ""
}
variable "zip_hash" {
  type    = string
  default = ""
}
variable "image_uri" {
  type    = string
  default = ""
}
variable "image_command" {
  type    = list(string)
  default = []
}

variable "policy_statements" {
  description = "Extra least-privilege statements: explicit actions on explicit resources."
  type = list(object({
    sid       = string
    actions   = list(string)
    resources = list(string)
  }))
  default = []
}
