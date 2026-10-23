output "stream_name" {
  value = aws_kinesis_stream.ticks.name
}

output "stream_arn" {
  value = aws_kinesis_stream.ticks.arn
}

output "function_name" {
  value = aws_lambda_function.producer.function_name
}

output "function_arn" {
  value = aws_lambda_function.producer.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.producer.name
}
