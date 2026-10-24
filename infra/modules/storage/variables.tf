variable "name_prefix" {
  description = "Prefix for every resource name, e.g. market-pipeline-dev"
  type        = string
}

variable "source_stream_arn" {
  description = "ARN of the Kinesis Data Stream that Firehose reads from"
  type        = string
}

variable "raw_expiration_days" {
  description = "Days after which objects under raw/ (and Firehose errors/) are deleted"
  type        = number
  default     = 30

  validation {
    condition     = var.raw_expiration_days >= 1
    error_message = "raw_expiration_days must be at least 1."
  }
}

variable "athena_results_expiration_days" {
  description = "Days after which Athena query results under athena-results/ are deleted"
  type        = number
  default     = 7
}

variable "buffering_interval_s" {
  description = "Firehose flushes to S3 after this many seconds (60 to 900), whichever of size/interval comes first"
  type        = number
  default     = 300
}

variable "buffering_size_mb" {
  description = "Firehose flushes to S3 after this many MiB (1 to 128)"
  type        = number
  default     = 5
}

variable "compression_format" {
  description = "Firehose compression for raw objects: UNCOMPRESSED, GZIP, ZIP, Snappy or HADOOP_SNAPPY"
  type        = string
  default     = "UNCOMPRESSED"
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete the lake bucket while it still holds objects"
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch log retention for Firehose delivery errors"
  type        = number
  default     = 14
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

variable "table_point_in_time_recovery" {
  description = "Continuous backups for the market table (billed per GB; the table is rebuildable from Kinesis and raw/)"
  type        = bool
  default     = false
}

variable "table_deletion_protection" {
  description = "Block deleting the market table; off so dev can be destroyed between sessions"
  type        = bool
  default     = false
}
