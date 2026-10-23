-- The 20 largest close-to-close moves between consecutive minutes, last 7 days.
-- Pairs separated by a gap are skipped: they are not 1-minute moves.
WITH moves AS (
    SELECT
        symbol,
        bar_start,
        close,
        lag(close) OVER (PARTITION BY symbol ORDER BY bar_start) AS prev_close,
        lag(bar_start) OVER (PARTITION BY symbol ORDER BY bar_start) AS prev_start
    FROM bars_1m
    WHERE dt >= current_date - INTERVAL '7' DAY
)
SELECT
    symbol,
    bar_start,
    prev_close,
    close,
    close - prev_close AS change,
    (close - prev_close) / prev_close * 100 AS change_pct
FROM moves
WHERE prev_start = bar_start - INTERVAL '1' MINUTE
ORDER BY abs((close - prev_close) / prev_close) DESC, symbol, bar_start
LIMIT 20
