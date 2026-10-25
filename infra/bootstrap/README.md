# bootstrap

One-time, per-account resources that everything else depends on. Uses local
state on purpose (there is no remote state yet). Run it once, commit nothing
from `.terraform/` or `terraform.tfstate`, and keep the local state file
somewhere safe (it only tracks a bucket, a budget and IAM, all easy to
re-import).

Creates:

- S3 bucket for Terraform state: versioned, SSE-S3, public access blocked,
  old versions expire after 90 days, `prevent_destroy`.
- AWS Budget with email alerts at 80% actual and 100% forecasted spend
  (skipped when `budget_alert_email` is empty).
- GitHub Actions deploy access (`github.tf`), described below.

```bash
cp terraform.tfvars.example terraform.tfvars   # pick a unique bucket name
terraform init
terraform apply
terraform output -raw backend_hcl > ../envs/dev/backend.hcl
terraform output -raw backend_hcl > ../envs/prod/backend.hcl
```

## GitHub OIDC deploy access

GitHub Actions exchanges its OIDC token for short-lived credentials, so no AWS
keys are stored in GitHub.

| Resource | Trusted subject (`repo:<github_repository>:…`) | Can do |
| --- | --- | --- |
| OIDC provider `token.actions.githubusercontent.com` | n/a | Audience `sts.amazonaws.com`. Set `create_github_oidc_provider = false` if the account already has one |
| `<project>-github-plan` | `pull_request` | Read what `terraform plan` refreshes in every environment, and read (not lock) the state. Fork PRs get no token |
| `<project>-github-deploy-<env>` | `environment:<env>` (`deploy_subjects`) | Create, update and delete `<project>-<env>-*` resources, and read and write `<env>/*` in the state bucket |
| `<project>-<env>-workload-boundary` | n/a | Permissions boundary: the most any role in that environment may do |
| `<project>-dev-apigateway-logs-boundary` | n/a | Permissions boundary for API Gateway's account logging role only: the seven log actions of its AWS-managed policy (`apigateway_account_environment`, dev by default) |

How the roles are kept narrow:

- **Named actions only.** Every statement lists its actions; there is no
  `service:*`. The service-by-service lists are in `local.services`, and the
  plan role gets the read half of each one.
- **Scoped by name.** Resources are `<project>-<env>-*` ARNs (the Glue
  database uses underscores). `"*"` is used only for
  `cloudwatch:DescribeAlarms` and `logs:DescribeLogGroups`, which take no
  ARN, and for `lambda:CreateEventSourceMapping`, which is limited by a
  `lambda:FunctionArn` condition instead.
- **Scoped by tag where IDs are random.** REST API, API key and usage plan IDs
  are generated, so API Gateway calls are allowed only on resources tagged
  `Environment = <env>`, which the providers' `default_tags` set, and new
  ones must be created with that tag. The REST API's children (resources,
  methods, stages, deployments) are authorized against the API's tags.
- **No privilege escalation through IAM.** The deploy role can only create a
  role, or change its policies, while that role carries the environment's
  permissions boundary (`iam:PermissionsBoundary` condition). The one
  exception is API Gateway's logging role, below. It cannot
  remove a boundary or edit the boundary policy, and `iam:PassRole` is limited
  to Lambda, Firehose and API Gateway. Every module sets the boundary on its
  roles (`permissions_boundary`, built from the account ID in envs/dev and
  envs/prod; null in envs/local).
- **One AWS-managed policy.** Only the `apigateway_account_environment` (dev)
  role may attach `AmazonAPIGatewayPushToCloudWatchLogs`, only to
  `<project>-dev-apigateway-logs`, and may set API Gateway's account-wide
  logging role. That role carries its own boundary,
  `<project>-dev-apigateway-logs-boundary`, which allows exactly the managed
  policy's seven log actions on every log group. API Gateway refuses a
  logging role that can't write to every log group ("The role ARN does not
  have required permissions configured"), so the workload boundary can't be
  its ceiling; the first AWS deploy hit that. The deploy role may set the
  logging boundary on that one role only, and can't give it an inline
  policy.

Each deploy role has two customer-managed policies (`-services` and
`-control`), because together they exceed the 10,240-character inline limit
and each must stay under the 6,144-character managed limit. Rendered sizes:
about 4,450 and 5,600 characters for dev.

### Turning deploys on (costs money)

The deploy workflow skips every job until these repository variables are set.
Set them only when you mean to pay for the dev stack (about $13 a month,
mostly the Kinesis shard):

| Variable | Value | Enables |
| --- | --- | --- |
| `AWS_REGION`, `TF_STATE_BUCKET` | region and `state_bucket_name` | all jobs |
| `AWS_PLAN_ROLE_ARN` | `terraform output plan_role_arn` | plans on pull requests |
| `AWS_DEPLOY_ROLE_ARN` | `deploy_role_arns["dev"]` | apply dev on every push to main |
| `AWS_PROD_DEPLOY_ROLE_ARN` | `deploy_role_arns["prod"]` | apply prod on a manual run |
| `ALARM_EMAIL`, `FEED_BASE_URL` | optional | `alarm_email`; the feed (defaults to `https://api.binance.us`) |

The deploy roles trust `environment:<env>` subjects, which GitHub sets only
for jobs that declare `environment:`. Environments in a private repository
need GitHub Pro or Team. If GitHub doesn't send that subject, either make the
repository public or set `deploy_subjects` to, for example,
`{ dev = ["ref:refs/heads/main"], prod = ["ref:refs/heads/main"] }`. Note
that with that setting, any job on main can assume either deploy role.

### What has been checked

The deploy and plan roles have never been applied to AWS. `terraform
validate` runs in CI, and a copy of this configuration was applied to
LocalStack 4.14 to render every policy and measure it. LocalStack does not
enforce IAM, so whether the action lists are complete, and whether the API
Gateway tag conditions hold, only shows on the first real deploy through
them. AWS IAM Access Analyzer's policy checks are the next step before that.

The boundaries have been. On 2026-10-25 the budget and the dev workload
boundary were applied to AWS, and dev ran under the boundary for an hour
([ADR 0011](../../docs/adr/0011-one-capped-aws-run.md)): every workload role
did its job within it. The logging role's own boundary was then applied with
just that role, and API Gateway accepted it. That run used an IAM user with
admin rights, not the deploy role, and kept state locally.
