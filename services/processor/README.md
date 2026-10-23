# processor (phase 3)

Kinesis consumer Lambda. Reads batches of tick records, builds 1-minute bars
per symbol, writes them to DynamoDB, and rewrites the matching Parquet hour
files under `processed/`.

| Module          | Role                                                                 |
| --------------- | -------------------------------------------------------------------- |
| `records.py`    | base64 Kinesis data -> newline-delimited JSON -> validated `Tick`s     |
| `bars.py`       | `Bar` (OHLC + `tick_count`) and its idempotent, commutative `merge`  |
| `store.py`      | DynamoDB: `LATEST` and `BAR#<start>` items, optimistic locking        |
| `lake.py`       | DuckDB writes the hour's bars to Parquet, uploaded to a fixed key     |
| `handler.py`    | groups the batch by symbol, returns a partial batch response         |

The feed carries prices only, so a bar's volume is `tick_count`, the number of
distinct ticks in the minute.

## Data written

DynamoDB, PK `symbol`, SK `sk`:

| `sk`                         | Attributes                                                        |
| ---------------------------- | ----------------------------------------------------------------- |
| `LATEST`                     | `price`, `ts`, `source`                                           |
| `BAR#<13-digit epoch ms>`    | `start`, `open`, `high`, `low`, `close`, `tick_count`, `tick_ts` (number set), `first_ts`, `last_ts`, `version`, `expires_at` (TTL, `BAR_TTL_DAYS` after the minute) |

S3, one file per symbol-hour:

```
processed/bars_1m/symbol=BTCUSDT/dt=2026-10-23/hour=14/bars.parquet
  bar_start TIMESTAMP (UTC), open/high/low/close DOUBLE, tick_count INTEGER,
  first_ts/last_ts BIGINT (epoch ms)
```

## Retries are safe

Kinesis delivers at least once, and a failed batch is retried, possibly
bisected. Every write is idempotent:

- **Bars** merge with max/min for high/low, earliest/latest tick time for
  open/close, and a set of tick timestamps for the count, so merging a tick
  twice changes nothing. Unchanged bars are not rewritten.
- **`LATEST`** is updated only if the incoming tick is newer.
- **Parquet** is rebuilt from DynamoDB for each touched hour and written to the
  same key, so a retry overwrites the file instead of adding rows.

Records that can never succeed (bad base64, bad JSON, invalid fields, unknown
schema version) are logged and dropped. Ticks from an hour whose bars may
already have expired are dropped too, so a replay cannot shrink an hour file.

If writing a symbol fails, the handler returns
`{"batchItemFailures": [{"itemIdentifier": <earliest sequence number for that symbol>}]}`
(the event-source mapping needs `FunctionResponseTypes = ["ReportBatchItemFailures"]`).
Lambda resumes the shard from there; symbols that already succeeded are
reprocessed harmlessly.

## Configuration

| Variable           | Default             |                                   |
| ------------------ | ------------------- | --------------------------------- |
| `TABLE_NAME`       | required            | the market table                  |
| `LAKE_BUCKET`      | required            | the lake bucket                   |
| `PROCESSED_PREFIX` | `processed/bars_1m` | key prefix for the hour files     |
| `BAR_TTL_DAYS`     | `7`                 | bar item TTL                      |
| `LOG_LEVEL`        | `INFO`              |                                   |

## Packaging

`requirements.txt` pins DuckDB, a native wheel (about 22 MB), bundled into the
zip; boto3 comes with the runtime. The wheel must match the architecture the
function runs on (arm64 on AWS, x86_64 on LocalStack), so the package is built
before Terraform zips it:

```bash
python scripts/build_lambda.py processor --arch arm64    # .build/processor-arm64/
```

See `infra/modules/processing/README.md` and
[ADR 0004](../../docs/adr/0004-duckdb-parquet-rebuilt-from-dynamodb.md).
