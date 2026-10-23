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

module "processing" {
  source = "../../modules/processing"

  name_prefix = local.name_prefix
  # Build first: python scripts/build_lambda.py processor --arch x86_64
  package_dir     = "${path.root}/../../../.build/processor-${var.lambda_architecture}"
  architecture    = var.lambda_architecture
  stream_arn      = module.ingest.stream_arn
  table_name      = module.storage.table_name
  table_arn       = module.storage.table_arn
  lake_bucket     = module.storage.bucket_name
  lake_bucket_arn = module.storage.bucket_arn
  create_alarms   = false
}

module "api" {
  source = "../../modules/api"

  name_prefix = local.name_prefix
  # Build first: python scripts/build_lambda.py api --arch x86_64
  package_dir  = "${path.root}/../../../.build/api-${var.lambda_architecture}"
  architecture = var.lambda_architecture
  table_name   = module.storage.table_name
  table_arn    = module.storage.table_arn
  symbols      = var.symbols
  # The account-level logging role is an AWS concern; LocalStack needs none.
  manage_account_logging_role = false
}

# No analytics module here: Glue and Athena are not in LocalStack's free
# edition. Run the saved Athena queries against processed/ with DuckDB instead:
#   python scripts/query_lake.py --localstack
