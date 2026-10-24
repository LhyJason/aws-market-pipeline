variable "name_prefix" {
  description = "Prefix for every resource name, e.g. market-pipeline-dev"
  type        = string
}

variable "package_dir" {
  description = "Directory zipped as the processor package, built by scripts/build_lambda.py for var.architecture"
  type        = string
}

variable "architecture" {
  description = "Lambda architecture; must match the wheels in package_dir (LocalStack runs on the host's, usually x86_64)"
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.architecture)
    error_message = "architecture must be arm64 or x86_64."
  }
}

variable "stream_arn" {
  description = "Kinesis Data Stream the processor consumes"
  type        = string
}

variable "table_name" {
  description = "Market DynamoDB table (latest prices and bars)"
  type        = string
}

variable "table_arn" {
  description = "ARN of the market table"
  type        = string
}

variable "lake_bucket" {
  description = "Lake bucket name"
  type        = string
}

variable "lake_bucket_arn" {
  description = "Lake bucket ARN"
  type        = string
}

variable "processed_prefix" {
  description = "Key prefix for the Parquet hour files; must be under processed/"
  type        = string
  default     = "processed/bars_1m"

  validation {
    condition     = startswith(var.processed_prefix, "processed/") && !endswith(var.processed_prefix, "/")
    error_message = "processed_prefix must start with processed/ and have no trailing slash."
  }
}

variable "bar_ttl_days" {
  description = "Days a bar stays in DynamoDB before TTL deletes it"
  type        = number
  default     = 7
}

variable "batch_size" {
  description = "Maximum records per invocation"
  type        = number
  default     = 100
}

variable "batching_window_s" {
  description = "Seconds the event-source mapping waits to fill a batch (0 to 300); higher means fewer S3 PUTs and more latency"
  type        = number
  default     = 0
}

variable "maximum_retry_attempts" {
  description = "Retries for a failing batch before it goes to the DLQ"
  type        = number
  default     = 5
}

variable "maximum_record_age_s" {
  description = "Records older than this are sent to the DLQ instead of processed (60 to 604800)"
  type        = number
  default     = 3600
}

variable "memory_size" {
  description = "Lambda memory in MB; DuckDB is comfortable at 512"
  type        = number
  default     = 512
}

variable "timeout_s" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 60
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the processor"
  type        = number
  default     = 14
}

variable "log_level" {
  description = "Python log level for the processor"
  type        = string
  default     = "INFO"
}

variable "permissions_boundary" {
  description = "ARN of the permissions boundary set on every IAM role here (infra/bootstrap creates one per environment); null for none"
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags merged with the provider default tags"
  type        = map(string)
  default     = {}
}
