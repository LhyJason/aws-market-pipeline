# aws-market-pipeline

A serverless, real-time market data pipeline on AWS: scheduled ingestion into
Kinesis, stream processing into DynamoDB and an S3 data lake, SQL analytics with
Athena, and a FastAPI endpoint on API Gateway. Everything is provisioned with
Terraform and deployed through GitHub Actions using OIDC (no long-lived AWS keys).

> Status: **Phase 5 (hardening)** done; phase 6 next. See [Phases](#phases).

## Architecture

```mermaid
flowchart LR
    EB[EventBridge<br/>rate(1 min)] --> P[Lambda<br/>producer]
    FEED[(Public price feed)] -.-> P
    P --> K[Kinesis Data Stream]
    K --> FH[Kinesis Data Firehose] --> RAW[(S3 data lake<br/>raw/ JSON, 30 days)]
    K --> C[Lambda<br/>processor]
    C -. failed batches .-> DLQ[SQS DLQ]
    C --> DDB[(DynamoDB<br/>latest + 1-min bars)]
    C --> S3[(S3 data lake<br/>Parquet, symbol/dt/hour)]
    S3 --> GLUE[Glue Catalog] --> ATH[Athena]
    CLIENT([client + API key]) --> APIGW[API Gateway<br/>REST API] --> API[Lambda<br/>FastAPI]
    API --> DDB
    CW[CloudWatch + X-Ray<br/>logs, dashboard, alarms, traces] -.-> P & C & API
    CW -. alarms .-> SNS[SNS<br/>email]
```

| Layer         | Services                                   | Phase |
| ------------- | ------------------------------------------ | ----- |
| Ingest        | EventBridge, Lambda, Kinesis Data Streams  | 1-2   |
| Storage       | Kinesis Data Firehose, S3 (raw + Parquet), DynamoDB | 2-3 |
| Processing    | Lambda (Kinesis consumer)                  | 3     |
| Analytics     | Glue Data Catalog, Athena                  | 3     |
| API           | API Gateway (REST, API key), Lambda + FastAPI | 4  |
| Platform      | Terraform, GitHub Actions (OIDC), CloudWatch, X-Ray, Budgets | 1, 5 |

Details: [docs/architecture.md](docs/architecture.md), decisions: [docs/adr](docs/adr),
cost model: [docs/cost.md](docs/cost.md).

## Repository layout

```
.
├── infra/
│   ├── bootstrap/        # one-time: state bucket, cost budget, GitHub OIDC roles + permissions boundaries
│   ├── modules/          # reusable Terraform modules (ingest, storage, processing, api, observability)
│   └── envs/{local,dev,prod}/  # per-environment root modules (local = LocalStack)
├── services/
│   ├── producer/         # Lambda: price feed -> Kinesis
│   ├── processor/        # Lambda: Kinesis -> DynamoDB + S3 Parquet
│   └── api/              # Lambda: FastAPI over DynamoDB, rolling stats
├── analytics/
│   ├── athena/           # table schema, DDL and saved queries
│   └── notebooks/
├── tests/
│   ├── unit/             # fast, no AWS (moto for AWS clients)
│   └── integration/      # end to end and chaos tests against LocalStack (or dev)
├── scripts/              # build_lambda.py (Lambda packages), query_lake.py (Athena SQL on DuckDB)
├── docs/                 # architecture, ADRs, cost
└── .github/             # ci.yml (lint, test, validate, actionlint), deploy.yml (OIDC plan/apply), composite actions
```

## Run it locally for free

`infra/envs/local` applies the same modules to [LocalStack](https://docs.localstack.cloud)
in Docker: producer, Kinesis, Firehose, the processor, DynamoDB, the S3 lake
and the API all run on your machine, no AWS account, $0. See
[infra/envs/local/README.md](infra/envs/local/README.md).

```bash
python scripts/build_lambda.py processor --arch x86_64   # processor package with DuckDB
python scripts/build_lambda.py api --arch x86_64         # API package with pydantic-core
cd infra/envs/local
docker compose up -d
terraform init && terraform apply
cd ../../..
python scripts/query_lake.py --localstack                # the Athena queries, on DuckDB
make test-integration                                    # producer -> ... -> API, end to end
make test-chaos                                          # inject failures, check the stack copes
```

`make local-up` does the builds, `docker compose up` and `apply` in one go.

## Quickstart

Prerequisites: Python 3.12, Terraform >= 1.10, an AWS account with the CLI configured.

```bash
# 1. Python tooling
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
make install

# 2. Lint and test (no AWS needed)
make lint
make test

# 3. One-time: remote state bucket + monthly budget alarm
cd infra/bootstrap
cp terraform.tfvars.example terraform.tfvars   # set a globally unique bucket name
terraform init && terraform apply
cd ../..

# 4. Deploy the dev environment
make build                                      # processor and API packages for arm64
cd infra/envs/dev
cp backend.hcl.example backend.hcl              # bucket name from step 3
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config=backend.hcl
terraform apply
```

After apply, the producer runs once a minute. Check the stream:

```bash
aws kinesis describe-stream-summary --stream-name market-pipeline-dev-ticks
aws logs tail /aws/lambda/market-pipeline-dev-producer --follow
```

Firehose flushes to the lake every five minutes, and the processor writes
bars and Parquet within seconds of each tick:

```bash
aws s3 ls "s3://$(terraform output -raw lake_bucket)/raw/" --recursive
aws s3 ls "s3://$(terraform output -raw lake_bucket)/processed/" --recursive
aws dynamodb query --table-name "$(terraform output -raw market_table)" \
  --key-condition-expression "symbol = :s" \
  --expression-attribute-values '{":s":{"S":"BTCUSDT"}}' --no-scan-index-forward --max-items 3
```

The saved Athena queries are in the workgroup named by
`terraform output -raw athena_workgroup`. The API is at
`terraform output -raw api_url`; see [API](#api).

To deploy from GitHub instead, set the role ARNs from step 3's outputs as
repository variables (see [infra/bootstrap/README.md](infra/bootstrap/README.md)).
Pull requests then get a read-only plan, every push to main applies dev and
smoke-tests the API, and prod is applied only by a manual workflow run
([ADR 0008](docs/adr/0008-manual-prod-deploys.md)). Every job is skipped
until its variable is set. Setting `AWS_DEPLOY_ROLE_ARN` starts real, billed
deploys.

Alarms email `alarm_email` (confirm the subscription from AWS's email), and
the `market-pipeline-dev` CloudWatch dashboard shows the whole pipeline. See
[docs/architecture.md](docs/architecture.md#observability).

Tear down with `terraform destroy` in `infra/envs/dev` when not in use. The
Kinesis shard is the only always-on cost (see [docs/cost.md](docs/cost.md)).

## API

A REST API on API Gateway in front of a FastAPI Lambda that reads DynamoDB
only. `/health` is open; the data routes need the `x-api-key` header. One key
in a usage plan: 10 requests/s (burst 20), 10,000 a day. Why a REST API with
an API key: [ADR 0005](docs/adr/0005-rest-api-with-api-key.md).

| Route                  | Returns                                                     |
| ---------------------- | ----------------------------------------------------------- |
| `GET /health`          | `{"status": "ok", "version": ...}`                          |
| `GET /prices/{symbol}` | latest tick: `price`, `ts`, `time`, `source`, `age_s`, `stale` |
| `GET /bars/{symbol}`   | 1-minute bars with `return`, `zscore`, `flags` (`spike`, `gap`), and rolling `stats` |

`/bars` takes `start` and `end` (ISO 8601, default the last hour, at most 7
days), `limit` (1-1000, default 60, the newest bars in the range) and
`window` (5-240 bars, default 60). Statistics are computed per request: a
bar's z-score compares its 1-minute return with the returns of the `window`
bars before it, and `|z| >= 3` flags a spike.

```bash
URL=$(terraform -chdir=infra/envs/local output -raw api_url)   # or infra/envs/dev
KEY=$(terraform -chdir=infra/envs/local output -raw api_key)

curl -s "$URL/health"
curl -s -H "x-api-key: $KEY" "$URL/prices/BTCUSDT"
curl -s -H "x-api-key: $KEY" "$URL/bars/BTCUSDT?limit=3&window=30"
curl -s -H "x-api-key: $KEY" "$URL/bars/ETHUSDT?start=2026-10-23T14:00:00Z&end=2026-10-23T15:00:00Z"
```

```json
{"symbol": "BTCUSDT", "price": 81180.01, "ts": 1792799698445,
 "time": "2026-10-23T23:54:58.445000Z", "source": "binance", "age_s": 2.696, "stale": false}
```

Errors: 403 without a valid key (from API Gateway), 422 for bad input, 404
for an unknown symbol, 503 if DynamoDB fails. More in
[services/api/README.md](services/api/README.md).

## Phases

- [x] **1. Foundation**: repo skeleton, Terraform remote state, CI (lint, tests, `terraform validate`), budget alarm, producer Lambda to Kinesis
- [x] **2. Ingest to lake**: raw records landing in S3 via Kinesis Data Firehose (`raw/dt=/hour=`, 30-day expiry)
- [x] **3. Processing and analytics**: processor Lambda (1-minute bars, idempotent, partial batch responses, SQS DLQ), DynamoDB, Parquet via DuckDB under `processed/`, Glue table with partition projection, Athena saved queries checked with DuckDB in CI
- [x] **4. API**: FastAPI on Lambda (Mangum) behind an API Gateway REST API with an API key, usage-plan throttling and quota, rolling stats and spike/gap flags computed at read time, end-to-end integration tests on LocalStack
- [x] **5. Hardening**: SNS-notified alarms (producer errors, iterator age, DLQ depth, API 5xx and p95 latency), a CloudWatch dashboard with end-to-end lag (EMF), X-Ray tracing on every Lambda and the API stage, GitHub OIDC plan/deploy roles with named actions and permissions boundaries, a prod environment deployed by manual run, a deploy smoke test, and chaos tests on LocalStack (poison records, a failing symbol to the DLQ, DynamoDB errors as 503, a bad deploy as 502)
- [ ] **6. Portfolio polish**: diagram, measured numbers (throughput, p95 latency, monthly cost), demo

## Results

Filled in at phase 6.

| Metric                         | Value |
| ------------------------------ | ----- |
| Events per day                 | TBD   |
| p95 feed-to-DynamoDB latency   | TBD   |
| Monthly cost (dev)             | TBD   |
| Unit test coverage             | TBD   |
