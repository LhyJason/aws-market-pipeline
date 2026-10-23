# storage module

Kinesis Data Stream -> Kinesis Data Firehose -> S3 lake bucket (`raw/`).

| Resource                         | Notes                                                                 |
| -------------------------------- | --------------------------------------------------------------------- |
| `aws_s3_bucket`                  | `<prefix>-lake-<account id>` (names are global), SSE-S3, versioning off |
| `aws_s3_bucket_public_access_block` + bucket policy | all public access blocked, non-TLS requests denied |
| `aws_s3_bucket_lifecycle_configuration` | expires `raw/` and `errors/` after `raw_expiration_days` (30), aborts stale multipart uploads |
| `aws_kinesis_firehose_delivery_stream` | reads `source_stream_arn`, writes uncompressed JSON lines        |
| `aws_iam_role` + inline policy   | read on the one stream, write on `raw/*` and `errors/*` only, logs to its own stream |
| `aws_cloudwatch_log_group`       | delivery errors, explicit retention                                   |

## Layout

```
s3://<prefix>-lake-<account id>/
├── raw/dt=YYYY-MM-DD/hour=HH/<firehose object>     # one tick per line
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

## Phase 3

Still to add here: `processed/` Parquet prefix (`symbol=/dt=/hour=`), the
DynamoDB table `<prefix>-market` (on-demand, PK `symbol`, SK `kind#ts`, TTL on
bars older than 7 days), and the Glue database and tables.
