-- Data quality: runs of missing minutes between bars, last 7 days, longest first.
-- A gap means no tick arrived for the symbol in that minute (feed outage,
-- paused schedule, or the processor dropping records to the DLQ).
WITH ordered AS (
    SELECT
        symbol,
        bar_start,
        lead(bar_start) OVER (PARTITION BY symbol ORDER BY bar_start) AS next_start
    FROM bars_1m
    WHERE dt >= current_date - INTERVAL '7' DAY
)
SELECT
    symbol,
    bar_start + INTERVAL '1' MINUTE AS gap_start,
    next_start AS gap_end,
    date_diff('minute', bar_start, next_start) - 1 AS missing_minutes
FROM ordered
WHERE date_diff('minute', bar_start, next_start) > 1
ORDER BY missing_minutes DESC, symbol, gap_start
