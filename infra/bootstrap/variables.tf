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

variable "github_repository" {
  description = "owner/name of the repository whose workflows may assume the deploy roles"
  type        = string
  default     = "LhyJason/aws-market-pipeline"
}

variable "environments" {
  description = "Environments that get a deploy role and a workload permissions boundary"
  type        = list(string)
  default     = ["dev", "prod"]
}

variable "deploy_subjects" {
  description = "OIDC subject claims (after repo:<owner>/<name>:) allowed to assume each environment's deploy role. GitHub sets environment:<name> for jobs that declare an environment"
  type        = map(list(string))
  default = {
    dev  = ["environment:dev"]
    prod = ["environment:prod"]
  }
}

variable "apigateway_account_environment" {
  description = "The one environment whose deploy role may set API Gateway's account-wide CloudWatch role (manage_apigateway_account = true there)"
  type        = string
  default     = "dev"
}

variable "create_github_oidc_provider" {
  description = "Create the token.actions.githubusercontent.com identity provider; false if the account already has one (one per account)"
  type        = bool
  default     = true
}
