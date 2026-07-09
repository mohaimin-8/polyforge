# ClickHouse Analytical Schema Design (W25a)

Decision record: [ADR 0011](adr/0011-clickhouse-analytical-store.md).
DDL: [`deploy/clickhouse/schema.sql`](../deploy/clickhouse/schema.sql).
Collector pipeline: [`deploy/clickhouse/otel-collector.yaml`](../deploy/clickhouse/otel-collector.yaml).

## Why a second store at all

The transactional repositories (SQLite locally, PostgreSQL in production)
answer the feature API over a bounded window — 15 minutes, 10k events.
The research phase asks a different shape of question: *scan months of
telemetry for every tenant, group by workload signature, feed the
evaluation matrix*. At the target scale (100M rows from trace replays) a
row store answers those in minutes; a columnar store in milliseconds to
seconds. The research pipeline (W25b replay, W26 feature extraction, W28
eviction benchmarks) reads from ClickHouse; the product API keeps reading
from the repository. Neither depends on the other being up.

## Table: `polyforge.requests_telemetry`

| Column | Type | Note |
|---|---|---|
| tenant_id | LowCardinality(String) | ≤ thousands of tenants |
| service | LowCardinality(String) | small fixed set |
| timestamp | DateTime64(3, 'UTC') | millisecond precision |
| rps_window | Float64, Gorilla+ZSTD | slowly-varying gauge — Gorilla XOR-encodes well |
| payload_bytes | UInt32, T64+ZSTD | integers cluster near zero |
| latency_ms | Float64, Gorilla+ZSTD | |
| cache_hit | Bool | |
| embedding_density | Float32, Gorilla+ZSTD | 0..1; Float32 halves storage, precision irrelevant |
| model_tier | LowCardinality(String) | {local, fast, quality, …} |
| child_spans | UInt16 | agent fan-out counter |

The column set matches `telemetry.Event` 1:1. The roadmap sketch named the
embedding column `embedding_norm`; the platform has called this signal
`embedding_density` since M4, and the schema follows the code, not the
sketch (deliberate deviation, noted in ADR 0011).

## Partitioning: `toYYYYMM(timestamp)`

- The program spans ~12 months → ~12 partitions. Partition count is a
  merge-scheduler cost; ClickHouse's own guidance is at most low hundreds
  of active parts per table.
- Retention is `TTL … + INTERVAL 12 MONTH`: dropping a partition is a
  metadata operation, no delete-merge.
- Daily partitioning (the tempting alternative) would produce ~365
  partitions × parts-per-insert-burst and slows both merges and
  `ALTER TABLE DROP PARTITION` workflows without helping queries: the
  evaluation queries are month- or run-scoped, so monthly pruning already
  eliminates the bulk of data.

## Primary index: `ORDER BY (tenant_id, timestamp)`

Every research query is tenant-scoped first (`WHERE tenant_id = 'acme'
AND timestamp BETWEEN …`). With the tenant leading the sort key, the
primary index skips every granule belonging to other tenants; the window
then reads a contiguous run of granules.

Sanity check to reproduce on a live cluster (the `EXPLAIN indexes = 1`
output belongs in the thesis appendix):

```sql
EXPLAIN indexes = 1
SELECT count(), quantile(0.95)(latency_ms)
FROM polyforge.requests_telemetry
WHERE tenant_id = 'acme'
  AND timestamp >= now() - INTERVAL 1 DAY;
```

Expected: `MinMax` prunes to 1–2 partitions; the primary-key step selects
a small fraction of granules (the tenant's share). With the reversed key
`(timestamp, tenant_id)` the same query selects *all* granules in the
window regardless of tenant — the 100×-slower failure mode W25a warns
about.

`index_granularity` stays at the 8192 default: rows are narrow (~60 bytes
compressed), so a granule is ~0.5 MB — the sweet spot between index size
and read amplification.

## Rollup: `tenant_minute_rollup`

The W27 online classifier polls a 15-minute window per tenant every 10
seconds. Against raw rows that is a scan per tenant per tick; against the
`AggregatingMergeTree` rollup it reads ≤ 15 pre-aggregated rows per
service. `quantileState(0.95)` keeps a mergeable sketch, so p95 over any
minute range is `quantileMerge` — no raw-row access. The MV is populated
at insert time; late data merges correctly because states are commutative.

## Ingest paths and their loss semantics

| Path | Mechanism | Loss behavior |
|---|---|---|
| OTel Collector (deployed) | `batch` → `sending_queue` on `file_storage` → clickhouse exporter | **No loss** across a ClickHouse pause: batches persist to the collector's disk queue and drain on recovery |
| In-process mirror (`internal/telemetry/analytics`) | bounded channel → batcher → HTTP `JSONEachRow` insert | **Best-effort**: full queue or exhausted retries drop events and increment a counter; the ingest response is never delayed |

Two further deliberate properties of the mirror:

- **At-least-once, not exactly-once.** A batch that times out after
  ClickHouse partially applied it is retried whole; duplicates are
  possible. Research queries are large-window aggregations where duplicate
  rates ≪ 1% do not move conclusions; runs that need exactness replay from
  the W25b parquet/CSV source of truth instead.
- **Trickle-insert avoidance.** ClickHouse creates a data part per insert;
  the batcher's 2048-row/2s coalescing keeps part creation well under the
  merge scheduler's comfort zone.

## Stress-test plan and status

Method (scripted, to run on the first Docker-capable machine or CI
follow-up):

1. `docker compose --profile analytics up -d clickhouse`
2. Replay driver (W25b) at 10× against `/v1/telemetry` with the mirror
   enabled until ≥ 1M rows land; record `system.events` insert metrics.
3. Scale the table to 100M rows with `INSERT … SELECT` amplification.
4. Run the tenant-window query above; assert p95 < 2s over 100 runs.
5. Pause the container 30s mid-replay (`docker pause`); assert the
   collector path loses nothing and the mirror's drop counter matches its
   log line.

**Status: not yet executed — this machine has no Docker.** What *is*
verified locally, by unit test: the JSONEachRow wire format, identifier
safety, batching, retry/backoff, queue-full drop accounting, and that a
failing sink never fails telemetry ingest. The schema DDL and collector
config are lint-clean YAML/SQL but have not been applied to a live
ClickHouse. Claims of p95 < 2s at 100M rows remain **unverified** until
the stress run executes.
