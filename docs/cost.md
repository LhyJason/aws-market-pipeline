# Cost model

Estimates for the dev environment at one producer run per minute and three
symbols, us-east-1, on-demand pricing. Re-check against the AWS pricing pages
before quoting numbers on a resume; fill the "measured" column from Cost
Explorer at phase 6.

| Component                         | Driver                              | Estimate / month | Measured |
| --------------------------------- | ----------------------------------- | ---------------- | -------- |
| Kinesis Data Streams, 1 shard     | $0.015/shard-hour, always on        | ~$11             |          |
| Kinesis PUT payload units         | ~130k records/month                 | < $0.01          |          |
| Lambda (producer)                 | 43k invocations, ~200 ms, 256 MB    | free tier        |          |
| Kinesis Data Firehose (phase 2)   | $0.029/GB, each record rounded up to 5 KB: ~0.65 GB billed | ~$0.02 |   |
| Lambda (processor, phase 3)       | 43k invocations, ~400 ms, 512 MB (~9k GB-s) | free tier |          |
| DynamoDB (phase 3)                | on demand: ~260k writes, ~800k strongly consistent reads; ~30k items | ~$0.30 |  |
| S3 raw/ (phase 2)                 | ~8.6k PUTs, ~10 MB held (30-day expiry) | < $0.05      |          |
| S3 processed/ (phase 3)           | ~130k PUTs (one per symbol-hour per batch), ~5 MB/month added | ~$0.65 |  |
| SQS dead-letter queue (phase 3)   | empty unless the processor fails    | free tier        |          |
| Glue Data Catalog (phase 3)       | 1 table, projected partitions (none stored) | free tier |          |
| Athena (phase 3)                  | $5/TB scanned, 10 MB minimum per query, 1 GiB cutoff | < $0.10 |      |
| CloudWatch alarm (phase 3)        | 1 metric alarm on iterator age      | $0.10            |          |
| API Gateway REST API (phase 4)    | $3.50 per million requests; the 10k/day quota caps it at ~300k/month | < $0.10 (cap ~$1.05) | |
| Lambda (API, phase 4)             | 512 MB, a few ms per request (~50 ms for the largest `/bars`) | free tier |  |
| DynamoDB reads (API, phase 4)     | one `GetItem` or two `Query` calls per request, eventually consistent | < $0.05 | |
| CloudWatch logs                   | a few MB, 14-day retention          | < $0.50          |          |
| **Total**                         |                                     | **~$13**         |          |

## Keeping it cheap

- To pay nothing, run `infra/envs/local` on LocalStack instead of AWS. It
  applies the same modules in Docker with no AWS account (see
  `infra/envs/local/README.md`).
- The Kinesis shard is the only always-on charge. Set `ingest_enabled = false`
  in `terraform.tfvars` to pause the schedule, or `terraform destroy` the dev
  environment between work sessions. Bootstrap resources (state bucket,
  budget) cost effectively nothing and stay.
- Firehose has no hourly charge; it bills only for data ingested, and the
  5 KB-per-record rounding dominates at this volume (records are ~70 bytes).
  It keeps reading while the producer is paused, at no cost. S3 PUTs scale
  with `buffering_interval_s`, not with traffic, so raise it to 900 if PUTs
  ever matter. `raw/` expires after 30 days, so the lake does not grow.
- The lake bucket refuses `terraform destroy` while it holds objects. Set
  `lake_force_destroy = true` in `terraform.tfvars` if the raw data is
  disposable, or empty the bucket first.
- Processed Parquet costs PUTs, not storage: each batch rewrites the hour
  file for every symbol it touches. Set `processor_batching_window_s` (for
  example 60) to batch several minutes per invocation, cutting PUTs and
  DynamoDB reads at the cost of up to that many seconds of latency.
- Athena bills at least 10 MB per query; every saved query filters on the
  last 7 days so partition projection prunes the rest. The workgroup cancels
  any query that would scan more than 1 GiB, and query results expire after
  7 days. To query for free, run `scripts/query_lake.py` with DuckDB instead.
- The API costs only when called. Its usage plan allows 10,000 requests a
  day, which caps API Gateway at about $1 a month even if the key leaks;
  lower `quota_per_day` (`api_quota_per_day` in dev) to cap it further.
  Unknown paths and requests without a key are rejected by API Gateway, so
  they cost no Lambda time. A REST API costs $3.50 per million requests
  against an HTTP API's $1, the price of testing auth on LocalStack
  ([ADR 0005](adr/0005-rest-api-with-api-key.md)).
- The DynamoDB table has no point-in-time recovery: bars can be rebuilt by
  replaying `raw/`, and TTL keeps the table at about 30k items.
- The bootstrap module creates an AWS Budget with an email alert at a
  configurable threshold (default $15). Set it on day one.
- AWS changed its Free Tier in mid-2025: new accounts receive a credit
  allowance that expires after six months instead of 12 months of free usage.
  This project fits comfortably inside that allowance.
