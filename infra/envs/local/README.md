# local environment (LocalStack, $0)

The `ingest` and `storage` modules from dev, applied to
[LocalStack](https://docs.localstack.cloud) on `localhost:4566` instead of AWS.
Nothing is billed and no AWS account is needed. The only outside traffic is
the producer's call to the public price feed.

Requires Docker Desktop (WSL2 backend on Windows) and Terraform.

## Run

PowerShell, from this directory:

```powershell
docker compose up -d                 # LocalStack 4.14.0
terraform init
terraform apply                      # 18 resources, local state

# the AWS CLI with LocalStack's dummy credentials
$env:AWS_ACCESS_KEY_ID = 'test'; $env:AWS_SECRET_ACCESS_KEY = 'test'; $env:AWS_DEFAULT_REGION = 'us-east-1'
aws --endpoint-url=http://localhost:4566 logs tail /aws/lambda/market-pipeline-local-producer --follow
aws --endpoint-url=http://localhost:4566 s3 ls s3://market-pipeline-local-lake-000000000000/raw/ --recursive
```

The schedule invokes the producer every minute, and objects appear under
`raw/` within about a minute.

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
  rather than arm64.
