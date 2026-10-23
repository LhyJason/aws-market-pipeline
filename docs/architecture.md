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
   batches, computes 1-minute OHLCV bars and rolling statistics, writes the
   latest state to DynamoDB, and appends Parquet files to S3 partitioned by
   `symbol/date/hour`.
6. **Query** (phase 3). A Glue table over the Parquet prefix lets Athena run
   SQL across the full history.
7. **Serve** (phase 4). API Gateway (HTTP API) fronts a FastAPI app on Lambda
   that reads DynamoDB for latest price, recent bars, and anomaly flags.

## Record schema (v1)

```json
{"symbol": "BTCUSDT", "price": 67321.5, "ts": 1729700000000, "source": "binance"}
```

Each Kinesis record is one such object followed by `
`. `ts` is the
producer-side observation time in epoch milliseconds. A `v`
field will be added if the schema changes; consumers treat missing `v` as 1.

## Environments

`infra/envs/dev` and `infra/envs/prod` are separate root modules with separate
state keys in the same state bucket. Resource names are prefixed
`<project>-<env>` so both can live in one account. Prod is a placeholder until
phase 5.

## Security posture

- One IAM role per Lambda or Firehose stream, scoped to the exact stream,
  table, or bucket prefix it uses. No wildcard resources.
- Kinesis encrypted at rest with the AWS-managed KMS key, the lake bucket
  with SSE-S3; the lake bucket denies non-TLS requests and blocks public access.
- Terraform state bucket: versioned, encrypted, public access blocked.
- Deploys from GitHub use OIDC federation to assume a role; no access keys are
  stored in GitHub (phase 5).
- CloudWatch log groups have explicit retention so logs do not accumulate
  forever.

## Observability (phase 5)

Structured JSON logs from every Lambda (already in place for the producer),
CloudWatch metrics and alarms on Lambda errors, iterator age on the consumer,
and API 5xx rate, with SNS email notification. X-Ray tracing across the
producer and API.
