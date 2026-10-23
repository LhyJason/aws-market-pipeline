output "stream_name" {
  value = module.ingest.stream_name
}

output "producer_function_name" {
  value = module.ingest.function_name
}

output "producer_log_group" {
  value = module.ingest.log_group_name
}
