output "function_name" {
  value = aws_lambda_function.api.function_name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.api.name
}

output "access_log_group_name" {
  value = var.enable_access_logs ? aws_cloudwatch_log_group.access[0].name : null
}

output "rest_api_id" {
  value = aws_api_gateway_rest_api.api.id
}

output "stage_name" {
  value = aws_api_gateway_stage.api.stage_name
}

# On AWS, e.g. https://abc123.execute-api.us-east-1.amazonaws.com/v1. LocalStack
# serves the API elsewhere, so envs/local builds its own URL from rest_api_id.
output "invoke_url" {
  value = aws_api_gateway_stage.api.invoke_url
}

output "api_key_id" {
  value = aws_api_gateway_api_key.default.id
}

output "api_key" {
  value     = aws_api_gateway_api_key.default.value
  sensitive = true
}

# The ApiName dimension of the AWS/ApiGateway metrics.
output "rest_api_name" {
  value = aws_api_gateway_rest_api.api.name
}
