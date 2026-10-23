"""Lambda entry point: turn a Kinesis batch into 1-minute bars in DynamoDB and processed/."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

import boto3

from processor.bars import Bar, build_bars, hour_start
from processor.config import Settings
from processor.lake import LakeWriter
from processor.logging_utils import configure_logging
from processor.records import RecordError, Tick, decode_record
from processor.store import MarketStore

configure_logging()
log = logging.getLogger(__name__)


class Store(Protocol):
    def put_latest(self, tick: Tick) -> bool: ...
    def merge_bar(self, bar: Bar) -> Bar: ...
    def hour_bars(self, symbol: str, hour_start: int) -> list[Bar]: ...


class Lake(Protocol):
    def write_hour(self, symbol: str, hour_start: int, bars: list[Bar]) -> str: ...


def process_symbol(symbol: str, ticks: list[Tick], store: Store, lake: Lake) -> int:
    """Merge one symbol's ticks into its bars, rewrite the touched hours, update latest.

    Every step is idempotent, so a retry may run all of it again. Returns the
    number of bars touched.
    """
    bars = build_bars(ticks)
    for bar in bars:
        store.merge_bar(bar)
    for hour in sorted({hour_start(bar.start) for bar in bars}):
        lake.write_hour(symbol, hour, store.hour_bars(symbol, hour))
    store.put_latest(max(ticks, key=lambda t: t.ts))
    return len(bars)


def process(
    event: dict[str, Any], store: Store, lake: Lake, *, now_ms: int, max_age_ms: int
) -> dict[str, Any]:
    """Process one batch and return a partial batch response.

    Malformed records are logged and dropped: retrying them can never succeed.
    Ticks from an hour that started ``max_age_ms`` or more ago are dropped too:
    some of that hour's bars may already have expired from DynamoDB, and
    rewriting the hour file from what is left would lose data. Kinesis keeps
    records for 24 hours and bars live for days, so this only guards replays.

    If a symbol fails, the response names the earliest record that carried one
    of its ticks. Lambda resumes the shard from the lowest reported sequence
    number, re-delivering the records after it; the writes are idempotent, so
    redoing symbols that already succeeded is harmless.
    """
    records = event.get("Records", [])
    ticks_by_symbol: dict[str, list[Tick]] = {}
    first_sequence: dict[str, str] = {}
    malformed = stale = 0

    for record in records:
        sequence = record["kinesis"]["sequenceNumber"]
        try:
            ticks = decode_record(record)
        except RecordError as exc:
            malformed += 1
            log.warning(
                "dropping malformed record", extra={"sequence_number": sequence, "error": str(exc)}
            )
            continue
        for tick in ticks:
            # Judged by the hour, not the tick: the hour's first bar is the
            # first to expire, and the whole hour file is rebuilt.
            if hour_start(tick.ts) <= now_ms - max_age_ms:
                stale += 1
                continue
            ticks_by_symbol.setdefault(tick.symbol, []).append(tick)
            seen = first_sequence.get(tick.symbol)
            if seen is None or int(sequence) < int(seen):
                first_sequence[tick.symbol] = sequence

    bars = 0
    failed: list[str] = []
    for symbol, ticks in ticks_by_symbol.items():
        try:
            bars += process_symbol(symbol, ticks, store, lake)
        except Exception:
            log.exception(
                "failed to process symbol",
                extra={"symbol": symbol, "sequence_number": first_sequence[symbol]},
            )
            failed.append(first_sequence[symbol])

    log.info(
        "processed batch",
        extra={
            "records": len(records),
            "malformed": malformed,
            "stale": stale,
            "symbols": len(ticks_by_symbol),
            "bars": bars,
            "failed_symbols": len(failed),
        },
    )
    failures = [{"itemIdentifier": min(failed, key=int)}] if failed else []
    return {"batchItemFailures": failures}


# Built once per container so warm invocations reuse the boto3 clients.
_deps: tuple[Settings, MarketStore, LakeWriter] | None = None


def _dependencies() -> tuple[Settings, MarketStore, LakeWriter]:
    global _deps
    if _deps is None:
        settings = Settings.from_env()
        table = boto3.resource("dynamodb").Table(settings.table_name)
        store = MarketStore(table, bar_ttl_s=settings.bar_ttl_s)
        lake = LakeWriter(boto3.client("s3"), settings.lake_bucket, settings.processed_prefix)
        _deps = (settings, store, lake)
    return _deps


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings, store, lake = _dependencies()
    return process(
        event,
        store,
        lake,
        now_ms=int(time.time() * 1000),
        max_age_ms=settings.bar_ttl_s * 1000,
    )
