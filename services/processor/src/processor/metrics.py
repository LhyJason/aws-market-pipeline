"""Custom metrics in CloudWatch Embedded Metric Format (EMF).

An EMF document is a JSON log line that CloudWatch Logs turns into a metric,
so publishing costs no API call and no extra IAM permission. It is written to
stdout directly: the JSON log formatter drops keys starting with "_", and EMF
needs a top-level "_aws". Outside AWS (LocalStack, tests) it is just a log line.
"""

from __future__ import annotations

import json
import os
import sys

NAMESPACE = "MarketPipeline"


def emf_document(name: str, value: float, unit: str, *, now_ms: int) -> dict[str, object]:
    """One metric with a FunctionName dimension, so dev and prod stay separate."""
    function = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "local")
    return {
        "_aws": {
            "Timestamp": now_ms,
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [["FunctionName"]],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }
            ],
        },
        "FunctionName": function,
        name: value,
    }


def put_metric(name: str, value: float, unit: str, *, now_ms: int) -> None:
    sys.stdout.write(json.dumps(emf_document(name, value, unit, now_ms=now_ms)) + "\n")
    sys.stdout.flush()
