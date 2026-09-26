# Root module: composes one module per concern for the environment given by -var-file.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name_prefix = "${var.project}-${var.environment}"
  registry    = jsondecode(file(var.registry_file))
  commands    = local.registry.commands # map: type -> {handler_name, handler_module, result_table}
}

module "buses" {
  source = "./modules/buses"

  name_prefix                    = local.name_prefix
  archive_writer_timeout_seconds = var.archive_writer_timeout_seconds
  max_receive_count              = var.max_receive_count
  rule_retry_attempts            = var.rule_retry_attempts
  rule_max_event_age_seconds     = var.rule_max_event_age_seconds
  alarm_email                    = var.alarm_email
}

module "translator" {
  source = "./modules/translator"

  name_prefix                = local.name_prefix
  service_name               = var.project
  environment                = var.environment
  is_local                   = var.is_local
  build_dir                  = var.build_dir
  ingress_bus_name           = module.buses.ingress_bus_name
  domain_bus_name            = module.buses.domain_bus_name
  domain_bus_arn             = module.buses.domain_bus_arn
  alarm_topic_arn            = module.buses.alarm_topic_arn
  timeout_seconds            = var.translator_timeout_seconds
  max_receive_count          = var.max_receive_count
  rule_retry_attempts        = var.rule_retry_attempts
  rule_max_event_age_seconds = var.rule_max_event_age_seconds
  idempotency_ttl_seconds    = var.idempotency_ttl_seconds
  log_retention_days         = var.log_retention_days
}

module "commands" {
  source = "./modules/commands"

  name_prefix                = local.name_prefix
  service_name               = var.project
  environment                = var.environment
  is_local                   = var.is_local
  build_dir                  = var.build_dir
  commands                   = local.commands
  domain_bus_name            = module.buses.domain_bus_name
  alarm_topic_arn            = module.buses.alarm_topic_arn
  timeout_seconds            = var.handler_timeout_seconds
  max_receive_count          = var.max_receive_count
  rule_retry_attempts        = var.rule_retry_attempts
  rule_max_event_age_seconds = var.rule_max_event_age_seconds
  idempotency_ttl_seconds    = var.idempotency_ttl_seconds
  log_retention_days         = var.log_retention_days
}

module "archive" {
  source = "./modules/archive"

  name_prefix             = local.name_prefix
  service_name            = var.project
  environment             = var.environment
  is_local                = var.is_local
  build_dir               = var.build_dir
  image_tag               = var.image_tag
  archive_queue_arn       = module.buses.archive_queue_arn
  quarantine_bucket_arn   = module.translator.quarantine_bucket_arn
  quarantine_bucket_name  = module.translator.quarantine_bucket_name
  alarm_topic_arn         = module.buses.alarm_topic_arn
  batch_size              = var.archive_batch_size
  batching_window_seconds = var.archive_batching_window_seconds
  writer_timeout_seconds  = var.archive_writer_timeout_seconds
  max_concurrency         = var.archive_max_concurrency
  log_retention_days      = var.log_retention_days
  iceberg_rest_uri        = var.iceberg_rest_uri
  iceberg_warehouse       = var.iceberg_warehouse
  iceberg_sigv4           = var.iceberg_sigv4
  iceberg_s3_endpoint     = var.iceberg_s3_endpoint
  account_id              = data.aws_caller_identity.current.account_id
  region                  = data.aws_region.current.region
}
