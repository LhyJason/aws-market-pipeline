"""Shared pytest configuration."""

import sys

import pytest


def pytest_configure(config: pytest.Config) -> None:
    # Service packages are imported straight from services/*/src, the same
    # directories Terraform zips for Lambda. Keep __pycache__ out of them.
    sys.dont_write_bytecode = True


@pytest.fixture(autouse=True)
def _aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake credentials so boto3 never looks for real ones; moto intercepts the calls."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
