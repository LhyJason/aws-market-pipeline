output "function_name" {
  value = aws_lambda_function.processor.function_name
}

output "function_arn" {
  value = aws_lambda_function.processor.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.processor.name
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "event_source_mapping_uuid" {
  value = aws_lambda_event_source_mapping.stream.uuid
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}
