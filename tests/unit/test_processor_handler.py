import base64
import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace

import duckdb
import pytest

from processor import handler as handler_module
from processor.bars import HOUR_MS, MINUTE_MS
from processor.lake import LakeWriter
from processor.store import LATEST_SK, MarketStore, bar_sk

HOUR = 1_729_699_200_000  # 2024-10-23T16:00:00Z
NOW = HOUR + 2 * HOUR_MS
TTL_S = 7 * 86_400
PREFIX = "processed/bars_1m"


def tick(symbol: str, price: float, offset_ms: int) -> dict:
    return {"symbol": symbol, "price": price, "ts": HOUR + offset_ms, "source": "test"}


def kinesis_record(sequence: int, *ticks: dict, data: bytes | None = None) -> dict:
    """A record as Lambda delivers it from Kinesis: base64 of the producer's bytes."""
    if data is None:
        data = "".join(json.dumps(t) + "\n" for t in ticks).encode("utf-8")
    return {
        "eventSource": "aws:kinesis",
        "kinesis": {
            "data": base64.b64encode(data).decode("ascii"),
            "sequenceNumber": str(sequence),
            "partitionKey": ticks[0]["symbol"] if ticks else "x",
        },
    }


def event(*records: dict) -> dict:
    return {"Records": list(records)}


@pytest.fixture
def store(market_table):
    return MarketStore(market_table, bar_ttl_s=TTL_S)


@pytest.fixture
def lake(s3, lake_bucket):
    return LakeWriter(s3, lake_bucket, PREFIX)


def process(evt, store, lake, now_ms=NOW):
    return handler_module.process(evt, store, lake, now_ms=now_ms, max_age_ms=TTL_S * 1000)


def get(table, sk, symbol="BTCUSDT"):
    return table.get_item(Key={"symbol": symbol, "sk": sk}).get("Item")


def lake_keys(s3, bucket):
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=bucket).get("Contents", []))


def read_hour(s3, bucket, key, tmp_path):
    local = tmp_path / key.replace("/", "_")
    s3.download_file(bucket, key, str(local))
    with duckdb.connect() as con:
        return con.execute(
            "SELECT bar_start, open, high, low, close, tick_count FROM read_parquet(?)",
            [str(local)],
        ).fetchall()


BATCH = event(
    kinesis_record(1, tick("BTCUSDT", 100.0, 1_000)),
    kinesis_record(2, tick("ETHUSDT", 10.0, 2_000)),
    kinesis_record(3, tick("BTCUSDT", 104.0, 20_000)),
    kinesis_record(4, tick("BTCUSDT", 98.0, 40_000)),
    kinesis_record(5, tick("BTCUSDT", 101.0, MINUTE_MS + 1_000)),
)


# ---- the happy path ----------------------------------------------------------


def test_batch_lands_in_dynamodb_and_the_lake(store, lake, market_table, s3, lake_bucket, tmp_path):
    assert process(BATCH, store, lake) == {"batchItemFailures": []}

    first = get(market_table, bar_sk(HOUR))
    assert (first["open"], first["high"], first["low"], first["close"]) == (
        Decimal("100.0"),
        Decimal("104.0"),
        Decimal("98.0"),
        Decimal("98.0"),
    )
    assert first["tick_count"] == 3
    assert first["expires_at"] == HOUR // 1000 + TTL_S
    latest = get(market_table, LATEST_SK)
    assert (latest["price"], latest["ts"]) == (Decimal("101.0"), HOUR + MINUTE_MS + 1_000)
    assert get(market_table, LATEST_SK, symbol="ETHUSDT")["price"] == Decimal("10.0")

    btc_key = f"{PREFIX}/symbol=BTCUSDT/dt=2024-10-23/hour=16/bars.parquet"
    assert lake_keys(s3, lake_bucket) == [
        btc_key,
        f"{PREFIX}/symbol=ETHUSDT/dt=2024-10-23/hour=16/bars.parquet",
    ]
    assert read_hour(s3, lake_bucket, btc_key, tmp_path) == [
        (dt.datetime(2024, 10, 23, 16, 0), 100.0, 104.0, 98.0, 98.0, 3),
        (dt.datetime(2024, 10, 23, 16, 1), 101.0, 101.0, 101.0, 101.0, 1),
    ]


def test_bars_crossing_an_hour_write_one_file_per_hour(store, lake, s3, lake_bucket):
    process(
        event(
            kinesis_record(1, tick("BTCUSDT", 1.0, HOUR_MS - 1_000)),
            kinesis_record(2, tick("BTCUSDT", 2.0, HOUR_MS + 1_000)),
        ),
        store,
        lake,
    )

    assert lake_keys(s3, lake_bucket) == [
        f"{PREFIX}/symbol=BTCUSDT/dt=2024-10-23/hour=16/bars.parquet",
        f"{PREFIX}/symbol=BTCUSDT/dt=2024-10-23/hour=17/bars.parquet",
    ]


# ---- idempotency ---------------------------------------------------------------


def test_replaying_a_batch_changes_nothing(store, lake, market_table, s3, lake_bucket, tmp_path):
    process(BATCH, store, lake)
    before = market_table.scan()["Items"]
    key = lake_keys(s3, lake_bucket)[0]
    rows_before = read_hour(s3, lake_bucket, key, tmp_path)

    # Kinesis retries deliver the same records again, in full or from a checkpoint.
    process(BATCH, store, lake)
    process(event(*BATCH["Records"][2:]), store, lake)

    assert market_table.scan()["Items"] == before  # versions unchanged: no rewrites
    assert len(lake_keys(s3, lake_bucket)) == 2
    assert read_hour(s3, lake_bucket, key, tmp_path) == rows_before


def test_a_minute_split_across_batches_merges(store, lake, market_table, s3, lake_bucket, tmp_path):
    process(event(kinesis_record(7, tick("BTCUSDT", 105.0, 30_000))), store, lake)
    process(event(kinesis_record(9, tick("BTCUSDT", 95.0, 10_000))), store, lake)

    item = get(market_table, bar_sk(HOUR))
    assert (item["open"], item["close"], item["low"], item["tick_count"]) == (
        Decimal("95.0"),
        Decimal("105.0"),
        Decimal("95.0"),
        2,
    )
    key = lake_keys(s3, lake_bucket)[0]
    assert read_hour(s3, lake_bucket, key, tmp_path) == [
        (dt.datetime(2024, 10, 23, 16, 0), 95.0, 105.0, 95.0, 105.0, 2)
    ]
    # The later batch carried an older tick, so the latest price stays put.
    assert get(market_table, LATEST_SK)["price"] == Decimal("105.0")


def emf_lines(capsys):
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    return [line for line in lines if "_aws" in line]


def test_batch_emits_tick_lag_for_its_oldest_tick(store, lake, capsys, monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "market-pipeline-dev-processor")

    process(BATCH, store, lake)

    [doc] = emf_lines(capsys)
    assert doc["TickLagMs"] == NOW - (HOUR + 1_000)
    assert doc["FunctionName"] == "market-pipeline-dev-processor"
    assert doc["_aws"]["Timestamp"] == NOW
    [directive] = doc["_aws"]["CloudWatchMetrics"]
    assert directive == {
        "Namespace": "MarketPipeline",
        "Dimensions": [["FunctionName"]],
        "Metrics": [{"Name": "TickLagMs", "Unit": "Milliseconds"}],
    }


# ---- records that are dropped -------------------------------------------------


def test_malformed_records_are_dropped_not_retried(store, lake, market_table):
    result = process(
        event(
            kinesis_record(1, data=b"not json\n"),
            kinesis_record(2, tick("BTCUSDT", 100.0, 0)),
            kinesis_record(3, data=b'{"symbol": "BTCUSDT", "price": -1, "ts": 1}\n'),
        ),
        store,
        lake,
    )

    assert result == {"batchItemFailures": []}
    assert get(market_table, bar_sk(HOUR))["tick_count"] == 1


def test_ticks_from_expired_hours_are_dropped(store, lake, market_table, s3, lake_bucket):
    late_now = HOUR + TTL_S * 1000  # the first bar of HOUR expires now

    process(event(kinesis_record(1, tick("BTCUSDT", 1.0, 30 * MINUTE_MS))), store, lake, late_now)

    assert market_table.scan()["Items"] == []
    assert lake_keys(s3, lake_bucket) == []

    process(event(kinesis_record(2, tick("BTCUSDT", 1.0, HOUR_MS))), store, lake, late_now)
    assert get(market_table, bar_sk(HOUR + HOUR_MS)) is not None


def test_empty_batch():
    assert process({"Records": []}, None, None) == {"batchItemFailures": []}


# ---- partial batch responses --------------------------------------------------


class FailingLake:
    """Delegates to a real writer but raises for the given symbols."""

    def __init__(self, lake, failing):
        self.lake = lake
        self.failing = set(failing)

    def write_hour(self, symbol, hour_start, bars):
        if symbol in self.failing:
            raise RuntimeError(f"S3 is down for {symbol}")
        return self.lake.write_hour(symbol, hour_start, bars)


def test_failed_symbol_reports_its_earliest_record(store, lake, market_table):
    result = process(BATCH, store, FailingLake(lake, {"ETHUSDT"}))

    assert result == {"batchItemFailures": [{"itemIdentifier": "2"}]}
    assert get(market_table, LATEST_SK)["price"] == Decimal("101.0")  # BTC still landed


def test_tick_lag_ignores_failed_symbols(store, lake, capsys):
    process(BATCH, store, FailingLake(lake, {"BTCUSDT"}))

    [doc] = emf_lines(capsys)
    assert doc["TickLagMs"] == NOW - (HOUR + 2_000)  # ETHUSDT's tick, not BTC's older one


def test_no_tick_lag_when_nothing_was_written(store, lake, capsys):
    process(BATCH, store, FailingLake(lake, {"BTCUSDT", "ETHUSDT"}))

    assert emf_lines(capsys) == []


def test_lowest_failed_sequence_is_reported(store, lake):
    big = 49_650_000_000_000_000_000_000_000_000_000_000_000_000_000_000  # Kinesis-sized numbers
    evt = event(
        kinesis_record(big + 10, tick("ETHUSDT", 1.0, 0)),
        kinesis_record(big + 9, tick("BTCUSDT", 1.0, 0)),
        kinesis_record(big + 11, tick("SOLUSDT", 1.0, 0)),
    )

    result = process(evt, store, FailingLake(lake, {"ETHUSDT", "BTCUSDT"}))

    assert result == {"batchItemFailures": [{"itemIdentifier": str(big + 9)}]}


def test_retry_after_a_failure_completes_the_symbol(store, lake, market_table, s3, lake_bucket):
    process(BATCH, store, FailingLake(lake, {"ETHUSDT"}))

    # Lambda resumes from sequence 2 and re-delivers everything after it.
    assert process(event(*BATCH["Records"][1:]), store, lake) == {"batchItemFailures": []}

    assert get(market_table, bar_sk(HOUR))["tick_count"] == 3
    assert len(lake_keys(s3, lake_bucket)) == 2


# ---- lambda_handler wiring ----------------------------------------------------


def test_lambda_handler_reads_settings_from_env(market_table, lake_bucket, monkeypatch, s3):
    monkeypatch.setenv("TABLE_NAME", market_table.name)
    monkeypatch.setenv("LAKE_BUCKET", lake_bucket)
    monkeypatch.setattr(handler_module, "_deps", None)
    monkeypatch.setattr(handler_module, "time", SimpleNamespace(time=lambda: NOW / 1000))

    result = handler_module.lambda_handler(BATCH, None)

    assert result == {"batchItemFailures": []}
    assert len(lake_keys(s3, lake_bucket)) == 2
    assert get(market_table, LATEST_SK)["price"] == Decimal("101.0")
