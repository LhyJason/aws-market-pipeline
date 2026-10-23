output "stream_name" {
  value = module.ingest.stream_name
}

output "producer_function_name" {
  value = module.ingest.function_name
}

output "lake_bucket" {
  value = module.storage.bucket_name
}

output "firehose_name" {
  value = module.storage.firehose_name
}

output "market_table" {
  value = module.storage.table_name
}

output "processor_function_name" {
  value = module.processing.function_name
}

output "processor_dlq_url" {
  value = module.processing.dlq_url
}
