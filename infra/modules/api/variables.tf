variable "name_prefix" {
  description = "Prefix for every resource name, e.g. market-pipeline-dev"
  type        = string
}

variable "package_dir" {
  description = "Directory zipped as the API package, built by scripts/build_lambda.py for var.architecture"
  type        = string
}

variable "architecture" {
  description = "Lambda architecture; must match the pydantic-core wheel in package_dir (LocalStack runs on the host's, usually x86_64)"
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.architecture)
    error_message = "architecture must be arm64 or x86_64."
  }
}

variable "table_name" {
  description = "Market DynamoDB table the API reads"
  type        = string
}

variable "table_arn" {
  description = "ARN of the market table"
  type        = string
}

variable "symbols" {
  description = "Symbols the API serves; others get a 404 without a read. Empty allows any"
  type        = list(string)
  default     = []
}

variable "bar_ttl_days" {
  description = "Bar TTL in the table; the longest range one /bars request may span"
  type        = number
  default     = 7
}

variable "anomaly_z" {
  description = "z-score at which a bar's return is flagged as a spike"
  type        = number
  default     = 3
}

variable "stale_after_s" {
  description = "Seconds after which /prices reports the latest tick as stale"
  type        = number
  default     = 180
}

variable "stage_name" {
  description = "API Gateway stage, the first path segment of the URL"
  type        = string
  default     = "v1"
}

variable "throttle_rate_limit" {
  description = "Steady-state requests per second, for the stage and the API key"
  type        = number
  default     = 10
}

variable "throttle_burst_limit" {
  description = "Burst requests, for the stage and the API key"
  type        = number
  default     = 20
}

variable "quota_per_day" {
  description = "Requests per day the API key may make; a cost cap"
  type        = number
  default     = 10000
}

variable "enable_access_logs" {
  description = "Write API Gateway access logs to CloudWatch; needs the account's API Gateway logging role (see manage_account_logging_role)"
  type        = bool
  default     = true
}

variable "manage_account_logging_role" {
  description = "Create the IAM role API Gateway uses to write logs and set it on the account (one per account and region). Leave false if something else manages it"
  type        = bool
  default     = false
}

variable "memory_size" {
  description = "Lambda memory in MB; CPU scales with it, and the stats are CPU-bound for large requests"
  type        = number
  default     = 512
}

variable "timeout_s" {
  description = "Lambda timeout in seconds; API Gateway gives up after 29"
  type        = number
  default     = 10
}

variable "log_retention_days" {
  description = "CloudWatch retention for the Lambda and access logs"
  type        = number
  default     = 14
}

variable "log_level" {
  description = "Python log level for the API"
  type        = string
  default     = "INFO"
}

variable "permissions_boundary" {
  description = "ARN of the permissions boundary set on every IAM role here (infra/bootstrap creates one per environment); null for none"
  type        = string
  default     = null
}

variable "logging_role_permissions_boundary" {
  description = "ARN of the permissions boundary for API Gateway's logging role (infra/bootstrap creates <prefix>-apigateway-logs-boundary); null for none. API Gateway rejects the role under the workload boundary"
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags merged with the provider default tags"
  type        = map(string)
  default     = {}
}
