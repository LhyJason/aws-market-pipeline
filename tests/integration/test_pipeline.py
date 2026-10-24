"""End to end: invoke the producer, then find its tick on the stream and in the API.

producer Lambda -> Kinesis -> processor Lambda -> DynamoDB -> API Gateway -> API Lambda
"""

import json
import time
from datetime import UTC, datetime

import pytest

from api import models

pytestmark = pytest.mark.integration

MINUTE_MS = 60_000
CLOCK_SLACK_MS = 2_000  # the producer's clock against this machine's


@pytest.fixture(scope="module")
def produced(stack) -> int:
    """Invoke the producer once; return a time just before its ticks were observed (epoch ms)."""
    before_ms = int(time.time() * 1000) - CLOCK_SLACK_MS
    response = stack.client("lambda").invoke(
        FunctionName=stack.producer_function, InvocationType="RequestResponse"
    )
    payload = response["Payload"].read()
    assert "FunctionError" not in response, payload
    result = json.loads(payload)
    assert result["ok"], result
    assert result["sent"] >= 1
    return before_ms


@pytest.fixture(scope="module")
def latest(stack, produced, wait_for) -> dict:
    """The /prices body once the processor has stored a tick from that invocation."""

    def newer():
        response = stack.get(f"/prices/{stack.symbol}")
        if response.status == 200 and response.body["ts"] >= produced:
            return response.body
        return None

    return wait_for(newer)


def test_the_tick_is_on_the_stream(stack, produced, wait_for):
    kinesis = stack.client("kinesis")
    shards = kinesis.list_shards(StreamName=stack.stream_name)["Shards"]

    def find():
        for shard in shards:
            iterator = kinesis.get_shard_iterator(
                StreamName=stack.stream_name,
                ShardId=shard["ShardId"],
                ShardIteratorType="AT_TIMESTAMP",
                Timestamp=datetime.fromtimestamp(produced / 1000, UTC),
            )["ShardIterator"]
            for record in kinesis.get_records(ShardIterator=iterator, Limit=1000)["Records"]:
                for line in record["Data"].decode("utf-8").splitlines():
                    tick = json.loads(line)
                    if tick["symbol"] == stack.symbol and tick["ts"] >= produced:
                        return tick
        return None

    tick = wait_for(find, timeout_s=30)

    assert set(tick) >= {"symbol", "price", "ts", "source"}
    assert tick["price"] > 0


def test_the_api_serves_the_new_tick_as_latest_price(stack, latest):
    price = models.Price.model_validate(latest)

    assert price.symbol == stack.symbol
    assert price.stale is False
    assert price.age_s < 120


def test_the_api_serves_the_new_bar(stack, latest, wait_for):
    minute = latest["ts"] - latest["ts"] % MINUTE_MS

    def bar():
        response = stack.get(
            f"/bars/{stack.symbol}",
            params={
                "start": datetime.fromtimestamp(minute / 1000, UTC).isoformat(),
                "end": datetime.fromtimestamp((minute + MINUTE_MS) / 1000, UTC).isoformat(),
            },
        )
        if response.status == 200 and response.body["count"] == 1:
            return response.body
        return None

    bars = models.Bars.model_validate(wait_for(bar))

    (only,) = bars.bars
    assert only.start == minute
    assert only.low <= latest["price"] <= only.high
    assert only.tick_count >= 1
    assert set(only.flags) <= {"spike", "gap"}
