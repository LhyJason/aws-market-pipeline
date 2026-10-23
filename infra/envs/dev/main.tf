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
