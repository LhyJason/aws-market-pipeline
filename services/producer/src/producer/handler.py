"""Lambda entry point: fetch ticks from the feed and publish them to Kinesis."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

import boto3

from producer.config import Settings
from producer.feed import BinanceFeed, Feed, Tick
from producer.logging_utils import configure_logging

configure_logging()
log = logging.getLogger(__name__)

KINESIS_MAX_BATCH = 500  # hard limit on records per PutRecords call


class KinesisClient(Protocol):
    """The slice of boto3's Kinesis client that the producer uses."""

    def put_records(self, *, StreamName: str, Records: list[dict[str, Any]]) -> dict[str, Any]: ...


def publish(
    ticks: list[Tick],
    stream_name: str,
    kinesis: KinesisClient,
    *,
    max_attempts: int = 3,
    backoff_s: float = 0.2,
) -> dict[str, int]:
    """Put ticks on the stream, retrying records that fail inside a batch.

    PutRecords is not atomic: individual records can fail (typically with
    ProvisionedThroughputExceededException) while the call as a whole succeeds.
    Failed records are collected and retried with linear backoff.

    Each record ends in a newline: Firehose concatenates records as-is, so the
    delimiter is what makes the objects under raw/ line-delimited JSON.
    """
    pending = [
        {"Data": (t.to_json() + "\n").encode("utf-8"), "PartitionKey": t.symbol} for t in ticks
    ]
    sent = 0

    for attempt in range(1, max_attempts + 1):
        if not pending:
            break
        still_failing: list[dict[str, Any]] = []
        for start in range(0, len(pending), KINESIS_MAX_BATCH):
            batch = pending[start : start + KINESIS_MAX_BATCH]
            response = kinesis.put_records(StreamName=stream_name, Records=batch)
            for record, outcome in zip(batch, response["Records"], strict=True):
                if "ErrorCode" in outcome:
                    still_failing.append(record)
                else:
                    sent += 1
        pending = still_failing
        if pending and attempt < max_attempts:
            log.warning(
                "retrying failed kinesis records",
                extra={"attempt": attempt, "failed": len(pending)},
            )
            time.sleep(backoff_s * attempt)

    return {"sent": sent, "failed": len(pending)}


def run(settings: Settings, feed: Feed, kinesis: KinesisClient) -> dict[str, Any]:
    """One producer cycle. Pure wiring; all dependencies are injected so tests need no AWS."""
    started = time.perf_counter()
    ticks = feed.fetch(settings.symbols)
    result = publish(ticks, settings.stream_name, kinesis)
    log.info(
        "published ticks",
        extra={
            "stream": settings.stream_name,
            "symbols": len(settings.symbols),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            **result,
        },
    )
    return {"ok": result["failed"] == 0, **result}


# Built once per container so warm invocations reuse the boto3 client.
_deps: tuple[Settings, Feed, KinesisClient] | None = None


def _dependencies() -> tuple[Settings, Feed, KinesisClient]:
    global _deps
    if _deps is None:
        settings = Settings.from_env()
        feed = BinanceFeed(settings.feed_base_url, settings.feed_timeout_s)
        _deps = (settings, feed, boto3.client("kinesis"))
    return _deps


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return run(*_dependencies())
