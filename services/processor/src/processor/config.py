"""Runtime settings for the processor, read from environment variables set by Terraform."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_PROCESSED_PREFIX = "processed/bars_1m"
DEFAULT_BAR_TTL_DAYS = 7


@dataclass(frozen=True)
class Settings:
    table_name: str
    lake_bucket: str
    processed_prefix: str = DEFAULT_PROCESSED_PREFIX
    bar_ttl_days: int = DEFAULT_BAR_TTL_DAYS

    @property
    def bar_ttl_s(self) -> int:
        return self.bar_ttl_days * 86_400

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        table_name = env.get("TABLE_NAME", "").strip()
        if not table_name:
            raise RuntimeError("TABLE_NAME is not set")

        lake_bucket = env.get("LAKE_BUCKET", "").strip()
        if not lake_bucket:
            raise RuntimeError("LAKE_BUCKET is not set")

        bar_ttl_days = int(env.get("BAR_TTL_DAYS", str(DEFAULT_BAR_TTL_DAYS)))
        if bar_ttl_days < 1:
            raise RuntimeError("BAR_TTL_DAYS must be at least 1")

        return cls(
            table_name=table_name,
            lake_bucket=lake_bucket,
            processed_prefix=env.get("PROCESSED_PREFIX", DEFAULT_PROCESSED_PREFIX).strip("/")
            or DEFAULT_PROCESSED_PREFIX,
            bar_ttl_days=bar_ttl_days,
        )
