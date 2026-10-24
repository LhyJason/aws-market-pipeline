"""Summarise the processor's logs: throughput, lag percentiles and errors.

    python scripts/load_report.py RUN_ID          # re-report a load test from .build/load/
    python scripts/load_report.py --minutes 60    # the last hour, e.g. the producer alone

Every processor batch logs one "processed batch" line (records, malformed,
failed_symbols, lag_ms) and the Lambda runtime one REPORT line with its
duration. This reads them back from CloudWatch Logs, LocalStack's by default,
for the window of a run that scripts/load_test.py recorded, or for the last
few minutes.

``lag_ms`` is per batch: how old the batch's oldest written tick was when its
bars had landed, measured on the processor's clock against the tick's ``ts``.
So the percentiles are over batches, and each is an upper bound on the lag of
the ticks in that batch.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import boto3

ROOT = Path(__file__).resolve().parent.parent
LOCALSTACK = "http://localhost:4566"
DEFAULT_ENV_DIR = os.environ.get("TF_ENV_DIR", "infra/envs/local")
RUN_DIR = ROOT / ".build" / "load"
_DURATION = re.compile(r"^REPORT RequestId: \S+\s+Duration: ([\d.]+) ms")


# ---- the stack -------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A deployed stack: its ``terraform output`` values and a boto3 session for it."""

    env_dir: str
    outputs: dict[str, Any]
    session: boto3.Session
    endpoint_url: str | None

    @property
    def is_local(self) -> bool:
        return self.endpoint_url is not None

    def client(self, service: str) -> Any:
        return self.session.client(service, endpoint_url=self.endpoint_url)

    def table(self) -> Any:
        dynamodb = self.session.resource("dynamodb", endpoint_url=self.endpoint_url)
        return dynamodb.Table(self.outputs["market_table"])

    @property
    def processor_log_group(self) -> str:
        return f"/aws/lambda/{self.outputs['processor_function_name']}"


def terraform_outputs(env_dir: Path) -> dict[str, Any]:
    terraform = os.environ.get("TERRAFORM") or shutil.which("terraform")
    if not terraform:
        raise SystemExit("terraform not found: put it on PATH or set TERRAFORM to its full path")
    result = subprocess.run(
        [terraform, f"-chdir={env_dir}", "output", "-json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return {name: output["value"] for name, output in json.loads(result.stdout).items()}


def connect(env_dir: str = DEFAULT_ENV_DIR) -> Target:
    """The stack in ``env_dir``; LocalStack for envs/local, AWS (boto3's credentials) otherwise."""
    is_local = Path(env_dir).name == "local"
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL", LOCALSTACK if is_local else "") or None
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    if endpoint_url:
        # LocalStack's documented dummy credentials, not real keys.
        session = boto3.Session(
            aws_access_key_id="test", aws_secret_access_key="test", region_name=region
        )
    else:
        session = boto3.Session(region_name=region)
    return Target(env_dir, terraform_outputs(ROOT / env_dir), session, endpoint_url)


# ---- a load test run ------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """What scripts/load_test.py did, saved as .build/load/<run_id>.json."""

    run_id: str
    env_dir: str
    local: bool
    rate: float  # ticks per second offered
    duration_s: float
    symbols: list[str]
    sent: int  # ticks accepted by Kinesis
    put_retries: int  # records PutRecords rejected and the generator sent again
    put_dropped: int  # records still rejected after every retry
    started_ms: int  # first send, generator clock
    sent_ms: int  # last send, generator clock
    landed: int  # ticks counted in DynamoDB bars (tick_count) when the wait ended
    landed_ms: int | None  # when all of them had landed; None if the wait timed out
    skew_ms: float | None  # Kinesis arrival time minus send time, median of a few records

    def save(self, directory: Path = RUN_DIR) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.json"
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, run_id: str, directory: Path = RUN_DIR) -> Run:
        return cls(**json.loads((directory / f"{run_id}.json").read_text(encoding="utf-8")))


# ---- the logs ---------------------------------------------------------------------


def fetch_events(logs: Any, log_group: str, start_ms: int, end_ms: int) -> list[dict]:
    """Every log event in ``[start_ms, end_ms]``, all pages."""
    events: list[dict] = []
    kwargs: dict[str, Any] = {"logGroupName": log_group, "startTime": start_ms, "endTime": end_ms}
    while True:
        page = logs.filter_log_events(**kwargs)
        events += page["events"]
        token = page.get("nextToken")
        if not token or token == kwargs.get("nextToken"):
            return events
        kwargs["nextToken"] = token


def log_lines(events: Iterable[dict]) -> Iterator[tuple[int, dict]]:
    """``(event timestamp, object)`` for each JSON log line in the events.

    AWS gives one event per line. LocalStack can put several log lines of an
    invocation into one event with nothing between them, and append
    "END RequestId: ..." to the last, so read JSON objects back to back until
    something else follows.
    """
    decoder = json.JSONDecoder()
    for event in events:
        text, pos = event["message"], 0
        while text.startswith("{", pos):
            try:
                obj, pos = decoder.raw_decode(text, pos)
            except json.JSONDecodeError:
                break
            yield event["timestamp"], obj
            while pos < len(text) and text[pos].isspace():
                pos += 1


@dataclass(frozen=True)
class Batch:
    at_ms: int  # when the processor logged it
    records: int
    malformed: int
    failed_symbols: int
    lag_ms: int | None


def batches(events: Iterable[dict]) -> list[Batch]:
    return [
        Batch(
            at_ms=at,
            records=line.get("records", 0),
            malformed=line.get("malformed", 0),
            failed_symbols=line.get("failed_symbols", 0),
            lag_ms=line.get("lag_ms"),
        )
        for at, line in log_lines(events)
        if line.get("msg") == "processed batch"
    ]


def durations_ms(events: Iterable[dict]) -> list[float]:
    """Invocation durations from the runtime's REPORT lines (not Billed or Init Duration)."""
    found = (_DURATION.match(event["message"]) for event in events)
    return [float(match.group(1)) for match in found if match]


# ---- the numbers -----------------------------------------------------------------


def percentile(values: Iterable[float], p: float) -> float:
    """Nearest-rank percentile: the smallest value with at least p% of values at or below it."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values")
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


@dataclass(frozen=True)
class Spread:
    p50: float
    p95: float
    max: float

    @classmethod
    def of(cls, values: list[float]) -> Spread | None:
        if not values:
            return None
        return cls(percentile(values, 50), percentile(values, 95), max(values))

    def shifted(self, by: float) -> Spread:
        return Spread(self.p50 + by, self.p95 + by, self.max + by)

    def __str__(self) -> str:
        return f"p50 {self.p50:,.0f}  p95 {self.p95:,.0f}  max {self.max:,.0f}"


@dataclass(frozen=True)
class Summary:
    batches: int
    records: int
    max_batch: int
    malformed: int
    failed_symbols: int
    first_ms: int | None
    last_ms: int | None
    lag: Spread | None
    duration: Spread | None

    @classmethod
    def of(cls, found: list[Batch], durations: list[float]) -> Summary:
        return cls(
            batches=len(found),
            records=sum(b.records for b in found),
            max_batch=max((b.records for b in found), default=0),
            malformed=sum(b.malformed for b in found),
            failed_symbols=sum(b.failed_symbols for b in found),
            first_ms=min((b.at_ms for b in found), default=None),
            last_ms=max((b.at_ms for b in found), default=None),
            lag=Spread.of([b.lag_ms for b in found if b.lag_ms is not None]),
            duration=Spread.of(durations),
        )


def format_summary(summary: Summary, *, skew_ms: float | None = None) -> list[str]:
    lines = []
    if summary.batches:
        mean = summary.records / summary.batches
        lines.append(
            f"processed   {summary.records:,} records in {summary.batches:,} batches "
            f"(mean {mean:,.1f}, max {summary.max_batch:,})"
        )
        if summary.first_ms is not None and summary.last_ms and summary.last_ms > summary.first_ms:
            # The processor's own pace while it had work: its ceiling once batches are full.
            span_s = (summary.last_ms - summary.first_ms) / 1000
            lines.append(
                f"  pace      {summary.records / span_s:,.1f} records/s "
                f"from the first batch to the last ({span_s:,.1f} s)"
            )
    else:
        lines.append("processed   no batches logged in the window")
    lines.append(
        f"errors      {summary.malformed:,} malformed records, "
        f"{summary.failed_symbols:,} failed symbols"
    )
    if summary.lag:
        lines.append(f"lag ms      {summary.lag}   (per batch: its oldest tick)")
        if skew_ms is not None:
            # Lag is read on the processor's clock against the generator's ts;
            # removing the measured offset leaves Kinesis arrival -> DynamoDB.
            lines.append(
                f"  from arrival {summary.lag.shifted(-skew_ms)}   "
                f"(minus {skew_ms:,.0f} ms generator-to-Kinesis offset)"
            )
    if summary.duration:
        lines.append(f"duration ms {summary.duration}   (processor invocations)")
    return lines


def format_run(run: Run, summary: Summary) -> str:
    where = "LocalStack" if run.local else "AWS"
    send_s = max((run.sent_ms - run.started_ms) / 1000, 0.001)
    lines = [
        f"load test {run.run_id} on {run.env_dir} ({where})",
        f"offered     {run.rate:,.0f} ticks/s for {run.duration_s:,.0f} s: "
        f"{run.sent:,} ticks over {len(run.symbols)} symbols, "
        f"{run.sent / send_s:,.1f} ticks/s achieved",
        f"kinesis     {run.put_retries:,} records retried, {run.put_dropped:,} dropped",
    ]
    if run.landed_ms is not None:
        drain_s = (run.landed_ms - run.sent_ms) / 1000
        total_s = (run.landed_ms - run.started_ms) / 1000
        lines.append(
            f"landed      {run.landed:,} of {run.sent:,} ticks in DynamoDB bars, "
            f"the last {drain_s:,.1f} s after the last send"
        )
        lines.append(f"throughput  {run.landed / total_s:,.1f} ticks/s end to end")
    else:
        lines.append(f"landed      {run.landed:,} of {run.sent:,} ticks: TIMED OUT waiting")
    lines += format_summary(summary, skew_ms=run.skew_ms)
    # A few producer ticks are expected (LocalStack ignores the paused
    # schedule); many more means the stream had a backlog when the run began.
    if summary.records > run.sent * 1.05 + 20:
        lines.append(
            f"WARNING     the processor handled {summary.records - run.sent:,} records more than "
            "this run sent: a backlog on the stream is mixed into these numbers"
        )
    return "\n".join(lines)


def summarise(target: Target, start_ms: int, end_ms: int) -> Summary:
    events = fetch_events(target.client("logs"), target.processor_log_group, start_ms, end_ms)
    return Summary.of(batches(events), durations_ms(events))


def run_window(run: Run, skew_ms: float = 0.0) -> tuple[int, int]:
    """The run's log window, on the processor's clock.

    It opens a second before the first send, because the measured offset
    includes the trip to Kinesis and so overstates the clock difference a
    little. It closes two seconds after every tick was seen in DynamoDB,
    before a following run can start.
    """
    end = run.landed_ms if run.landed_ms is not None else run.sent_ms + 300_000
    return int(run.started_ms + skew_ms) - 1_000, int(end + skew_ms) + 2_000


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("run_id", nargs="?", help="a run saved under .build/load/")
    which.add_argument("--minutes", type=float, help="report the last N minutes instead")
    parser.add_argument("--env-dir", default=DEFAULT_ENV_DIR, help="for --minutes")
    args = parser.parse_args(argv)

    if args.run_id:
        run = Run.load(args.run_id)
        target = connect(run.env_dir)
        print(format_run(run, summarise(target, *run_window(run, run.skew_ms or 0.0))))
        return

    target = connect(args.env_dir)
    end = int(time.time() * 1000)
    summary = summarise(target, end - int(args.minutes * 60_000), end)
    where = "LocalStack" if target.is_local else "AWS"
    print(f"processor logs, last {args.minutes:g} minutes on {args.env_dir} ({where})")
    print("\n".join(format_summary(summary)))


if __name__ == "__main__":
    main()
