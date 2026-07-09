# ADR 0011: ClickHouse as the analytical telemetry store

Status: accepted · 2026-07-10 · sink + batcher implemented, cluster deploy pending (W25a)

## Context

The research phase (M7+) reads telemetry at a scale the transactional
stores were never meant for: trace replays produce 100M+ rows, and the
classifier (W26–W27), the eviction benchmarks (W28), and the evaluation
matrix (M10) all issue wide analytical scans over them. The feature API's
bounded window (15 min / 10k events, see SESSION_LOG on `MaxFeatureEvents`)
is the correct product behavior but cannot support research queries. The
open decision recorded at the end of M2 was ClickHouse vs. "just use
PostgreSQL harder".

## Decision

**ClickHouse is the analytical store; the transactional repository remains
the source of truth for the product API.**

- PostgreSQL-with-indexes was rejected for the analytical role: the
  research queries are full-column scans and quantiles over months, which
  a row store answers via heap scans at two to three orders of magnitude
  more I/O. BRIN indexes and partitioning narrow but do not close the gap,
  and tuning them would consume research weeks to approximate what a
  columnar engine gives by construction.
- TimescaleDB (Postgres extension) was the closest contender — one fewer
  system to run. Rejected because the evaluation matrix's group-by-signature
  queries are classic OLAP, the roadmap's paper baseline (Table 1 queries,
  §Evaluation) assumes sub-2s scans at 100M rows, and ClickHouse's
  AggregatingMergeTree rollups map directly onto the W27 10-second polling
  loop.

Consequences of the split: the analytical store may lag or lose events and
the product must not care. That is encoded in the two ingest paths — the
OTel Collector pipeline with a persistent queue (no-loss, deployed path)
and the in-process best-effort mirror (`internal/telemetry/analytics`)
whose loss semantics are bounded, counted, and never block ingest.

## Deliberate deviations from the roadmap sketch

1. **`embedding_density` instead of `embedding_norm`.** The platform has
   emitted `embedding_density` since M4; renaming in the analytics schema
   would create a silent semantic seam between the stores. The schema
   follows the code.
2. **Helm deploy deferred.** The roadmap says "deploy via Helm chart"; this
   machine has no Docker/K8s, so W25a ships the compose fixture, the DDL,
   and the collector config, and the live stress test (1M-row ingest, p95
   < 2s at 100M rows, 30s-pause backpressure drill) is documented as
   pending in `docs/CLICKHOUSE_DESIGN.md` rather than claimed.
3. **HTTP `JSONEachRow` over the native protocol.** No driver dependency,
   mesh-friendly, tap-inspectable; at ≤ 2048-row batches the protocol
   difference is noise.

## Verification

- `internal/telemetry/analytics`: wire format, injection-safe identifiers,
  batch/flush/retry/drop accounting — unit-tested, race-clean.
- `internal/platform`: accepted events are mirrored; rejected events are
  not; a full or failing analytics queue never changes an ingest response.
- Live-cluster claims: **pending**, tracked in `docs/CLICKHOUSE_DESIGN.md`.
