# Cost model

Estimates for the dev environment at one producer run per minute and three
symbols, us-east-1, on-demand pricing. Re-check against the AWS pricing pages
before quoting numbers on a resume; fill the "measured" column from Cost
Explorer at phase 6.

| Component                         | Driver                              | Estimate / month | Measured |
| --------------------------------- | ----------------------------------- | ---------------- | -------- |
| Kinesis Data Streams, 1 shard     | $0.015/shard-hour, always on        | ~$11             |          |
| Kinesis PUT payload units         | ~130k records/month                 | < $0.01          |          |
| Lambda (producer)                 | 43k invocations, ~200 ms, 256 MB    | free tier        |          |
| Kinesis Data Firehose (phase 2)   | $0.029/GB, each record rounded up to 5 KB: ~0.65 GB billed | ~$0.02 |   |
| Lambda (processor, phase 3)       | similar                             | free tier        |          |
| DynamoDB (phase 3)                | on-demand, small                    | free tier        |          |
| S3 raw/ (phase 2)                 | ~8.6k PUTs, ~10 MB held (30-day expiry) | < $0.05      |          |
| S3 processed/ (phase 3)           | a few hundred MB                    | < $0.10          |          |
| Athena (phase 3)                  | $5/TB scanned, tiny tables          | < $0.10          |          |
| API Gateway HTTP API (phase 4)    | $1 per million requests             | < $0.10          |          |
| CloudWatch logs                   | a few MB, 14-day retention          | < $0.50          |          |
| **Total**                         |                                     | **~$12**         |          |

## Keeping it cheap

- To pay nothing, run `infra/envs/local` on LocalStack instead of AWS. It
  applies the same modules in Docker with no AWS account (see
  `infra/envs/local/README.md`).
- The Kinesis shard is the only always-on charge. Set `ingest_enabled = false`
  in `terraform.tfvars` to pause the schedule, or `terraform destroy` the dev
  environment between work sessions. Bootstrap resources (state bucket,
  budget) cost effectively nothing and stay.
- Firehose has no hourly charge; it bills only for data ingested, and the
  5 KB-per-record rounding dominates at this volume (records are ~70 bytes).
  It keeps reading while the producer is paused, at no cost. S3 PUTs scale
  with `buffering_interval_s`, not with traffic, so raise it to 900 if PUTs
  ever matter. `raw/` expires after 30 days, so the lake does not grow.
- The lake bucket refuses `terraform destroy` while it holds objects. Set
  `lake_force_destroy = true` in `terraform.tfvars` if the raw data is
  disposable, or empty the bucket first.
- The bootstrap module creates an AWS Budget with an email alert at a
  configurable threshold (default $15). Set it on day one.
- AWS changed its Free Tier in mid-2025: new accounts receive a credit
  allowance that expires after six months instead of 12 months of free usage.
  This project fits comfortably inside that allowance.
