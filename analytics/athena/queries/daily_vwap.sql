-- Daily volume-weighted average price per symbol, last 7 days (UTC).
-- The feed has no traded volume, so tick_count is the weight and each bar's
-- typical price (high + low + close) / 3 stands in for its trades.
SELECT
    symbol,
    dt,
    sum((high + low + close) / 3 * tick_count) / sum(tick_count) AS vwap,
    min(low) AS low,
    max(high) AS high,
    count(*) AS bars,
    sum(tick_count) AS ticks
FROM bars_1m
WHERE dt >= current_date - INTERVAL '7' DAY
GROUP BY symbol, dt
ORDER BY symbol, dt
