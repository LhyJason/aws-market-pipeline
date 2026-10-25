"""Fixtures for the integration tests: a deployed stack, LocalStack by default.

The tests are skipped unless RUN_INTEGRATION=1. Each setting comes from an
environment variable, falling back to ``terraform output`` in TF_ENV_DIR:

| Variable            | Fallback                                                       |
| ------------------- | -------------------------------------------------------------- |
| `TF_ENV_DIR`        | `infra/envs/local`                                             |
| `API_URL`           | output `api_url`                                               |
| `API_KEY`           | output `api_key`                                               |
| `PRODUCER_FUNCTION` | output `producer_function_name`                                |
| `STREAM_NAME`       | output `stream_name`                                           |
| `AWS_ENDPOINT_URL`  | `http://localhost:4566` for envs/local; unset means real AWS    |
| `SYMBOL`            | `BTCUSDT`                                                      |
| `TERRAFORM`         | `terraform` on PATH                                            |

Against LocalStack the tests use its dummy credentials; against AWS, boto3's
usual credential chain.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCALSTACK = "http://localhost:4566"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set RUN_INTEGRATION=1 to run against a deployed stack")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@dataclass(frozen=True)
class Response:
    status: int
    body: Any  # parsed JSON, or the raw text if the body is not JSON


@dataclass(frozen=True)
class Stack:
    api_url: str
    api_key: str
    producer_function: str
    stream_name: str
    symbol: str
    session: boto3.Session
    endpoint_url: str | None

    def client(self, service: str) -> Any:
        return self.session.client(service, endpoint_url=self.endpoint_url)

    def get(self, path: str, *, key: str | None = "", params: dict | None = None) -> Response:
        """GET ``path`` on the API. ``key`` "" sends the stack's key, None sends none."""
        url = self.api_url.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {} if key is None else {"x-api-key": key or self.api_key}
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        text = raw.decode("utf-8")
        try:
            return Response(status, json.loads(text))
        except json.JSONDecodeError:
            return Response(status, text)


def _terraform_outputs(env_dir: Path) -> dict[str, Any]:
    terraform = os.environ.get("TERRAFORM") or shutil.which("terraform")
    if not terraform:
        pytest.fail(
            "terraform not found: set TERRAFORM, or set API_URL, API_KEY, PRODUCER_FUNCTION "
            "and STREAM_NAME directly"
        )
    result = subprocess.run(
        [terraform, f"-chdir={env_dir}", "output", "-json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return {name: output["value"] for name, output in json.loads(result.stdout).items()}


@pytest.fixture(scope="session")
def stack() -> Stack:
    env_dir = ROOT / os.environ.get("TF_ENV_DIR", "infra/envs/local")
    names = {
        "API_URL": "api_url",
        "API_KEY": "api_key",
        "PRODUCER_FUNCTION": "producer_function_name",
        "STREAM_NAME": "stream_name",
    }
    values = {var: os.environ.get(var, "") for var in names}
    if not all(values.values()):
        outputs = _terraform_outputs(env_dir)
        values = {var: value or outputs[names[var]] for var, value in values.items()}

    is_local = env_dir.name == "local"
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL", LOCALSTACK if is_local else "") or None
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    if endpoint_url:
        # LocalStack's documented dummy credentials, not real keys.
        session = boto3.Session(
            aws_access_key_id="test", aws_secret_access_key="test", region_name=region
        )
    else:
        session = boto3.Session(region_name=region)

    deployed = Stack(
        api_url=values["API_URL"],
        api_key=values["API_KEY"],
        producer_function=values["PRODUCER_FUNCTION"],
        stream_name=values["STREAM_NAME"],
        symbol=os.environ.get("SYMBOL", "BTCUSDT"),
        session=session,
        endpoint_url=endpoint_url,
    )
    # On AWS a new API key is refused (403, as if it were wrong) for about a
    # minute after apply, so wait until API Gateway accepts it.
    _wait_for(lambda: deployed.get(f"/prices/{deployed.symbol}").status != 403 or None)
    return deployed


@pytest.fixture(scope="session")
def outputs() -> dict[str, Any]:
    """Every ``terraform output`` of TF_ENV_DIR, for tests that need more than ``stack``."""
    return _terraform_outputs(ROOT / os.environ.get("TF_ENV_DIR", "infra/envs/local"))


def _wait_for[T](
    probe: Callable[[], T | None], *, timeout_s: float = 120, interval_s: float = 2
) -> T:
    """Call ``probe`` until it returns something other than None, or fail after ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    while True:
        result = probe()
        if result is not None:
            return result
        if time.monotonic() > deadline:
            pytest.fail(f"gave up after {timeout_s:.0f} s")
        time.sleep(interval_s)


@pytest.fixture(scope="session")
def wait_for() -> Callable[..., Any]:
    """``_wait_for`` as a fixture: the test directories are not packages, so
    conftest modules cannot be imported by name."""
    return _wait_for
