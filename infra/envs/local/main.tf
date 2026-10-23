# Same modules as dev, pointed at LocalStack. State is local (terraform.tfstate
# in this directory, gitignored) because there is no state bucket to use.
locals {
  name_prefix = "${var.project}-${var.environment}"
}

module "ingest" {
  source = "../../modules/ingest"

  name_prefix         = local.name_prefix
  producer_source_dir = "${path.root}/../../../services/producer/src"
  symbols             = var.symbols
  feed_base_url       = var.feed_base_url
  schedule_expression = "rate(1 minute)"
  enabled             = true
}

module "storage" {
  source = "../../modules/storage"

  name_prefix          = local.name_prefix
  source_stream_arn    = module.ingest.stream_arn
  buffering_interval_s = 60
  force_destroy        = true
}
