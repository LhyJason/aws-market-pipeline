"""Runtime settings for the producer, read from environment variables set by Terraform."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_FEED_BASE_URL = "https://api.binance.com"


@dataclass(frozen=True)
class Settings:
    stream_name: str
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    feed_base_url: str = DEFAULT_FEED_BASE_URL
    feed_timeout_s: float = 5.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        stream_name = env.get("KINESIS_STREAM_NAME", "").strip()
        if not stream_name:
            raise RuntimeError("KINESIS_STREAM_NAME is not set")

        raw_symbols = env.get("SYMBOLS", "")
        symbols = tuple(s.strip().upper() for s in raw_symbols.split(",") if s.strip())

        return cls(
            stream_name=stream_name,
            symbols=symbols or DEFAULT_SYMBOLS,
            feed_base_url=env.get("FEED_BASE_URL", DEFAULT_FEED_BASE_URL).rstrip("/"),
            feed_timeout_s=float(env.get("FEED_TIMEOUT_S", "5")),
        )
