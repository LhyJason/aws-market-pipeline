# Partial backend configuration. Bucket and region come from backend.hcl,
# which is gitignored; copy backend.hcl.example or run
#   terraform -chdir=../../bootstrap output -raw backend_hcl > backend.hcl
# then
#   terraform init -backend-config=backend.hcl
terraform {
  backend "s3" {
    key          = "prod/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
