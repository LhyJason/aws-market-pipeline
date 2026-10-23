# observability module (phase 5)

Planned resources:

- SNS topic with email subscription for alarms.
- CloudWatch alarms: producer errors, processor iterator age, API 5xx rate.
- CloudWatch dashboard: records per minute, end-to-end latency, Lambda
  duration p95, cost (from Budgets).
- X-Ray tracing enabled on all Lambdas plus the `AWSXRayDaemonWriteAccess`
  policy attachment.
- GitHub OIDC provider and deploy role (may move to bootstrap).
