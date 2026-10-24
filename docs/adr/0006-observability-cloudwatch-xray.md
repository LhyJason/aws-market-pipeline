# ADR 0006: CloudWatch alarms, a dashboard and X-Ray without an SDK

**Status:** accepted · **Date:** 2026-10-23

## Context

Through phase 4 the pipeline had JSON logs and one alarm (processor iterator
age) that notified nobody. Phase 5 needs failures to reach a person, a page
that shows the pipeline's health, and traces of slow requests. It should add
little to a stack that costs about $13 a month, and as much of it as possible
should apply to LocalStack, where the project runs for $0.

Each piece was tried against the pinned `localstack/localstack:4.14.0` image,
through the AWS CLI and through Terraform with provider 6.57.1:

| Piece | Free LocalStack 4.14 |
| --- | --- |
| SNS topic, email subscription | works (no email is sent) |
| CloudWatch dashboard | works through the CLI and Terraform |
| CloudWatch metric alarm | `PutMetricAlarm` works, but the provider's `DescribeAlarms` (rpc-v2-cbor) gets a 500, so `apply` hangs. The CLI can describe it |
| X-Ray API | not in the free edition |
| Lambda Active tracing, REST stage tracing | accepted, nothing recorded |

## Decision

- **One `observability` module** per environment owns the alarm topic, every
  alarm and the dashboard. The service modules own their tracing settings
  and expose names. The iterator-age alarm moved there from `processing`.
- **SNS with email.** The topic policy lets only this account's
  `<prefix>-*` alarms publish, over TLS. No SSE: CloudWatch can't publish
  under the AWS-managed `aws/sns` key, and a customer-managed key ($1 a month)
  would protect nothing secret.
- **Five alarms:** producer errors, processor iterator age, DLQ depth, API
  5xx and API p95 latency. Thresholds are tuned so a single cold start or one
  failed feed call doesn't page. The producer now raises when ticks still
  fail to reach Kinesis after its retries. Before, it returned
  `{"ok": false}`, which Lambda doesn't count as an error, so the alarm could
  not see it.
- **One custom metric, `TickLagMs`, through Embedded Metric Format.** It is
  the end-to-end lag from the producer's observation to the bar being
  written. EMF is a log line, so it needs no API call and no IAM permission.
- **One dashboard** per environment. It has no cost widget: Budgets data
  isn't a CloudWatch metric, and the budget email already covers cost.
- **X-Ray Active tracing** on every Lambda and the API stage, **without the
  X-Ray SDK.** The SDK would add DynamoDB, S3 and Kinesis subsegments, but it
  is in maintenance mode, and OpenTelemetry (ADOT) is the upgrade path. The
  ADOT layer can't be tested locally, because free LocalStack doesn't mount
  layers. The X-Ray write actions take no resource ARN, so each role gets an
  inline statement on `"*"` rather than the AWS-managed
  `AWSXRayDaemonWriteAccess` policy.
- **On LocalStack**, `create_alarms = false` skips the alarms. The topic,
  dashboard and tracing settings apply as they do on AWS.

## Consequences

- **Cost.** At list price this adds about $3.80 a month, $3 of it the
  dashboard, plus up to about $1.45 of traces if the API is used to its
  quota. CloudWatch's and X-Ray's always-free allowances cover all of it at
  this size.
- **Pages, and when they come.** A broken feed, a stuck or failing
  processor, a failed batch, DynamoDB errors behind the API and a broken API
  deploy each fire an alarm. The DLQ alarm stays in ALARM until the queue is
  drained. SQS stops reporting an idle queue, so the first DLQ message can
  take up to about 15 minutes to page.
- **What LocalStack proves.** It proves the configuration and the failure
  handling (the chaos tests), not the alarms. Whether each alarm fires, and
  whether the email arrives, only shows on AWS.
- **Traces.** Producer and processor traces are separate, because X-Ray
  doesn't propagate through Kinesis. `TickLagMs` covers that gap for latency.
- **The alarm hang.** If a later LocalStack image fixes `DescribeAlarms` over
  CBOR, setting `create_alarms = true` in envs/local is the only change
  needed.
