import pytest

from api import store as api_store
from api.store import Latest, MarketReader, StoredBar
from processor import store as processor_store
from processor.bars import MINUTE_MS, Bar
from processor.records import Tick
from processor.store import MarketStore

HOUR = 1_729_699_200_000  # 2024-10-23T16:00:00Z
TTL_S = 7 * 86_400


def tick(price: float, offset_ms: int, symbol: str = "BTCUSDT") -> Tick:
    return Tick(symbol, price, HOUR + offset_ms, "test")


@pytest.fixture
def writer(market_table):
    return MarketStore(market_table, bar_ttl_s=TTL_S)


@pytest.fixture
def reader(market_table):
    return MarketReader(market_table)


@pytest.mark.parametrize("start", [0, 5, HOUR, HOUR + MINUTE_MS, 9_999_999_999_999])
def test_key_layout_matches_the_processor(start):
    # The API cannot import the processor in Lambda, so it keeps its own copy.
    assert api_store.bar_sk(start) == processor_store.bar_sk(start)
    assert api_store.LATEST_SK == processor_store.LATEST_SK
    assert api_store.BAR_SK_PREFIX == processor_store.BAR_SK_PREFIX


def test_latest_reads_what_the_processor_wrote(writer, reader):
    writer.put_latest(tick(67321.5, 1_000))

    assert reader.latest("BTCUSDT") == Latest("BTCUSDT", 67321.5, HOUR + 1_000, "test")
    assert reader.latest("ETHUSDT") is None


def test_recent_bars_reads_what_the_processor_wrote(writer, reader):
    writer.merge_bar(Bar.from_tick(tick(100.0, 10_000)))
    writer.merge_bar(Bar.from_tick(tick(90.0, 50_000)))

    (bar,) = reader.recent_bars("BTCUSDT", first=HOUR, last=HOUR, limit=10)

    assert bar == StoredBar(
        symbol="BTCUSDT",
        start=HOUR,
        open=100.0,
        high=100.0,
        low=90.0,
        close=90.0,
        tick_count=2,
        first_ts=HOUR + 10_000,
        last_ts=HOUR + 50_000,
    )


def test_recent_bars_returns_the_newest_in_range_oldest_first(writer, reader):
    for minute in range(10):
        writer.merge_bar(Bar.from_tick(tick(100.0 + minute, minute * MINUTE_MS)))
    writer.merge_bar(Bar.from_tick(tick(1.0, 5 * MINUTE_MS, symbol="ETHUSDT")))
    writer.put_latest(tick(1.0, 0))

    bars = reader.recent_bars(
        "BTCUSDT", first=HOUR + 2 * MINUTE_MS, last=HOUR + 7 * MINUTE_MS, limit=3
    )

    assert [b.start for b in bars] == [HOUR + m * MINUTE_MS for m in (5, 6, 7)]
    assert {b.symbol for b in bars} == {"BTCUSDT"}


@pytest.mark.parametrize(("first", "last", "limit"), [(HOUR, HOUR, 0), (HOUR + 1, HOUR, 5)])
def test_recent_bars_short_circuits_empty_requests(first, last, limit):
    class NoTable:
        def query(self, **kwargs):
            raise AssertionError("no read expected")

    assert MarketReader(NoTable()).recent_bars("BTCUSDT", first=first, last=last, limit=limit) == []


class RecordingTable:
    """Answers from canned pages and records every call. It has no scan()."""

    def __init__(self, pages=(), item=None):
        self.pages = list(pages)
        self.item = item
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(kwargs)
        return {"Item": self.item} if self.item else {}

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages.pop(0)


def item(minute: int) -> dict:
    start = HOUR + minute * MINUTE_MS
    return {
        "start": start,
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 1,
        "tick_count": 1,
        "first_ts": start,
        "last_ts": start,
    }


def test_recent_bars_reads_newest_first_without_processor_only_fields():
    table = RecordingTable(pages=[{"Items": [item(1), item(0)]}])

    bars = MarketReader(table).recent_bars("BTCUSDT", first=HOUR, last=HOUR + MINUTE_MS, limit=5)

    assert [b.start for b in bars] == [HOUR, HOUR + MINUTE_MS]
    (call,) = table.calls
    assert call["ScanIndexForward"] is False
    assert call["Limit"] == 5
    assert call["ExpressionAttributeValues"] == {
        ":pk": "BTCUSDT",
        ":first": "BAR#1729699200000",
        ":last": "BAR#1729699260000",
    }
    projected = set(call["ExpressionAttributeNames"].values())
    assert {"tick_ts", "version"}.isdisjoint(projected)
    assert "ConsistentRead" not in call  # eventually consistent: half the cost


def test_recent_bars_follows_pages_until_the_limit():
    table = RecordingTable(
        pages=[
            {"Items": [item(9), item(8)], "LastEvaluatedKey": {"sk": "BAR#8"}},
            {"Items": [item(7)], "LastEvaluatedKey": {"sk": "BAR#7"}},
        ]
    )

    bars = MarketReader(table).recent_bars("BTCUSDT", first=HOUR, last=HOUR + HOUR, limit=3)

    assert [b.start for b in bars] == [HOUR + m * MINUTE_MS for m in (7, 8, 9)]
    assert [c["Limit"] for c in table.calls] == [3, 1]
    assert table.calls[1]["ExclusiveStartKey"] == {"sk": "BAR#8"}


def test_latest_projects_only_its_fields():
    table = RecordingTable(item={"price": 1, "ts": HOUR, "source": "test"})

    MarketReader(table).latest("BTCUSDT")

    (call,) = table.calls
    assert call["Key"] == {"symbol": "BTCUSDT", "sk": "LATEST"}
    assert set(call["ExpressionAttributeNames"].values()) == {"price", "ts", "source"}
