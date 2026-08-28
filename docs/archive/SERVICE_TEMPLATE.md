# PolyForge Service Template

Roadmap W16 deliverable. The control-plane's Projects API is the reference
implementation; every future PolyForge service (AI gateway, auth split-out,
JCAC controller) is reviewed against this checklist before it merges.

## Cross-cutting concerns — every endpoint, no exceptions

| Concern | Reference implementation | Proven by |
|---|---|---|
| Input validation | `validResourceID` / `validName`, strict JSON decoding (`DisallowUnknownFields`, single object, 1 MB cap) | `TestProjectCRUDAndRequestErrorContract` |
| SQL injection | Parameterized queries only; no string-built SQL | code review gate |
| AuthN | Tenant API key (SHA-256 digest lookup) or RS256 JWT via `Authorization: Bearer` | `TestAuthTokenIssuanceAndBearerRBAC` |
| AuthZ / RBAC | `ScopeAllows` per route: reads need `read`, mutations need `full`; 401 vs 403 distinction | `TestReadOnlyScopeIsEnforced` |
| Tenant isolation | Repository-level scoping + PostgreSQL RLS (`NOBYPASSRLS` app role) | RLS integration tests (CI) |
| Rate limiting | Redis sliding-window Lua script, one budget per tenant; token bucket fallback; fail-open | `TestRedisSlidingWindowLimitsPerTenant` |
| Idempotency | `Idempotency-Key` header → claim → response cache → replay; body-digest scoped | `TestIdempotencyKeyMakesWritesRepeatSafe` |
| Tracing | OTel SDK server span per request, W3C propagation, route-pattern span names | `TestRequestsEmitOTelServerSpans` |
| Metrics | Four golden signals at `/metrics` (traffic, errors, latency histogram, in-flight) | `/metrics` scrape |
| Logging | Structured slog with `request_id`, `trace_id`, `span_id`; secrets never logged, 5xx details never sent to clients | `TestRecoverPanicsReturnsStructured500` |
| Error contract | `errorEnvelope` with stable `code`, message, request ID | error-contract tests |
| Panic safety | `recoverPanics` → structured 500, `http.ErrAbortHandler` re-raised | panic tests |
| Graceful shutdown | signal → `server.Shutdown` with deadline | `cmd/control-plane/main.go` |
| Health/container probes | `/healthz` + `-healthcheck` self-probe subcommand (distroless has no shell) | compose/k8s manifests |

## Performance gate

Every service must hold **5,000 RPS sustained for 5 minutes with p99 < 50 ms
and zero 5xx** on its hottest read endpoint before it ships. Measured with
`cmd/loadgen -rate 5000`; artifact committed under `artifacts/`.

Reference result (projects list, authenticated, SQLite backend, this
machine): 5,266 RPS sustained for 5 minutes, p99 22.6 ms, zero errors —
see `artifacts/m4-projects-5krps.json`.
The gate initially failed at ~830 RPS; the fix (WAL + connection pool,
`internal/storage/sqlite`) is the worked example of "measure, find the
bottleneck, fix, re-measure".

## Not yet in the template (deferred whole, ADR 0005)

Outbox-backed events, sagas, and CQRS projections (W15) wait for the NATS
JetStream backbone decision. When they land, "every write publishes an
outbox event" joins the checklist above.
