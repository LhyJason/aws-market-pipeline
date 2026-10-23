from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from processor.bars import MINUTE_MS, Bar, build_bars
from processor.records import Tick
from processor.store import (
    LATEST_SK,
    ConcurrentUpdateError,
    MarketStore,
    bar_from_item,
    bar_sk,
    bar_to_item,
)

HOUR = 1_729_699_200_000  # 2024-10-23T16:00:00Z
TTL_S = 7 * 86_400


def tick(price: float, offset_ms: int, symbol: str = "BTCUSDT") -> Tick:
    return Tick(symbol, price, HOUR + offset_ms, "test")


def bar(price: float, offset_ms: int) -> Bar:
    return Bar.from_tick(tick(price, offset_ms))


def conditional_failure() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}}, "PutItem"
    )


@pytest.fixture
def store(market_table):
    return MarketStore(market_table, bar_ttl_s=TTL_S)


def get(table, sk, symbol="BTCUSDT"):
    return table.get_item(Key={"symbol": symbol, "sk": sk}).get("Item")


def test_bar_sort_keys_order_by_time():
    assert bar_sk(HOUR) == "BAR#1729699200000"
    assert bar_sk(5) == "BAR#0000000000005"
    assert bar_sk(HOUR) < bar_sk(HOUR + MINUTE_MS)


def test_item_round_trip_keeps_exact_prices():
    (original,) = build_bars([tick(67321.5, 1_000), tick(0.1, 2_000)])

    item = bar_to_item(original, version=1, expires_at=0)

    assert item["low"] == Decimal("0.1")
    assert bar_from_item(item) == original


def test_merge_bar_creates_the_item_with_ttl(store, market_table):
    store.merge_bar(bar(100.0, 5_000))

    item = get(market_table, bar_sk(HOUR))
    assert item["version"] == 1
    assert item["tick_count"] == 1
    assert item["tick_ts"] == {HOUR + 5_000}
    assert item["expires_at"] == HOUR // 1000 + TTL_S


def test_merge_bar_merges_into_the_stored_bar(store, market_table):
    store.merge_bar(bar(100.0, 30_000))

    merged = store.merge_bar(bar(90.0, 10_000))

    assert (merged.open, merged.low, merged.close, merged.tick_count) == (90.0, 90.0, 100.0, 2)
    item = get(market_table, bar_sk(HOUR))
    assert item["version"] == 2
    assert bar_from_item(item) == merged


def test_merge_bar_skips_the_write_when_nothing_changes(store, market_table):
    store.merge_bar(bar(100.0, 5_000))
    store.merge_bar(bar(100.0, 5_000))

    assert get(market_table, bar_sk(HOUR))["version"] == 1


class ConflictingTable:
    """Wraps a real table; the first ``conflicts`` puts fail their condition."""

    def __init__(self, table, conflicts):
        self.table = table
        self.conflicts = conflicts
        self.puts = 0

    def get_item(self, **kwargs):
        return self.table.get_item(**kwargs)

    def put_item(self, **kwargs):
        self.puts += 1
        if self.conflicts:
            self.conflicts -= 1
            raise conditional_failure()
        return self.table.put_item(**kwargs)


def test_merge_bar_retries_after_a_conflicting_write(market_table):
    table = ConflictingTable(market_table, conflicts=1)

    MarketStore(table, bar_ttl_s=TTL_S).merge_bar(bar(100.0, 0))

    assert table.puts == 2
    assert get(market_table, bar_sk(HOUR))["version"] == 1


def test_merge_bar_gives_up_after_max_attempts(market_table):
    table = ConflictingTable(market_table, conflicts=10)

    with pytest.raises(ConcurrentUpdateError):
        MarketStore(table, bar_ttl_s=TTL_S, max_attempts=3).merge_bar(bar(100.0, 0))
    assert table.puts == 3


def test_merge_bar_propagates_other_errors(market_table):
    class BrokenTable(ConflictingTable):
        def put_item(self, **kwargs):
            raise ClientError({"Error": {"Code": "ValidationException"}}, "PutItem")

    with pytest.raises(ClientError, match="ValidationException"):
        MarketStore(BrokenTable(market_table, 0), bar_ttl_s=TTL_S).merge_bar(bar(1.0, 0))


def test_put_latest_only_moves_forward(store, market_table):
    assert store.put_latest(tick(100.0, 20_000))
    assert not store.put_latest(tick(90.0, 10_000))  # older: a replay
    assert not store.put_latest(tick(95.0, 20_000))  # same tick again

    item = get(market_table, LATEST_SK)
    assert (item["price"], item["ts"], item["source"]) == (Decimal("100.0"), HOUR + 20_000, "test")

    assert store.put_latest(tick(110.0, 30_000))
    assert get(market_table, LATEST_SK)["price"] == Decimal("110.0")


def test_put_latest_propagates_other_errors(market_table):
    class BrokenTable:
        def update_item(self, **kwargs):
            raise ClientError({"Error": {"Code": "ThrottlingException"}}, "UpdateItem")

    with pytest.raises(ClientError, match="ThrottlingException"):
        MarketStore(BrokenTable(), bar_ttl_s=TTL_S).put_latest(tick(1.0, 0))


def test_hour_bars_returns_only_that_hour_oldest_first(store):
    for offset in (59 * MINUTE_MS, 0, 60 * MINUTE_MS, -MINUTE_MS, 30 * MINUTE_MS):
        store.merge_bar(bar(1.0, offset))
    store.merge_bar(Bar.from_tick(tick(1.0, 0, symbol="ETHUSDT")))
    store.put_latest(tick(1.0, 0))

    starts = [b.start for b in store.hour_bars("BTCUSDT", HOUR)]

    assert starts == [HOUR, HOUR + 30 * MINUTE_MS, HOUR + 59 * MINUTE_MS]


def test_hour_bars_follows_pagination():
    items = [bar_to_item(bar(1.0, i * MINUTE_MS), version=1, expires_at=0) for i in range(3)]

    class PagedTable:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {"Items": items[:2], "LastEvaluatedKey": {"sk": items[1]["sk"]}}
            return {"Items": items[2:]}

    table = PagedTable()

    bars = MarketStore(table, bar_ttl_s=TTL_S).hour_bars("BTCUSDT", HOUR)

    assert len(bars) == 3
    assert table.calls[1]["ExclusiveStartKey"] == {"sk": items[1]["sk"]}
    assert all(call["ConsistentRead"] for call in table.calls)
