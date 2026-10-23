"""The saved Athena queries, run with DuckDB over Parquet written by the processor.

Athena itself never runs in CI (or on free LocalStack); this checks the SQL
against real files in the real layout, and that it parses as Trino.
"""

import datetime as dt
import json
import re
from pathlib import Path

import duckdb
import pytest
import sqlglot

import query_lake
from processor.bars import build_bars, hour_start
from processor.lake import LakeWriter, write_parquet
from processor.records import Tick

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(query_lake.SCHEMA_FILE.read_text(encoding="utf-8"))
UTC = dt.UTC

TODAY = dt.datetime.now(UTC).date()
DAY1 = TODAY - dt.timedelta(days=2)  # inside the 7-day window
DAY2 = TODAY - dt.timedelta(days=1)
OLD = TODAY - dt.timedelta(days=10)  # outside it


def at(day: dt.date, hh: int, mm: int, ss: int = 0) -> int:
    return int(dt.datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=UTC).timestamp() * 1000)


def minute(day: dt.date, hh: int, mm: int) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hh, mm)


TICKS = [
    # BTCUSDT on DAY1: 10:00-10:03 every minute, nothing 10:04-10:06, then 10:07.
    Tick("BTCUSDT", 100.0, at(DAY1, 10, 0), "test"),
    Tick("BTCUSDT", 110.0, at(DAY1, 10, 0, 30), "test"),  # second tick in 10:00
    Tick("BTCUSDT", 101.0, at(DAY1, 10, 1), "test"),  # -8.18% from 110
    Tick("BTCUSDT", 102.0, at(DAY1, 10, 2), "test"),
    Tick("BTCUSDT", 99.0, at(DAY1, 10, 3), "test"),
    Tick("BTCUSDT", 150.0, at(DAY1, 10, 7), "test"),  # big jump, but across a gap
    # BTCUSDT on DAY2, crossing an hour boundary.
    Tick("BTCUSDT", 200.0, at(DAY2, 23, 59), "test"),
    Tick("BTCUSDT", 202.0, at(DAY2 + dt.timedelta(days=1), 0, 0), "test"),  # +1%, next day
    # ETHUSDT on DAY1, steady.
    Tick("ETHUSDT", 10.0, at(DAY1, 10, 0), "test"),
    Tick("ETHUSDT", 10.1, at(DAY1, 10, 1), "test"),  # +1%
    # Outside the window: must not show up anywhere.
    Tick("BTCUSDT", 1.0, at(OLD, 12, 0), "test"),
    Tick("BTCUSDT", 1000.0, at(OLD, 12, 1), "test"),
]


@pytest.fixture(scope="module")
def con(tmp_path_factory) -> duckdb.DuckDBPyConnection:
    """processed/ laid out exactly as the processor writes it."""
    root = tmp_path_factory.mktemp("lake")
    writer = LakeWriter(None, "lake", "processed/bars_1m")
    hours: dict[tuple[str, int], list] = {}
    for bar in build_bars(TICKS):
        hours.setdefault((bar.symbol, hour_start(bar.start)), []).append(bar)
    for (symbol, hour), bars in hours.items():
        path = root / writer.key_for(symbol, hour)
        path.parent.mkdir(parents=True)
        write_parquet(bars, str(path))
    return query_lake.connect(root / "processed")


def run(con, name: str) -> list[tuple]:
    return con.sql(query_lake.load_query(name)).fetchall()


# ---- the queries -------------------------------------------------------------


def test_bars_1m_view_has_partition_columns(con):
    row = con.sql(
        "SELECT symbol, dt, hour FROM bars_1m WHERE bar_start = ?", params=[minute(DAY1, 10, 3)]
    ).fetchall()

    assert row == [("BTCUSDT", DAY1, 10)]


def test_daily_vwap_weights_bars_by_tick_count(con):
    rows = {(symbol, day): rest for symbol, day, *rest in run(con, "daily_vwap")}

    # 10:00 has two ticks: high 110, low 100, close 110 -> typical 320/3, weight 2.
    expected = (320 / 3 * 2 + 101 + 102 + 99 + 150) / 6
    vwap, low, high, bars, ticks = rows[("BTCUSDT", DAY1)]
    assert vwap == pytest.approx(expected)
    assert (low, high, bars, ticks) == (99.0, 150.0, 5, 6)
    assert rows[("ETHUSDT", DAY1)][0] == pytest.approx(10.05)
    assert all(day >= TODAY - dt.timedelta(days=7) for _, day in rows)
    assert ("BTCUSDT", OLD) not in rows


def test_largest_moves_ranks_consecutive_minutes_only(con):
    rows = run(con, "largest_moves")

    symbol, bar_start, prev_close, close, change, change_pct = rows[0]
    assert (symbol, bar_start, prev_close, close) == ("BTCUSDT", minute(DAY1, 10, 1), 110.0, 101.0)
    assert change == pytest.approx(-9.0)
    assert change_pct == pytest.approx(-9 / 110 * 100)
    starts = [r[1] for r in rows]
    assert minute(DAY1, 10, 7) not in starts  # +51% but after a 3-minute gap
    assert minute(OLD, 12, 1) not in starts  # outside the window
    # The 23:59 -> 00:00 move spans a day and an hour partition but is one minute.
    assert minute(DAY2 + dt.timedelta(days=1), 0, 0) in starts


def test_minute_gaps_reports_missing_runs_longest_first(con):
    rows = run(con, "minute_gaps")

    # 10:07 on DAY1 to 23:59 on DAY2 is one day and 13 h 52 min apart.
    assert rows == [
        ("BTCUSDT", minute(DAY1, 10, 8), minute(DAY2, 23, 59), 1440 + 832 - 1),
        ("BTCUSDT", minute(DAY1, 10, 4), minute(DAY1, 10, 7), 3),
    ]


@pytest.mark.parametrize("name", query_lake.query_names())
def test_queries_parse_as_trino(name):
    # Athena engine 3 is Trino; DuckDB accepting the SQL is not enough.
    statements = sqlglot.parse(query_lake.load_query(name), read="trino")

    assert len(statements) == 1


# ---- schema in step: JSON, Parquet, DDL, Terraform ------------------------------

PARQUET_TO_GLUE = {
    "TIMESTAMP": "timestamp",
    "DOUBLE": "double",
    "INTEGER": "int",
    "BIGINT": "bigint",
}


def test_schema_matches_the_processor_parquet(tmp_path):
    path = str(tmp_path / "bars.parquet")
    write_parquet(build_bars(TICKS[:1]), path)

    with duckdb.connect() as c:
        described = c.execute("DESCRIBE SELECT * FROM read_parquet(?)", [path]).fetchall()

    assert [(name, PARQUET_TO_GLUE[t]) for name, t, *_ in described] == [
        (col["name"], col["type"]) for col in SCHEMA["columns"]
    ]


def _ddl_block(ddl: str, start: str, end: str) -> list[tuple[str, str]]:
    block = re.search(rf"{start}\s*\((.*?)\)\s*{end}", ddl, re.S).group(1)
    return [tuple(line.strip().rstrip(",").split()) for line in block.strip().splitlines()]


def test_ddl_matches_schema():
    ddl = (ROOT / "analytics" / "athena" / "ddl" / "bars_1m.sql").read_text(encoding="utf-8")

    assert _ddl_block(ddl, r"bars_1m", "PARTITIONED BY") == [
        (c["name"], c["type"]) for c in SCHEMA["columns"]
    ]
    assert _ddl_block(ddl, "PARTITIONED BY", "STORED AS") == [
        (k["name"], k["type"]) for k in SCHEMA["partition_keys"]
    ]


def test_terraform_saves_every_query_file():
    main_tf = (ROOT / "infra" / "modules" / "analytics" / "main.tf").read_text(encoding="utf-8")
    block = re.search(r"queries = \{(.*?)\n  \}", main_tf, re.S).group(1)

    assert sorted(re.findall(r"^\s*(\w+)\s*=", block, re.M)) == query_lake.query_names()


# ---- the script --------------------------------------------------------------


def test_connect_refuses_an_empty_directory(tmp_path):
    with pytest.raises(SystemExit, match="no Parquet files"):
        query_lake.connect(tmp_path)


def test_main_prints_every_query(con, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(query_lake, "connect", lambda root: con)

    query_lake.main(["--dir", str(tmp_path)])

    out = capsys.readouterr().out
    for name in query_lake.query_names():
        assert f"-- {name}" in out
    assert "symbol\tdt\tvwap" in out


def test_main_rejects_unknown_queries(tmp_path):
    with pytest.raises(SystemExit):
        query_lake.main(["--dir", str(tmp_path), "nope"])
