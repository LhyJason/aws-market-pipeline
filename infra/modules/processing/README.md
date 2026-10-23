# processing module

Kinesis Data Stream -> event-source mapping -> processor Lambda -> DynamoDB
and the lake's `processed/` prefix. Code: `services/processor`.

| Resource                             | Notes                                                        |
| ------------------------------------ | ------------------------------------------------------------ |
| `aws_lambda_function`                | python3.12, `architecture` (arm64 on AWS), 512 MB, 60 s, zipped from `package_dir` |
| `aws_lambda_event_source_mapping`    | batch size 100, `TRIM_HORIZON`, `ReportBatchItemFailures`, `bisect_batch_on_function_error`, 5 retries, 1-hour max record age, parallelization 1 |
| `aws_sqs_queue`                      | on-failure destination (DLQ), SSE, 14-day retention          |
| `aws_iam_role` + inline policy       | read on the one stream, Get/Put/Update/Query on the one table, `PutObject` on `processed/*`, `SendMessage` on its DLQ, logs to its own group |
| `aws_cloudwatch_log_group`           | explicit retention                                           |
| `aws_cloudwatch_metric_alarm`        | `IteratorAge` max > 5 min for 5 minutes; `create_alarms` (off in local) |

## Packaging

The processor bundles DuckDB, a native wheel, so the package is built before
Terraform runs and must match `architecture`:

```bash
python scripts/build_lambda.py processor --arch arm64    # envs/dev
python scripts/build_lambda.py processor --arch x86_64   # envs/local
```

The result lands in `.build/processor-<arch>/` and Terraform zips it (about
22 MB). Rebuild after changing `services/processor`; `source_code_hash` then
picks up the change. See [ADR 0004](../../../docs/adr/0004-duckdb-parquet-rebuilt-from-dynamodb.md).

## Failure handling

The handler returns the earliest sequence number of any symbol it could not
write; Lambda retries from there. A batch that still fails after
`maximum_retry_attempts`, or whose records are older than
`maximum_record_age_s`, is skipped and its metadata (shard, sequence range)
goes to the DLQ. The records themselves stay in the stream for 24 hours and in
`raw/` for 30 days for replay.
