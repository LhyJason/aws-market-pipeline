locals {
  name_prefix = "${var.project}-${var.environment}"
}

module "ingest" {
  source = "../../modules/ingest"

  name_prefix         = local.name_prefix
  producer_source_dir = "${path.root}/../../../services/producer/src"
  symbols             = var.symbols
  feed_base_url       = var.feed_base_url
  schedule_expression = var.schedule_expression
  enabled             = var.ingest_enabled
  shard_count         = 1
  retention_hours     = 24
  log_retention_days  = 14
}

module "storage" {
  source = "../../modules/storage"

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

  name_prefix = local.name_prefix
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

  name_prefix = local.name_prefix
  # Build first: python scripts/build_lambda.py api --arch arm64
  package_dir                 = "${path.root}/../../../.build/api-arm64"
  architecture                = "arm64"
  table_name                  = module.storage.table_name
  table_arn                   = module.storage.table_arn
  symbols                     = var.symbols
  quota_per_day               = var.api_quota_per_day
  manage_account_logging_role = var.manage_apigateway_account
  log_retention_days          = 14
}
