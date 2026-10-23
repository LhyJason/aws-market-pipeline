-- Equivalent of the Glue table Terraform creates (infra/modules/analytics),
-- for running by hand in Athena. Replace <lake bucket> and the symbol list.
-- Columns must match analytics/athena/schema/bars_1m.json (checked by tests).
--
-- Partition projection: Athena computes partition locations from these
-- properties instead of reading them from the catalog, so new hours are
-- queryable as soon as the processor writes them, with no MSCK REPAIR or
-- crawler.
CREATE EXTERNAL TABLE IF NOT EXISTS bars_1m (
    bar_start timestamp,
    open double,
    high double,
    low double,
    close double,
    tick_count int,
    first_ts bigint,
    last_ts bigint
)
PARTITIONED BY (
    symbol string,
    dt date,
    hour int
)
STORED AS PARQUET
LOCATION 's3://<lake bucket>/processed/bars_1m/'
TBLPROPERTIES (
    'projection.enabled' = 'true',
    'projection.symbol.type' = 'enum',
    'projection.symbol.values' = 'BTCUSDT,ETHUSDT,SOLUSDT',
    'projection.dt.type' = 'date',
    'projection.dt.format' = 'yyyy-MM-dd',
    'projection.dt.range' = '2026-10-01,NOW',
    'projection.dt.interval' = '1',
    'projection.dt.interval.unit' = 'DAYS',
    'projection.hour.type' = 'integer',
    'projection.hour.range' = '0,23',
    'projection.hour.digits' = '2',
    'storage.location.template' = 's3://<lake bucket>/processed/bars_1m/symbol=${symbol}/dt=${dt}/hour=${hour}/'
)
