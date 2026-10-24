variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "market-pipeline"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "symbols" {
  description = "Symbols the producer fetches"
  type        = list(string)
  default     = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
}

variable "feed_base_url" {
  description = "Price feed base URL; use https://api.binance.us from the US"
  type        = string
  default     = "https://api.binance.com"
}

variable "schedule_expression" {
  type    = string
  default = "rate(1 minute)"
}

variable "ingest_enabled" {
  description = "Set false to pause the producer schedule while keeping the stream"
  type        = bool
  default     = true
}

variable "lake_force_destroy" {
  description = "Let terraform destroy empty the lake bucket; leave false unless the raw data is disposable"
  type        = bool
  default     = false
}

variable "processor_batching_window_s" {
  description = "Seconds the processor waits to fill a batch; raising it cuts S3 PUTs and adds latency"
  type        = number
  default     = 0
}

variable "api_quota_per_day" {
  description = "Requests per day the API key may make; a cost cap"
  type        = number
  default     = 10000
}

variable "manage_apigateway_account" {
  description = "Create API Gateway's CloudWatch logging role and set it on the account. One per account and region, and dev already manages it, so false unless prod has an account of its own"
  type        = bool
  default     = false
}

variable "alarm_email" {
  description = "Email for alarm notifications (confirm the subscription from AWS's email); empty means no subscription, which prod should not run with"
  type        = string
  default     = ""
}
