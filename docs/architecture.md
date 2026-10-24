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
`<project>-<env>` so both can live in one account. Prod is a placeholder until
phase 5.

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
whose REST API waiter needs a status LocalStack does not report.

## Security posture

- One IAM role per Lambda or Firehose stream, scoped to the exact stream,
  table, or bucket prefix it uses. No wildcard resources, with one
  exception: API Gateway's account-wide logging role uses the AWS-managed
  `AmazonAPIGatewayPushToCloudWatchLogs` policy. The API role can only
  `GetItem` and `Query` the market table.
- The API's data routes require an API key; API Gateway rejects requests
  without one, and unknown paths, before Lambda runs. A stage-wide throttle
  and the usage plan's quota bound what one caller can do. API keys meter
  callers rather than strongly authenticate them; a Cognito authorizer is
  the upgrade path.
- Kinesis encrypted at rest with the AWS-managed KMS key, the lake bucket
  with SSE-S3; the lake bucket denies non-TLS requests and blocks public access.
- Terraform state bucket: versioned, encrypted, public access blocked.
- Deploys from GitHub use OIDC federation to assume a role; no access keys are
  stored in GitHub (phase 5).
- CloudWatch log groups have explicit retention so logs do not accumulate
  forever.

## Observability (phase 5)

Structured JSON logs from every Lambda (already in place for the producer),
CloudWatch metrics and alarms on Lambda errors, iterator age on the consumer
(the alarm exists since phase 3, without a notification target), and API 5xx
rate, with SNS email notification. X-Ray tracing across the
producer and API.
