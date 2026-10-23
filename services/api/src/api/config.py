"""Runtime settings for the API, read from environment variables set by Terraform."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_BAR_TTL_DAYS = 7
DEFAULT_ANOMALY_Z = 3.0
DEFAULT_STALE_AFTER_S = 180


@dataclass(frozen=True)
class Settings:
    table_name: str
    # Empty means any well-formed symbol; otherwise others get a 404 without a read.
    symbols: frozenset[str] = frozenset()
    # Bars expire after this many days, so no request may span more.
    bar_ttl_days: int = DEFAULT_BAR_TTL_DAYS
    anomaly_z: float = DEFAULT_ANOMALY_Z
    stale_after_s: int = DEFAULT_STALE_AFTER_S

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        table_name = env.get("TABLE_NAME", "").strip()
        if not table_name:
            raise RuntimeError("TABLE_NAME is not set")

        symbols = frozenset(
            s.strip().upper() for s in env.get("SYMBOLS", "").split(",") if s.strip()
        )

        bar_ttl_days = int(env.get("BAR_TTL_DAYS", str(DEFAULT_BAR_TTL_DAYS)))
        if bar_ttl_days < 1:
            raise RuntimeError("BAR_TTL_DAYS must be at least 1")

        anomaly_z = float(env.get("ANOMALY_Z", str(DEFAULT_ANOMALY_Z)))
        if not anomaly_z > 0:
            raise RuntimeError("ANOMALY_Z must be positive")

        stale_after_s = int(env.get("STALE_AFTER_S", str(DEFAULT_STALE_AFTER_S)))
        if stale_after_s < 1:
            raise RuntimeError("STALE_AFTER_S must be at least 1")

        return cls(
            table_name=table_name,
            symbols=symbols,
            bar_ttl_days=bar_ttl_days,
            anomaly_z=anomaly_z,
            stale_after_s=stale_after_s,
        )
