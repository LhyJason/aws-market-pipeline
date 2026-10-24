# aws-market-pipeline

A serverless, real-time market data pipeline on AWS, built as infrastructure
as code and tested end to end without an AWS bill. An EventBridge schedule
fetches live crypto prices into Kinesis. A Lambda builds idempotent 1-minute
bars in DynamoDB and Parquet in an S3 lake, which Athena queries. A FastAPI
Lambda behind API Gateway serves the data with rolling statistics.

- **Terraform for everything:** 6 modules, and the same modules in `dev`,
  `prod` and `local` (LocalStack, $0).
- **At-least-once, exactly-counted processing:** conditional writes, partial
  batch responses, bisect-on-error and a dead-letter queue. Chaos tests break
  the stack on purpose and check that each failure stays contained.
- **Least-privilege CI/CD:** GitHub Actions with OIDC (no stored AWS keys),
  per-environment deploy roles under permissions boundaries, and prod only by
  a manual run.
- **Observable:** structured logs, an end-to-end lag metric, 5 SNS alarms, a
  dashboard and X-Ray tracing.
- **Measured honestly:** a load generator and a log-based report, with every
  number labelled by where it was measured.

**Try it in about 5 minutes for $0:** `make install && make demo` (see
[Run it locally](#run-it-locally-for-0)).

> Status: all six phases done ([Phases](#phases)). Nothing has been applied to
> a real AWS account: the results below come from LocalStack, configuration
> or price lists, and each one says which ([ADR 0009](docs/adr/0009-measure-on-localstack-only.md)).

## Results

| Metric | Value | Source |
| --- | --- | --- |
| Events per day at the default schedule | 4,320 ticks (3 symbols, one run a minute) | configuration |
| p95 tick-to-DynamoDB lag, default schedule | 1,023 ms (p50 422 ms, 37 batches) | LocalStack |
| p95 lag under load, 10 to 70 ticks/s | 1.4 to 1.7 s | LocalStack load test |
| Processor throughput, 1 shard, 100-record batches | about 70 ticks/s before lag grows | LocalStack load test |
| Ticks lost or counted twice under load | 0 of 25,800 (5 runs, 10 to 200 ticks/s) | LocalStack load test |
| Monthly cost, dev | about $13 (about $17 at list price) | estimate from AWS prices, not measured |
| Unit tests | 216 tests, 95% statement and branch coverage of `services/` | `pytest-cov`, in CI |
| Fresh clone to a finished demo | about 4 minutes, with Docker images cached | timed on a Windows laptop |

Lag is measured from when the producer read the price to when the bars
landed in DynamoDB; under load, from when the generator sent the tick. The
LocalStack figures describe one laptop running everything: they say nothing
about AWS latency, limits or cost. How they were measured, and what they can
and cannot show: [docs/load-test.md](docs/load-test.md). Cost model:
[docs/cost.md](docs/cost.md).

## Architecture

![Architecture: an EventBridge schedule invokes the producer Lambda, which puts ticks on a Kinesis stream; Firehose lands them raw in S3, and the processor Lambda writes bars to DynamoDB and Parquet to S3 for Glue and Athena; API Gateway and a FastAPI Lambda serve DynamoDB to clients with an API key; GitHub Actions deploys through OIDC](docs/architecture.svg)

The diagram is drawn from [docs/diagrams/architecture.d2](docs/diagrams/architecture.d2)
with [D2](https://d2lang.com) 0.9.0 (`make diagram`); CI renders it again and
fails if the committed SVG is out of date.

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
│   ├── modules/          # reusable Terraform modules (ingest, storage, processing, analytics, api, observability)
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
├── scripts/              # Lambda packaging, DuckDB queries, load test and report, demo
├── docs/                 # architecture (D2 diagram), ADRs, cost, load test, demo recording, resume notes
└── .github/             # ci.yml (lint, test, validate, actionlint), deploy.yml (OIDC plan/apply), composite actions
```

## Run it locally for $0

`infra/envs/local` applies the same modules to [LocalStack](https://docs.localstack.cloud)
4.14 in Docker. The producer, Kinesis, Firehose, the processor, DynamoDB, the
S3 lake and the API all run on your machine, with no AWS account. Glue and
Athena are not in LocalStack's free edition; their saved queries run on
DuckDB instead. Details: [infra/envs/local/README.md](infra/envs/local/README.md).

Prerequisites: Docker (running), Terraform >= 1.10, Python 3.12. On Windows,
clone to a short path such as `C:\src`: botocore's data files are deep
enough to hit the 260-character path limit under a long one.

```bash
git clone https://github.com/LhyJason/aws-market-pipeline.git && cd aws-market-pipeline
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
make install                                        # or: pip install -r requirements-dev.txt
make demo                                           # or: python scripts/demo.py
```

### Demo

`scripts/demo.py` runs the whole pipeline as a guided tour and times each
step:

1. Build the Lambda packages and start LocalStack.
2. Apply the stack.
3. Put a live tick through to the API.
4. Run a 20-second load test.
5. Break the stack twice (poison records, a bad deploy) and watch it recover.
6. Run the Athena query on DuckDB.
7. Tear down. `--keep` leaves the stack up.

![The demo: nine timed steps from building the Lambda packages to teardown, about two and a half minutes](docs/demo/demo.gif)

From a fresh clone on a Windows laptop it took about 4 minutes. The
virtualenv and `pip install` took 53 s and the demo 3 minutes, two of them in
`terraform apply`. That was with the Docker images already pulled; the first
run also downloads `localstack/localstack:4.14.0` (1.8 GB unpacked) and the
Lambda Python 3.12 image (0.75 GB). The GIF is
[docs/demo/demo.cast](docs/demo/demo.cast), a recording of a real run made by
`scripts/demo.py --record`, through [agg](https://github.com/asciinema/agg).

### Step by step

```bash
make local-up                                            # build, docker compose up, terraform apply
python scripts/query_lake.py --localstack                # the Athena queries, on DuckDB
make test-integration                                    # producer -> ... -> API, end to end
make test-chaos                                          # inject failures, check the stack copes
make load RATE=50 DURATION=60                            # load test: throughput, lag percentiles
make local-down                                          # stop LocalStack, drop its state
```

## Deploy to AWS

This costs money: about $13 to $17 a month for dev while it is up, mostly
the Kinesis shard ([docs/cost.md](docs/cost.md)). It has not been run against
a real account yet. Prerequisites: Python 3.12, Terraform >= 1.10, and an AWS
account with the CLI configured.

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

# 4. Deploy the dev environment (billed from here on)
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
- [x] **6. Portfolio polish**: an architecture diagram drawn as code (D2) and checked in CI, a load generator and log-based report, measured results labelled by source (LocalStack, configuration, estimate), a timed demo script with a recording, and resume notes ([docs/resume.md](docs/resume.md))

## License

[MIT](LICENSE)
