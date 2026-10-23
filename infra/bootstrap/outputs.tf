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
