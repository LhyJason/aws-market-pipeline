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

output "market_table" {
  value = module.storage.table_name
}

output "processor_function_name" {
  value = module.processing.function_name
}

output "processor_log_group" {
  value = module.processing.log_group_name
}

output "processor_dlq_url" {
  value = module.processing.dlq_url
}

output "glue_database" {
  value = module.analytics.database_name
}

output "athena_workgroup" {
  value = module.analytics.workgroup
}

output "api_url" {
  value = module.api.invoke_url
}

output "api_log_group" {
  value = module.api.log_group_name
}

output "api_access_log_group" {
  value = module.api.access_log_group_name
}

# terraform output -raw api_key
output "api_key" {
  value     = module.api.api_key
  sensitive = true
}
