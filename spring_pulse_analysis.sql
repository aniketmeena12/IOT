-- ============================================================================
-- SPRING MACHINE PULSE ANALYSIS - InfluxDB SQL Queries
-- ============================================================================
-- 
-- CONTEXT:
--   A spring-making machine emits pulses (one per turn) via a sensor.
--   Pulses within a series (one spring) are closely spaced.
--   Between series (between springs), there is a larger time gap.
--
-- ASSUMPTIONS:
--   - Measurement name : "spring_pulses"
--   - Each pulse is a row with a timestamp (the "time" column).
--   - There may be a field like "value" (e.g., always 1) to record each pulse.
--   - The database is InfluxDB v3 (or IOx) which supports SQL / FlightSQL.
--   - "Inter-series gap" is significantly larger than "intra-series gap".
--     We use a threshold to distinguish them.
--
-- STRATEGY:
--   1. Compute the time gap between consecutive pulses.
--   2. Classify each gap as "intra-series" (within a spring) or
--      "inter-series" (between springs) using a threshold.
--   3. Assign a series number to each pulse based on where the large gaps fall.
--   4. Compute intra-series and inter-series averages over the last 10 series.
--
-- *** IMPORTANT: Adjust the threshold below to match your machine. ***
--   If intra-series gaps are ~50ms and inter-series gaps are ~500ms,
--   a threshold of 200ms (200000000 ns) is a good middle ground.
--   InfluxDB stores time in nanoseconds internally.
-- ============================================================================


-- ============================================================================
-- PARAMETER: GAP THRESHOLD (nanoseconds)
-- ============================================================================
-- Gaps BELOW this threshold  -> intra-series (pulses within one spring)
-- Gaps ABOVE this threshold  -> inter-series (gap between two springs)
--
-- Example: 200 ms = 200,000,000 ns.  Adjust to suit your machine.
-- In the queries below, replace 200000000 with your actual threshold.
-- ============================================================================


-- ============================================================================
-- QUERY 1: INTRA-SERIES AVERAGE (average time gap between consecutive
--          pulses WITHIN each of the last 10 series, plus overall average)
-- ============================================================================
-- This tells you the average "turn time" for each spring in the last 10,
-- and the overall average turn time across all 10 springs.
-- ============================================================================

WITH

-- Step 1: Get all pulses ordered by time, compute gap to previous pulse
pulse_gaps AS (
    SELECT
        time,
        time - LAG(time) OVER (ORDER BY time)  AS gap_ns
    FROM
        spring_pulses
    WHERE
        time >= now() - INTERVAL '1 hour'       -- adjust lookback window as needed
    ORDER BY time
),

-- Step 2: Flag each gap as inter-series (1) or intra-series (0)
--         The very first pulse has NULL gap; treat it as a series boundary.
flagged AS (
    SELECT
        time,
        gap_ns,
        CASE
            WHEN gap_ns IS NULL           THEN 1   -- first pulse ever => new series
            WHEN gap_ns > 200000000       THEN 1   -- gap > threshold => new series
            ELSE 0
        END AS is_new_series
    FROM pulse_gaps
),

-- Step 3: Assign a series number by running sum of the boundary flags
series_numbered AS (
    SELECT
        time,
        gap_ns,
        is_new_series,
        SUM(is_new_series) OVER (ORDER BY time ROWS UNBOUNDED PRECEDING) AS series_id
    FROM flagged
),

-- Step 4: Identify the last 10 distinct series
last_10_series AS (
    SELECT DISTINCT series_id
    FROM series_numbered
    ORDER BY series_id DESC
    LIMIT 10
),

-- Step 5: Keep only pulses belonging to the last 10 series
filtered AS (
    SELECT
        sn.time,
        sn.gap_ns,
        sn.is_new_series,
        sn.series_id
    FROM series_numbered sn
    INNER JOIN last_10_series l10 ON sn.series_id = l10.series_id
)

-- Step 6: Compute per-series and overall intra-series averages
--         Exclude rows where is_new_series = 1 (those gaps are inter-series)
SELECT
    series_id                                           AS series_number,
    COUNT(*)                                            AS pulse_count_in_series,
    ROUND(AVG(gap_ns) / 1000000.0, 3)                  AS avg_intra_gap_ms,
    ROUND(MIN(gap_ns) / 1000000.0, 3)                  AS min_intra_gap_ms,
    ROUND(MAX(gap_ns) / 1000000.0, 3)                  AS max_intra_gap_ms
FROM filtered
WHERE is_new_series = 0          -- only intra-series gaps
GROUP BY series_id
ORDER BY series_id;

-- --------------------------------------------------------------------------
-- To get the OVERALL intra-series average across all 10 series in one number:
-- --------------------------------------------------------------------------

WITH

pulse_gaps AS (
    SELECT
        time,
        time - LAG(time) OVER (ORDER BY time)  AS gap_ns
    FROM spring_pulses
    WHERE time >= now() - INTERVAL '1 hour'
    ORDER BY time
),

flagged AS (
    SELECT
        time,
        gap_ns,
        CASE
            WHEN gap_ns IS NULL       THEN 1
            WHEN gap_ns > 200000000   THEN 1
            ELSE 0
        END AS is_new_series
    FROM pulse_gaps
),

series_numbered AS (
    SELECT
        time,
        gap_ns,
        is_new_series,
        SUM(is_new_series) OVER (ORDER BY time ROWS UNBOUNDED PRECEDING) AS series_id
    FROM flagged
),

last_10_series AS (
    SELECT DISTINCT series_id
    FROM series_numbered
    ORDER BY series_id DESC
    LIMIT 10
),

filtered AS (
    SELECT sn.*
    FROM series_numbered sn
    INNER JOIN last_10_series l10 ON sn.series_id = l10.series_id
)

SELECT
    'Overall Intra-Series Average (last 10 series)'    AS metric,
    COUNT(*)                                            AS total_intra_gaps,
    ROUND(AVG(gap_ns) / 1000000.0, 3)                  AS avg_intra_gap_ms
FROM filtered
WHERE is_new_series = 0;


-- ============================================================================
-- QUERY 2: INTER-SERIES AVERAGE (average time gap between the LAST pulse
--          of one series and the FIRST pulse of the next series,
--          for the last 10 series)
-- ============================================================================
-- This tells you how long the wire-push / changeover takes on average.
-- ============================================================================

WITH

pulse_gaps AS (
    SELECT
        time,
        time - LAG(time) OVER (ORDER BY time)  AS gap_ns
    FROM spring_pulses
    WHERE time >= now() - INTERVAL '1 hour'
    ORDER BY time
),

flagged AS (
    SELECT
        time,
        gap_ns,
        CASE
            WHEN gap_ns IS NULL       THEN 1
            WHEN gap_ns > 200000000   THEN 1
            ELSE 0
        END AS is_new_series
    FROM pulse_gaps
),

series_numbered AS (
    SELECT
        time,
        gap_ns,
        is_new_series,
        SUM(is_new_series) OVER (ORDER BY time ROWS UNBOUNDED PRECEDING) AS series_id
    FROM flagged
),

last_10_series AS (
    SELECT DISTINCT series_id
    FROM series_numbered
    ORDER BY series_id DESC
    LIMIT 10
),

-- The inter-series gaps are exactly the rows where is_new_series = 1
-- AND gap_ns IS NOT NULL (exclude the very first pulse which has no prior).
-- Each such row's gap_ns = (first pulse of series N) - (last pulse of series N-1).
inter_gaps AS (
    SELECT
        sn.series_id,
        sn.time                                         AS series_start_time,
        sn.gap_ns
    FROM series_numbered sn
    INNER JOIN last_10_series l10 ON sn.series_id = l10.series_id
    WHERE sn.is_new_series = 1
      AND sn.gap_ns IS NOT NULL                         -- skip the very first pulse
)

SELECT
    series_id                                           AS series_number,
    series_start_time,
    ROUND(gap_ns / 1000000.0, 3)                        AS inter_series_gap_ms
FROM inter_gaps
ORDER BY series_id;

-- --------------------------------------------------------------------------
-- To get the OVERALL inter-series average in one number:
-- --------------------------------------------------------------------------

WITH

pulse_gaps AS (
    SELECT
        time,
        time - LAG(time) OVER (ORDER BY time)  AS gap_ns
    FROM spring_pulses
    WHERE time >= now() - INTERVAL '1 hour'
    ORDER BY time
),

flagged AS (
    SELECT
        time,
        gap_ns,
        CASE
            WHEN gap_ns IS NULL       THEN 1
            WHEN gap_ns > 200000000   THEN 1
            ELSE 0
        END AS is_new_series
    FROM pulse_gaps
),

series_numbered AS (
    SELECT
        time,
        gap_ns,
        is_new_series,
        SUM(is_new_series) OVER (ORDER BY time ROWS UNBOUNDED PRECEDING) AS series_id
    FROM flagged
),

last_10_series AS (
    SELECT DISTINCT series_id
    FROM series_numbered
    ORDER BY series_id DESC
    LIMIT 10
),

inter_gaps AS (
    SELECT sn.gap_ns
    FROM series_numbered sn
    INNER JOIN last_10_series l10 ON sn.series_id = l10.series_id
    WHERE sn.is_new_series = 1
      AND sn.gap_ns IS NOT NULL
)

SELECT
    'Overall Inter-Series Average (last 10 series)'    AS metric,
    COUNT(*)                                            AS total_inter_gaps,
    ROUND(AVG(gap_ns) / 1000000.0, 3)                  AS avg_inter_series_gap_ms,
    ROUND(MIN(gap_ns) / 1000000.0, 3)                  AS min_inter_series_gap_ms,
    ROUND(MAX(gap_ns) / 1000000.0, 3)                  AS max_inter_series_gap_ms
FROM inter_gaps;


-- ============================================================================
-- COMBINED DASHBOARD QUERY: Both metrics in one shot
-- ============================================================================
-- Returns one row per series with its intra-series avg and the inter-series
-- gap that preceded it.
-- ============================================================================

WITH

pulse_gaps AS (
    SELECT
        time,
        time - LAG(time) OVER (ORDER BY time)  AS gap_ns
    FROM spring_pulses
    WHERE time >= now() - INTERVAL '1 hour'
    ORDER BY time
),

flagged AS (
    SELECT
        time,
        gap_ns,
        CASE
            WHEN gap_ns IS NULL       THEN 1
            WHEN gap_ns > 200000000   THEN 1
            ELSE 0
        END AS is_new_series
    FROM pulse_gaps
),

series_numbered AS (
    SELECT
        time,
        gap_ns,
        is_new_series,
        SUM(is_new_series) OVER (ORDER BY time ROWS UNBOUNDED PRECEDING) AS series_id
    FROM flagged
),

last_10_series AS (
    SELECT DISTINCT series_id
    FROM series_numbered
    ORDER BY series_id DESC
    LIMIT 10
),

filtered AS (
    SELECT sn.*
    FROM series_numbered sn
    INNER JOIN last_10_series l10 ON sn.series_id = l10.series_id
),

-- Per-series intra averages
intra AS (
    SELECT
        series_id,
        COUNT(*) + 1                                    AS total_pulses,  -- +1 for the boundary pulse
        COUNT(*)                                        AS intra_gap_count,
        ROUND(AVG(gap_ns) / 1000000.0, 3)              AS avg_intra_gap_ms
    FROM filtered
    WHERE is_new_series = 0
    GROUP BY series_id
),

-- Per-series inter gap (the gap that started this series)
inter AS (
    SELECT
        series_id,
        ROUND(gap_ns / 1000000.0, 3)                   AS preceding_inter_gap_ms
    FROM filtered
    WHERE is_new_series = 1 AND gap_ns IS NOT NULL
)

SELECT
    intra.series_id                                     AS series_number,
    intra.total_pulses,
    intra.avg_intra_gap_ms,
    inter.preceding_inter_gap_ms
FROM intra
LEFT JOIN inter ON intra.series_id = inter.series_id
ORDER BY intra.series_id;


-- ============================================================================
-- NOTES & TUNING
-- ============================================================================
--
-- 1. THRESHOLD (200000000 ns = 200 ms):
--    This is the critical parameter. Set it to a value halfway between your
--    typical intra-series gap and your typical inter-series gap.
--    Example: if intra gaps ~ 50 ms and inter gaps ~ 500 ms, use 200 ms.
--
-- 2. LOOKBACK WINDOW (now() - INTERVAL '1 hour'):
--    Adjust this to cover enough history to include at least 10 complete
--    series. If your machine produces 1 spring per minute, 1 hour gives
--    ~60 series. If production is slower, increase accordingly.
--
-- 3. MEASUREMENT NAME:
--    Replace "spring_pulses" with your actual InfluxDB measurement name.
--
-- 4. InfluxDB VERSION COMPATIBILITY:
--    - InfluxDB 3.x (Cloud Serverless / IOx): Full SQL support, these
--      queries should work as-is.
--    - InfluxDB 2.x: Uses Flux, not SQL. You would need to rewrite in Flux.
--    - InfluxDB 1.x (InfluxQL): Window functions (LAG, SUM OVER) are NOT
--      supported. You'd need to export data or use a wrapper.
--
-- 5. TIME UNITS:
--    InfluxDB stores timestamps in nanoseconds. The queries convert to
--    milliseconds (÷ 1,000,000) for readability. Change the divisor if
--    you prefer microseconds or seconds.
--
-- ============================================================================
