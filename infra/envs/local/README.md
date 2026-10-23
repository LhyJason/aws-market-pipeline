# local environment (LocalStack, $0)

The `ingest`, `storage`, `processing` and `api` modules from dev, applied to
[LocalStack](https://docs.localstack.cloud) on `localhost:4566` instead of AWS.
Nothing is billed and no AWS account is needed. The only outside traffic is
the producer's call to the public price feed.

Requires Docker Desktop (WSL2 backend on Windows), Terraform and Python 3.12.

## Run

PowerShell, from this directory:

```powershell
docker compose up -d                 # LocalStack 4.14.0
python ../../../scripts/build_lambda.py processor --arch x86_64   # DuckDB wheels for the processor
python ../../../scripts/build_lambda.py api --arch x86_64         # pydantic-core wheels for the API
terraform init
terraform apply                      # 49 resources, local state

# the AWS CLI with LocalStack's dummy credentials
$env:AWS_ACCESS_KEY_ID = 'test'; $env:AWS_SECRET_ACCESS_KEY = 'test'; $env:AWS_DEFAULT_REGION = 'us-east-1'
aws --endpoint-url=http://localhost:4566 logs tail /aws/lambda/market-pipeline-local-producer --follow
aws --endpoint-url=http://localhost:4566 s3 ls s3://market-pipeline-local-lake-000000000000/raw/ --recursive

# processor output: bars and latest prices in DynamoDB, Parquet hour files in processed/
aws --endpoint-url=http://localhost:4566 dynamodb query --table-name market-pipeline-local-market `
  --key-condition-expression "symbol = :s" --expression-attribute-values '{\":s\":{\"S\":\"BTCUSDT\"}}' `
  --no-scan-index-forward --max-items 3
aws --endpoint-url=http://localhost:4566 s3 ls s3://market-pipeline-local-lake-000000000000/processed/ --recursive
```

Call the API (REST API on LocalStack's edge port; `/health` needs no key):

```powershell
$url = terraform output -raw api_url      # http://localhost:4566/_aws/execute-api/<id>/v1
$key = terraform output -raw api_key
curl.exe -s "$url/health"
curl.exe -s -H "x-api-key: $key" "$url/prices/BTCUSDT"
curl.exe -s -H "x-api-key: $key" "$url/bars/BTCUSDT?limit=5"
curl.exe -s -o NUL -w "%{http_code}" "$url/prices/BTCUSDT"     # 403 without the key
```

The schedule invokes the producer every minute. Objects appear under `raw/`
within about a minute; the processor writes a bar and rewrites the hour's
Parquet file a few seconds after each tick. On the first apply it also reads
the stream's backlog (`TRIM_HORIZON`, up to 24 hours).

## Stop

```powershell
terraform destroy                    # optional; the next step wipes everything anyway
docker compose down                  # LocalStack keeps no state between runs
```

LocalStack's free edition does not persist anything, so after `docker compose
down` delete `terraform.tfstate*` here (or run `terraform destroy` first)
before the next `apply`.

## Differences from AWS

- **Image pinned to 4.14.0.** It is the last LocalStack release that starts
  without an account token. Newer images (2026.03.0 onward) need a free
  LocalStack account.
- **IAM is not enforced.** The least-privilege policies are created but not
  checked, so a local run does not prove they are sufficient.
- **Firehose prefixes are not evaluated.** LocalStack keeps
  `raw/dt=!{timestamp:yyyy-MM-dd}/hour=!{timestamp:HH}/` literally and appends
  its own `YYYY/MM/DD/HH/` folders; real Firehose writes `raw/dt=.../hour=.../`.
  LocalStack also writes each batch straight away instead of buffering.
- **Account ID is `000000000000`,** so the bucket is
  `market-pipeline-local-lake-000000000000`.
- **Lambda runs on the host's architecture** (`LAMBDA_IGNORE_ARCHITECTURE=1`)
  rather than arm64. The producer is pure Python so it does not care; the
  processor (DuckDB) and the API (pydantic-core) bundle native wheels, so both
  are built for x86_64 here (`lambda_architecture`; set `arm64` on Apple
  silicon).
- **No CloudWatch alarms.** LocalStack 4.14 creates them but fails to describe
  them back to the AWS provider, so `terraform apply` hangs; `create_alarms` is
  false here.
- **DynamoDB TTL is not enforced.** Bars get `expires_at` but LocalStack does
  not delete expired items unless configured to.
- **AWS provider pinned below 6.58.** From 6.58.0 the provider waits for a new
  REST API to report `AVAILABLE`, a status LocalStack 4.14 never sets, so
  `apply` fails. envs/dev uses the latest provider.
- **API Gateway throttling and quotas are not enforced.** The stage throttle and
  the usage plan are created, and API keys are checked (403 without one), but
  LocalStack serves every request however fast they come.
- **No API Gateway access logs.** The stage's access-log settings are accepted
  and the log group exists, but LocalStack writes nothing to it. The API's own
  JSON request lines are in `/aws/lambda/market-pipeline-local-api`.
- **No account-level API Gateway logging role** (`manage_account_logging_role =
  false`); LocalStack does not need one.
- **Glue and Athena are not in the free edition.** The analytics module is
  left out of this environment.
