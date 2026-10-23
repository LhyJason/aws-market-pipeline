import io
import json
from urllib.error import HTTPError

import pytest

from producer import feed as feed_module
from producer.feed import BinanceFeed, FeedError, Tick


class FakeResponse(io.BytesIO):
    """Minimal stand-in for the object urlopen returns."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_tick_json_is_compact_and_stable():
    tick = Tick(symbol="BTCUSDT", price=67321.5, ts=1_729_700_000_000, source="binance")
    assert (
        tick.to_json()
        == '{"symbol":"BTCUSDT","price":67321.5,"ts":1729700000000,"source":"binance"}'
    )


def test_price_url_encodes_symbol_list():
    url = BinanceFeed("https://api.binance.us/").price_url(("BTCUSDT", "ETHUSDT"))
    assert url == (
        "https://api.binance.us/api/v3/ticker/price?symbols=%5B%22BTCUSDT%22%2C%22ETHUSDT%22%5D"
    )


def test_fetch_parses_ticks(monkeypatch):
    body = json.dumps(
        [{"symbol": "BTCUSDT", "price": "67321.50"}, {"symbol": "ETHUSDT", "price": "2500"}]
    )
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["user_agent"] = request.get_header("User-agent")
        return FakeResponse(body.encode())

    monkeypatch.setattr(feed_module, "urlopen", fake_urlopen)

    ticks = BinanceFeed("https://api.binance.com", timeout_s=3).fetch(("BTCUSDT", "ETHUSDT"))

    assert seen["timeout"] == 3
    assert "/api/v3/ticker/price?" in seen["url"]
    assert seen["user_agent"].startswith("aws-market-pipeline")
    assert [(t.symbol, t.price, t.source) for t in ticks] == [
        ("BTCUSDT", 67321.5, "binance"),
        ("ETHUSDT", 2500.0, "binance"),
    ]
    assert all(t.ts > 1_700_000_000_000 for t in ticks)


def test_fetch_wraps_http_errors(monkeypatch):
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 429, "Too Many Requests", hdrs=None, fp=None)

    monkeypatch.setattr(feed_module, "urlopen", fake_urlopen)

    with pytest.raises(FeedError, match="HTTP 429"):
        BinanceFeed("https://api.binance.com").fetch(("BTCUSDT",))


def test_fetch_rejects_non_list_payload(monkeypatch):
    monkeypatch.setattr(
        feed_module, "urlopen", lambda request, timeout: FakeResponse(b'{"code": -1121}')
    )

    with pytest.raises(FeedError, match="unexpected"):
        BinanceFeed("https://api.binance.com").fetch(("NOPE",))


def test_fetch_with_no_symbols_makes_no_request(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("urlopen should not be called")

    monkeypatch.setattr(feed_module, "urlopen", boom)

    assert BinanceFeed("https://api.binance.com").fetch(()) == []
