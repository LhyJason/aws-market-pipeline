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
  default = "dev"
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
