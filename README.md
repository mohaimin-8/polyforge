# PolyForge

PolyForge is a thesis-grade platform engineering research project for adaptive multi-tenant backend infrastructure that hosts hybrid CRUD and AI workloads.

The target research contribution is a workload-aware controller that observes tenant traffic, classifies workload behavior, and recommends joint control actions across replicas, cache budget, and model routing.

## Current Phase

The current implementation provides a persistent control-plane backend:

- tenant registration API
- complete project CRUD with keyset pagination
- SQLite local persistence and PostgreSQL production persistence behind repository interfaces
- admin and tenant API-key authentication
- API-key listing, revocation, and atomic rotation
- request IDs, W3C `traceparent` propagation, structured JSON errors, and structured request logs
- Prometheus-compatible `/metrics` endpoint for HTTP and accepted telemetry metrics
- in-process token-bucket rate limiting for authenticated and anonymous API traffic
- PostgreSQL row-level security with transaction-local tenant context
- workload telemetry ingestion
- classifier feature-query API over a bounded time window
- deterministic workload classifier
- adaptive policy recommendation endpoint

SQLite remains the zero-infrastructure development path. PostgreSQL is the production path and requires separate admin and application roles so row-level security is testable rather than bypassed accidentally.

## Run

```powershell
.\scripts\run-control-plane.ps1
```

The run script uses the local admin key `pf_admin_local`. Create a tenant:

```powershell
$headers = @{ "X-PolyForge-Admin-Key" = "pf_admin_local" }
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8080/v1/tenants `
  -Headers $headers `
  -ContentType "application/json" `
  -Body '{"id":"acme","name":"Acme Research"}'
```

The response contains a one-time `bootstrap_api_key.secret`. Use it as `X-PolyForge-API-Key` for tenant-scoped endpoints.

## Test

```powershell
.\scripts\test.ps1
.\scripts\smoke.ps1
```

The API contract is at `api/openapi.yaml`.

## Observability

Scrape local metrics:

```powershell
Invoke-WebRequest http://localhost:8080/metrics
```

The endpoint exposes request counters, request-latency histograms, accepted telemetry-event counters, telemetry-latency histograms, and payload-size histograms. It intentionally does not expose tenant IDs as Prometheus labels; tenant-level analysis uses the telemetry repository.

Every HTTP response includes `X-Request-ID` and `traceparent`. If a valid incoming `traceparent` header is provided, the trace ID is preserved and PolyForge creates a new server span ID. If it is absent or invalid, PolyForge starts a new trace context.

With Docker installed, start the local Prometheus and Grafana stack:

```powershell
.\scripts\run-observability.ps1
```

Prometheus opens at `http://localhost:9090`. Grafana opens at `http://localhost:3000` with `admin` / `polyforge-local` and provisions the `PolyForge Control Plane` dashboard. Keep the control plane running on port `8080`; Prometheus scrapes it through `host.docker.internal:8080`.

## Classifier Features

Aggregate a tenant's telemetry into per-service classifier features:

```powershell
$headers = @{ "X-PolyForge-API-Key" = $tenantKey }
$uri = "http://localhost:8080/v1/tenants/acme/telemetry/features?since=2026-01-01T00:00:00Z"
Invoke-RestMethod -Uri $uri -Headers $headers
```

The window is `[since, until)`. Both bounds are optional RFC 3339 timestamps; `until`
defaults to now and `since` defaults to 15 minutes earlier. An inverted or unparsable
window returns `400` rather than being silently corrected. Add `service=<name>` to
restrict the aggregation to one service.

## Local CRUD Baseline

Generate a repeatable local SQLite CRUD profile:

```powershell
.\scripts\bench-crud.ps1
```

The default run creates 4 tenants and performs 25 project create/read/update/list/delete cycles per tenant. The latest artifact is `artifacts/m1-crud-baseline.json`.

## Rate Limiting

The control plane enables a local token-bucket limiter by default:

```text
POLYFORGE_RATE_LIMIT_RPM=600
POLYFORGE_RATE_LIMIT_BURST=60
```

Limits are keyed by remote address for tenant-management routes, tenant API-key fingerprint for tenant-scoped routes, or remote address when no credential is present. Set either value to `0` only for isolated local debugging.

## PostgreSQL

With Docker installed, run the RLS integration suite:

```powershell
.\scripts\test-postgres.ps1
```

To run the service against the local PostgreSQL fixture:

```powershell
docker compose up -d --wait postgres
$env:POLYFORGE_POSTGRES_ADMIN_URL = "postgres://polyforge_admin@localhost:5432/polyforge?sslmode=disable"
$env:POLYFORGE_POSTGRES_APP_URL = "postgres://polyforge_app@localhost:5432/polyforge?sslmode=disable"
$env:POLYFORGE_ADMIN_KEY = "replace-for-local-use"
go run ./cmd/control-plane
```

The Compose database uses trust authentication and binds only to `127.0.0.1`; it is a test fixture, not a deployment configuration.

## Long-Term Target

The production/research target includes:

- Go data-plane services
- PostgreSQL tenant isolation
- Redis semantic cache
- AI gateway with local/provider routing
- online workload classifier
- Kubernetes operator and CRDs
- controller/planner
- reproducible benchmark harness
- 1,800-run evaluation matrix
- thesis report, figures, slides, and defense material

See `docs/EXECUTION_PLAN.md` for the full program.
