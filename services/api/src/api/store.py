"""Read-only access to the market table, with GetItem and Query only (never Scan).

The key layout is the processor's (processor/store.py), repeated here because
each Lambda zip holds only its own package. tests/unit/test_api_store.py checks
the two stay in step. PK ``symbol``, SK:

- ``LATEST``: the newest tick for the symbol.
- ``BAR#<start>``: one 1-minute bar, ``start`` as 13-digit epoch ms, so the
  sort key orders bars by time.

Reads are eventually consistent: half the cost of strongly consistent reads,
and at worst a second or so behind the processor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

LATEST_SK = "LATEST"
BAR_SK_PREFIX = "BAR#"

# Every attribute name is aliased: OPEN, CLOSE, START and others are DynamoDB
# reserved words. Bars are read without tick_ts and version, which only the
# processor needs.
_LATEST_FIELDS = ("price", "ts", "source")
_BAR_FIELDS = ("start", "open", "high", "low", "close", "tick_count", "first_ts", "last_ts")


class Table(Protocol):
    """The slice of a boto3 DynamoDB Table resource that the reader uses."""

    def get_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def query(self, **kwargs: Any) -> dict[str, Any]: ...


def bar_sk(start: int) -> str:
    return f"{BAR_SK_PREFIX}{start:013d}"


def _projection(fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        "ProjectionExpression": ", ".join(f"#{f}" for f in fields),
        "ExpressionAttributeNames": {f"#{f}": f for f in fields},
    }


@dataclass(frozen=True)
class Latest:
    symbol: str
    price: float
    ts: int
    source: str


@dataclass(frozen=True)
class StoredBar:
    symbol: str
    start: int
    open: float
    high: float
    low: float
    close: float
    tick_count: int
    first_ts: int
    last_ts: int


def _bar_from_item(symbol: str, item: dict[str, Any]) -> StoredBar:
    return StoredBar(
        symbol=symbol,
        start=int(item["start"]),
        open=float(item["open"]),
        high=float(item["high"]),
        low=float(item["low"]),
        close=float(item["close"]),
        tick_count=int(item["tick_count"]),
        first_ts=int(item["first_ts"]),
        last_ts=int(item["last_ts"]),
    )


class MarketReader:
    def __init__(self, table: Table) -> None:
        self.table = table

    def latest(self, symbol: str) -> Latest | None:
        item = self.table.get_item(
            Key={"symbol": symbol, "sk": LATEST_SK}, **_projection(_LATEST_FIELDS)
        ).get("Item")
        if item is None:
            return None
        return Latest(
            symbol=symbol, price=float(item["price"]), ts=int(item["ts"]), source=item["source"]
        )

    def recent_bars(self, symbol: str, *, first: int, last: int, limit: int) -> list[StoredBar]:
        """Up to ``limit`` bars starting in ``[first, last]`` (epoch ms), the newest ones.

        Returned oldest first. One Query reads newest first and stops at
        ``limit``, following pages only if DynamoDB's 1 MB page limit cuts it short.
        """
        if limit < 1 or first > last:
            return []
        projection = _projection(_BAR_FIELDS)
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "#pk = :pk AND #sk BETWEEN :first AND :last",
            "ProjectionExpression": projection["ProjectionExpression"],
            "ExpressionAttributeNames": {
                "#pk": "symbol",
                "#sk": "sk",
                **projection["ExpressionAttributeNames"],
            },
            "ExpressionAttributeValues": {
                ":pk": symbol,
                ":first": bar_sk(first),
                ":last": bar_sk(last),
            },
            "ScanIndexForward": False,
        }
        bars: list[StoredBar] = []
        while len(bars) < limit:
            page = self.table.query(**kwargs, Limit=limit - len(bars))
            bars.extend(_bar_from_item(symbol, item) for item in page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        bars.reverse()
        return bars
