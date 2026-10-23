"""Response models: the API's JSON contract, also published as its OpenAPI schema."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Flag = Literal["spike", "gap"]


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class Price(BaseModel):
    symbol: str
    price: float
    ts: int = Field(description="observation time, epoch ms")
    time: datetime = Field(description="ts as ISO 8601, UTC")
    source: str
    age_s: float = Field(description="seconds between ts and the request")
    stale: bool = Field(description="age_s is above the stale threshold")


class Bar(BaseModel):
    # "return" is a Python keyword, hence the alias; JSON always uses the alias.
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    start: int = Field(description="minute start, epoch ms")
    time: datetime = Field(description="start as ISO 8601, UTC")
    open: float
    high: float
    low: float
    close: float
    tick_count: int = Field(description="distinct ticks in the minute; the feed has no volume")
    return_: float | None = Field(alias="return", description="close / previous minute's close - 1")
    zscore: float | None = Field(description="return against the preceding window's returns")
    flags: list[Flag]


class Stats(BaseModel):
    window: int = Field(description="bars in the rolling window")
    z_threshold: float
    sma_close: float | None = Field(description="mean close over the window ending at the last bar")
    mean_return: float | None
    volatility: float | None = Field(description="sample std of returns over the window")
    last_zscore: float | None
    anomalies: int = Field(description="returned bars flagged spike")


class Bars(BaseModel):
    symbol: str
    start: datetime
    end: datetime
    limit: int
    count: int
    bars: list[Bar] = Field(description="oldest first; the newest `limit` in [start, end)")
    stats: Stats


class Error(BaseModel):
    detail: str
