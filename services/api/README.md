# api (phase 4)

FastAPI application served from Lambda through Mangum, behind an API Gateway
REST API (Lambda proxy integration, payload v1) with an API key
([ADR 0005](../../docs/adr/0005-rest-api-with-api-key.md); Terraform in
`infra/modules/api`). It reads the market table
only, with `GetItem` and `Query`, never `Scan`, and never touches the stream.

```
API Gateway (REST, API key) -> Mangum handler -> FastAPI routes -> MarketReader -> DynamoDB
```

| Module             | Role                                                                    |
| ------------------ | ----------------------------------------------------------------------- |
| `app.py`           | routes, input validation, `create_app()`; `handler` is the Lambda entry |
| `models.py`        | pydantic response models: the JSON contract and the OpenAPI schema      |
| `store.py`         | `MarketReader`: `LATEST` by `GetItem`, bars by one newest-first `Query` |
| `stats.py`         | returns, rolling z-scores, `spike`/`gap` flags, the window summary      |
| `config.py`        | `Settings.from_env()`                                                   |
| `logging_utils.py` | JSON log lines, one per request (method, path, status, duration)        |

## Routes

| Route                   | Auth (API Gateway) | Reads                          |
| ----------------------- | ------------------ | ------------------------------ |
| `GET /health`           | none               | nothing                        |
| `GET /prices/{symbol}`  | API key            | one `GetItem` on `LATEST`      |
| `GET /bars/{symbol}`    | API key            | up to two `Query` calls        |

Symbols follow the processor's rule (`[A-Z0-9._-]{1,32}`), case-insensitive.
With `SYMBOLS` set, any other symbol is a 404 without a read.

`/bars/{symbol}` query parameters:

| Parameter | Default      | Rule                                                        |
| --------- | ------------ | ----------------------------------------------------------- |
| `start`   | `end` - 1 h  | ISO 8601, inclusive; no zone means UTC (send `Z`, or `%2B` for `+`) |
| `end`     | now          | ISO 8601, exclusive; after `start`, at most `BAR_TTL_DAYS` later |
| `limit`   | 60           | 1-1000; the newest `limit` bars in the range are returned   |
| `window`  | 60           | 5-240 bars of rolling window                                |

Errors: 422 for bad input (FastAPI's `{"detail": [...]}` shape), 404 for an
unknown symbol or a symbol with no price, 503 when DynamoDB fails (throttling,
missing table). An empty range is a 200 with `"count": 0`.

```json
GET /prices/BTCUSDT
{"symbol": "BTCUSDT", "price": 67321.5, "ts": 1729699200000, "time": "2024-10-23T16:00:00Z",
 "source": "binance", "age_s": 12.4, "stale": false}

GET /bars/BTCUSDT?limit=2
{"symbol": "BTCUSDT", "start": "...", "end": "...", "limit": 2, "count": 2,
 "bars": [{"start": 1729699200000, "time": "2024-10-23T16:00:00Z", "open": 67321.5,
           "high": 67330.0, "low": 67310.2, "close": 67325.1, "tick_count": 1,
           "return": 0.00012, "zscore": 0.41, "flags": []}, ...],
 "stats": {"window": 60, "z_threshold": 3.0, "sma_close": 67290.3, "mean_return": 0.00001,
           "volatility": 0.0003, "last_zscore": 0.41, "anomalies": 0}}
```

## Rolling statistics and anomaly flags

Computed per request in `stats.py`, not stored by the processor: a late tick
can still move a bar's close, and the window or threshold can change without a
backfill. For each bar:

- `return`: `close / previous close - 1`, only when the previous minute has a
  bar. After a missing minute it is `null` and the bar is flagged `gap`, so a
  gap never looks like one big move.
- `zscore`: the return against the mean and sample standard deviation of the
  `window` bars before it (its own return excluded). `null` with fewer than 20
  returns in the window or none of them different.
- `spike`: `|zscore| >= ANOMALY_Z`.

The first `Query` reads the newest `limit` bars in the range. The second reads
the `window + 1` bars before the first of them, so the oldest bar shown has a
full window too. `stats` summarises the window ending at the newest bar;
`anomalies` counts the returned bars flagged `spike`. The worst case (1000
bars, window 240) takes about 50 ms of CPU locally.

`tick_count` is not used: the producer takes one sample per minute, so it is
always 1.

## Configuration

| Variable        | Default  |                                                  |
| --------------- | -------- | ------------------------------------------------ |
| `TABLE_NAME`    | required | the market table                                 |
| `SYMBOLS`       | empty    | comma-separated allowlist; empty allows any      |
| `BAR_TTL_DAYS`  | `7`      | longest range a request may span                 |
| `ANOMALY_Z`     | `3`      | spike threshold                                  |
| `STALE_AFTER_S` | `180`    | `/prices` reports `stale` past this age          |
| `LOG_LEVEL`     | `INFO`   |                                                  |

## Packaging

`requirements.txt` pins FastAPI, Mangum and pydantic. pydantic-core is a
native wheel, so the package is built for the architecture the function runs
on, like the processor's:

```bash
python scripts/build_lambda.py api --arch arm64     # AWS:        .build/api-arm64/
python scripts/build_lambda.py api --arch x86_64    # LocalStack: .build/api-x86_64/
```

About 11 MB unpacked and 3 MB zipped, well under Lambda's 50 MB direct-upload
limit. The handler is `api.app.handler`.

## Running it locally

There is no server to run for development: the unit tests drive the app
in-process with FastAPI's `TestClient` against moto, and the table is filled
by the processor's own `MarketStore`, so they also check that the two agree on
the key layout.

```bash
pytest tests/unit -k api
```

FastAPI's `/docs` and `/openapi.json` exist in the app, but API Gateway only
routes the three paths above.

Tests: `tests/unit/test_api_app.py`, `test_api_store.py`, `test_api_stats.py`,
`test_api_config.py`; against a deployed stack, `tests/integration`
(`make test-integration`).
