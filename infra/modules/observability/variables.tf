variable "name_prefix" {
  description = "Prefix for every resource name, e.g. market-pipeline-dev; alarms are named <name_prefix>-*"
  type        = string
}

variable "alarm_email" {
  description = "Email subscribed to the alarm topic (AWS sends a confirmation link first); empty string means no subscription"
  type        = string
  default     = ""
}

variable "create_alarms" {
  description = "Create the CloudWatch alarms. LocalStack 4.14 answers the provider's DescribeAlarms with a 500, so apply hangs there; local turns this off"
  type        = bool
  default     = true
}

variable "producer_function_name" {
  description = "Producer Lambda"
  type        = string
}

variable "processor_function_name" {
  description = "Processor Lambda"
  type        = string
}

variable "api_function_name" {
  description = "API Lambda"
  type        = string
}

variable "stream_name" {
  description = "Kinesis stream the producer writes"
  type        = string
}

variable "firehose_name" {
  description = "Firehose stream that lands raw/"
  type        = string
}

variable "table_name" {
  description = "Market DynamoDB table"
  type        = string
}

variable "dlq_name" {
  description = "The processor's dead-letter queue"
  type        = string
}

variable "api_name" {
  description = "REST API name, the ApiName dimension of AWS/ApiGateway metrics"
  type        = string
}

variable "api_stage" {
  description = "REST API stage, the Stage dimension"
  type        = string
}

variable "producer_error_minutes" {
  description = "Alarm when the producer fails in this many of the last 5 minutes"
  type        = number
  default     = 3
}

variable "iterator_age_alarm_ms" {
  description = "Alarm when the stream's oldest unprocessed record stays older than this for 5 minutes"
  type        = number
  default     = 300000
}

variable "api_5xx_threshold" {
  description = "Alarm when the API returns at least this many 5xx responses in 5 minutes"
  type        = number
  default     = 5
}

variable "api_latency_p95_ms" {
  description = "Alarm when the API's p95 latency stays above this for 15 minutes"
  type        = number
  default     = 2000
}

variable "tags" {
  description = "Extra tags merged with the provider default tags"
  type        = map(string)
  default     = {}
}
