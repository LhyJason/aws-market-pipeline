# prod environment

The same modules as `../dev`, with `environment = "prod"` (names prefixed
`market-pipeline-prod`) and its own state key (`prod/terraform.tfstate`) in
the same bucket. It lives in the same account as dev.

Differences from dev:

- **`manage_apigateway_account = false`.** API Gateway's CloudWatch logging
  role is one per account and region, and dev sets it. Deploy dev first, or
  prod's stage fails to enable access logs.
- **Kinesis retention stays at 24 hours,** the same as dev. Anything longer is
  extended retention at $0.02 per shard-hour, about +$14.60 a month, more
  than the shard itself.
- **Set `alarm_email`** (or the `ALARM_EMAIL` repository variable in CI):
  without it, prod alarms notify nobody.
- **Its own deploy role and boundary** (`market-pipeline-github-deploy-prod`,
  `market-pipeline-prod-workload-boundary`), so a dev deploy cannot touch
  prod resources.

## Deploying

Only through a manual run of the `deploy` workflow from `main` with
`environment = prod`; pushes never deploy prod. On a private repository on
GitHub Free, environments have no required reviewers, so starting that run is
the approval. The job declares `environment: prod`, so reviewers can be added
under Settings → Environments (public repository, or GitHub Pro/Team) with no
workflow change. Pull requests run a read-only `terraform plan` for prod
alongside dev.

By hand, after `infra/bootstrap`:

```bash
cp terraform.tfvars.example terraform.tfvars
terraform -chdir=../../bootstrap output -raw backend_hcl > backend.hcl
python ../../../scripts/build_lambda.py processor --arch arm64
python ../../../scripts/build_lambda.py api --arch arm64
terraform init -backend-config=backend.hcl
terraform apply
```

A second stack costs about as much as dev (about $13 a month, mostly the
Kinesis shard; see `docs/cost.md`). It has not been applied; dev has run on
AWS once (`docs/adr/0011-one-capped-aws-run.md`).
