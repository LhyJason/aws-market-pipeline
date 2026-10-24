# Integration tests (phase 4)

End-to-end tests against a deployed stack, LocalStack by default. They are
marked `@pytest.mark.integration`, deselected by the default `pytest` run (and
so by CI and `make test`), and skipped unless `RUN_INTEGRATION=1`.

| File               | What it checks                                                           |
| ------------------ | ------------------------------------------------------------------------ |
| `test_pipeline.py` | invokes the producer once, finds its tick on the stream (`AT_TIMESTAMP`), then waits for the processor and asserts the API returns it as the latest price and its minute as a bar |
| `test_api.py`      | `/health` needs no key; a missing or wrong key is a 403 from API Gateway; unknown paths are a 403; bad input is a 422; symbols outside `SYMBOLS` are a 404; every response matches the `api.models` schema exactly |

The schema checks validate against `services/api/src/api/models.py`, so the
deployed API and the code in the repository cannot drift apart unnoticed.

## Run against LocalStack ($0)

Bring up `infra/envs/local` first (see its README), then from the repository
root:

```bash
make test-integration
```

or, without make (PowerShell):

```powershell
$env:RUN_INTEGRATION = '1'
pytest -m integration tests/integration -v
```

The tests read the API URL, API key, producer function and stream name from
`terraform output` in `infra/envs/local`, and talk to LocalStack with its
dummy `test` credentials. If `terraform` is not on PATH, set `TERRAFORM` to
its full path. A run takes a few seconds; each wait gives up after two minutes.

## Run against another stack

Every setting can be given directly, and `TF_ENV_DIR` picks the Terraform
directory to read the rest from. For dev on AWS (which costs money; see
`docs/cost.md`):

```bash
RUN_INTEGRATION=1 TF_ENV_DIR=infra/envs/dev pytest -m integration tests/integration -v
```

| Variable            | Default                                                    |
| ------------------- | ---------------------------------------------------------- |
| `TF_ENV_DIR`        | `infra/envs/local`                                         |
| `API_URL`           | `terraform output api_url`                                 |
| `API_KEY`           | `terraform output api_key`                                 |
| `PRODUCER_FUNCTION` | `terraform output producer_function_name`                  |
| `STREAM_NAME`       | `terraform output stream_name`                             |
| `AWS_ENDPOINT_URL`  | `http://localhost:4566` for envs/local, unset (real AWS) otherwise |
| `AWS_DEFAULT_REGION`| `us-east-1`                                                |
| `SYMBOL`            | `BTCUSDT`                                                  |
| `TERRAFORM`         | `terraform` on PATH                                        |

Against AWS the tests use boto3's usual credential chain.

## What they cannot check on LocalStack

LocalStack does not enforce IAM, API Gateway throttling or usage-plan quotas,
and does not deliver API Gateway access logs, so passing locally does not
prove the IAM policies are sufficient or that throttling works. Those need a
run against dev.
