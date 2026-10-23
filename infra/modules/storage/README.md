# storage module (phase 2-3)

Planned resources:

- S3 bucket `<prefix>-lake` with prefixes `raw/` (Firehose JSON) and
  `processed/` (Parquet, partitioned by `symbol=/dt=/hour=`), versioning off,
  SSE-S3, lifecycle rule to expire `raw/` after 30 days.
- DynamoDB table `<prefix>-market` (on-demand), PK `symbol`, SK `kind#ts`
  holding latest price and 1-minute bars; TTL on bars older than 7 days.
- Glue database and table pointing at `processed/`.
