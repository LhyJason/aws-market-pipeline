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
