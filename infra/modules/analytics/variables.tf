variable "name_prefix" {
  description = "Prefix for every resource name, e.g. market-pipeline-dev"
  type        = string
}

variable "lake_bucket" {
  description = "Lake bucket holding processed/ (and Athena results under athena-results/)"
  type        = string
}

variable "symbols" {
  description = "Symbols projected as the symbol partition; add one here when the producer starts fetching it"
  type        = list(string)
}

variable "projection_start_date" {
  description = "First dt partition Athena projects (yyyy-MM-dd); earlier dates are never scanned"
  type        = string
  default     = "2026-10-01"
}

variable "bytes_scanned_cutoff" {
  description = "Athena cancels any query in the workgroup that would scan more than this many bytes (cost guard)"
  type        = number
  default     = 1073741824 # 1 GiB, about half a cent
}

variable "force_destroy" {
  description = "Let terraform destroy the workgroup while it has saved queries and history"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Extra tags merged with the provider default tags"
  type        = map(string)
  default     = {}
}
