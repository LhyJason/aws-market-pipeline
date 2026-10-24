# storage module

Kinesis Data Stream -> Kinesis Data Firehose -> S3 lake bucket (`raw/`), plus
the market DynamoDB table the processor writes.

| Resource                         | Notes                                                                 |
| -------------------------------- | --------------------------------------------------------------------- |
| `aws_s3_bucket`                  | `<prefix>-lake-<account id>` (names are global), SSE-S3, versioning off |
| `aws_s3_bucket_public_access_block` + bucket policy | all public access blocked, non-TLS requests denied |
| `aws_s3_bucket_lifecycle_configuration` | expires `raw/` and `errors/` after `raw_expiration_days` (30), `athena-results/` after `athena_results_expiration_days` (7), aborts stale multipart uploads |
| `aws_kinesis_firehose_delivery_stream` | reads `source_stream_arn`, writes uncompressed JSON lines        |
| `aws_iam_role` + inline policy   | read on the one stream, write on `raw/*` and `errors/*` only, logs to its own stream; `permissions_boundary` on the role (dev/prod: the env's boundary from bootstrap) |
| `aws_cloudwatch_log_group`       | delivery errors, explicit retention                                   |
| `aws_dynamodb_table`             | `<prefix>-market`, on-demand, PK `symbol`, SK `sk`, TTL on `expires_at`; PITR and deletion protection off by default |

## Layout

```
s3://<prefix>-lake-<account id>/
├── raw/dt=YYYY-MM-DD/hour=HH/<firehose object>     # one tick per line
├── processed/bars_1m/symbol=S/dt=YYYY-MM-DD/hour=HH/bars.parquet   # written by the processor
├── athena-results/                                 # Athena query output (analytics module)
└── errors/<error type>/dt=YYYY-MM-DD/              # records Firehose could not deliver
```

`dt` and `hour` are the record's approximate arrival time at Firehose, in UTC,
not the tick's `ts`. They differ by at most the buffering interval, which is
fine for partition pruning; queries that need exact times filter on `ts`.

Firehose concatenates records verbatim, so the producer terminates each record
with `\n` to make the objects line-delimited JSON.

## Buffering

Firehose flushes when `buffering_size_mb` (5) or `buffering_interval_s` (300)
is reached, whichever is first. At three ticks a minute the interval always
wins: about 288 small objects a day. Raise the interval to 900 to cut S3 PUTs
further at the cost of freshness in `raw/`.

`processed/` has no lifecycle rule: it is the long-term history. The
processor rewrites each hour file whole (see `services/processor/README.md`).

## Market table

| `sk`                      | Item                                              |
| ------------------------- | ------------------------------------------------- |
| `LATEST`                  | newest price per symbol                           |
| `BAR#<13-digit epoch ms>` | one 1-minute bar; `expires_at` deletes it after 7 days |

At three symbols the table holds about 30k items and costs well under a
dollar a month on demand. Point-in-time recovery is off: everything in the
table can be rebuilt by replaying the stream or `raw/`.
