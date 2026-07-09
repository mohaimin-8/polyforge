-- PolyForge analytical schema (roadmap W25a, ADR 0011).
-- Mounted as ClickHouse initdb so `docker compose up clickhouse` creates it.
--
-- Design rationale, EXPLAIN evidence, and the stress-test method live in
-- docs/CLICKHOUSE_DESIGN.md. The two decisions that dominate query cost:
--
--   PARTITION BY toYYYYMM(timestamp)
--     Monthly parts keep the part count low (a 47-week program touches ~12
--     partitions) while letting retention drop whole months. Finer daily
--     partitioning would create thousands of parts at research scale and
--     slow merges; coarser (yearly) would defeat partition pruning for the
--     month-scoped evaluation queries.
--
--   ORDER BY (tenant_id, timestamp)
--     Every research query starts "for tenant X over window W". Leading
--     with tenant_id makes the primary index skip all foreign-tenant
--     granules; timestamp second makes the window a contiguous range scan.
--     The reverse order (timestamp first) would scan every tenant's rows
--     for the window — measurably 100x worse on wide windows.

CREATE DATABASE IF NOT EXISTS polyforge;

CREATE TABLE IF NOT EXISTS polyforge.requests_telemetry
(
    tenant_id         LowCardinality(String),
    service           LowCardinality(String),
    timestamp         DateTime64(3, 'UTC'),
    rps_window        Float64 CODEC(Gorilla, ZSTD(1)),
    payload_bytes     UInt32  CODEC(T64, ZSTD(1)),
    latency_ms        Float64 CODEC(Gorilla, ZSTD(1)),
    cache_hit         Bool,
    embedding_density Float32 CODEC(Gorilla, ZSTD(1)),
    model_tier        LowCardinality(String),
    child_spans       UInt16
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (tenant_id, timestamp)
TTL toDateTime(timestamp) + INTERVAL 12 MONTH
SETTINGS index_granularity = 8192;

-- Pre-aggregated per-minute rollup for the online classifier's feature
-- queries (W27 reads a 15-minute window per tenant every 10 seconds; that
-- must not scan raw rows at 100M-row scale). AggregatingMergeTree keeps
-- exact counts and mergeable quantile sketches.
CREATE TABLE IF NOT EXISTS polyforge.tenant_minute_rollup
(
    tenant_id       LowCardinality(String),
    service         LowCardinality(String),
    minute          DateTime('UTC'),
    events          AggregateFunction(count),
    rps             AggregateFunction(avg, Float64),
    payload         AggregateFunction(avg, Float64),
    latency_avg     AggregateFunction(avg, Float64),
    latency_p95     AggregateFunction(quantile(0.95), Float64),
    cache_hits      AggregateFunction(sum, UInt64),
    embedding       AggregateFunction(avg, Float64),
    child_spans_avg AggregateFunction(avg, Float64)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(minute)
ORDER BY (tenant_id, service, minute);

CREATE MATERIALIZED VIEW IF NOT EXISTS polyforge.tenant_minute_rollup_mv
TO polyforge.tenant_minute_rollup
AS SELECT
    tenant_id,
    service,
    toStartOfMinute(timestamp)                    AS minute,
    countState()                                  AS events,
    avgState(rps_window)                          AS rps,
    avgState(toFloat64(payload_bytes))            AS payload,
    avgState(latency_ms)                          AS latency_avg,
    quantileState(0.95)(latency_ms)               AS latency_p95,
    sumState(toUInt64(cache_hit))                 AS cache_hits,
    avgState(toFloat64(embedding_density))        AS embedding,
    avgState(toFloat64(child_spans))              AS child_spans_avg
FROM polyforge.requests_telemetry
GROUP BY tenant_id, service, minute;
