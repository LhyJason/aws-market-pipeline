variable "aws_region" {
  description = "Region for the state bucket"
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name, used in tags and the budget name"
  type        = string
  default     = "market-pipeline"
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for Terraform state"
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget; alerts at 80% actual and 100% forecasted"
  type        = number
  default     = 15
}

variable "budget_alert_email" {
  description = "Email for budget alerts; empty string disables the budget"
  type        = string
  default     = ""
}
