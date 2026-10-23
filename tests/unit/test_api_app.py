"""The API end to end in-process: TestClient -> FastAPI -> MarketReader -> moto DynamoDB.

The table is filled by the processor's own MarketStore, so these tests also
check that the API reads exactly what the processor writes.
"""

import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from mangum import Mangum

from api import __version__
from api import app as app_module
from api.app import create_app
from api.config import Settings
from api.store import MarketReader
from processor.bars import HOUR_MS, MINUTE_MS, Bar
from processor.records import Tick
from processor.store import MarketStore

HOUR = 1_729_699_200_000  # 2024-10-23T16:00:00Z
NOW_MS = HOUR + 2 * HOUR_MS  # 18:00
TTL_S = 7 * 86_400


def minute(m: int) -> int:
    return HOUR + m * MINUTE_MS


def wiggle(n: int, base: float = 100.0) -> list[float]:
    return [base * (1 + 0.001 * (-1) ** i) for i in range(n)]


@pytest.fixture
def writer(market_table):
    return MarketStore(market_table, bar_ttl_s=TTL_S)


def seed(writer, closes, *, first_minute=0, symbol="BTCUSDT", skip=()):
    """One tick per minute from ``first_minute``, skipping the minutes in ``skip``."""
    m = first_minute
    for close in closes:
        while m in skip:
            m += 1
        writer.merge_bar(Bar.from_tick(Tick(symbol, close, minute(m) + 5_000, "test")))
        m += 1


def make_client(table, **settings):
    app = create_app(
        Settings(table_name=table.name, **settings),
        MarketReader(table),
        clock=lambda: NOW_MS / 1000,
    )
    return TestClient(app)


@pytest.fixture
def client(market_table):
    return make_client(market_table)


# ---- /health -----------------------------------------------------------------


def test_health_needs_no_table():
    client = TestClient(create_app(Settings(table_name="missing"), reader=None))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


# ---- /prices ----------------------------------------------------------------


def test_price_returns_the_latest_tick(client, writer):
    writer.put_latest(Tick("BTCUSDT", 67321.5, NOW_MS - 30_000, "binance"))

    response = client.get("/prices/btcusdt")  # symbols are case-insensitive

    assert response.status_code == 200
    assert response.json() == {
        "symbol": "BTCUSDT",
        "price": 67321.5,
        "ts": NOW_MS - 30_000,
        "time": "2024-10-23T17:59:30Z",
        "source": "binance",
        "age_s": 30.0,
        "stale": False,
    }


def test_price_is_stale_after_the_threshold(market_table, writer):
    writer.put_latest(Tick("BTCUSDT", 1.0, NOW_MS - 61_000, "test"))

    body = make_client(market_table, stale_after_s=60).get("/prices/BTCUSDT").json()

    assert body["age_s"] == 61.0
    assert body["stale"] is True


def test_price_404s_without_data(client):
    response = client.get("/prices/BTCUSDT")

    assert response.status_code == 404
    assert response.json() == {"detail": "no price for BTCUSDT"}


def test_unknown_symbols_404_without_a_read():
    class NoReads:
        def latest(self, symbol):
            raise AssertionError("no read expected")

        recent_bars = latest

    app = create_app(Settings(table_name="t", symbols=frozenset({"BTCUSDT"})), NoReads())
    client = TestClient(app)

    for path in ("/prices/ETHUSDT", "/bars/ETHUSDT"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "unknown symbol ETHUSDT"}


@pytest.mark.parametrize("symbol", ["BTC$USD", "BTC%20USD", "A" * 33])
def test_malformed_symbols_are_rejected(client, symbol):
    response = client.get(f"/prices/{symbol}")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "symbol"]


def test_dynamodb_errors_become_503():
    class Throttled:
        def latest(self, symbol):
            raise ClientError({"Error": {"Code": "ThrottlingException"}}, "GetItem")

    client = TestClient(create_app(Settings(table_name="t"), Throttled()))

    response = client.get("/prices/BTCUSDT")

    assert response.status_code == 503
    assert response.json() == {"detail": "market data is temporarily unavailable"}


# ---- /bars ------------------------------------------------------------------


def test_bars_default_to_the_last_hour(client, writer):
    seed(writer, wiggle(120))  # 16:00 to 17:59

    response = client.get("/bars/BTCUSDT")

    assert response.status_code == 200
    body = response.json()
    assert (body["start"], body["end"]) == ("2024-10-23T17:00:00Z", "2024-10-23T18:00:00Z")
    assert body["count"] == body["limit"] == 60
    assert body["bars"][0]["time"] == "2024-10-23T17:00:00Z"
    assert body["bars"][-1]["time"] == "2024-10-23T17:59:00Z"
    # The hour before 17:00 is read as history, so even the first bar has a z-score.
    assert all(bar["zscore"] is not None for bar in body["bars"])
    assert body["stats"]["anomalies"] == 0


def test_bar_schema(client, writer):
    seed(writer, [100.0, 101.0], first_minute=110)

    (_, bar) = client.get("/bars/BTCUSDT").json()["bars"]

    assert bar == {
        "start": minute(111),
        "time": "2024-10-23T17:51:00Z",
        "open": 101.0,
        "high": 101.0,
        "low": 101.0,
        "close": 101.0,
        "tick_count": 1,
        "return": pytest.approx(0.01),
        "zscore": None,
        "flags": [],
    }


def test_bars_honour_start_end_and_limit(client, writer):
    seed(writer, wiggle(120))

    body = client.get(
        "/bars/BTCUSDT",
        # A naive start is UTC; an offset is converted.
        params={"start": "2024-10-23T16:10:00", "end": "2024-10-23T18:20:00+02:00", "limit": 5},
    ).json()

    assert (body["start"], body["end"]) == ("2024-10-23T16:10:00Z", "2024-10-23T16:20:00Z")
    assert [bar["start"] for bar in body["bars"]] == [minute(m) for m in range(15, 20)]


def test_bars_without_enough_history_have_no_zscore(client, writer):
    seed(writer, wiggle(30))

    bars = client.get(
        "/bars/BTCUSDT", params={"start": "2024-10-23T16:00:00Z", "end": "2024-10-23T16:30:00Z"}
    ).json()["bars"]

    assert [bar["zscore"] is None for bar in bars] == [True] * 21 + [False] * 9


def test_a_spike_is_flagged_and_counted(client, writer):
    closes = wiggle(119)
    closes.append(closes[-1] * 1.05)
    seed(writer, closes)

    body = client.get("/bars/BTCUSDT", params={"limit": 10}).json()

    assert body["bars"][-1]["flags"] == ["spike"]
    assert body["stats"]["anomalies"] == 1
    assert body["stats"]["last_zscore"] > 3
    assert body["stats"]["z_threshold"] == 3.0


def test_the_threshold_comes_from_settings(market_table, writer):
    closes = wiggle(119)
    closes.append(closes[-1] * 1.05)
    seed(writer, closes)

    body = make_client(market_table, anomaly_z=1_000.0).get("/bars/BTCUSDT").json()

    assert body["stats"]["anomalies"] == 0


def test_a_gap_is_flagged_even_at_the_start_of_the_range(client, writer):
    seed(writer, wiggle(119), skip={60})  # nothing at 17:00

    first = client.get("/bars/BTCUSDT").json()["bars"][0]

    assert first["time"] == "2024-10-23T17:01:00Z"
    assert first["flags"] == ["gap"]
    assert first["return"] is None


def test_stats_summarise_the_window(client, writer):
    seed(writer, [100.0 + m for m in range(120)])

    stats = client.get("/bars/BTCUSDT", params={"window": 10}).json()["stats"]

    assert stats["window"] == 10
    assert stats["sma_close"] == pytest.approx(sum(100.0 + m for m in range(110, 120)) / 10)
    assert stats["mean_return"] > 0
    assert stats["volatility"] > 0


def test_an_empty_range_is_not_an_error(client, writer):
    seed(writer, wiggle(10))

    body = client.get("/bars/BTCUSDT").json()  # the last hour has no bars

    assert body["count"] == 0
    assert body["bars"] == []
    assert body["stats"]["sma_close"] is None
    assert body["stats"]["anomalies"] == 0


def test_bars_of_other_symbols_are_not_mixed_in(client, writer):
    seed(writer, wiggle(60), first_minute=60)
    seed(writer, [5.0] * 60, first_minute=60, symbol="ETHUSDT")

    bars = client.get("/bars/ETHUSDT").json()["bars"]

    assert {bar["close"] for bar in bars} == {5.0}


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"limit": 0}, "greater than or equal to 1"),
        ({"limit": 1001}, "less than or equal to 1000"),
        ({"window": 4}, "greater than or equal to 5"),
        ({"window": 241}, "less than or equal to 240"),
        ({"start": "yesterday"}, "valid datetime"),
        ({"start": "2024-10-23T17:00:00Z", "end": "2024-10-23T17:00:00Z"}, "before end"),
        ({"start": "2024-10-01T00:00:00Z", "end": "2024-10-23T00:00:00Z"}, "at most 7 days"),
    ],
)
def test_bad_query_parameters_are_422(client, params, message):
    response = client.get("/bars/BTCUSDT", params=params)

    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["loc"][0] == "query"
    assert message in error["msg"]


# ---- Lambda -----------------------------------------------------------------


def rest_event(path: str, query: dict | None = None) -> dict:
    """An API Gateway REST API (payload v1) proxy event, as LocalStack sends it too."""
    return {
        "resource": path,
        "path": path,
        "httpMethod": "GET",
        "headers": {"Host": "example.execute-api.us-east-1.amazonaws.com"},
        "multiValueHeaders": {"Host": ["example.execute-api.us-east-1.amazonaws.com"]},
        "queryStringParameters": query,
        "multiValueQueryStringParameters": {k: [v] for k, v in query.items()} if query else None,
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourcePath": path,
            "httpMethod": "GET",
            "path": f"/v1{path}",
            "stage": "v1",
            "requestId": "req-1",
            "identity": {"sourceIp": "127.0.0.1"},
        },
        "body": None,
        "isBase64Encoded": False,
    }


LAMBDA_CONTEXT = SimpleNamespace(aws_request_id="lambda-1")


def test_lambda_handler_serves_rest_api_events():
    response = app_module.handler(rest_event("/health"), LAMBDA_CONTEXT)

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"status": "ok", "version": __version__}


def test_lambda_handler_passes_query_strings(market_table, writer):
    seed(writer, wiggle(120))
    app = create_app(
        Settings(table_name=market_table.name),
        MarketReader(market_table),
        clock=lambda: NOW_MS / 1000,
    )

    response = Mangum(app, lifespan="off")(
        rest_event("/bars/BTCUSDT", {"limit": "3"}), LAMBDA_CONTEXT
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["count"] == 3


def test_dependencies_come_from_the_environment(market_table, writer, monkeypatch):
    monkeypatch.setenv("TABLE_NAME", market_table.name)
    writer.put_latest(Tick("BTCUSDT", 1.5, NOW_MS, "test"))

    response = TestClient(create_app()).get("/prices/BTCUSDT")

    assert response.status_code == 200
    assert response.json()["price"] == 1.5
