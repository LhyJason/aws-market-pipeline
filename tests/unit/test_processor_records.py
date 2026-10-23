import base64
import json

import pytest

from processor.records import RecordError, Tick, decode_record, parse_tick

TS = 1_729_700_000_000


def record(data: bytes | str) -> dict:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return {"kinesis": {"data": base64.b64encode(data).decode("ascii"), "sequenceNumber": "1"}}


def tick_json(**overrides) -> str:
    return json.dumps(
        {"symbol": "BTCUSDT", "price": 67321.5, "ts": TS, "source": "binance"} | overrides
    )


def test_decodes_a_newline_terminated_record():
    assert decode_record(record(tick_json() + "\n")) == [Tick("BTCUSDT", 67321.5, TS, "binance")]


def test_decodes_every_line_and_skips_blank_ones():
    data = tick_json() + "\n\n" + tick_json(symbol="ETHUSDT", price=2500, ts=TS + 1) + "\n"

    ticks = decode_record(record(data))

    assert [(t.symbol, t.price, t.ts) for t in ticks] == [
        ("BTCUSDT", 67321.5, TS),
        ("ETHUSDT", 2500.0, TS + 1),
    ]
    assert isinstance(ticks[1].price, float)


def test_missing_version_means_v1_and_missing_source_is_tolerated():
    tick = parse_tick({"symbol": "BTCUSDT", "price": 1, "ts": TS})

    assert tick == Tick("BTCUSDT", 1.0, TS, "unknown")
    assert parse_tick(json.loads(tick_json(v=1))).symbol == "BTCUSDT"


@pytest.mark.parametrize(
    "bad",
    [
        {"v": 2},
        {"symbol": "btc/usdt"},
        {"symbol": "A=B"},
        {"symbol": ""},
        {"symbol": None},
        {"price": "67321.5"},
        {"price": True},
        {"price": 0},
        {"price": -1.0},
        {"price": float("nan")},
        {"ts": 1.5},
        {"ts": False},
        {"ts": -5},
        {"source": 7},
    ],
)
def test_parse_tick_rejects_invalid_fields(bad):
    with pytest.raises(RecordError):
        parse_tick(json.loads(tick_json(**bad)))


def test_parse_tick_rejects_non_objects():
    with pytest.raises(RecordError, match="not a JSON object"):
        parse_tick([1, 2, 3])


@pytest.mark.parametrize(
    ("rec", "match"),
    [
        ({"kinesis": {"data": "not base64!", "sequenceNumber": "1"}}, "base64"),
        ({"kinesis": {}}, "base64"),
        (record(b"\xff\xfe"), "base64-encoded UTF-8"),
        (record("{oops\n"), "not valid JSON"),
        (record("\n \n"), "no ticks"),
        (record(tick_json() + "\n" + "[]\n"), "not a JSON object"),
    ],
)
def test_decode_record_rejects_unusable_records(rec, match):
    with pytest.raises(RecordError, match=match):
        decode_record(rec)
