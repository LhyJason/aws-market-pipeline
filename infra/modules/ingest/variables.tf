variable "name_prefix" {
  description = "Prefix for every resource name, e.g. market-pipeline-dev"
  type        = string
}

variable "producer_source_dir" {
  description = "Directory zipped as the producer Lambda package; must contain the producer/ package"
  type        = string
}

variable "symbols" {
  description = "Symbols fetched on every run"
  type        = list(string)
  default     = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
}

variable "feed_base_url" {
  description = "Price feed base URL (use https://api.binance.us from the US)"
  type        = string
  default     = "https://api.binance.com"
}

variable "schedule_expression" {
  description = "EventBridge schedule expression"
  type        = string
  default     = "rate(1 minute)"
}

variable "enabled" {
  description = "Whether the schedule is enabled; false pauses ingestion without destroying anything"
  type        = bool
  default     = true
}

variable "shard_count" {
  description = "Provisioned shards; one handles 1 MB/s or 1000 records/s"
  type        = number
  default     = 1
}

variable "retention_hours" {
  description = "Kinesis record retention in hours (24 to 8760)"
  type        = number
  default     = 24
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the producer"
  type        = number
  default     = 14
}

variable "log_level" {
  description = "Python log level for the producer"
  type        = string
  default     = "INFO"
}

variable "tags" {
  description = "Extra tags merged with the provider default tags"
  type        = map(string)
  default     = {}
}
