# bootstrap

One-time, per-account resources that everything else depends on. Uses local
state on purpose (there is no remote state yet). Run it once, commit nothing
from `.terraform/` or `terraform.tfstate`, and keep the local state file
somewhere safe (it only tracks a bucket and a budget, both easy to re-import).

Creates:

- S3 bucket for Terraform state: versioned, SSE-S3, public access blocked,
  old versions expire after 90 days, `prevent_destroy`.
- AWS Budget with email alerts at 80% actual and 100% forecasted spend
  (skipped when `budget_alert_email` is empty).

```bash
cp terraform.tfvars.example terraform.tfvars   # pick a unique bucket name
terraform init
terraform apply
terraform output -raw backend_hcl > ../envs/dev/backend.hcl
```

Phase 5 adds the GitHub OIDC identity provider and the deploy role here.
