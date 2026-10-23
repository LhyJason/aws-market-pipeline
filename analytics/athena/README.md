# Athena

SQL over the Parquet lake, `s3://<lake>/processed/bars_1m/`.

| Path                       | What                                                                 |
| -------------------------- | -------------------------------------------------------------------- |
| `schema/bars_1m.json`      | columns and partition keys; Terraform builds the Glue table from it  |
| `ddl/bars_1m.sql`          | the same table as `CREATE EXTERNAL TABLE`, for creating it by hand   |
| `queries/daily_vwap.sql`   | daily VWAP per symbol (weighted by `tick_count`; the feed has no volume) |
| `queries/largest_moves.sql`| 20 largest close-to-close moves between consecutive minutes          |
| `queries/minute_gaps.sql`  | data quality: runs of missing minutes per symbol                      |

All queries look back 7 days (`dt >= current_date - INTERVAL '7' DAY`) so
Athena prunes partitions and scans only what it needs.

## Table

`bars_1m` is partitioned by `symbol` (enum), `dt` (date) and `hour` (0-23)
with **partition projection**: Athena works out partition locations from table
properties, so a new hour is queryable as soon as the processor writes it, with
no crawler and no `MSCK REPAIR TABLE`. Adding a symbol means adding it to
`symbols` in Terraform.

Terraform (`infra/modules/analytics`, dev only) creates the Glue database and
table, an Athena engine v3 workgroup with a 1 GiB per-query scan limit and
results under `athena-results/` (expired after 7 days), and the three queries
as saved queries.

## Running the queries for $0

Glue and Athena are not in LocalStack's free edition. The queries are written
in the SQL that Trino (Athena engine v3) and DuckDB share, so they run
unchanged on DuckDB against the Parquet files:

```bash
python scripts/query_lake.py --localstack            # pull processed/ from LocalStack, run all three
python scripts/query_lake.py --dir <copy of processed/> minute_gaps
```

`tests/unit/test_athena_queries.py` runs in CI and checks:

- each query's results on Parquet the processor writes, in the real layout
- each query parses as Trino (sqlglot)
- the schema file, the Parquet files, the DDL and the Terraform saved queries
  all agree

What still needs a real Athena run: partition projection itself and engine-
specific behaviour such as type coercion.
