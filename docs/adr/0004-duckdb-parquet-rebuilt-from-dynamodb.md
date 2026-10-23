# ADR 0004: Parquet via DuckDB, hour files rebuilt from DynamoDB

**Status:** accepted · **Date:** 2026-10-23

## Context

The processor Lambda must write 1-minute bars to `processed/` as Parquet for
Athena. Two problems:

1. **Packaging.** The usual Parquet library, pyarrow, ships as a 51-54 MB wheel
   (cp312 manylinux), over Lambda's 50 MB direct-upload limit and well over
   100 MB unzipped. The project must also run on free LocalStack 4.14, which
   creates Lambda layers but does not mount them, and has no ECR for container
   images.
2. **Duplicates.** Kinesis delivers at least once. A failed batch is retried,
   possibly bisected or resumed from a checkpoint, so "one Parquet file per
   batch" adds duplicate or partial rows on every retry. A minute's ticks can
   also arrive in two batches.

Options for packaging (wheel sizes from PyPI, October 2026):

| Option                                  | Size          | Free LocalStack |
| --------------------------------------- | ------------- | --------------- |
| pyarrow in the zip                      | 51-54 MB      | only via S3 upload; heavy cold start |
| AWS SDK for pandas managed layer        | 0 in the zip  | no (layers not mounted) |
| Container image                         | up to 10 GB   | no (ECR is Pro) |
| Firehose record format conversion       | 0             | no (needs Glue); converts ticks, not bars |
| polars                                  | 50-55 MB      | same as pyarrow |
| fastparquet + numpy + pandas            | ~30 MB        | yes, pulls in pandas |
| **DuckDB**                              | **20-24 MB, no dependencies** | **yes** |
| Own minimal Parquet writer              | ~0            | yes; we would own a file format |

## Decision

- **DuckDB** writes Parquet: rows go into an in-memory table, `COPY ... TO`
  a file in `/tmp`, then boto3 `put_object`. No httpfs extension, so nothing is
  downloaded at runtime. `scripts/build_lambda.py` installs the Linux wheel for
  the function's architecture into `.build/processor-<arch>/`, which Terraform
  zips (about 22 MB zipped, 61 MB unzipped).
- **DynamoDB is the source of truth for bars.** Bar merges are idempotent
  (max/min, earliest/latest tick time, a set of tick timestamps for the count).
  For each symbol-hour a batch touches, the processor queries that hour's bars
  back and overwrites one file at a fixed key,
  `processed/bars_1m/symbol=/dt=/hour=/bars.parquet`.

## Consequences

- Retries, bisects and replays overwrite the same object; Athena never sees
  duplicates, and gets 24 files per symbol per day instead of thousands.
- Each batch costs one Query and one PUT per touched symbol-hour: about 130k
  PUTs a month at three symbols, roughly $0.65. A batching window cuts it.
- Hour files can only be rebuilt while their bars are in DynamoDB (7-day TTL),
  so the processor drops ticks from older hours. Kinesis keeps 24 hours, so
  this only matters for manual replays.
- DuckDB is a native wheel: the zip must be built for the architecture the
  function runs on. LocalStack runs functions on the host's architecture
  (x86_64 here), AWS dev on arm64, so `envs/local` and `envs/dev` build
  different packages.
- DuckDB is also what checks the Athena SQL for free in CI (phase 3), so the
  project has one Parquet tool instead of two.
- Pinned to 1.5.6; 2.0.0 was two days old when this was decided.
