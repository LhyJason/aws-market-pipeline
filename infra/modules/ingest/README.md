# ingest module

EventBridge schedule -> producer Lambda -> Kinesis Data Stream.

| Resource                      | Notes                                                    |
| ----------------------------- | -------------------------------------------------------- |
| `aws_kinesis_stream`          | provisioned, `shard_count` shards, KMS encrypted         |
| `aws_lambda_function`         | python3.12 on arm64, X-Ray Active tracing, zipped from `producer_source_dir` |
| `aws_iam_role` + inline policy| logs on its own group, `PutRecord(s)` on this stream only, X-Ray writes (`*`) |
| `aws_cloudwatch_log_group`    | explicit retention                                       |
| `aws_cloudwatch_event_rule`   | `schedule_expression`, toggled by `enabled`              |

Packaging: the Lambda zip is built by the `archive` provider straight from
`services/producer/src`, which has no third-party dependencies (boto3 is in
the runtime). Keep `__pycache__` out of that directory or it ends up in the
zip; `tests/conftest.py` disables bytecode writing for this reason.

The producer-errors alarm lives in the `observability` module.
