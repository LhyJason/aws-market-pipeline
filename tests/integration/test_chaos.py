"""Chaos: break one part of a running stack at a time and check that the rest copes.

| Injected failure                          | Expected                                         |
| ----------------------------------------- | ------------------------------------------------ |
| poison records on the stream              | dropped and logged; good ticks still land        |
| a symbol whose stored bar is corrupt      | its batch goes to the DLQ; other symbols flow on |
| the API's table missing (DynamoDB errors) | 503 from the data routes; /health stays up       |
| a bad API deploy (no such handler)        | 502 from API Gateway until it is rolled back     |

Every injection is undone in a ``finally`` block, and the test symbols are
deleted afterwards, so the stack is left as it was found. The symbols are
unique per run and outside the producer's list, so the API never serves them
and the assertions read DynamoDB directly.

These change a live stack, so they run against LocalStack only, unless
ALLOW_CHAOS_ON_AWS=1.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from boto3.dynamodb.conditions import Key

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

RUN = uuid.uuid4().hex[:8].upper()
MINUTE_MS = 60_000
DLQ_TIMEOUT_S = 300  # LocalStack retries at once; AWS backs off between retries
# The API 5xx alarm fires on 5 in five minutes (api_5xx_threshold); each API
# test sends that many on its own, so on AWS either one trips it.
ALARM_5XX = 5


@pytest.fixture(scope="module", autouse=True)
def _local_only(stack) -> None:
    if stack.endpoint_url is None and os.environ.get("ALLOW_CHAOS_ON_AWS") != "1":
        pytest.skip("chaos tests change the stack; set ALLOW_CHAOS_ON_AWS=1 to run them on AWS")


@pytest.fixture(scope="module")
def table(stack, outputs):
    return stack.session.resource("dynamodb", endpoint_url=stack.endpoint_url).Table(
        outputs["market_table"]
    )


@pytest.fixture
def symbols(stack, outputs, table) -> Iterator[list[str]]:
    """Test symbols to create; their items and Parquet files are deleted afterwards."""
    created: list[str] = []
    yield created
    s3 = stack.client("s3")
    for symbol in created:
        for item in table.query(KeyConditionExpression=Key("symbol").eq(symbol))["Items"]:
            table.delete_item(Key={"symbol": symbol, "sk": item["sk"]})
        prefix = f"processed/bars_1m/symbol={symbol}/"
        listing = s3.list_objects_v2(Bucket=outputs["lake_bucket"], Prefix=prefix)
        for obj in listing.get("Contents", []):
            s3.delete_object(Bucket=outputs["lake_bucket"], Key=obj["Key"])


def tick(symbol: str, price: float, ts: int) -> bytes:
    """A record exactly as the producer writes it: one JSON line."""
    line = {"symbol": symbol, "price": price, "ts": ts, "source": "chaos"}
    return (json.dumps(line) + "\n").encode("utf-8")


def put(stack, *records: bytes) -> list[str]:
    """Put records on the stream in order; return their sequence numbers."""
    response = stack.client("kinesis").put_records(
        StreamName=stack.stream_name,
        Records=[{"Data": data, "PartitionKey": f"chaos-{i}"} for i, data in enumerate(records)],
    )
    assert response["FailedRecordCount"] == 0, response
    return [record["SequenceNumber"] for record in response["Records"]]


def landed(table, symbol: str, price: float):
    """A probe for wait_for: the symbol's LATEST item once it holds ``price``."""

    def probe():
        item = table.get_item(Key={"symbol": symbol, "sk": "LATEST"}).get("Item")
        return item if item and float(item["price"]) == price else None

    return probe


def now_ms() -> int:
    return int(time.time() * 1000)


def json_lines(events: list[dict]) -> Iterator[dict]:
    """The JSON log lines in CloudWatch Logs events.

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
            yield obj
            while pos < len(text) and text[pos].isspace():
                pos += 1


# ---- the stream ----------------------------------------------------------------


def test_poison_records_are_dropped_and_good_ticks_land(stack, outputs, table, symbols, wait_for):
    good = f"CHAOS-OK-{RUN}"
    symbols.append(good)
    started = now_ms()
    poison = [
        b"\x00\xff not json at all\n",
        b"[1, 2, 3]\n",
        b'{"symbol": "' + good.encode() + b'"}\n',  # no price or ts
        tick(good, -5.0, started),  # a negative price
        tick("lower-case", 1.0, started),  # not a valid symbol
    ]

    *poison_seqs, _ = put(stack, *poison, tick(good, 101.0, started))

    wait_for(landed(table, good, 101.0), timeout_s=60)

    # Each poison record was seen and dropped by the processor, not lost.
    logs = stack.client("logs")

    def dropped():
        events = logs.filter_log_events(
            logGroupName=f"/aws/lambda/{outputs['processor_function_name']}",
            startTime=started - 5_000,
        )["events"]
        seqs = {
            line.get("sequence_number")
            for line in json_lines(events)
            if line.get("msg") == "dropping malformed record"
        }
        return seqs if seqs >= set(poison_seqs) else None

    wait_for(dropped, timeout_s=60)
    # Retrying a malformed record can never succeed, so none reaches the DLQ.
    assert dlq_messages_for(stack, outputs, poison_seqs, wait_s=5) == []


def dlq_messages_for(stack, outputs, sequences: list[str], *, wait_s: int) -> list[dict]:
    """DLQ messages whose batch covers any of ``sequences``; those are deleted."""
    sqs = stack.client("sqs")
    found: list[dict] = []
    deadline = time.monotonic() + wait_s
    while not found and time.monotonic() < deadline:
        messages = sqs.receive_message(
            QueueUrl=outputs["processor_dlq_url"],
            MaxNumberOfMessages=10,
            WaitTimeSeconds=min(5, wait_s),
            VisibilityTimeout=2,
        ).get("Messages", [])
        for message in messages:
            body = json.loads(message["Body"])
            batch = body["KinesisBatchInfo"]
            start, end = int(batch["startSequenceNumber"]), int(batch["endSequenceNumber"])
            if any(start <= int(seq) <= end for seq in sequences):
                found.append(body)
                sqs.delete_message(
                    QueueUrl=outputs["processor_dlq_url"], ReceiptHandle=message["ReceiptHandle"]
                )
    return found


def test_a_failing_symbol_goes_to_the_dlq_and_others_keep_flowing(
    stack, outputs, table, symbols, wait_for
):
    bad, good = f"CHAOS-BAD-{RUN}", f"CHAOS-OK-{RUN}"
    symbols.extend([bad, good])
    minute = now_ms() // MINUTE_MS * MINUTE_MS
    # A bar left behind by a broken writer: the processor cannot merge into
    # it, so every attempt at this symbol raises.
    table.put_item(Item={"symbol": bad, "sk": f"BAR#{minute:013d}"})

    bad_seq, _ = put(stack, tick(bad, 1.0, minute + 1_000), tick(good, 202.0, minute + 1_000))

    # The good symbol in the same batch is written on the first attempt.
    wait_for(landed(table, good, 202.0), timeout_s=60)

    # The failing batch is retried, bisected, and finally sent to the DLQ as
    # metadata naming its sequence range, so it can be replayed.
    [failure] = dlq_messages_for(stack, outputs, [bad_seq], wait_s=DLQ_TIMEOUT_S)
    assert failure["requestContext"]["condition"] == "RetryAttemptsExhausted"
    assert failure["KinesisBatchInfo"]["streamArn"].endswith(f"/{stack.stream_name}")

    # The shard is unblocked: records after the failed batch are processed.
    put(stack, tick(good, 203.0, now_ms()))
    wait_for(landed(table, good, 203.0), timeout_s=60)
    assert table.get_item(Key={"symbol": bad, "sk": "LATEST"}).get("Item") is None


# ---- the API -------------------------------------------------------------------


@contextmanager
def reconfigured(stack, function: str, **changes: Any) -> Iterator[None]:
    """Change a Lambda's configuration for the duration of the block."""
    lam = stack.client("lambda")
    waiter = lam.get_waiter("function_updated_v2")
    before = lam.get_function_configuration(FunctionName=function)
    original = {
        "Handler": before["Handler"],
        "Environment": {"Variables": before["Environment"]["Variables"]},
    }
    lam.update_function_configuration(FunctionName=function, **changes)
    waiter.wait(FunctionName=function)
    try:
        yield
    finally:
        lam.update_function_configuration(FunctionName=function, **original)
        waiter.wait(FunctionName=function)


def recovered(stack):
    def probe():
        response = stack.get(f"/prices/{stack.symbol}")
        return response if response.status == 200 else None

    return probe


def test_the_api_returns_503_when_dynamodb_fails(stack, outputs, wait_for):
    function = outputs["api_function_name"]
    variables = stack.client("lambda").get_function_configuration(FunctionName=function)[
        "Environment"
    ]["Variables"]
    missing = {**variables, "TABLE_NAME": f"{outputs['market_table']}-missing-{RUN.lower()}"}

    with reconfigured(stack, function, Environment={"Variables": missing}):
        prices = [stack.get(f"/prices/{stack.symbol}") for _ in range(ALARM_5XX - 1)]
        bars = stack.get(f"/bars/{stack.symbol}")
        health = stack.get("/health", key=None)

    assert [response.status for response in prices] == [503] * (ALARM_5XX - 1)
    assert prices[0].body == {"detail": "market data is temporarily unavailable"}
    assert bars.status == 503
    assert health.status == 200  # reads nothing, so it stays up
    wait_for(recovered(stack), timeout_s=60)


def test_a_bad_deploy_is_a_502_until_rolled_back(stack, outputs, wait_for):
    with reconfigured(stack, outputs["api_function_name"], Handler="api.app.missing"):
        prices = [stack.get(f"/prices/{stack.symbol}") for _ in range(ALARM_5XX - 1)]
        health = stack.get("/health", key=None)

    # API Gateway answers for the broken Lambda; the deploy workflow's smoke
    # test fails on exactly this, and on AWS the API 5xx alarm fires.
    assert [response.status for response in prices] == [502] * (ALARM_5XX - 1)
    assert health.status == 502
    wait_for(recovered(stack), timeout_s=60)
