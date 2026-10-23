"""Structured JSON logging, one object per line, for CloudWatch Logs Insights.

A copy of producer/logging_utils.py: each Lambda zip is built from its own src/
directory, so the services cannot import a shared module.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": _iso8601(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Anything passed as logger.info(..., extra={...}) becomes a top-level field.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def _iso8601(epoch_s: float) -> str:
    whole = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch_s))
    return f"{whole}.{int((epoch_s % 1) * 1000):03d}Z"


def configure_logging(level: str | None = None) -> None:
    """Install the JSON formatter on the root logger.

    The Lambda runtime pre-installs a handler on the root logger; reuse it so
    nothing is logged twice. Outside Lambda, log to stdout.
    """
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stdout))
    for handler in root.handlers:
        handler.setFormatter(JsonFormatter())
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))
