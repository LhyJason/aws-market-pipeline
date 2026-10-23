# ADR 0005: REST API with an API key as the front door

**Status:** accepted · **Date:** 2026-10-23

## Context

The phase 4 API (FastAPI on Lambda) needs a public front door and some form
of auth. The project runs end to end on free LocalStack 4.14 for $0, so
whatever is chosen should deploy the same way in dev and local and its auth
should be testable locally. The architecture doc had pencilled in an HTTP API.

Each option was tried against the pinned `localstack/localstack:4.14.0` image
with the AWS CLI before deciding:

| Option                                   | Free LocalStack 4.14                              | AWS cost / million requests |
| ---------------------------------------- | ------------------------------------------------- | -------------- |
| API Gateway HTTP API (`apigatewayv2`)     | no: "not included in your current license plan"   | $1.00          |
| Cognito user pool + JWT authorizer        | no: same error                                    | MAU-priced     |
| **REST API + API key + usage plan**       | **yes: key enforced (403 without or with a wrong one), proxy events match AWS** | **$3.50** |
| REST API + IAM (SigV4) auth               | created, but LocalStack does not enforce IAM      | $3.50          |
| Lambda function URL (`AWS_IAM` or none)   | created, but `AWS_IAM` not enforced (unsigned call got 200); no keys or throttling | free |
| HTTP API in dev, skipped in local         | dev checked only by `terraform validate`          | $1.00          |

On LocalStack, usage-plan and stage throttling were accepted but not enforced,
and REST access logs were accepted but not delivered.

## Decision

- A **regional REST API** with one resource per route (`/health`,
  `/prices/{symbol}`, `/bars/{symbol}`), all `GET`, Lambda proxy integration,
  stage `v1`.
- **API key** on the two data routes; `/health` is open for uptime checks. One
  key in a **usage plan** with a 10 req/s (burst 20) throttle and a
  10,000/day quota; the stage has the same default throttle for every caller.
- **JSON access logs** to a log group with explicit retention. The
  account-level API Gateway logging role is created by dev
  (`manage_account_logging_role`).
- envs/local applies the same module. Only the account logging role is
  switched off there.

## Consequences

- Local integration tests exercise the real auth path: no key, a wrong key
  and a valid key give the same answers on LocalStack as on AWS.
- An API key identifies and meters a caller; it is not strong authentication,
  and anyone holding it can read the data. That is acceptable for a read-only
  API over public market prices. The upgrade path is a Cognito user-pool
  authorizer on the same REST API in dev, tested in CI only.
- A REST API costs $3.50 per million requests against $1.00 for an HTTP API.
  At this project's volume both are under $0.05 a month, and the daily quota
  caps the worst case at about $1 a month.
- Throttling, quotas, IAM and access-log delivery cannot be verified on
  LocalStack; they are checked by `terraform validate` in CI until the stack
  runs on AWS.
- From AWS provider 6.58.0, `aws_api_gateway_rest_api` waits for a status
  LocalStack 4.14 never reports, so envs/local pins the provider below 6.58.
  envs/dev is not pinned.
- Unknown paths are rejected by API Gateway before Lambda runs, so they cost
  no Lambda invocations.
