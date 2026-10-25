# Cost model

Estimates for the dev environment at one producer run per minute and three
symbols, us-east-1, on-demand pricing. The phase 5 monitoring is billed at
list price below, but CloudWatch's and X-Ray's always-free allowances cover it
at this size. Re-check against the AWS pricing pages
before quoting numbers on a resume.

**The monthly figures are estimates, not measurements.** Dev has run on AWS
once, for 56 minutes ([ADR 0011](adr/0011-one-capped-aws-run.md); see
[The capped AWS run](#the-capped-aws-run) below), which is too short to
measure a month. The Measured column says what a run showed about a cost
driver and where: *AWS* for that run, *LocalStack* for the local ones.
LocalStack bills nothing, so it can only check usage, never price.

| Component                         | Driver                              | Estimate / month | Measured |
| --------------------------------- | ----------------------------------- | ---------------- | -------- |
| Kinesis Data Streams, 1 shard     | $0.015/shard-hour, always on        | ~$11             | — |
| Kinesis PUT payload units         | ~130k records/month                 | < $0.01          | — |
| Lambda (producer)                 | 43k invocations, ~200 ms, 256 MB    | free tier        | — |
| Kinesis Data Firehose (phase 2)   | $0.029/GB, each record rounded up to 5 KB: ~0.65 GB billed | ~$0.02 | — |
| Lambda (processor, phase 3)       | 43k invocations, ~400 ms, 512 MB (~9k GB-s) | free tier | 740 to 950 ms per invocation at 512 MB, p50 per load run; 163 MB used at most (*AWS*, arm64). ~330 ms (*LocalStack*, x86_64). At ~0.8 s, still ~17k GB-s: free tier |
| DynamoDB (phase 3)                | on demand: ~260k writes, ~800k strongly consistent reads; ~30k items | ~$0.30 | — |
| S3 raw/ (phase 2)                 | ~8.6k PUTs, ~10 MB held (30-day expiry) | < $0.05      | — |
| S3 processed/ (phase 3)           | ~130k PUTs (one per symbol-hour per batch), ~5 MB/month added | ~$0.65 | — |
| SQS dead-letter queue (phase 3)   | empty unless the processor fails    | free tier        | — |
| Glue Data Catalog (phase 3)       | 1 table, projected partitions (none stored) | free tier | — |
| Athena (phase 3)                  | $5/TB scanned, 10 MB minimum per query, 1 GiB cutoff | < $0.10 | — |
| API Gateway REST API (phase 4)    | $3.50 per million requests; the 10k/day quota caps it at ~300k/month | < $0.10 (cap ~$1.05) | — |
| Lambda (API, phase 4)             | 512 MB, a few ms per request (~50 ms for the largest `/bars`) | free tier | — |
| DynamoDB reads (API, phase 4)     | one `GetItem` or two `Query` calls per request, eventually consistent | < $0.05 | — |
| CloudWatch logs                   | a few MB, 14-day retention          | < $0.50          | — |
| CloudWatch alarms (phase 5)       | 5 standard alarms at $0.10 (producer errors, iterator age, DLQ depth, API 5xx, API p95 latency) | $0.50 (free tier: 10 alarms) | — |
| CloudWatch dashboard (phase 5)    | 1 dashboard at $3                   | $3.00 (free tier: 3 dashboards) | — |
| Custom metric `TickLagMs` (phase 5) | 1 metric via EMF log lines, no API calls | $0.30 (free tier: 10 metrics) | — |
| X-Ray (phase 5)                   | ~86k producer and processor traces a month plus API requests; Lambda traces nearly every invocation at this rate. $5 per million after 100k free | $0 (up to ~$1.45 at the API's full quota) | — |
| SNS alarm topic (phase 5)         | email; first 1,000 notifications free | $0             | — |
| GitHub OIDC roles, boundaries (phase 5) | IAM is free                   | $0               | — |
| **Total**                         | list price                          | **~$17**         | — (one 56-minute run, below) |
| **Total with the always-free tier** | alarms, dashboard, metric and traces within their free allowances | **~$13** | — (one 56-minute run, below) |

## The capped AWS run

On 2026-10-25 dev was applied to AWS, load-tested with 98,550 ticks in 8
runs, chaos-tested and destroyed, 56 minutes from the first resource to the
last ([ADR 0011](adr/0011-one-capped-aws-run.md)). A $5 AWS Budget guarded it.

| | Cost |
| --- | --- |
| Estimate at list price | about $0.10: one shard-hour ($0.015), ~0.5 GB of Firehose ingestion after rounding ($0.015), ~5.5k S3 PUTs for `processed/` ($0.03), DynamoDB writes, logs and the rest at a cent or less each; Lambda and X-Ray within their free allowances |
| Measured | pending: AWS reports charges about a day late. The account's credits cover it either way |

The ADR 0009 estimate of about $0.75 assumed a 20-minute load test of about
228k ticks; this run sent less than half that.

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
  configurable threshold (default $15). Set it on day one. At list price the
  phase 5 dev stack (~$17) is above it, so the 100% forecast alert is the
  sign that the free allowances no longer cover the monitoring.
- Phase 5 monitoring is nearly all fixed-price and inside the always-free
  allowances (10 alarms, 3 dashboards, 10 custom metrics, 100k X-Ray traces
  a month). Outside them, the dashboard is the largest item at $3 a month;
  delete it, or keep it under three dashboards per account, rather than
  paying for it. Lambda traces the first request each second and 5% after
  that, so traces grow with API traffic only past one request a second.
- Deploys from GitHub cost nothing until a role variable is set: every job
  in `deploy.yml` is skipped without one. Setting `AWS_DEPLOY_ROLE_ARN` makes
  every push to main apply dev (and so start the Kinesis shard). On a
  private repository, plans and deploys use GitHub Actions minutes (2,000
  free a month); each job takes a few minutes, mostly building the Lambda
  packages.
- A prod stack (`infra/envs/prod`) would cost about the same as dev again,
  roughly $13 to $17 a month. It keeps 24-hour Kinesis retention: anything
  longer is extended retention at $0.02 per shard-hour (about +$14.60 a
  month), more than the shard itself.
- The chaos tests (`make test-chaos`) are free on LocalStack. On AWS
  (`ALLOW_CHAOS_ON_AWS=1`) they cost cents, and they fire the DLQ and API
  5xx alarms on purpose.
- AWS changed its Free Tier in mid-2025: new accounts receive a credit
  allowance that expires after six months instead of 12 months of free usage.
  This project fits comfortably inside that allowance.
