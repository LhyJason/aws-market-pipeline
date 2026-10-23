# processing module (phase 3)

Planned resources:

- Processor Lambda with a Kinesis event-source mapping (batch size 100,
  `bisect_batch_on_function_error`, on-failure destination to SQS DLQ).
- IAM role scoped to the stream, the DynamoDB table, and the lake bucket's
  `processed/` prefix.
- CloudWatch alarm on `IteratorAgeMilliseconds`.
