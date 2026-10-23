"""Parquet hour files under processed/, written with DuckDB.

Layout: ``<prefix>/symbol=<symbol>/dt=YYYY-MM-DD/hour=HH/bars.parquet``, one
file per symbol-hour at a fixed key. Each write replaces the whole file with
the hour's bars as stored in DynamoDB, so Kinesis retries and batch bisects
overwrite the same object instead of adding duplicate rows.

``symbol``, ``dt`` and ``hour`` live in the path only (Hive partitions), not in
the file.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Sequence
from typing import Any, Protocol

import duckdb

from processor.bars import Bar

CONTENT_TYPE = "application/vnd.apache.parquet"


class S3Client(Protocol):
    """The slice of boto3's S3 client that the lake writer uses."""

    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...


def write_parquet(bars: Sequence[Bar], path: str) -> None:
    """Write ``bars`` to a Snappy-compressed Parquet file at ``path``, oldest first.

    Columns: bar_start (TIMESTAMP, UTC), open, high, low, close (DOUBLE),
    tick_count (INTEGER), first_ts, last_ts (BIGINT epoch ms).
    """
    directory = os.path.dirname(path) or "."
    # DuckDB keeps extensions and secrets under its home directory. Lambda's
    # filesystem is read-only outside /tmp, so point it next to the output.
    with duckdb.connect(config={"home_directory": directory}) as con:
        con.execute(
            "CREATE TABLE bars (start_ms BIGINT, open DOUBLE, high DOUBLE, low DOUBLE,"
            " close DOUBLE, tick_count INTEGER, first_ts BIGINT, last_ts BIGINT)"
        )
        con.executemany(
            "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (b.start, b.open, b.high, b.low, b.close, b.tick_count, b.first_ts, b.last_ts)
                for b in bars
            ],
        )
        target = path.replace("'", "''")
        con.execute(
            "COPY (SELECT epoch_ms(start_ms) AS bar_start, open, high, low, close,"
            " tick_count, first_ts, last_ts FROM bars ORDER BY start_ms)"
            f" TO '{target}' (FORMAT parquet, COMPRESSION snappy)"
        )


class LakeWriter:
    def __init__(self, s3: S3Client, bucket: str, prefix: str) -> None:
        self.s3 = s3
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def key_for(self, symbol: str, hour_start: int) -> str:
        hour = time.gmtime(hour_start / 1000)
        return (
            f"{self.prefix}/symbol={symbol}/dt={time.strftime('%Y-%m-%d', hour)}"
            f"/hour={time.strftime('%H', hour)}/bars.parquet"
        )

    def write_hour(self, symbol: str, hour_start: int, bars: Sequence[Bar]) -> str:
        """Replace the symbol-hour's Parquet file with ``bars`` and return its key."""
        key = self.key_for(symbol, hour_start)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bars.parquet")
            write_parquet(bars, path)
            with open(path, "rb") as body:
                self.s3.put_object(
                    Bucket=self.bucket, Key=key, Body=body.read(), ContentType=CONTENT_TYPE
                )
        return key
