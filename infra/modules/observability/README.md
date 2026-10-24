# observability module

Alarms, their notification topic and the dashboard for one environment. The
service modules own their own tracing; this module only reads their names.

Creates:

- **SNS topic** `<name_prefix>-alarms`. Its policy lets only this account's
  `<name_prefix>-*` CloudWatch alarms publish, over TLS only. No SSE:
  CloudWatch cannot publish to a topic under the AWS-managed `aws/sns` key, a
  customer-managed key costs $1 a month, and alarm messages hold nothing
  secret. With `alarm_email` set, an email subscription that stays pending
  until the link in AWS's confirmation email is clicked.
- **Alarms** (behind `create_alarms`). Each one notifies on ALARM and OK and
  treats missing data as fine:

  | Alarm | Metric | Fires when |
  | --- | --- | --- |
  | `-producer-errors` | Lambda `Errors` | errors in 3 of the last 5 minutes (`producer_error_minutes`) |
  | `-processor-iterator-age` | Lambda `IteratorAge` max | above 5 minutes for 5 minutes (`iterator_age_alarm_ms`) |
  | `-processor-dlq` | SQS `ApproximateNumberOfMessagesVisible` | any message in the DLQ; stays in ALARM until it is drained |
  | `-api-5xx` | API Gateway `5XXError` sum | 5 or more in 5 minutes (`api_5xx_threshold`) |
  | `-api-latency` | API Gateway `Latency` p95 | above 2 s for three 5-minute periods (`api_latency_p95_ms`) |

- **Dashboard** named `<name_prefix>`: alarm status, producer invocations and
  errors, Kinesis and Firehose records, processor iterator age, duration and
  errors, end-to-end lag (`MarketPipeline/TickLagMs`, emitted by the processor
  in Embedded Metric Format), DLQ depth and DynamoDB capacity and throttles,
  API requests, 4xx/5xx and latency, and the API Lambda's duration.

X-Ray tracing is configured in the `ingest`, `processing` and `api` modules:
Active tracing on each Lambda, an inline `xray:PutTraceSegments` /
`xray:PutTelemetryRecords` statement in each role (those actions take no
resource ARNs), and `xray_tracing_enabled` on the API stage. Traces do not
cross Kinesis, so API Gateway and the API Lambda share a trace while the
producer and processor each get their own.

## LocalStack

LocalStack 4.14 creates the topic, the subscription (no email is sent) and the
dashboard. It accepts `PutMetricAlarm` but answers the provider's
`DescribeAlarms` with a 500, so `apply` would hang: envs/local sets
`create_alarms = false`. The X-Ray API is not in the free edition; the tracing
settings are accepted and record nothing.
