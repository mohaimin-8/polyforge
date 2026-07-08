# Milestone 2: Observability and Telemetry

## Current Scope

This milestone turns the control plane into a measurable platform component. The first implemented slice is dependency-free runtime metrics plus existing workload telemetry ingestion.

Implemented:

- Prometheus-compatible `GET /metrics`
- W3C `traceparent` propagation for all HTTP responses
- structured request logs with request ID, trace ID, span ID, parent span ID, and route template
- process start timestamp gauge
- HTTP request counter by method, route, and status
- HTTP request latency histogram by method and route
- accepted telemetry-event counter by service, model tier, and cache-hit flag
- accepted telemetry latency histogram by service
- accepted telemetry payload-size histogram by service
- metrics tests covering request and telemetry series
- local Prometheus scrape configuration
- local Grafana datasource and `PolyForge Control Plane` dashboard provisioning
- PowerShell observability config validator
- OpenAPI documentation for `/metrics`
- classifier feature-query API: `GET /v1/tenants/{tenant_id}/telemetry/features`
- feature aggregation across all three repositories (in-memory, SQLite, PostgreSQL)

Still required for full Milestone 2 exit:

- distributed trace exporter/backend integration
- analytical telemetry store beyond the operational SQLite/PostgreSQL repositories
- batch export path for offline classifier training datasets
- documented scrape/deployment topology for production-like runs

## Feature-Query API

The feature-query API is the boundary between raw telemetry and the Milestone 4
classifier. It aggregates events into one row per service over a half-open time
window and returns the features the classifier consumes.

```text
GET /v1/tenants/{tenant_id}/telemetry/features?service=&since=&until=
```

Semantics:

- the window is `[since, until)`: `since` is inclusive, `until` is exclusive
- `until` defaults to now; `since` defaults to `until - 15m` (`DefaultFeatureLookback`)
- an unparsable or inverted window is rejected with `400`, not silently repaired
- at most `MaxFeatureEvents` (10,000) events are read per query
- rows are sorted by service; blank service and blank model tier bucket as `unknown`
- a tenant API key only ever observes its own tenant's rows

Each row carries `event_count`, `avg_rps_window`, `avg_payload_bytes`,
`avg_latency_ms`, `p95_latency_ms`, `cache_hit_rate`, `avg_embedding_density`,
`avg_child_spans`, `model_tier_counts`, and the first/last event timestamps.

The aggregation math lives in `internal/telemetry` (`BuildFeatureSet`), so all three
repositories produce identical rows from identical events. Each repository is
responsible only for selecting the events inside the window. PostgreSQL additionally
enforces the tenant boundary with row-level security rather than trusting the
`tenant_id` predicate in the query.

Rejecting an inverted window at the API edge is deliberate. `NormalizeFeatureQuery`
repairs one internally so no repository is ever handed an invalid range, but a
silently repaired window would let an experiment record a time range it never
actually measured. Evaluation runs must fail loudly instead.

## Metrics Boundary

The `/metrics` endpoint avoids tenant IDs in labels. Tenant identifiers are high-cardinality and sensitive in many deployments. Tenant-level research analysis should query the telemetry store or future analytical store instead.

Current exported metric families:

```text
polyforge_process_start_time_seconds
polyforge_http_requests_total
polyforge_http_request_duration_seconds
polyforge_telemetry_events_total
polyforge_telemetry_latency_ms
polyforge_telemetry_payload_bytes
```

## Trace Boundary

PolyForge now handles HTTP trace context directly:

- valid incoming `traceparent` values preserve the trace ID
- each request receives a new server span ID
- invalid or missing `traceparent` values are replaced with a new context
- `X-Request-ID` remains the short operational correlation ID, while `traceparent` enables distributed trace correlation

This is propagation support, not a full trace backend. Milestone 2 still needs exporter integration and a dashboard or trace viewer for production-like runs.

## Dashboard Stack

The local dashboard stack is Docker Compose based:

```powershell
.\scripts\run-control-plane.ps1
.\scripts\run-observability.ps1
```

Endpoints:

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`
- Grafana credentials: `admin` / `polyforge-local`

Prometheus scrapes `host.docker.internal:8080/metrics` so the control plane can continue running directly on the host. Grafana provisions the `PolyForge Control Plane` dashboard from `deploy/observability/grafana/dashboards/polyforge-control-plane.json`.

## Evidence

Run:

```powershell
go test ./internal/platform -run TestMetricsEndpointExposesRequestAndTelemetryMetrics -count=1 -v
go test ./internal/platform -run TraceParent -count=1 -v
go test ./internal/telemetry -count=1 -v
go test ./internal/platform -run TestTelemetryFeaturesEndpointAggregatesPerService -count=1 -v
go test ./internal/storage/sqlite -run TestFeaturesAggregatesWithinWindowAndTenant -count=1 -v
.\scripts\test-postgres.ps1
.\scripts\smoke.ps1
.\scripts\check-observability-config.ps1
```

The feature-query tests assert the same behavior at three layers: `internal/telemetry`
covers the aggregation math (percentile edges, half-open window boundaries, `unknown`
bucketing), the SQLite and PostgreSQL suites cover repository selection and the tenant
boundary, and the `internal/platform` suite covers request validation and HTTP status
mapping. `TestPostgresFeaturesAreTenantScopedByRowLevelSecurity` additionally queries
`telemetry_events` with no `tenant_id` predicate to prove row-level security guards the
analytical path, not just the CRUD tables. It requires Docker and is skipped when
`POLYFORGE_TEST_POSTGRES_ADMIN_URL` and `POLYFORGE_TEST_POSTGRES_APP_URL` are unset.

The test verifies that accepted telemetry appears in `/metrics` and that request metrics use stable route templates instead of raw high-cardinality URLs.

The smoke script builds the service, exercises tenant/project/telemetry/classifier/controller behavior, scrapes `/metrics`, and fails if the core PolyForge metric families are missing.

The observability config check validates the dashboard JSON and verifies that Prometheus and Grafana provisioning reference the expected PolyForge targets and datasource UID. Docker is still required to prove runtime scraping and dashboard rendering.

Run the service locally, create a tenant, ingest telemetry, then scrape:

```powershell
Invoke-WebRequest http://localhost:8080/metrics
```

Expect Prometheus text exposition with HTTP and telemetry series.
