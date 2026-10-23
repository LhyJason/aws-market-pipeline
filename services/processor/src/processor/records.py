"""Decode Kinesis event records into ticks (record schema v1, see docs/architecture.md)."""

from __future__ import annotations

import base64
import json
import math
import re
from dataclasses import dataclass
from typing import Any

SUPPORTED_VERSIONS = frozenset({1})

# Symbols become DynamoDB keys and S3 partition values (symbol=...), so anything
# that could break a Hive-style path ("/", "=") is rejected here.
_SYMBOL = re.compile(r"[A-Z0-9._-]{1,32}")


class RecordError(ValueError):
    """A record that can never be processed. It is logged and dropped, not retried."""


@dataclass(frozen=True)
class Tick:
    """One observed price. ``ts`` is epoch milliseconds at observation time."""

    symbol: str
    price: float
    ts: int
    source: str


def parse_tick(obj: Any) -> Tick:
    if not isinstance(obj, dict):
        raise RecordError("tick is not a JSON object")

    version = obj.get("v", 1)  # records without "v" are v1
    if version not in SUPPORTED_VERSIONS:
        raise RecordError(f"unsupported schema version {version!r}")

    symbol = obj.get("symbol")
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise RecordError(f"invalid symbol {symbol!r}")

    price = obj.get("price")
    # bool is an int subclass, and json.loads accepts NaN and Infinity.
    if (
        isinstance(price, bool)
        or not isinstance(price, int | float)
        or not math.isfinite(price)
        or price <= 0
    ):
        raise RecordError(f"invalid price {price!r}")

    ts = obj.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, int) or ts <= 0:
        raise RecordError(f"invalid ts {ts!r}")

    source = obj.get("source", "unknown")
    if not isinstance(source, str):
        raise RecordError(f"invalid source {source!r}")

    return Tick(symbol=symbol, price=float(price), ts=ts, source=source)


def decode_record(record: dict[str, Any]) -> list[Tick]:
    """Ticks in one Lambda Kinesis event record.

    The producer writes one newline-terminated JSON object per record, but any
    number of lines is accepted. A record with any bad line is rejected whole.
    """
    try:
        text = base64.b64decode(record["kinesis"]["data"], validate=True).decode("utf-8")
    except (KeyError, TypeError, ValueError) as exc:  # binascii and Unicode errors are ValueErrors
        raise RecordError("record data is not base64-encoded UTF-8") from exc

    ticks = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            raise RecordError("line is not valid JSON") from exc
        ticks.append(parse_tick(obj))

    if not ticks:
        raise RecordError("record contains no ticks")
    return ticks
