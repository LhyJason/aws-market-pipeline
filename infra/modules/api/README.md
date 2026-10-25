# api module

API Gateway REST API -> API Lambda (FastAPI via Mangum) -> the market table,
read-only. Code: `services/api`.

| Resource                                  | Notes                                                      |
| ----------------------------------------- | ---------------------------------------------------------- |
| `aws_lambda_function`                     | python3.12, `architecture` (arm64 on AWS), 512 MB, 10 s, handler `api.app.handler`, X-Ray Active tracing, zipped from `package_dir` |
| `aws_iam_role` + inline policy            | `GetItem` and `Query` on the one table (no `Scan`, no writes), logs to its own group, X-Ray writes (`*`); `permissions_boundary` on the role (dev/prod: the env's boundary from bootstrap) |
| `aws_api_gateway_rest_api`                | regional; one resource per route, no `{proxy+}`            |
| `aws_api_gateway_method` / `_integration` | `GET`, Lambda proxy (`AWS_PROXY`); `/health` open, `/prices/{symbol}` and `/bars/{symbol}` need an API key |
| `aws_api_gateway_deployment` + `_stage`   | stage `v1`; redeployed when any route changes              |
| `aws_api_gateway_method_settings`         | stage-wide throttle `*/*`: 10 req/s, burst 20              |
| `aws_api_gateway_api_key` + `_usage_plan` | one key; usage plan with the same throttle and a 10,000/day quota as a cost cap |
| `aws_cloudwatch_log_group` (x2)           | Lambda logs and JSON access logs, explicit retention       |
| `aws_api_gateway_account` + IAM role      | API Gateway's logging role; `manage_account_logging_role`  |

Unknown paths get API Gateway's own 403 (`Missing Authentication Token`) and
never reach Lambda. A missing or wrong key is a 403 `Forbidden` from API
Gateway; bad input is a 422 from the app.

## Why a REST API with an API key

HTTP APIs (`apigatewayv2`) and Cognito are not in LocalStack's free edition, so
neither could be tested locally. A REST API with an API key deploys the same
way in dev and local, and LocalStack enforces the key, so the same integration
tests cover auth in both. An API key identifies a caller and meters it with
the usage plan; it is not strong authentication. That is enough for a
read-only market-data API; a Cognito authorizer in dev is the upgrade path.
See [ADR 0005](../../../docs/adr/0005-rest-api-with-api-key.md).

## The account logging role

API Gateway writes access logs with one IAM role per account and region, set
by `aws_api_gateway_account`. Set `manage_account_logging_role = true` in one
environment per account (dev does); leave it false wherever something else
manages it. The role uses the AWS-managed
`AmazonAPIGatewayPushToCloudWatchLogs` policy, the one exception to the
project's no-wildcards rule: it is shared by every REST API in the account.

It takes `logging_role_permissions_boundary`, not the workload
`permissions_boundary`: API Gateway refuses a logging role whose boundary
doesn't allow writing to every log group, so dev and prod pass
`<prefix>-apigateway-logs-boundary` from `infra/bootstrap`, which allows
exactly the managed policy's actions.

## Packaging

The API bundles pydantic-core, a native wheel, so the package is built before
Terraform runs and must match `architecture`:

```bash
python scripts/build_lambda.py api --arch arm64    # envs/dev
python scripts/build_lambda.py api --arch x86_64   # envs/local
```

The result lands in `.build/api-<arch>/` and Terraform zips it (about 3 MB).
Rebuild after changing `services/api`; `source_code_hash` picks up the change.

## Outputs

`invoke_url` is the AWS URL (`https://<id>.execute-api.<region>.amazonaws.com/v1`).
`api_key` is sensitive: `terraform output -raw api_key`. On LocalStack the API
is served at `http://localhost:4566/_aws/execute-api/<id>/v1`, which
envs/local outputs as `api_url`.

## Not enforced on LocalStack

Throttling, the usage-plan quota, IAM permissions and access-log delivery are
created but not enforced or delivered locally (see `infra/envs/local/README.md`).
They are checked only by `terraform validate` in CI until the stack runs on AWS.
