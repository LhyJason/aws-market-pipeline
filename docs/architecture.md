# Architecture

## Goal

Ingest live prices for a handful of symbols, keep a queryable history, and
serve the latest state through an HTTP API, using only managed, pay-per-use
AWS services so the whole thing costs a few dollars a month and needs no
servers to patch.

## Data flow

1. **Schedule.** An EventBridge rule fires the producer Lambda every minute
   (configurable). One-minute granularity is the EventBridge floor; a Fargate
   task on a websocket feed is the upgrade path if sub-minute data is ever
   needed.
2. **Fetch.** The producer calls a public REST price endpoint for the
   configured symbols. No API key is needed for the default feed; if a keyed
   feed is swapped in, the key goes in Secrets Manager and is read at cold
   start.
3. **Publish.** Each tick becomes one Kinesis record, partitioned by symbol so
   a symbol's records stay ordered within a shard. Failed records from a
   `PutRecords` batch are retried with backoff before the invocation gives up.
4. **Land raw** (phase 2). Kinesis Data Firehose reads the same stream and
   writes the records untouched to the lake bucket under
   `raw/dt=YYYY-MM-DD/hour=HH/`, one JSON object per line, flushing every
   five minutes. Objects expire after 30 days: raw is a replay buffer for
   reprocessing, not the system of record.
5. **Consume** (phase 3). A Lambda event-source mapping reads the stream in
   batches of up to 100 records and the processor builds 1-minute bars per
   symbol (OHLC plus `tick_count`, since the feed has no volume). DynamoDB
   holds the latest price and the last 7 days of bars and is the source of
   truth for bars. For every symbol-hour a batch touches, the processor
   rewrites one Parquet file at a fixed key,
   `processed/bars_1m/symbol=/dt=/hour=/bars.parquet`, from that hour's bars
   in DynamoDB, using DuckDB ([ADR 0004](adr/0004-duckdb-parquet-rebuilt-from-dynamodb.md)).
   Rolling statistics and anomaly flags move to phase 4, where the API uses
   them.
6. **Query** (phase 3). A Glue table over `processed/bars_1m/` uses partition
   projection, so Athena sees new hours without a crawler. Three saved queries
   (daily VWAP, largest 1-minute moves, gaps in minutes) run in an Athena
   workgroup with a 1 GiB per-query scan limit.
7. **Serve** (phase 4). An API Gateway REST API fronts a FastAPI app on
   Lambda (through Mangum) that reads DynamoDB only: `GET /prices/{symbol}`
   is one `GetItem` on `LATEST`, `GET /bars/{symbol}` one `Query` for the
   newest bars in a time range plus one for the rolling window before them,
   and `GET /health` reads nothing. Rolling statistics and anomaly flags are
   computed per request rather than stored, because a late tick can still
   move a bar's close: each bar's 1-minute return gets a z-score against the
   preceding window's returns, `|z| >= 3` is a `spike`, and a missing minute
   is a `gap`. The data routes need an API key, metered by a usage plan
   (10 req/s, 10,000 a day); see [ADR 0005](adr/0005-rest-api-with-api-key.md).

## Record schema (v1)

```json
{"symbol": "BTCUSDT", "price": 67321.5, "ts": 1729700000000, "source": "binance"}
```

Each Kinesis record is one such object followed by `
`. `ts` is the
producer-side observation time in epoch milliseconds. A `v`
field will be added if the schema changes; consumers treat missing `v` as 1.

## Market table (DynamoDB)

On demand, PK `symbol`, SK `sk`:

| `sk`                      | Item                                                       |
| ------------------------- | ---------------------------------------------------------- |
| `LATEST`                  | newest `price`, `ts`, `source`; only moves forward          |
| `BAR#<13-digit epoch ms>` | one minute: `open`, `high`, `low`, `close`, `tick_count`, `tick_ts` (set), `version`, `expires_at` (TTL, 7 days) |

The 13-digit sort key orders bars by time, so the phase 4 API reads recent
bars with one `Query`.

## Delivery guarantees

Kinesis delivers at least once, and every processor write is idempotent:

- Bar merges use max/min for high/low, the earliest and latest tick time for
  open/close, and a set of tick timestamps for the count. Merging a tick twice
  changes nothing, and an unchanged bar is not rewritten. Writes are
  conditional on the version that was read.
- `LATEST` is updated only by a newer tick.
- Parquet hour files are rebuilt whole and overwrite the same key.

The event-source mapping uses `ReportBatchItemFailures`. If a symbol fails,
the handler returns the earliest sequence number that carried its ticks and
Lambda retries from there. It also bisects batches on errors, retries 5 times,
and gives up on records older than an hour. A batch that keeps failing goes
to an SQS dead-letter queue as metadata (shard, sequence range); the records
can be replayed from the stream (24 hours) or `raw/` (30 days). Malformed
records are logged and dropped, since retrying cannot fix them.

## Environments

`infra/envs/dev` and `infra/envs/prod` are separate root modules with separate
state keys in the same state bucket. Resource names are prefixed
`<project>-<env>` so both can live in one account. Prod differs from dev only
in its prefix and state key, and in leaving the account-wide API Gateway
logging role to dev. Neither has been applied to AWS.

Deploys run from GitHub Actions (`deploy.yml`). Pull requests get a read-only
`terraform plan` of both environments. A push to main applies dev and then
smoke-tests its API: `/health` must answer, and `/prices` must not return a
403 or a 5xx. Prod is applied only by a manual run of the workflow from main
([ADR 0008](adr/0008-manual-prod-deploys.md)). Every job is skipped until its
role ARN is set as a repository variable.

`infra/envs/local` applies the ingest, storage, processing and api modules to
LocalStack 4.14 in Docker for $0. Glue and Athena are not in LocalStack's free
edition, so local leaves the analytics module out, and
`scripts/query_lake.py` runs the same saved queries with DuckDB over the
Parquet files instead. The processor bundles DuckDB and the API pydantic-core,
both native wheels, so `scripts/build_lambda.py` builds their packages for
the architecture they run on: arm64 on AWS, the Docker host's (usually
x86_64) on LocalStack. LocalStack enforces API keys, so
`tests/integration` checks auth and the whole path from producer to API
locally; it does not enforce IAM, API Gateway throttling or quotas, which
only a run on AWS can check. envs/local pins the AWS provider below 6.58,
whose REST API waiter needs a status LocalStack does not report. It also
skips the CloudWatch alarms (`create_alarms = false`): LocalStack 4.14 answers
the provider's `DescribeAlarms` with a 500, so `apply` would hang. The alarm
topic, dashboard and tracing settings are created there.

`make test-chaos` breaks the local stack on purpose: poison records, a symbol
that always fails, the API's table missing, a bad API deploy. It checks that
each failure stays contained, then restores the stack
(`tests/integration/README.md`).

## Security posture

- **Workload roles.** One IAM role per Lambda or Firehose stream, scoped to
  the exact stream, table, bucket prefix or queue it uses. The API role can
  only `GetItem` and `Query` the market table. Resource `"*"` appears in two
  places only: X-Ray's `PutTraceSegments` and `PutTelemetryRecords`, which
  take no resource ARN, and API Gateway's account-wide logging role, which
  uses the AWS-managed `AmazonAPIGatewayPushToCloudWatchLogs` policy.
- **Permissions boundaries.** In dev and prod every one of those roles
  carries its environment's permissions boundary
  (`<project>-<env>-workload-boundary`, from `infra/bootstrap`). The boundary
  is the union of what the workloads need, limited to that environment's
  resources. A role can never do more than the boundary allows, whatever
  policy is attached to it.
- **Deploys from GitHub.** They use OIDC federation, so no AWS access keys are
  stored in GitHub ([ADR 0007](adr/0007-github-oidc-deploy-roles.md)):
  - **Plan role:** trusts only this repository's `pull_request` tokens, is
    read-only, and plans without locking the state.
  - **Deploy roles:** one per environment, each trusting only
    `environment:<env>` tokens.
  - **Named actions only.** No `service:*` actions anywhere. Resources are
    `<project>-<env>-*` ARNs, or, for API Gateway's random IDs, those tagged
    with the environment.
  - **No privilege escalation.** A deploy role may create a role, or change
    its policies, only if that role carries the environment's boundary. It
    can't remove a boundary or edit the boundary policy, and may pass roles
    only to Lambda, Firehose and API Gateway.
- **API access.** The data routes require an API key. API Gateway rejects
  requests without one, and unknown paths, before Lambda runs. A stage-wide
  throttle and the usage plan's quota bound what one caller can do. API keys
  meter callers rather than strongly authenticate them; a Cognito authorizer
  is the upgrade path.
- **Encryption.** Kinesis is encrypted at rest with the AWS-managed KMS key,
  the lake bucket with SSE-S3. The lake bucket denies non-TLS requests and
  blocks public access.
- **Alarm topic.** Only this account's `<project>-<env>-*` CloudWatch alarms
  may publish to it, and only over TLS. It is unencrypted: CloudWatch cannot
  publish under the AWS-managed SNS key, and alarm messages hold nothing
  secret.
- **Terraform state bucket.** Versioned, encrypted, public access blocked.
  Each deploy role reads and writes only its own `<env>/` keys. The state
  holds the API keys, so the plan role can read them; only collaborators can
  open pull requests that get it.
- **Log retention.** CloudWatch log groups have explicit retention, so logs
  don't accumulate forever.

## Observability

See [ADR 0006](adr/0006-observability-cloudwatch-xray.md) and
`infra/modules/observability`.

- **Logs:** structured JSON from every Lambda, one object per line, queryable
  in Logs Insights. Each processor batch logs `records`, `malformed`,
  `stale`, `bars`, `failed_symbols` and `lag_ms`.
- **Metrics:** AWS's own Lambda, Kinesis, Firehose, SQS, DynamoDB and API
  Gateway metrics (stage-level, so no detailed-metrics charge), plus one
  custom metric. The processor writes `MarketPipeline/TickLagMs` as an
  Embedded Metric Format log line: the age of the batch's oldest written
  tick, from the producer's observation to now. That is the end-to-end
  latency. `IteratorAge` covers only the wait on the stream.
- **Alarms:** each one notifies an SNS topic with an optional email
  subscription (`alarm_email`), on ALARM and on OK, and treats missing data
  as fine.

  | Alarm | Fires when | Usually means |
  | --- | --- | --- |
  | `-producer-errors` | Lambda errors in 3 of 5 minutes | the feed is down, or ticks still fail to reach Kinesis after retries (the producer raises then) |
  | `-processor-iterator-age` | oldest unprocessed record > 5 min for 5 min | the processor is failing or too slow |
  | `-processor-dlq` | any message in the DLQ, until it is drained | a batch failed every retry and needs a replay |
  | `-api-5xx` | ≥ 5 5xx responses in 5 min | DynamoDB errors (503) or a broken Lambda (502) |
  | `-api-latency` | p95 > 2 s for three 5-minute periods | slow queries or a struggling Lambda |

- **Dashboard** `<project>-<env>`: alarm status, producer runs and errors,
  stream and Firehose records, processor iterator age, duration and errors,
  end-to-end lag (p50/p95/max), DLQ depth with DynamoDB capacity and
  throttles, API requests, 4xx/5xx and latency, and the API Lambda.
- **Tracing:** X-Ray Active tracing on the producer, processor and API
  Lambdas and on the API stage, with no SDK. API Gateway and the API Lambda
  share a trace. Traces don't cross Kinesis, so the producer and the
  processor each get their own.
- **On LocalStack:** the topic and dashboard exist but stay empty. There are
  no alarms, LocalStack publishes few AWS metrics and doesn't parse EMF, and
  X-Ray is not in its free edition.
