-- Top 25 tables by Trino compute cost, last 7 days.
-- Point this at your Iceberg-backed query log (partitioned by day(query_date)).
-- Scoped to a 7-day window and SELECT-only to keep the scan bounded; the query
-- log itself is large enough that unscoped aggregations time out.
--
-- "Trino cost" here = cpu_time_millis, the resource Trino itself bills against (CPU-seconds is
-- the standard Trino cost-attribution unit -- distinct from S3 storage/request cost). Also
-- reports total physical bytes read and query count as supporting signals for WHY a table is
-- expensive (large full scans vs. many small hot queries).
WITH per_query_tables AS (
    SELECT
        query_id,
        cpu_time_millis,
        wall_time_millis,
        physical_input_bytes,
        json_extract_scalar(t, '$.schema') AS schema_name,
        json_extract_scalar(t, '$.table') AS table_name
    FROM example.trino_logs.trino_queries
    CROSS JOIN UNNEST(CAST(json_parse(inputs_json) AS ARRAY(json))) AS u(t)
    WHERE query_date >= current_date - interval '7' day
      AND query_type = 'SELECT'
      AND query_state = 'FINISHED'
      AND json_extract_scalar(t, '$.connectorName') = 'iceberg'
      -- Exclude Iceberg metadata-table reads ($files, $snapshots, $manifests, ...) so a table's
      -- own maintenance/introspection queries don't inflate its "user query cost" ranking.
      AND json_extract_scalar(t, '$.table') NOT LIKE '%$%'
)
SELECT
    schema_name,
    table_name,
    count(DISTINCT query_id) AS query_count_7d,
    sum(cpu_time_millis) / 1000.0 / 3600.0 AS total_cpu_hours_7d,
    sum(wall_time_millis) / 1000.0 / 3600.0 AS total_wall_hours_7d,
    sum(physical_input_bytes) / power(1024, 3) AS total_physical_gb_scanned_7d,
    avg(cpu_time_millis) / 1000.0 AS avg_cpu_seconds_per_query
FROM per_query_tables
GROUP BY schema_name, table_name
ORDER BY total_cpu_hours_7d DESC
LIMIT 25;
