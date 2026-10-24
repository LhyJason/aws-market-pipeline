"""The deployed API's front door: auth, input validation and the response schema."""

import pytest

from api import models

pytestmark = pytest.mark.integration


def fields(model) -> set[str]:
    """The JSON keys a response model produces, aliases included."""
    return set(model.model_json_schema(by_alias=True)["properties"])


def test_health_needs_no_key(stack):
    response = stack.get("/health", key=None)

    assert response.status == 200
    assert models.Health.model_validate(response.body).status == "ok"


@pytest.mark.parametrize("path", ["/prices/{symbol}", "/bars/{symbol}"])
@pytest.mark.parametrize("key", [None, "not-the-key"], ids=["no-key", "wrong-key"])
def test_data_routes_need_the_api_key(stack, path, key):
    response = stack.get(path.format(symbol=stack.symbol), key=key)

    # API Gateway answers before Lambda runs.
    assert response.status == 403
    assert response.body == {"message": "Forbidden"}


def test_unknown_paths_never_reach_lambda(stack):
    response = stack.get("/nope")

    assert response.status == 403
    assert response.body == {"message": "Missing Authentication Token"}


def test_price_schema(stack):
    response = stack.get(f"/prices/{stack.symbol.lower()}")

    assert response.status == 200
    assert set(response.body) == fields(models.Price)
    price = models.Price.model_validate(response.body)
    assert price.symbol == stack.symbol
    assert price.price > 0


def test_bars_schema(stack):
    response = stack.get(f"/bars/{stack.symbol}", params={"limit": 5})

    assert response.status == 200
    assert set(response.body) == fields(models.Bars)
    assert set(response.body["stats"]) == fields(models.Stats)
    for bar in response.body["bars"]:
        assert set(bar) == fields(models.Bar)
    bars = models.Bars.model_validate(response.body)
    assert bars.count == len(bars.bars) <= 5
    assert [b.start for b in bars.bars] == sorted(b.start for b in bars.bars)


@pytest.mark.parametrize(
    ("path", "params", "message"),
    [
        ("/bars/{symbol}", {"limit": 0}, "greater than or equal to 1"),
        ("/bars/{symbol}", {"limit": 1001}, "less than or equal to 1000"),
        ("/bars/{symbol}", {"window": 4}, "greater than or equal to 5"),
        ("/bars/{symbol}", {"start": "not-a-time"}, "valid datetime"),
        (
            "/bars/{symbol}",
            {"start": "2026-01-02T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
            "before end",
        ),
        (
            "/bars/{symbol}",
            {"start": "2026-01-01T00:00:00Z", "end": "2026-02-01T00:00:00Z"},
            "at most",
        ),
        ("/prices/BTC$USD", None, "should match pattern"),
    ],
)
def test_bad_input_is_422(stack, path, params, message):
    response = stack.get(path.format(symbol=stack.symbol), params=params)

    assert response.status == 422
    (error,) = response.body["detail"]
    assert message in error["msg"]


def test_symbols_outside_the_allowlist_are_404(stack):
    response = stack.get("/prices/NOTASYMBOL")

    assert response.status == 404
    assert response.body == {"detail": "unknown symbol NOTASYMBOL"}
