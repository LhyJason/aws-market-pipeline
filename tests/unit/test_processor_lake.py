import datetime as dt
import os

import duckdb

from processor.bars import MINUTE_MS, build_bars
from processor.lake import CONTENT_TYPE, LakeWriter, write_parquet
from processor.records import Tick

HOUR = 1_729_699_200_000  # 2024-10-23T16:00:00Z


def bars(*offsets_ms: int):
    return build_bars(
        Tick("BTCUSDT", 100.0 + i, HOUR + o, "test") for i, o in enumerate(offsets_ms)
    )


def read_rows(path: str) -> list[tuple]:
    with duckdb.connect() as con:
        return con.execute("SELECT * FROM read_parquet(?)", [path]).fetchall()


def test_key_layout_is_hive_partitioned_in_utc():
    writer = LakeWriter(None, "lake", "/processed/bars_1m/")

    assert writer.key_for("BTCUSDT", HOUR) == (
        "processed/bars_1m/symbol=BTCUSDT/dt=2024-10-23/hour=16/bars.parquet"
    )
    assert writer.key_for("BTCUSDT", 1_729_641_600_000).endswith(
        "dt=2024-10-23/hour=00/bars.parquet"
    )


def test_write_parquet_schema_and_rows(tmp_path):
    path = str(tmp_path / "bars.parquet")

    # Out of order on purpose: the file is sorted by bar_start.
    write_parquet(list(reversed(bars(0, 5_000, MINUTE_MS + 1_000))), path)

    with duckdb.connect() as con:
        schema = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [path]).fetchall()
    assert [(name, type_) for name, type_, *_ in schema] == [
        ("bar_start", "TIMESTAMP"),
        ("open", "DOUBLE"),
        ("high", "DOUBLE"),
        ("low", "DOUBLE"),
        ("close", "DOUBLE"),
        ("tick_count", "INTEGER"),
        ("first_ts", "BIGINT"),
        ("last_ts", "BIGINT"),
    ]
    assert read_rows(path) == [
        (dt.datetime(2024, 10, 23, 16, 0), 100.0, 101.0, 100.0, 101.0, 2, HOUR, HOUR + 5_000),
        (
            dt.datetime(2024, 10, 23, 16, 1),
            102.0,
            102.0,
            102.0,
            102.0,
            1,
            HOUR + MINUTE_MS + 1_000,
            HOUR + MINUTE_MS + 1_000,
        ),
    ]


def test_write_parquet_tolerates_quotes_in_the_path(tmp_path):
    directory = tmp_path / "it's"
    directory.mkdir()
    path = str(directory / "bars.parquet")

    write_parquet(bars(0), path)

    assert len(read_rows(path)) == 1


def test_write_hour_uploads_one_object_and_cleans_up(s3, lake_bucket, tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    writer = LakeWriter(s3, lake_bucket, "processed/bars_1m")

    key = writer.write_hour("BTCUSDT", HOUR, bars(0, MINUTE_MS))

    head = s3.head_object(Bucket=lake_bucket, Key=key)
    assert head["ContentType"] == CONTENT_TYPE
    local = tmp_path / "download.parquet"
    s3.download_file(lake_bucket, key, str(local))
    assert len(read_rows(str(local))) == 2
    assert os.listdir(tmp_path) == ["download.parquet"]  # the temp dir was removed
