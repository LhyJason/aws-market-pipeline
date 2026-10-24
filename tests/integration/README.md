# Integration and chaos tests (phases 4 and 5)

End-to-end tests against a deployed stack, LocalStack by default. They are
marked `@pytest.mark.integration`, deselected by the default `pytest` run (and
so by CI and `make test`), and skipped unless `RUN_INTEGRATION=1`.

| File               | What it checks                                                           |
| ------------------ | ------------------------------------------------------------------------ |
| `test_pipeline.py` | invokes the producer once, finds its tick on the stream (`AT_TIMESTAMP`), then waits for the processor and asserts the API returns it as the latest price and its minute as a bar |
| `test_chaos.py`    | fault injection, marked `chaos` as well; see [Chaos tests](#chaos-tests) |
| `test_api.py`      | `/health` needs no key; a missing or wrong key is a 403 from API Gateway; unknown paths are a 403; bad input is a 422; symbols outside `SYMBOLS` are a 404; every response matches the `api.models` schema exactly |

The schema checks validate against `services/api/src/api/models.py`, so the
deployed API and the code in the repository cannot drift apart unnoticed.

## Run against LocalStack ($0)

Bring up `infra/envs/local` first (see its README), then from the repository
root:

```bash
make test-integration     # end to end, leaves the stack untouched
make test-chaos           # fault injection, restores everything it breaks
```

or, without make (PowerShell):

```powershell
$env:RUN_INTEGRATION = '1'
pytest -m "integration and not chaos" tests/integration -v
pytest -m chaos tests/integration -v
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

## Chaos tests

`test_chaos.py` breaks one part of the running stack at a time and checks
that the failure stays contained. Every injection is undone in a `finally`
block. The test symbols (`CHAOS-OK-<run>`, `CHAOS-BAD-<run>`) are unique per
run, aren't in the producer's list, and are deleted from DynamoDB and
`processed/` afterwards; their records stay in `raw/`, which expires after 30
days. About 35 seconds on LocalStack.

| Injected failure | Assertions | What catches it on AWS |
| --- | --- | --- |
| **Poison records** on the stream: bytes that aren't UTF-8, a JSON array, a tick with no price, a negative price, an invalid symbol, then one good tick | The good tick reaches `LATEST`. Each poison record's sequence number appears in a `dropping malformed record` log line. None of them reaches the DLQ, since retrying can't fix them | The `malformed` count in the `processed batch` log line (Logs Insights) |
| **A symbol that always fails:** a corrupt `BAR#` item (no attributes) at the minute of a test tick, so merging into it raises | A good symbol in the same batch lands at once. The failing batch is retried, bisected and sent to the DLQ with `condition = RetryAttemptsExhausted` and its sequence range. A tick put afterwards lands, so the shard is unblocked. The bad symbol never gets a `LATEST` | The `-processor-dlq` alarm (DLQ depth > 0), and `-processor-iterator-age` if the retries take long |
| **DynamoDB errors behind the API:** the API Lambda's `TABLE_NAME` points at a table that doesn't exist | `/prices` and `/bars` return **503** `{"detail": "market data is temporarily unavailable"}` instead of crashing, `/health` stays 200, and `/prices` recovers once `TABLE_NAME` is restored | The `-api-5xx` alarm |
| **A bad deploy:** the API handler set to `api.app.missing` | API Gateway returns **502** on every route until the handler is restored, then `/prices` is 200 again | The smoke test in `deploy.yml` fails the dev deploy before prod can follow, then the `-api-5xx` alarm |

Throttling isn't injected live: LocalStack only simulates it with
`DYNAMODB_ERROR_PROBABILITY` set when the container starts, which would fail
every table call, the processor's included. `ProvisionedThroughputExceeded`
and `ThrottlingException` are `ClientError`s like the missing table, and the
unit test `test_dynamodb_errors_become_503` covers that mapping.

### What only a run on AWS can show

The chaos tests prove that the code and the Lambda and Kinesis wiring contain
each failure. On LocalStack they cannot show that anyone is told about it:

- **Alarms firing and email arriving.** envs/local has no alarms
  (`create_alarms = false`), and LocalStack publishes almost none of the AWS
  metrics they watch: Lambda `Errors`, `IteratorAge`, SQS queue depth, API
  Gateway `5XXError` and `Latency`.
- **X-Ray traces** of the failing calls, since the X-Ray API is not in
  LocalStack's free edition.
- **The `TickLagMs` metric,** which only CloudWatch turns from EMF log lines
  into a metric.
- **Retry timing.** LocalStack retries a failed batch at once, so the DLQ test
  takes seconds. On AWS, retries back off and the shard stays blocked longer,
  which is what the iterator-age alarm is for.
- **IAM.** LocalStack doesn't enforce it, so the tests can't show that the
  processor role may send to the DLQ. Only a real failure on AWS can.

To run them against dev on AWS (billed, and they briefly break the dev API):

```bash
RUN_INTEGRATION=1 ALLOW_CHAOS_ON_AWS=1 TF_ENV_DIR=infra/envs/dev pytest -m chaos tests/integration -v
```
