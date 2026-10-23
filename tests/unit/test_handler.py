import json

import boto3
import pytest
from moto import mock_aws

from producer.config import Settings
from producer.feed import Tick
from producer.handler import KINESIS_MAX_BATCH, publish, run

TS = 1_729_700_000_000


def make_ticks(n: int) -> list[Tick]:
    return [Tick(symbol=f"SYM{i}", price=float(i), ts=TS, source="test") for i in range(n)]


class StaticFeed:
    def __init__(self, ticks):
        self.ticks = ticks
        self.requested = None

    def fetch(self, symbols):
        self.requested = symbols
        return [t for t in self.ticks if t.symbol in symbols]


class FakeKinesis:
    """Records batch sizes; optionally fails the first record of the first call."""

    def __init__(self, fail_first_record_once: bool = False):
        self.fail_first_record_once = fail_first_record_once
        self.batch_sizes: list[int] = []

    def put_records(self, *, StreamName, Records):
        self.batch_sizes.append(len(Records))
        outcomes = [
            {"SequenceNumber": str(i), "ShardId": "shardId-000000000000"}
            for i in range(len(Records))
        ]
        failed = 0
        if self.fail_first_record_once:
            self.fail_first_record_once = False
            outcomes[0] = {
                "ErrorCode": "ProvisionedThroughputExceededException",
                "ErrorMessage": "slow down",
            }
            failed = 1
        return {"FailedRecordCount": failed, "Records": outcomes}


class AlwaysFailingKinesis:
    def put_records(self, *, StreamName, Records):
        return {
            "FailedRecordCount": len(Records),
            "Records": [{"ErrorCode": "InternalFailure", "ErrorMessage": "x"} for _ in Records],
        }


@pytest.fixture
def kinesis():
    with mock_aws():
        client = boto3.client("kinesis", region_name="us-east-1")
        client.create_stream(StreamName="ticks", ShardCount=1)
        yield client


def read_stream(client, stream_name):
    records = []
    for shard in client.list_shards(StreamName=stream_name)["Shards"]:
        iterator = client.get_shard_iterator(
            StreamName=stream_name,
            ShardId=shard["ShardId"],
            ShardIteratorType="TRIM_HORIZON",
        )["ShardIterator"]
        records.extend(client.get_records(ShardIterator=iterator)["Records"])
    return records


# ---- run(): end to end against a mocked stream ------------------------------


def test_run_publishes_requested_symbols(kinesis):
    ticks = [
        Tick("BTCUSDT", 50000.5, TS, "test"),
        Tick("ETHUSDT", 3000.25, TS, "test"),
        Tick("DOGEUSDT", 0.1, TS, "test"),
    ]
    settings = Settings(stream_name="ticks", symbols=("BTCUSDT", "ETHUSDT"))
    feed = StaticFeed(ticks)

    result = run(settings, feed, kinesis)

    assert result == {"ok": True, "sent": 2, "failed": 0}
    assert feed.requested == ("BTCUSDT", "ETHUSDT")

    records = read_stream(kinesis, "ticks")
    by_symbol = {json.loads(r["Data"])["symbol"]: json.loads(r["Data"]) for r in records}
    assert set(by_symbol) == {"BTCUSDT", "ETHUSDT"}
    assert by_symbol["BTCUSDT"] == {
        "symbol": "BTCUSDT",
        "price": 50000.5,
        "ts": TS,
        "source": "test",
    }
    assert {r["PartitionKey"] for r in records} == {"BTCUSDT", "ETHUSDT"}


def test_run_reports_not_ok_when_records_never_land():
    settings = Settings(stream_name="ticks", symbols=("SYM0",))

    result = run(settings, StaticFeed(make_ticks(1)), AlwaysFailingKinesis())

    assert result == {"ok": False, "sent": 0, "failed": 1}


# ---- publish(): batching and retry semantics --------------------------------


def test_publish_with_no_ticks_makes_no_calls():
    client = FakeKinesis()

    assert publish([], "ticks", client) == {"sent": 0, "failed": 0}
    assert client.batch_sizes == []


def test_publish_splits_into_kinesis_sized_batches():
    client = FakeKinesis()

    result = publish(make_ticks(KINESIS_MAX_BATCH * 2 + 7), "ticks", client, backoff_s=0)

    assert result == {"sent": KINESIS_MAX_BATCH * 2 + 7, "failed": 0}
    assert client.batch_sizes == [KINESIS_MAX_BATCH, KINESIS_MAX_BATCH, 7]


def test_publish_retries_only_the_failed_records():
    client = FakeKinesis(fail_first_record_once=True)

    result = publish(make_ticks(3), "ticks", client, backoff_s=0)

    assert result == {"sent": 3, "failed": 0}
    assert client.batch_sizes == [3, 1]


def test_publish_gives_up_after_max_attempts():
    result = publish(make_ticks(2), "ticks", AlwaysFailingKinesis(), max_attempts=2, backoff_s=0)

    assert result == {"sent": 0, "failed": 2}
