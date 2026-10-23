# api module (phase 4)

Planned resources:

- API Gateway HTTP API with routes `/prices/{symbol}`, `/bars/{symbol}`,
  `/health`.
- Lambda running FastAPI via Mangum, packaged with vendored dependencies.
- API key or Cognito JWT authorizer (decide in an ADR).
- Access logs to CloudWatch, throttling defaults.
