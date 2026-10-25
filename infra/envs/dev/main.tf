data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name_prefix = "${var.project}-${var.environment}"

  # Created by infra/bootstrap. The deploy role may only create roles that
  # carry it, so every module sets it on its roles.
  permissions_boundary = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/${local.name_prefix}-workload-boundary"

  # API Gateway's logging role has a boundary of its own; see infra/bootstrap.
  logging_role_permissions_boundary = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/${local.name_prefix}-apigateway-logs-boundary"
}

module "ingest" {
  source = "../../modules/ingest"

  permissions_boundary = local.permissions_boundary
  name_prefix          = local.name_prefix
  producer_source_dir  = "${path.root}/../../../services/producer/src"
  symbols              = var.symbols
  feed_base_url        = var.feed_base_url
  schedule_expression  = var.schedule_expression
  enabled              = var.ingest_enabled
  shard_count          = 1
  retention_hours      = 24
  log_retention_days   = 14
}

module "storage" {
  source = "../../modules/storage"

  permissions_boundary = local.permissions_boundary
  name_prefix          = local.name_prefix
  source_stream_arn    = module.ingest.stream_arn
  raw_expiration_days  = 30
  buffering_interval_s = 300
  buffering_size_mb    = 5
  force_destroy        = var.lake_force_destroy
  log_retention_days   = 14
}

module "processing" {
  source = "../../modules/processing"

  permissions_boundary = local.permissions_boundary
  name_prefix          = local.name_prefix
  # Build first: python scripts/build_lambda.py processor --arch arm64
  package_dir        = "${path.root}/../../../.build/processor-arm64"
  architecture       = "arm64"
  stream_arn         = module.ingest.stream_arn
  table_name         = module.storage.table_name
  table_arn          = module.storage.table_arn
  lake_bucket        = module.storage.bucket_name
  lake_bucket_arn    = module.storage.bucket_arn
  batching_window_s  = var.processor_batching_window_s
  log_retention_days = 14
}

module "analytics" {
  source = "../../modules/analytics"

  name_prefix = local.name_prefix
  lake_bucket = module.storage.bucket_name
  symbols     = var.symbols
}

module "api" {
  source = "../../modules/api"

  permissions_boundary = local.permissions_boundary
  name_prefix          = local.name_prefix
  # Build first: python scripts/build_lambda.py api --arch arm64
  package_dir                       = "${path.root}/../../../.build/api-arm64"
  architecture                      = "arm64"
  table_name                        = module.storage.table_name
  table_arn                         = module.storage.table_arn
  symbols                           = var.symbols
  quota_per_day                     = var.api_quota_per_day
  manage_account_logging_role       = var.manage_apigateway_account
  logging_role_permissions_boundary = local.logging_role_permissions_boundary
  log_retention_days                = 14
}

module "observability" {
  source = "../../modules/observability"

  name_prefix             = local.name_prefix
  alarm_email             = var.alarm_email
  create_alarms           = true
  producer_function_name  = module.ingest.function_name
  processor_function_name = module.processing.function_name
  api_function_name       = module.api.function_name
  stream_name             = module.ingest.stream_name
  firehose_name           = module.storage.firehose_name
  table_name              = module.storage.table_name
  dlq_name                = module.processing.dlq_name
  api_name                = module.api.rest_api_name
  api_stage               = module.api.stage_name
}
