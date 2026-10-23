import pytest

from processor.config import DEFAULT_BAR_TTL_DAYS, DEFAULT_PROCESSED_PREFIX, Settings


def test_from_env_parses_everything():
    settings = Settings.from_env(
        {
            "TABLE_NAME": " market ",
            "LAKE_BUCKET": "lake",
            "PROCESSED_PREFIX": "/processed/bars/",
            "BAR_TTL_DAYS": "3",
        }
    )

    assert settings.table_name == "market"
    assert settings.lake_bucket == "lake"
    assert settings.processed_prefix == "processed/bars"
    assert settings.bar_ttl_days == 3
    assert settings.bar_ttl_s == 3 * 86_400


def test_from_env_falls_back_to_defaults():
    settings = Settings.from_env({"TABLE_NAME": "market", "LAKE_BUCKET": "lake"})

    assert settings.processed_prefix == DEFAULT_PROCESSED_PREFIX
    assert settings.bar_ttl_days == DEFAULT_BAR_TTL_DAYS


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"LAKE_BUCKET": "lake"}, "TABLE_NAME"),
        ({"TABLE_NAME": "market", "LAKE_BUCKET": " "}, "LAKE_BUCKET"),
        ({"TABLE_NAME": "market", "LAKE_BUCKET": "lake", "BAR_TTL_DAYS": "0"}, "BAR_TTL_DAYS"),
    ],
)
def test_from_env_rejects_missing_or_invalid_values(env, missing):
    with pytest.raises(RuntimeError, match=missing):
        Settings.from_env(env)
