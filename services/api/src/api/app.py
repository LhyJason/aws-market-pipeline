"""Lambda entry point: FastAPI routes over the market table, adapted by Mangum.

API Gateway (REST API, Lambda proxy integration) sends the request event;
Mangum turns it into an ASGI request and the response back into the proxy
response. The routes read DynamoDB only, with GetItem and Query.

No ``from __future__ import annotations`` here: FastAPI reads the route
signatures at runtime, and the dependency aliases are local to create_app.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, NoReturn, Protocol

import boto3
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mangum import Mangum

from api import __version__, models
from api.config import Settings
from api.logging_utils import configure_logging
from api.stats import SPIKE, annotate, summarize
from api.store import Latest, MarketReader, StoredBar

configure_logging()
log = logging.getLogger(__name__)
# Mangum logs every request at INFO too; the middleware below already does, with more fields.
logging.getLogger("mangum").setLevel(logging.WARNING)

# The processor's symbol rule (processor/records.py), case-insensitive here:
# symbols are stored upper case and requests are uppercased before the read.
SYMBOL_PATTERN = r"^[A-Za-z0-9._-]{1,32}$"
DEFAULT_SPAN = timedelta(hours=1)
DEFAULT_LIMIT, MAX_LIMIT = 60, 1000
DEFAULT_WINDOW, MIN_WINDOW, MAX_WINDOW = 60, 5, 240

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)


class Reader(Protocol):
    def latest(self, symbol: str) -> Latest | None: ...
    def recent_bars(self, symbol: str, *, first: int, last: int, limit: int) -> list[StoredBar]: ...


def _utc(value: datetime) -> datetime:
    """Times without a zone are UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _floor_ms(value: datetime) -> int:
    return (value - _EPOCH) // _MS


def _ceil_ms(value: datetime) -> int:
    return -((_EPOCH - value) // _MS)


def _from_ms(ms: int) -> datetime:
    return _EPOCH + ms * _MS


def _invalid(field: str, message: str) -> NoReturn:
    """A 422 shaped like FastAPI's own validation errors."""
    raise RequestValidationError(
        [{"type": "value_error", "loc": ("query", field), "msg": message, "input": None}]
    )


def create_app(
    settings: Settings | None = None,
    reader: Reader | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """Build the app. Without ``settings`` or ``reader`` they come from the
    environment on the first request that needs them, then live as long as the
    container, so warm invocations reuse the boto3 client.
    """
    app = FastAPI(title="market-pipeline API", version=__version__)

    def dependencies() -> tuple[Settings, Reader]:
        nonlocal settings, reader
        if settings is None:
            settings = Settings.from_env()
        if reader is None:
            reader = MarketReader(boto3.resource("dynamodb").Table(settings.table_name))
        return settings, reader

    Deps = Annotated[tuple[Settings, Reader], Depends(dependencies)]

    def symbol_param(
        symbol: Annotated[str, Path(pattern=SYMBOL_PATTERN, examples=["BTCUSDT"])], deps: Deps
    ) -> str:
        symbol = symbol.upper()
        allowed = deps[0].symbols
        if allowed and symbol not in allowed:
            raise HTTPException(404, f"unknown symbol {symbol}")
        return symbol

    Symbol = Annotated[str, Depends(symbol_param)]
    not_found = {404: {"model": models.Error}}

    @app.middleware("http")
    async def log_requests(request: Request, call_next: Callable) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        context = request.scope.get("aws.context")  # set by Mangum in Lambda
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "request_id": getattr(context, "aws_request_id", None),
            },
        )
        return response

    @app.exception_handler(ClientError)
    async def storage_unavailable(request: Request, exc: ClientError) -> JSONResponse:
        # Throttling or a missing table: not the client's fault, worth a retry.
        log.error("dynamodb request failed", exc_info=exc)
        return JSONResponse({"detail": "market data is temporarily unavailable"}, status_code=503)

    @app.get("/health")
    def health() -> models.Health:
        """Liveness only: no DynamoDB read, and no API key needed."""
        return models.Health(version=__version__)

    @app.get("/prices/{symbol}", responses=not_found)
    def price(symbol: Symbol, deps: Deps) -> models.Price:
        """The symbol's latest tick."""
        app_settings, market = deps
        latest = market.latest(symbol)
        if latest is None:
            raise HTTPException(404, f"no price for {symbol}")
        age_s = max(0.0, round(clock() - latest.ts / 1000, 3))
        return models.Price(
            symbol=symbol,
            price=latest.price,
            ts=latest.ts,
            time=_from_ms(latest.ts),
            source=latest.source,
            age_s=age_s,
            stale=age_s > app_settings.stale_after_s,
        )

    @app.get("/bars/{symbol}", responses=not_found)
    def bars(
        symbol: Symbol,
        deps: Deps,
        start: Annotated[
            datetime | None,
            Query(description="ISO 8601, inclusive; without a zone it is UTC. Default: end - 1h"),
        ] = None,
        end: Annotated[
            datetime | None, Query(description="ISO 8601, exclusive. Default: now")
        ] = None,
        limit: Annotated[
            int, Query(ge=1, le=MAX_LIMIT, description="most recent bars to return")
        ] = DEFAULT_LIMIT,
        window: Annotated[
            int, Query(ge=MIN_WINDOW, le=MAX_WINDOW, description="rolling window, in bars")
        ] = DEFAULT_WINDOW,
    ) -> models.Bars:
        """1-minute bars in ``[start, end)``, the newest ``limit`` of them, with rolling stats."""
        app_settings, market = deps
        end_at = _utc(end) if end else datetime.fromtimestamp(clock(), UTC)
        start_at = _utc(start) if start else end_at - DEFAULT_SPAN
        if start_at >= end_at:
            _invalid("start", "start must be before end")
        if end_at - start_at > timedelta(days=app_settings.bar_ttl_days):
            _invalid("start", f"the range may span at most {app_settings.bar_ttl_days} days")

        shown = market.recent_bars(
            symbol, first=_ceil_ms(start_at), last=_floor_ms(end_at) - 1, limit=limit
        )
        # The window before the first bar shown, so every bar gets a full history.
        # One extra bar, because the oldest one's return needs the bar before it.
        history = (
            market.recent_bars(symbol, first=0, last=shown[0].start - 1, limit=window + 1)
            if shown
            else []
        )
        series = history + shown
        series_stats = annotate(series, window=window, z_threshold=app_settings.anomaly_z)
        shown_stats = series_stats[len(history) :]
        summary = summarize(series, series_stats, window=window)

        return models.Bars(
            symbol=symbol,
            start=start_at,
            end=end_at,
            limit=limit,
            count=len(shown),
            bars=[
                models.Bar(
                    start=bar.start,
                    time=_from_ms(bar.start),
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    tick_count=bar.tick_count,
                    return_=s.ret,
                    zscore=s.zscore,
                    flags=list(s.flags),
                )
                for bar, s in zip(shown, shown_stats, strict=True)
            ],
            stats=models.Stats(
                window=window,
                z_threshold=app_settings.anomaly_z,
                sma_close=summary.sma_close,
                mean_return=summary.mean_return,
                volatility=summary.volatility,
                last_zscore=summary.last_zscore,
                anomalies=sum(SPIKE in s.flags for s in shown_stats),
            ),
        )

    return app


app = create_app()
# REST API events (payload v1). No lifespan events: nothing to set up or tear down.
handler = Mangum(app, lifespan="off")
