import pytest

from api.config import (
    DEFAULT_ANOMALY_Z,
    DEFAULT_BAR_TTL_DAYS,
    DEFAULT_STALE_AFTER_S,
    Settings,
)


def test_from_env_parses_everything():
    settings = Settings.from_env(
        {
            "TABLE_NAME": " market ",
            "SYMBOLS": "btcusdt, ETHUSDT,,",
            "BAR_TTL_DAYS": "3",
            "ANOMALY_Z": "2.5",
            "STALE_AFTER_S": "60",
        }
    )

    assert settings.table_name == "market"
    assert settings.symbols == {"BTCUSDT", "ETHUSDT"}
    assert settings.bar_ttl_days == 3
    assert settings.anomaly_z == 2.5
    assert settings.stale_after_s == 60


def test_from_env_falls_back_to_defaults():
    settings = Settings.from_env({"TABLE_NAME": "market"})

    assert settings.symbols == frozenset()  # any well-formed symbol
    assert settings.bar_ttl_days == DEFAULT_BAR_TTL_DAYS
    assert settings.anomaly_z == DEFAULT_ANOMALY_Z
    assert settings.stale_after_s == DEFAULT_STALE_AFTER_S


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({}, "TABLE_NAME"),
        ({"TABLE_NAME": " "}, "TABLE_NAME"),
        ({"TABLE_NAME": "market", "BAR_TTL_DAYS": "0"}, "BAR_TTL_DAYS"),
        ({"TABLE_NAME": "market", "ANOMALY_Z": "0"}, "ANOMALY_Z"),
        ({"TABLE_NAME": "market", "ANOMALY_Z": "nan"}, "ANOMALY_Z"),
        ({"TABLE_NAME": "market", "STALE_AFTER_S": "0"}, "STALE_AFTER_S"),
    ],
)
def test_from_env_rejects_missing_or_invalid_values(env, missing):
    with pytest.raises(RuntimeError, match=missing):
        Settings.from_env(env)
