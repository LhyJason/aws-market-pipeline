"""Push ticks onto the stream at a set rate, wait for them to land, and report.

    python scripts/load_test.py --rate 50 --duration 60     # LocalStack (infra/envs/local)
    make load RATE=50 DURATION=60

The ticks use the producer's record format for symbols ``LOAD-<run>-<i>``,
which the producer never fetches, so real bars are untouched. Each tick of a
symbol gets its own millisecond: the processor treats ``(symbol, ts)`` as a
tick's identity, so two ticks in one millisecond would count once.

A run:

1. pauses the producer's schedule, so every batch in the logs is load (put
   back afterwards; ``--keep-schedule`` leaves it running). LocalStack 4.14
   keeps firing a disabled rule, so there the producer's 3 ticks a minute
   are mixed in,
2. sends ``rate x duration`` ticks with PutRecords, paced every 50 ms and
   retrying records Kinesis rejects,
3. waits until DynamoDB's bars count every tick (``tick_count``), which proves
   nothing was lost or counted twice,
4. reads a few of them back to measure how far the generator's clock is from
   Kinesis's (``ApproximateArrivalTimestamp``),
5. prints the report (scripts/load_report.py), saves the run under
   .build/load/, and deletes the test symbols from DynamoDB and processed/.

It changes a live stack, so it runs on LocalStack unless ALLOW_LOAD_ON_AWS=1.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from boto3.dynamodb.conditions import Key

import load_report
from load_report import Run, Target

KINESIS_MAX_BATCH = 500
SHARD_MAX_RECORDS_S = 1000  # per shard; the local and dev streams have one
PACE_S = 0.05
MAX_PUT_ATTEMPTS = 5
SKEW_SAMPLES = 5


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class TickSource:
    """Ticks for ``symbols`` in turn, on a random walk, each with a millisecond of its own."""

    symbols: list[str]
    clock: Callable[[], int] = now_ms
    rng: random.Random = field(default_factory=random.Random)
    _next: int = 0
    _prices: dict[str, float] = field(default_factory=dict)
    _last_ts: dict[str, int] = field(default_factory=dict)

    def __call__(self) -> dict[str, Any]:
        symbol = self.symbols[self._next % len(self.symbols)]
        self._next += 1
        price = self._prices.get(symbol, 100.0) * (1 + self.rng.gauss(0, 0.0005))
        self._prices[symbol] = price
        ts = max(self.clock(), self._last_ts.get(symbol, 0) + 1)
        self._last_ts[symbol] = ts
        line = {"symbol": symbol, "price": round(price, 4), "ts": ts, "source": "loadtest"}
        # The producer's format: one JSON object and a newline per record.
        return {"Data": (json.dumps(line) + "\n").encode("utf-8"), "PartitionKey": symbol}


def due(rate: float, elapsed_s: float, total: int) -> int:
    """How many ticks should have been sent ``elapsed_s`` into the run."""
    return min(int(rate * elapsed_s), total)


@dataclass
class PutStats:
    sent: int = 0
    retries: int = 0
    dropped: int = 0
    samples: list[tuple[str, str, int]] = field(default_factory=list)  # shard, sequence, ts


def put(kinesis: Any, stream: str, records: list[dict[str, Any]], stats: PutStats) -> None:
    """PutRecords in chunks of 500, retrying the records Kinesis rejects with a short backoff."""
    pending = records
    for attempt in range(1, MAX_PUT_ATTEMPTS + 1):
        failed: list[dict[str, Any]] = []
        for start in range(0, len(pending), KINESIS_MAX_BATCH):
            chunk = pending[start : start + KINESIS_MAX_BATCH]
            response = kinesis.put_records(StreamName=stream, Records=chunk)
            for record, outcome in zip(chunk, response["Records"], strict=True):
                if "ErrorCode" in outcome:
                    failed.append(record)
                    continue
                stats.sent += 1
                if len(stats.samples) < SKEW_SAMPLES and stats.sent % 97 == 1:
                    ts = json.loads(record["Data"])["ts"]
                    stats.samples.append((outcome["ShardId"], outcome["SequenceNumber"], ts))
        if not failed:
            return
        if attempt < MAX_PUT_ATTEMPTS:
            stats.retries += len(failed)
            time.sleep(0.1 * attempt)
        pending = failed
    stats.dropped += len(pending)


def send(
    kinesis: Any, stream: str, source: TickSource, rate: float, duration_s: float
) -> tuple[PutStats, int, int]:
    """Send ``rate x duration_s`` ticks evenly; return the stats and first and last send times."""
    total = round(rate * duration_s)
    stats = PutStats()
    started = now_ms()
    begin = time.monotonic()
    queued = 0
    while queued < total:
        n = due(rate, time.monotonic() - begin, total) - queued
        if n > 0:
            put(kinesis, stream, [source() for _ in range(n)], stats)
            queued += n
        time.sleep(PACE_S)
    return stats, started, now_ms()


def clock_skew_ms(kinesis: Any, stream: str, samples: list[tuple[str, str, int]]) -> float | None:
    """Median of Kinesis arrival time minus the tick's ``ts`` (the generator's send time).

    It includes the one-way trip to Kinesis: a few ms locally, tens to AWS.
    """
    offsets = []
    for shard, sequence, ts in samples:
        iterator = kinesis.get_shard_iterator(
            StreamName=stream,
            ShardId=shard,
            ShardIteratorType="AT_SEQUENCE_NUMBER",
            StartingSequenceNumber=sequence,
        )["ShardIterator"]
        records = kinesis.get_records(ShardIterator=iterator, Limit=1)["Records"]
        if records:
            arrival = records[0]["ApproximateArrivalTimestamp"].timestamp() * 1000
            offsets.append(arrival - ts)
    return statistics.median(offsets) if offsets else None


def landed_ticks(table: Any, symbol: str) -> int:
    """Ticks counted in the symbol's bars."""
    total = 0
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("symbol").eq(symbol) & Key("sk").begins_with("BAR#"),
        "ProjectionExpression": "tick_count",
    }
    while True:
        page = table.query(**kwargs)
        total += sum(int(item.get("tick_count", 0)) for item in page["Items"])
        if "LastEvaluatedKey" not in page:
            return total
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def wait_landed(
    table: Any, symbols: list[str], expected: int, timeout_s: float
) -> tuple[int, int | None]:
    """Poll until the bars hold ``expected`` ticks; return the count and when (None: timed out).

    The timeout runs from the last time the count grew, so a long backlog that
    is still draining is waited for and only a stalled one gives up.
    """
    deadline = time.monotonic() + timeout_s
    previous = -1
    while True:
        landed = sum(landed_ticks(table, symbol) for symbol in symbols)
        if landed >= expected:
            return landed, now_ms()
        if landed > previous:
            previous = landed
            deadline = time.monotonic() + timeout_s
        elif time.monotonic() > deadline:
            return landed, None
        time.sleep(1)


def wait_logged(target: Target, run: Run, timeout_s: float = 60) -> load_report.Summary:
    """The run's summary once its log lines account for every landed tick.

    LocalStack writes a Lambda's log lines a few seconds after the invocation.
    """
    start, end = load_report.run_window(run, run.skew_ms or 0.0)
    deadline = time.monotonic() + timeout_s
    while True:
        summary = load_report.summarise(target, start, end)
        if summary.records >= run.landed or time.monotonic() > deadline:
            return summary
        time.sleep(2)


@contextmanager
def schedule_paused(target: Target, keep: bool) -> Iterator[None]:
    """Disable the producer's EventBridge rule for the block, if it was enabled."""
    events = target.client("events")
    rule = f"{target.outputs['producer_function_name']}-schedule"
    enabled = events.describe_rule(Name=rule)["State"] == "ENABLED"
    if keep or not enabled:
        yield
        return
    events.disable_rule(Name=rule)
    try:
        yield
    finally:
        events.enable_rule(Name=rule)


def delete_symbols(target: Target, symbols: list[str]) -> None:
    """Remove the test symbols' items and Parquet files; their raw/ records expire on their own."""
    table = target.table()
    s3 = target.client("s3")
    bucket = target.outputs["lake_bucket"]
    for symbol in symbols:
        items = table.query(
            KeyConditionExpression=Key("symbol").eq(symbol), ProjectionExpression="sk"
        )["Items"]
        with table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"symbol": symbol, "sk": item["sk"]})
        listing = s3.list_objects_v2(Bucket=bucket, Prefix=f"processed/bars_1m/symbol={symbol}/")
        for obj in listing.get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rate", type=float, default=50, help="ticks per second (default 50)")
    parser.add_argument("--duration", type=float, default=60, help="seconds (default 60)")
    parser.add_argument("--symbols", type=int, default=5, help="symbols to spread over")
    parser.add_argument("--env-dir", default=load_report.DEFAULT_ENV_DIR)
    parser.add_argument(
        "--drain-timeout", type=float, default=300, help="seconds with no tick landing to give up"
    )
    parser.add_argument("--keep-schedule", action="store_true", help="leave the producer on")
    parser.add_argument("--keep-data", action="store_true", help="leave the test symbols")
    args = parser.parse_args(argv)

    if not 0 < args.rate <= SHARD_MAX_RECORDS_S:
        parser.error(f"--rate must be in (0, {SHARD_MAX_RECORDS_S}], one shard's record limit")
    if args.duration <= 0 or args.symbols < 1:
        parser.error("--duration and --symbols must be positive")

    target = load_report.connect(args.env_dir)
    if not target.is_local and os.environ.get("ALLOW_LOAD_ON_AWS") != "1":
        raise SystemExit("this stack is on AWS, which bills for it; set ALLOW_LOAD_ON_AWS=1")

    run_id = uuid.uuid4().hex[:6].upper()
    symbols = [f"LOAD-{run_id}-{i}" for i in range(args.symbols)]
    stream = target.outputs["stream_name"]
    kinesis = target.client("kinesis")
    print(
        f"run {run_id}: {args.rate:g} ticks/s for {args.duration:g} s over {len(symbols)} symbols"
    )

    try:
        with schedule_paused(target, args.keep_schedule):
            stats, started, sent_at = send(
                kinesis, stream, TickSource(symbols), args.rate, args.duration
            )
            print(f"sent {stats.sent:,} ticks; waiting for them to land")
            # Landing first: on AWS the read-back took about 20 s, probably as
            # GetRecords shares the shard's 5 reads a second with the
            # processor, and that would otherwise count as drain time.
            landed, landed_at = wait_landed(target.table(), symbols, stats.sent, args.drain_timeout)
            skew = clock_skew_ms(kinesis, stream, stats.samples)
        run = Run(
            run_id=run_id,
            env_dir=args.env_dir,
            local=target.is_local,
            rate=args.rate,
            duration_s=args.duration,
            symbols=symbols,
            sent=stats.sent,
            put_retries=stats.retries,
            put_dropped=stats.dropped,
            started_ms=started,
            sent_ms=sent_at,
            landed=landed,
            landed_ms=landed_at,
            skew_ms=skew,
        )
        path = run.save()
        summary = wait_logged(target, run)
        print()
        print(load_report.format_run(run, summary))
        print(f"\nsaved {path.relative_to(load_report.ROOT)}")
    finally:
        if not args.keep_data:
            delete_symbols(target, symbols)


if __name__ == "__main__":
    main()
