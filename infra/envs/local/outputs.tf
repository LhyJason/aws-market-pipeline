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

output "api_function_name" {
  value = module.api.function_name
}

# LocalStack serves REST APIs under its edge endpoint, not at the AWS-style
# invoke URL Terraform computes.
output "api_url" {
  value = "${var.localstack_endpoint}/_aws/execute-api/${module.api.rest_api_id}/${module.api.stage_name}"
}

output "alarm_topic_arn" {
  value = module.observability.alarm_topic_arn
}

output "dashboard_name" {
  value = module.observability.dashboard_name
}

# terraform output -raw api_key
output "api_key" {
  value     = module.api.api_key
  sensitive = true
}
