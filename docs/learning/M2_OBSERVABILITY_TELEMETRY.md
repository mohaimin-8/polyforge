# Learning Guide: Milestone 2 Observability and Telemetry

## What You Should Be Able to Explain

1. **Metrics versus logs:** logs describe individual events; metrics aggregate behavior over time and are cheaper to scrape repeatedly.
2. **Counters versus histograms:** counters only increase and answer "how many"; histograms bucket observations and support latency or payload percentile estimates.
3. **Route labels:** request metrics use route templates such as `/v1/tenants/{tenant_id}/projects` instead of raw URLs to avoid unbounded label cardinality.
4. **Tenant-cardinality boundary:** tenant IDs are intentionally excluded from `/metrics`; tenant-specific research queries belong in the telemetry store.
5. **Accepted telemetry:** telemetry metrics are recorded only after validation, authentication, and persistence succeed, so rejected payloads do not become workload evidence.
6. **Scrape timing:** the `/metrics` request itself is recorded after the response is written, so a scrape usually appears in the next scrape.
7. **Request ID versus trace ID:** `X-Request-ID` identifies one PolyForge HTTP exchange. `traceparent` can correlate the exchange with upstream and downstream services.
8. **Server span creation:** an incoming valid trace ID is preserved, but PolyForge generates a new server span ID so the server work is distinct from the caller's parent span.
9. **Scrape topology:** Prometheus runs in Docker and scrapes the host control plane through `host.docker.internal:8080`.
10. **Dashboard provisioning:** Grafana loads datasource and dashboard definitions from files, making the dashboard reproducible instead of manually configured.

## Code Reading Order

1. `internal/platform/metrics.go` for the in-process registry and Prometheus text output.
2. `internal/platform/trace.go` for W3C `traceparent` parsing and generation.
3. `internal/platform/server.go` for request middleware, route-pattern extraction, trace response headers, and telemetry metric recording.
4. `internal/platform/server_test.go` for the metrics and trace propagation regression tests.
5. `scripts/smoke.ps1` for end-to-end metrics verification during a local run.
6. `deploy/observability/prometheus/prometheus.yml` for the scrape target.
7. `deploy/observability/grafana/provisioning` for datasource and dashboard loading.
8. `deploy/observability/grafana/dashboards/polyforge-control-plane.json` for dashboard panels and PromQL.
9. `api/openapi.yaml` for the public `/metrics` and response-header contract.

## Hands-On Checks

```powershell
go test ./internal/platform -run TestMetricsEndpointExposesRequestAndTelemetryMetrics -count=1 -v
go test ./internal/platform -run TraceParent -count=1 -v
.\scripts\smoke.ps1
.\scripts\check-observability-config.ps1
```

With Docker installed:

```powershell
.\scripts\run-control-plane.ps1
.\scripts\run-observability.ps1
```

Open Grafana at `http://localhost:3000` and inspect the `PolyForge Control Plane` dashboard.

Run the control plane, ingest telemetry, and scrape:

```powershell
Invoke-WebRequest http://localhost:8080/metrics
```

Find these metric families:

- `polyforge_http_requests_total`
- `polyforge_http_request_duration_seconds`
- `polyforge_telemetry_events_total`
- `polyforge_telemetry_latency_ms`
- `polyforge_telemetry_payload_bytes`

Send a request with:

```text
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```

Confirm the response keeps trace ID `4bf92f3577b34da6a3ce929d0e0e4736` but returns a different span ID.

## Defense Questions

- Why is tenant ID excluded from Prometheus labels?
- Why should route templates be used instead of raw request paths?
- What claim can a request counter prove, and what claim can it not prove?
- Why does the telemetry counter increment only after storage succeeds?
- What additional evidence is needed before claiming full distributed tracing support?
- Why should a service generate a new span ID instead of reusing the incoming parent span ID?
- When would `X-Request-ID` be more convenient than a trace ID during debugging?
- Why does the local Prometheus target use `host.docker.internal`?
- Why is dashboard provisioning better evidence than a manually clicked dashboard?
