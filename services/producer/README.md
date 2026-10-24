# producer

Scheduled Lambda that fetches spot prices for a list of symbols and publishes
one Kinesis record per symbol.

```
EventBridge (rate) -> lambda_handler -> BinanceFeed.fetch -> publish -> Kinesis PutRecords
```

- `config.py`: `Settings.from_env()` reads `KINESIS_STREAM_NAME`, `SYMBOLS`,
  `FEED_BASE_URL`, `FEED_TIMEOUT_S`.
- `feed.py`: `Tick` record and the `BinanceFeed` implementation (stdlib only).
  Any object with `fetch(symbols) -> list[Tick]` satisfies the `Feed` protocol.
- `handler.py`: `publish()` batches records (max 500 per call) and retries
  partial failures; `run()` is the dependency-injected cycle and raises
  `PublishError` if ticks are still unsent after the retries, so the run
  counts as a Lambda error (the producer-errors alarm watches that);
  `lambda_handler` wires real dependencies once per container.
- `logging_utils.py`: JSON log lines; `extra={...}` fields become top-level
  keys, queryable in CloudWatch Logs Insights.

No third-party dependencies: boto3 ships with the Lambda runtime, so the
deployment package is just this directory zipped by Terraform.

Tests: `tests/unit/test_config.py`, `test_feed.py`, `test_handler.py`
(moto stands in for Kinesis).
