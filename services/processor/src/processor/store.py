"""DynamoDB access for the market table.

Single-table layout, PK ``symbol``, SK ``sk``:

- ``LATEST``: the newest tick for the symbol.
- ``BAR#<start>``: one 1-minute bar, ``start`` as 13-digit epoch ms so the sort
  key orders bars by time. Expires ``bar_ttl_s`` after the minute starts.

The table is the source of truth for bars. The Parquet hour files under
processed/ are rebuilt from it, never appended to.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from processor.bars import HOUR_MS, MINUTE_MS, Bar
from processor.records import Tick

LATEST_SK = "LATEST"
BAR_SK_PREFIX = "BAR#"


class Table(Protocol):
    """The slice of a boto3 DynamoDB Table resource that the store uses."""

    def get_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def put_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def update_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def query(self, **kwargs: Any) -> dict[str, Any]: ...


class ConcurrentUpdateError(RuntimeError):
    """A bar kept changing between read and write. The batch will be retried."""


def bar_sk(start: int) -> str:
    return f"{BAR_SK_PREFIX}{start:013d}"


def _decimal(value: float) -> Decimal:
    # Via str so 67321.5 is stored as 67321.5, not its binary expansion.
    return Decimal(str(value))


def _is_conditional_failure(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def bar_to_item(bar: Bar, *, version: int, expires_at: int) -> dict[str, Any]:
    return {
        "symbol": bar.symbol,
        "sk": bar_sk(bar.start),
        "start": bar.start,
        "open": _decimal(bar.open),
        "high": _decimal(bar.high),
        "low": _decimal(bar.low),
        "close": _decimal(bar.close),
        "first_ts": bar.first_ts,
        "last_ts": bar.last_ts,
        "tick_count": bar.tick_count,
        "tick_ts": set(bar.tick_ts),  # number set; never empty, as DynamoDB requires
        "version": version,
        "expires_at": expires_at,
    }


def bar_from_item(item: dict[str, Any]) -> Bar:
    return Bar(
        symbol=item["symbol"],
        start=int(item["start"]),
        open=float(item["open"]),
        high=float(item["high"]),
        low=float(item["low"]),
        close=float(item["close"]),
        first_ts=int(item["first_ts"]),
        last_ts=int(item["last_ts"]),
        tick_ts=frozenset(int(ts) for ts in item["tick_ts"]),
    )


class MarketStore:
    def __init__(self, table: Table, *, bar_ttl_s: int, max_attempts: int = 3) -> None:
        self.table = table
        self.bar_ttl_s = bar_ttl_s
        self.max_attempts = max_attempts

    def put_latest(self, tick: Tick) -> bool:
        """Record ``tick`` as the symbol's latest price unless a newer one is stored.

        Returns False when the stored tick is as new or newer, which is how a
        replay of old records is kept from moving the latest price backwards.
        """
        try:
            self.table.update_item(
                Key={"symbol": tick.symbol, "sk": LATEST_SK},
                UpdateExpression="SET #price = :price, #ts = :ts, #source = :source",
                ConditionExpression="attribute_not_exists(#ts) OR #ts < :ts",
                ExpressionAttributeNames={"#price": "price", "#ts": "ts", "#source": "source"},
                ExpressionAttributeValues={
                    ":price": _decimal(tick.price),
                    ":ts": tick.ts,
                    ":source": tick.source,
                },
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise
        return True

    def merge_bar(self, bar: Bar) -> Bar:
        """Merge ``bar`` into the stored bar for its minute and return the result.

        Read, merge, then write conditionally on the version that was read
        (optimistic locking). A symbol lives on one shard and Lambda runs one
        batch per shard at a time, so a conflict means something unexpected is
        writing; retry a few times, then fail the batch.
        """
        key = {"symbol": bar.symbol, "sk": bar_sk(bar.start)}
        for _ in range(self.max_attempts):
            item = self.table.get_item(Key=key, ConsistentRead=True).get("Item")
            if item is None:
                merged, version = bar, 0
                condition: dict[str, Any] = {
                    "ConditionExpression": "attribute_not_exists(sk)",
                }
            else:
                current = bar_from_item(item)
                merged, version = current.merge(bar), int(item["version"])
                if merged == current:
                    return current  # every tick was already in the bar: a retry
                condition = {
                    "ConditionExpression": "#version = :version",
                    "ExpressionAttributeNames": {"#version": "version"},
                    "ExpressionAttributeValues": {":version": version},
                }

            try:
                self.table.put_item(
                    Item=bar_to_item(
                        merged,
                        version=version + 1,
                        expires_at=merged.start // 1000 + self.bar_ttl_s,
                    ),
                    **condition,
                )
            except ClientError as exc:
                if _is_conditional_failure(exc):
                    continue
                raise
            return merged

        raise ConcurrentUpdateError(f"bar {bar.symbol}@{bar.start} changed on every attempt")

    def hour_bars(self, symbol: str, hour_start: int) -> list[Bar]:
        """Every stored bar for ``symbol`` in the hour starting at ``hour_start``, oldest first."""
        condition = Key("symbol").eq(symbol) & Key("sk").between(
            bar_sk(hour_start), bar_sk(hour_start + HOUR_MS - MINUTE_MS)
        )
        bars: list[Bar] = []
        kwargs: dict[str, Any] = {"KeyConditionExpression": condition, "ConsistentRead": True}
        while True:
            page = self.table.query(**kwargs)
            bars.extend(bar_from_item(item) for item in page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return bars
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
