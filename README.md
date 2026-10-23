# aws-market-pipeline

A serverless, real-time market data pipeline on AWS: scheduled ingestion into
Kinesis, stream processing into DynamoDB and an S3 data lake, SQL analytics with
Athena, and a FastAPI endpoint on API Gateway. Everything is provisioned with
Terraform and deployed through GitHub Actions using OIDC (no long-lived AWS keys).

> Status: **Phase 2 (ingest to lake)** done; phase 3 next. See [Phases](#phases).

## Architecture

```mermaid
flowchart LR
    EB[EventBridge<br/>rate(1 min)] --> P[Lambda<br/>producer]
    FEED[(Public price feed)] -.-> P
    P --> K[Kinesis Data Stream]
    K --> FH[Kinesis Data Firehose] --> RAW[(S3 data lake<br/>raw/ JSON, 30 days)]
    K --> C[Lambda<br/>processor]
    C --> DDB[(DynamoDB<br/>latest + bars)]
    C --> S3[(S3 data lake<br/>Parquet, partitioned)]
    S3 --> GLUE[Glue Catalog] --> ATH[Athena]
    APIGW[API Gateway] --> API[Lambda<br/>FastAPI]
    API --> DDB
    CW[CloudWatch<br/>logs, dashboards, alarms] -.-> P & C & API
```

| Layer         | Services                                   | Phase |
| ------------- | ------------------------------------------ | ----- |
| Ingest        | EventBridge, Lambda, Kinesis Data Streams  | 1-2   |
| Storage       | Kinesis Data Firehose, S3 (raw + Parquet), DynamoDB | 2-3 |
| Processing    | Lambda (Kinesis consumer)                  | 3     |
| Analytics     | Glue Data Catalog, Athena                  | 3     |
| API           | API Gateway (HTTP), Lambda + FastAPI       | 4     |
| Platform      | Terraform, GitHub Actions (OIDC), CloudWatch, X-Ray, Budgets | 1, 5 |

Details: [docs/architecture.md](docs/architecture.md), decisions: [docs/adr](docs/adr),
cost model: [docs/cost.md](docs/cost.md).

## Repository layout

```
.
├── infra/
│   ├── bootstrap/        # one-time: Terraform state bucket + cost budget
│   ├── modules/          # reusable Terraform modules (ingest, storage, processing, api, observability)
│   └── envs/{dev,prod}/  # per-environment root modules
├── services/
│   ├── producer/         # Lambda: price feed -> Kinesis
│   ├── processor/        # Lambda: Kinesis -> DynamoDB + S3   (phase 3)
│   └── api/              # FastAPI on Lambda                   (phase 4)
├── analytics/
│   ├── athena/           # table DDL and saved queries         (phase 3)
│   └── notebooks/
├── tests/
│   ├── unit/             # fast, no AWS (moto for AWS clients)
│   └── integration/      # against a deployed dev stack        (phase 4)
├── docs/                 # architecture, ADRs, cost
└── .github/workflows/    # ci.yml (lint, test, validate), deploy.yml (OIDC + terraform apply)
```

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

Firehose flushes to the lake every five minutes:

```bash
aws s3 ls "s3://$(terraform output -raw lake_bucket)/raw/" --recursive
```

Tear down with `terraform destroy` in `infra/envs/dev` when not in use. The
Kinesis shard is the only always-on cost (see [docs/cost.md](docs/cost.md)).

## Phases

- [x] **1. Foundation**: repo skeleton, Terraform remote state, CI (lint, tests, `terraform validate`), budget alarm, producer Lambda to Kinesis
- [x] **2. Ingest to lake**: raw records landing in S3 via Kinesis Data Firehose (`raw/dt=/hour=`, 30-day expiry)
- [ ] **3. Processing and analytics**: consumer Lambda, DynamoDB, Parquet partitions, Glue, Athena queries
- [ ] **4. API**: FastAPI on Lambda behind API Gateway, auth, integration tests
- [ ] **5. Hardening**: OIDC deploys from GitHub, dashboards, alarms, X-Ray, chaos test
- [ ] **6. Portfolio polish**: diagram, measured numbers (throughput, p95 latency, monthly cost), demo

## Results

Filled in at phase 6.

| Metric                         | Value |
| ------------------------------ | ----- |
| Events per day                 | TBD   |
| p95 feed-to-DynamoDB latency   | TBD   |
| Monthly cost (dev)             | TBD   |
| Unit test coverage             | TBD   |
