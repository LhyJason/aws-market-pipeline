# analytics module

Glue Data Catalog and Athena over the lake's `processed/` Parquet. Used by
`envs/dev` only: Glue and Athena are not in LocalStack's free edition, so
`envs/local` leaves the module out (see `analytics/athena/README.md` for the
DuckDB substitute).

| Resource                    | Notes                                                           |
| --------------------------- | --------------------------------------------------------------- |
| `aws_glue_catalog_database` | `<prefix>` with dashes as underscores, e.g. `market_pipeline_dev` |
| `aws_glue_catalog_table`    | `bars_1m`, Parquet, columns from `analytics/athena/schema/bars_1m.json`, partition projection on `symbol` (enum of `symbols`), `dt` (date from `projection_start_date`), `hour` (0-23) |
| `aws_athena_workgroup`      | engine v3, enforced settings, `bytes_scanned_cutoff` (1 GiB) per query, results SSE-S3 under `athena-results/` |
| `aws_athena_named_query`    | one per file in `analytics/athena/queries/`                     |

The results prefix lives in the lake bucket; the storage module expires it
after `athena_results_expiration_days` (7).

No IAM here: queries run as whoever opens the Athena console or CLI.
