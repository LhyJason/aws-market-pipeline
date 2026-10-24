# Resume notes

Bullets about this project that the repository backs up, each with where to
check it. Every number is labelled by where it was measured. Nothing here was
measured on AWS ([ADR 0009](adr/0009-measure-on-localstack-only.md)), so
don't quote a latency, throughput or cost as an AWS figure.

## Bullets

- **Built a serverless, real-time market data pipeline on AWS** (EventBridge,
  Lambda, Kinesis Data Streams and Firehose, DynamoDB, S3 Parquet lake, Glue,
  Athena, API Gateway) **entirely in Terraform**: 6 reusable modules applied
  to dev, prod and a LocalStack environment that runs the whole stack for $0.
- **Designed at-least-once stream processing that counts every event exactly
  once**: conditional DynamoDB writes, partial batch responses,
  bisect-on-error retries and an SQS dead-letter queue. Load tests on
  LocalStack lost or double-counted 0 of 25,800 ticks, including runs where
  the processor fell minutes behind.
- **Wrote chaos tests** that inject poison records, a permanently failing
  key, DynamoDB outages and a broken deploy into the running stack, and check
  that each failure is contained, reported (DLQ, 503, 502) and recovered
  from.
- **Secured CI/CD with GitHub Actions OIDC** (no stored AWS keys):
  - a read-only plan role for pull requests;
  - per-environment deploy roles limited to named actions on the
    environment's resources;
  - permissions boundaries that stop the roles from escalating privileges;
  - a manual prod gate and a post-deploy smoke test.
- **Built a FastAPI service on Lambda behind API Gateway** with API keys,
  usage-plan throttling and a daily quota that caps its cost. It computes
  rolling z-scores and spike and gap flags per request.
- **Instrumented the pipeline** with structured JSON logs, an end-to-end lag
  metric in CloudWatch Embedded Metric Format, 5 SNS-notified alarms, a
  dashboard and X-Ray tracing.
- **Measured it honestly:**
  - a load generator and a log-based report giving throughput, p50/p95 lag
    and a clock-skew correction;
  - on LocalStack, the single-shard ceiling (about 70 ticks/s, set by batch
    size) and p95 lag of about 1.4 to 1.7 s up to that rate;
  - 216 unit tests at 95% statement and branch coverage.
- **Kept a $13 to $17 a month cost model** by component, and showed how to
  pay $0 by running the same Terraform on LocalStack.

Shorter versions, for a one-line entry:

- Serverless real-time data pipeline on AWS (Kinesis, Lambda, DynamoDB,
  S3/Parquet, Athena, API Gateway) in Terraform, with OIDC CI/CD, chaos tests,
  and a $0 LocalStack environment.
- Exactly-once counting on an at-least-once stream (idempotent conditional
  writes, DLQ); load-tested with zero lost or duplicated events.

## Where each claim is backed

| Claim | Evidence |
| --- | --- |
| 6 modules, three environments | `infra/modules/*`, `infra/envs/{dev,prod,local}` |
| $0 local environment | `infra/envs/local/README.md`, `make demo` |
| Idempotent, exactly-counted processing | `services/processor/src/processor/store.py` (conditional writes, tick sets), `docs/architecture.md#delivery-guarantees` |
| 0 of 25,800 ticks lost or double-counted | `docs/load-test.md`, results table (LocalStack) |
| Chaos tests | `tests/integration/test_chaos.py`, `tests/integration/README.md#chaos-tests` |
| OIDC roles, permissions boundaries, no escalation | `infra/bootstrap/github.tf`, [ADR 0007](adr/0007-github-oidc-deploy-roles.md) |
| Manual prod gate, smoke test | `.github/workflows/deploy.yml`, [ADR 0008](adr/0008-manual-prod-deploys.md) |
| API keys, throttling, quota | `infra/modules/api/main.tf`, [ADR 0005](adr/0005-rest-api-with-api-key.md) |
| Rolling statistics at read time | `services/api/src/api/stats.py` |
| Observability | `infra/modules/observability`, `services/processor/src/processor/metrics.py`, [ADR 0006](adr/0006-observability-cloudwatch-xray.md) |
| Throughput ceiling and lag | `docs/load-test.md` (LocalStack, one laptop) |
| 216 tests, 95% coverage | `make test` (`pytest-cov`, statements and branches of `services/`) |
| Cost model | `docs/cost.md` (estimate from AWS prices, not measured) |

## What not to claim

- **AWS performance:** latency, throughput or cost measured on AWS. None of
  them were.
- **Production use:** prod has never been applied, and the stack has never
  run on a real account.
- **IAM sufficiency:** that the IAM policies are proven sufficient.
  LocalStack doesn't enforce IAM, so only a deploy to AWS would show it.
