import pytest

from producer.config import DEFAULT_SYMBOLS, Settings


def test_from_env_parses_everything():
    settings = Settings.from_env(
        {
            "KINESIS_STREAM_NAME": "ticks",
            "SYMBOLS": " btcusdt, ETHUSDT ,,",
            "FEED_BASE_URL": "https://api.binance.us/",
            "FEED_TIMEOUT_S": "2.5",
        }
    )

    assert settings.stream_name == "ticks"
    assert settings.symbols == ("BTCUSDT", "ETHUSDT")
    assert settings.feed_base_url == "https://api.binance.us"
    assert settings.feed_timeout_s == 2.5


def test_from_env_falls_back_to_defaults():
    settings = Settings.from_env({"KINESIS_STREAM_NAME": "ticks"})

    assert settings.symbols == DEFAULT_SYMBOLS
    assert settings.feed_base_url == "https://api.binance.com"
    assert settings.feed_timeout_s == 5.0


def test_from_env_requires_stream_name():
    with pytest.raises(RuntimeError, match="KINESIS_STREAM_NAME"):
        Settings.from_env({"KINESIS_STREAM_NAME": "  "})
