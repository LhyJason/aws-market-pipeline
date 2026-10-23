"""Price feeds. A feed turns a tuple of symbols into Tick records."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = "aws-market-pipeline/0.1"


@dataclass(frozen=True)
class Tick:
    """One observed price. ``ts`` is epoch milliseconds at observation time."""

    symbol: str
    price: float
    ts: int
    source: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))


class FeedError(RuntimeError):
    """The upstream feed could not be reached or returned an unusable response."""


class Feed(Protocol):
    def fetch(self, symbols: tuple[str, ...]) -> list[Tick]: ...


class BinanceFeed:
    """Spot prices from Binance's public REST API. No API key required.

    Use ``base_url="https://api.binance.us"`` from the United States; the global
    endpoint rejects US traffic.
    """

    source = "binance"

    def __init__(self, base_url: str, timeout_s: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def price_url(self, symbols: tuple[str, ...]) -> str:
        symbols_param = json.dumps(list(symbols), separators=(",", ":"))
        return f"{self.base_url}/api/v3/ticker/price?{urlencode({'symbols': symbols_param})}"

    def fetch(self, symbols: tuple[str, ...]) -> list[Tick]:
        if not symbols:
            return []

        url = self.price_url(symbols)
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:  # must precede URLError, its parent class
            raise FeedError(f"feed returned HTTP {exc.code} for {url}") from exc
        except URLError as exc:
            raise FeedError(f"feed unreachable: {exc.reason}") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise FeedError("feed returned a non-JSON body") from exc

        if not isinstance(payload, list):
            raise FeedError(f"unexpected feed payload: {payload!r}"[:200])

        observed_ms = int(time.time() * 1000)
        return [
            Tick(
                symbol=item["symbol"],
                price=float(item["price"]),
                ts=observed_ms,
                source=self.source,
            )
            for item in payload
        ]
