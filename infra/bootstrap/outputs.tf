output "state_bucket_name" {
  value = aws_s3_bucket.tfstate.bucket
}

output "state_bucket_region" {
  value = var.aws_region
}

output "backend_hcl" {
  description = "Paste into infra/envs/<env>/backend.hcl"
  value       = <<-EOT
    bucket = "${aws_s3_bucket.tfstate.bucket}"
    region = "${var.aws_region}"
  EOT
}

# Set as repository variables only when you mean to deploy (and pay):
#   AWS_PLAN_ROLE_ARN         plans on pull requests (read-only)
#   AWS_DEPLOY_ROLE_ARN       applies dev on every push to main
#   AWS_PROD_DEPLOY_ROLE_ARN  applies prod on a manual workflow run
output "plan_role_arn" {
  value = aws_iam_role.plan.arn
}

output "deploy_role_arns" {
  value = { for env, role in aws_iam_role.deploy : env => role.arn }
}

output "permissions_boundary_arns" {
  value = { for env, policy in aws_iam_policy.boundary : env => policy.arn }
}
