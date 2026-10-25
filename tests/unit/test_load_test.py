import json
import random

import boto3
import pytest

import load_report
import load_test
from load_test import PutStats, TickSource

STREAM = "ticks"
PRODUCER = "market-pipeline-local-producer"


def decoded(record: dict) -> dict:
    data = record["Data"]
    assert data.endswith(b"\n")
    return json.loads(data)


def test_tick_source_rotates_symbols_in_the_producers_format():
    source = TickSource(["A", "B"], clock=lambda: 1_000, rng=random.Random(1))
    records = [source() for _ in range(4)]
    ticks = [decoded(r) for r in records]
    assert [t["symbol"] for t in ticks] == ["A", "B", "A", "B"]
    assert [r["PartitionKey"] for r in records] == ["A", "B", "A", "B"]
    assert {t["source"] for t in ticks} == {"loadtest"}
    assert set(ticks[0]) == {"symbol", "price", "ts", "source"}
    assert all(t["price"] > 0 for t in ticks)


def test_tick_source_never_repeats_a_millisecond_for_a_symbol():
    # The processor counts a tick once per (symbol, ts), so a repeat would look lost.
    source = TickSource(["A", "B"], clock=lambda: 5_000)
    ticks = [decoded(source()) for _ in range(6)]
    assert [t["ts"] for t in ticks if t["symbol"] == "A"] == [5_000, 5_001, 5_002]
    assert [t["ts"] for t in ticks if t["symbol"] == "B"] == [5_000, 5_001, 5_002]


def test_tick_source_follows_the_clock_when_it_moves_on():
    now = iter([1_000, 9_000])
    source = TickSource(["A"], clock=lambda: next(now))
    assert [decoded(source())["ts"] for _ in range(2)] == [1_000, 9_000]


@pytest.mark.parametrize(
    ("elapsed_s", "expected"),
    [(0.0, 0), (0.05, 2), (1.0, 50), (2.5, 100), (100.0, 100)],
)
def test_due(elapsed_s, expected):
    assert load_test.due(50, elapsed_s, total=100) == expected


class FlakyKinesis:
    """Rejects the first ``fail`` records it is ever sent, like a throttled shard."""

    def __init__(self, fail: int = 0):
        self.fail = fail
        self.calls: list[int] = []

    def put_records(self, StreamName, Records):
        self.calls.append(len(Records))
        outcomes = []
        for i, _ in enumerate(Records):
            if self.fail > 0:
                self.fail -= 1
                outcomes.append({"ErrorCode": "ProvisionedThroughputExceededException"})
            else:
                outcomes.append(
                    {"ShardId": "shard-0", "SequenceNumber": str(len(self.calls) * 1000 + i)}
                )
        return {"Records": outcomes}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(load_test.time, "sleep", lambda s: None)


def test_put_chunks_by_500_and_samples_a_few_records_for_the_clock():
    kinesis = FlakyKinesis()
    source = TickSource(["A"])
    stats = PutStats()
    load_test.put(kinesis, STREAM, [source() for _ in range(1200)], stats)
    assert kinesis.calls == [500, 500, 200]
    assert (stats.sent, stats.retries, stats.dropped) == (1200, 0, 0)
    assert len(stats.samples) == load_test.SKEW_SAMPLES
    assert stats.samples[0][0] == "shard-0"


def test_put_retries_rejected_records():
    kinesis = FlakyKinesis(fail=3)
    stats = PutStats()
    load_test.put(kinesis, STREAM, [TickSource(["A"])() for _ in range(10)], stats)
    assert kinesis.calls == [10, 3]
    assert (stats.sent, stats.retries, stats.dropped) == (10, 3, 0)


def test_put_gives_up_after_the_last_attempt():
    kinesis = FlakyKinesis(fail=1_000)
    stats = PutStats()
    load_test.put(kinesis, STREAM, [TickSource(["A"])() for _ in range(2)], stats)
    assert len(kinesis.calls) == load_test.MAX_PUT_ATTEMPTS
    assert (stats.sent, stats.retries, stats.dropped) == (
        0,
        2 * (load_test.MAX_PUT_ATTEMPTS - 1),
        2,
    )


def test_send_paces_the_whole_run(monkeypatch):
    clock = iter(x * 0.25 for x in range(100))
    monkeypatch.setattr(load_test.time, "monotonic", lambda: next(clock))
    kinesis = FlakyKinesis()
    stats, started, sent_at = load_test.send(kinesis, STREAM, TickSource(["A"]), 40, 1.0)
    # 0.25 s apart: 10, 10, 10, 10 ticks, and nothing at the start.
    assert kinesis.calls == [10, 10, 10, 10]
    assert stats.sent == 40
    assert started <= sent_at


# ---- against moto -----------------------------------------------------------------


@pytest.fixture
def target(aws, market_table, lake_bucket):
    session = boto3.Session(region_name="us-east-1")
    outputs = {
        "stream_name": STREAM,
        "market_table": market_table.name,
        "lake_bucket": lake_bucket,
        "producer_function_name": PRODUCER,
        "processor_function_name": "processor",
    }
    session.client("kinesis").create_stream(StreamName=STREAM, ShardCount=1)
    return load_report.Target("infra/envs/dev", outputs, session, None)


def test_clock_skew_compares_kinesis_arrival_with_the_send_time(target):
    kinesis = target.client("kinesis")
    late = load_test.now_ms() - 2_000  # as if the generator's clock ran 2 s behind
    tick = {"symbol": "A", "price": 1.0, "ts": late, "source": "loadtest"}
    response = kinesis.put_records(
        StreamName=STREAM, Records=[{"Data": json.dumps(tick).encode(), "PartitionKey": "A"}]
    )
    [outcome] = response["Records"]
    skew = load_test.clock_skew_ms(
        kinesis, STREAM, [(outcome["ShardId"], outcome["SequenceNumber"], late)]
    )
    assert 1_500 < skew < 10_000
    assert load_test.clock_skew_ms(kinesis, STREAM, []) is None


def test_landed_ticks_sums_tick_count_over_bars_only(market_table):
    market_table.put_item(Item={"symbol": "A", "sk": "LATEST", "price": 1})
    market_table.put_item(Item={"symbol": "A", "sk": "BAR#0000000060000", "tick_count": 3})
    market_table.put_item(Item={"symbol": "A", "sk": "BAR#0000000120000", "tick_count": 4})
    market_table.put_item(Item={"symbol": "B", "sk": "BAR#0000000060000", "tick_count": 9})
    assert load_test.landed_ticks(market_table, "A") == 7
    assert load_test.landed_ticks(market_table, "C") == 0


def test_landed_ticks_follows_pages():
    class Table:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            if "ExclusiveStartKey" in kwargs:
                return {"Items": [{"tick_count": 2}]}
            return {"Items": [{"tick_count": 1}], "LastEvaluatedKey": {"sk": "x"}}

    table = Table()
    assert load_test.landed_ticks(table, "A") == 3
    assert table.calls[1]["ExclusiveStartKey"] == {"sk": "x"}


def test_wait_landed(market_table):
    market_table.put_item(Item={"symbol": "A", "sk": "BAR#0000000060000", "tick_count": 5})
    landed, at = load_test.wait_landed(market_table, ["A"], 5, timeout_s=0)
    assert landed == 5
    assert at is not None
    assert load_test.wait_landed(market_table, ["A"], 6, timeout_s=0) == (5, None)


def test_wait_landed_keeps_waiting_while_the_backlog_drains(monkeypatch):
    # Ten seconds a poll against a 15 s timeout: a count that keeps growing
    # outlasts it, and one that stalls gives up two polls later.
    clock = iter(x * 10.0 for x in range(100))
    monkeypatch.setattr(load_test.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(load_test.time, "sleep", lambda _: None)
    counts = iter([10, 20, 30, 40, 40, 40, 40])
    monkeypatch.setattr(load_test, "landed_ticks", lambda table, symbol: next(counts))
    assert load_test.wait_landed(None, ["A"], 50, timeout_s=15) == (40, None)
    assert next(counts) == 40  # gave up on the sixth poll, not the second


def test_wait_logged_waits_until_the_logs_account_for_every_landed_tick(monkeypatch):
    run = load_report.Run(
        run_id="R",
        env_dir="infra/envs/local",
        local=True,
        rate=10,
        duration_s=1,
        symbols=["A"],
        sent=10,
        put_retries=0,
        put_dropped=0,
        started_ms=1_000,
        sent_ms=2_000,
        landed=10,
        landed_ms=3_000,
        skew_ms=None,
    )
    seen = iter([4, 10])
    windows = []

    def summarise(target, start, end):
        windows.append((start, end))
        return load_report.Summary.of([load_report.Batch(1, next(seen), 0, 0, 5)], [])

    monkeypatch.setattr(load_report, "summarise", summarise)
    assert load_test.wait_logged(None, run).records == 10
    assert windows == [(0, 5_000), (0, 5_000)]


def test_schedule_is_paused_for_the_block_and_restored(target):
    events = target.client("events")
    rule = f"{PRODUCER}-schedule"
    events.put_rule(Name=rule, ScheduleExpression="rate(1 minute)", State="ENABLED")

    with load_test.schedule_paused(target, keep=False):
        assert events.describe_rule(Name=rule)["State"] == "DISABLED"
    assert events.describe_rule(Name=rule)["State"] == "ENABLED"

    with pytest.raises(RuntimeError), load_test.schedule_paused(target, keep=False):
        raise RuntimeError("the run failed")
    assert events.describe_rule(Name=rule)["State"] == "ENABLED"

    with load_test.schedule_paused(target, keep=True):
        assert events.describe_rule(Name=rule)["State"] == "ENABLED"


def test_a_disabled_schedule_stays_disabled(target):
    events = target.client("events")
    rule = f"{PRODUCER}-schedule"
    events.put_rule(Name=rule, ScheduleExpression="rate(1 minute)", State="DISABLED")
    with load_test.schedule_paused(target, keep=False):
        pass
    assert events.describe_rule(Name=rule)["State"] == "DISABLED"


def test_delete_symbols_removes_items_and_parquet_of_the_test_symbols_only(
    target, market_table, s3
):
    for symbol in ("LOAD-X-0", "BTCUSDT"):
        market_table.put_item(Item={"symbol": symbol, "sk": "LATEST"})
        market_table.put_item(Item={"symbol": symbol, "sk": "BAR#0000000060000"})
        key = f"processed/bars_1m/symbol={symbol}/dt=2026-10-24/hour=01/bars.parquet"
        s3.put_object(Bucket=target.outputs["lake_bucket"], Key=key, Body=b"x")

    load_test.delete_symbols(target, ["LOAD-X-0"])

    remaining = {item["symbol"] for item in market_table.scan()["Items"]}
    assert remaining == {"BTCUSDT"}
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=target.outputs["lake_bucket"])["Contents"]]
    assert all("symbol=BTCUSDT" in key for key in keys)


# ---- the command line -------------------------------------------------------------


@pytest.mark.parametrize("argv", [["--rate", "0"], ["--rate", "1001"], ["--duration", "0"]])
def test_main_rejects_bad_arguments(argv):
    with pytest.raises(SystemExit):
        load_test.main(argv)


def test_main_refuses_aws_without_the_flag(monkeypatch):
    monkeypatch.delenv("ALLOW_LOAD_ON_AWS", raising=False)
    aws_target = load_report.Target(
        "infra/envs/dev", {}, boto3.Session(region_name="us-east-1"), None
    )
    monkeypatch.setattr(load_report, "connect", lambda env_dir: aws_target)
    with pytest.raises(SystemExit, match="ALLOW_LOAD_ON_AWS"):
        load_test.main(["--env-dir", "infra/envs/dev"])
