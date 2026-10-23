output "stream_name" {
  value = module.ingest.stream_name
}

output "producer_function_name" {
  value = module.ingest.function_name
}

output "producer_log_group" {
  value = module.ingest.log_group_name
}

output "lake_bucket" {
  value = module.storage.bucket_name
}

output "firehose_name" {
  value = module.storage.firehose_name
}

output "firehose_log_group" {
  value = module.storage.firehose_log_group_name
}
