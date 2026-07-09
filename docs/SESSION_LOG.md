# Session Log

`docs/EXECUTION_PLAN.md` requires that every work session produce runnable code,
passing tests, measured data, thesis text, or a decision record, and that the
session close by recording milestone status, what changed, how it was verified,
and what to study next. This file is that record. Newest entry first.

---

## 2026-07-09 (session 7) — W20-W24: LLM routing, tenant isolation ladder, canary, Vault/OIDC, security hardening

Milestone status: closes the *implementable-on-this-machine* core of W20
(local LLM serving + routing policy), W21 (multi-tenant isolation modes),
W22 (mesh + canary), W23 (Vault + OIDC + secret scanning), and W24
(network policy + OWASP + supply chain). The user explicitly asked for the
W20-24 block in one session (overriding the usual ~5% slice), with license
to deviate from the HTML where a better path exists. Honest roadmap
position after this session: **~70-71%** against the roadmap's 72% marker
for end-of-W24 — the gap is exactly the cluster-execution items this
machine cannot run (live Ollama benchmark numbers, Linkerd mTLS tap,
live Keycloak login, ZAP scan, compromised-pod simulation), all of which
now have runnable harnesses/runbooks and pending-status documentation.

### What changed — W20 local LLM serving + routing (ADR 0007 extended)

- **Native Ollama adapter** (`gateway.Ollama`, `/api/chat` NDJSON): the
  native API reports `prompt_eval_count`/`eval_count`, which the benchmark
  needs for honest tokens/sec. Tool calls map both directions.
- **Policy router** (`gateway.Router` + `deploy/routing.json`): routes
  /chat per request by tenant plan, prompt length, and a per-tenant daily
  budget (UTC-day ledger; exhaustion degrades to the cheapest backend
  rather than failing). Decision exposed via `X-PolyForge-Backend` /
  `X-PolyForge-Route-Reason` headers; usage charged post-response from
  provider-reported tokens. Routing happens only on cache miss. This
  static rule table is the baseline JCAC (M8) will replace.
- **Benchmark harness** (`internal/ai/bench`, `cmd/llmbench`): identical
  streaming instrumentation for every backend; TTFT = first delta,
  throughput = tokens/(total−TTFT); per-request CSV to `benchmarks/`,
  aggregate markdown for `docs/INFERENCE_BENCH.md`. **No live numbers
  published** — no Ollama on this machine; the doc says so explicitly.
- Compose gains an `ai` profile (ollama + one-shot model puller).

### What changed — W21 isolation ladder (ADR 0008)

- `isolation_mode` was already a column; it is now a *state machine*:
  `POST /v1/tenants/{id}/isolation` (admin-only) promotes pool → bridge →
  silo, upward-only (skip allowed, downgrade 409). Implemented in all
  three stores; Postgres takes `FOR UPDATE` on the tenant row; every
  promotion writes a `tenant.promoted` outbox event in-transaction, so
  namespace provisioning can be event-driven later.
- `deploy/k8s/tenant-silo-template.yaml`: Namespace + ResourceQuota +
  LimitRange per silo tenant (envsubst-instantiated).
- Deliberate deviation, documented in the ADR: bridge's schema-per-tenant
  data migration is specified but **not implemented** — zero-downtime
  copy/cutover needs a real Postgres to prove and this machine has none.

### What changed — W22 mesh + canary (ADR 0009)

- **Two canary layers.** Mesh: `deploy/linkerd/` (install runbook, mTLS
  verification commands, SMI TrafficSplit 95/5 over new
  `deploy/k8s/ai-gateway.yaml` v1/v2 deployments; inject annotations added
  to workloads). App: `gateway.CanaryProvider` — deterministic
  position-mod-100 split between model hosts with a sliding-window breaker
  that auto-rolls-back on error spike. The roadmap's rollback gate is a
  unit test (`TestCanaryErrorSpikeAutoRollsBack`), not a manual step.

### What changed — W23 secrets + OIDC (ADR 0010)

- `internal/secrets`: `Dir` (Vault-Agent files; re-read per call, so
  rotation is instant — beats the 60s gate) → `Vault` (KV v2, Kubernetes
  auth with lease-aware token renewal) → `Env` chain. Control plane admin
  key and gateway LLM key now resolve through it.
- `internal/auth/oidc.go`: stdlib OIDC relying party — discovery, JWKS
  with rotation-tolerant refetch, S256 PKCE, code exchange, ID-token
  validation with alg pinned to RS256. Tested end-to-end against a fake
  IdP that *enforces* PKCE server-side; negative tests cover wrong aud,
  expired, wrong issuer, tampered payload.
- CI gains a `secret-scan` job (gitleaks, full history) and an `sbom` job
  (syft CycloneDX artifact). `deploy/vault/`, `deploy/keycloak/` dev
  manifests + runbooks.

### What changed — W24 hardening

- `deploy/k8s/networkpolicies.yaml`: default-deny both directions, then
  DNS + ingress→services + control-plane→{postgres,redis} +
  ai-gateway→ollama; postgres/redis also pin inbound to the control plane.
- `docs/SECURITY.md`: OWASP API Top 10 (2023) line-by-line with code
  citations, threat model, supply-chain section, and an explicit
  known-gaps list (API7 egress allowlist is network-level only; ZAP and
  pod-pivot simulation pending Docker).
- `release.yml`: tag-triggered image push to GHCR + cosign keyless sign +
  CycloneDX SBOM attestation. `scripts/zap-baseline.sh` for the pen-test.
- OpenAPI spec documents the new isolation endpoint (redocly-valid).

### How it was verified

```text
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1 -cover           -> all pass (bench 89%, gateway 74%,
                                           auth 81%, secrets 70%, tenant 83%)
npx @redocly/cli lint api/openapi.yaml  -> valid
```

Not verified locally: `-race` (no gcc on this machine — CI runs it),
PostgreSQL suite incl. the new `PromoteTenantIsolation` (skips without
Docker; CI executes it), golangci-lint (not installed; CI runs v2.6.2),
gitleaks/sbom/release workflows (first execution on push), and everything
listed above as needing a cluster. Treat all of those as unproven until
the next green CI run.

### What to be able to explain next

- Why the router charges budget from *reported* usage post-response
  rather than estimating pre-route, and what a provider that reports no
  usage does to the ledger (answer: nothing — cost 0; the bench estimates
  at 4 chars/token but the ledger never guesses).
- Why isolation demotion is a 409 and not a feature.
- Why the canary splits deterministically (position mod 100) instead of
  randomly, and what that does to test reproducibility and low-traffic
  rollouts.
- Why the ID-token verifier pins RS256 (alg=none / RS-HS confusion).

### Immediate next tasks

1. Push; confirm CI green including the two new jobs — that run is the
   proof for -race, Postgres promotion test, gitleaks, and SBOM.
2. First live run of `cmd/llmbench` on a machine with Ollama; paste the
   table into docs/INFERENCE_BENCH.md and commit `benchmarks/inference.csv`.
3. W25 (M7 research phase) starts the telemetry-pipeline/classifier work —
   the routing policy and isolation ladder built this session are its
   actuators.

---

## 2026-07-09 (session 6) — ADR 0006 events cluster + M5 AI cluster (W14/W15, W17-W19)

Milestone status: closes the W14/W15 events cluster whole (outbox, NATS
relay, audit events, onboarding saga, CQRS projection — the slice ADR 0006
specified) and lands the M5 AI-workload MVP: W17 embeddings + vector
search, W18 LLM gateway with semantic cache and streaming, W19 tool-use
agent loop. Two commits, each cluster landing with its tests, plus one
CI-fix commit. CI is green (65.4% coverage). Honest roadmap position after
this session: ~59-60% (W19 is the roadmap's own 59% marker; pgvector and
local model serving are the noted deferrals).

### What changed — events cluster (commit 1)

- **Transactional outbox in all three stores.** Every write path (tenant
  provision/create/delete, project create/update/delete, key
  create/revoke/rotate) inserts the domain row and an audit event in the
  same transaction — memory store under the same mutex, SQLite and
  PostgreSQL in the same tx (`003_outbox.sql`; app role gets INSERT only,
  RLS scopes it to its tenant; the outbox has deliberately no FK to
  tenants so the stream outlives its subjects). Shared constructors in
  `internal/tenant/audit.go` keep payloads byte-identical across backends
  and never include secrets or hashes.
- **Relay + JetStream backbone (`internal/events`).** The relay drains
  unpublished rows in insertion order and publishes with
  `Nats-Msg-Id = outbox.id`. Crash between publish and mark-published is
  the tested case: the retry republishes and JetStream's duplicate window
  drops it — `TestRelayRetryAfterCrashDoesNotDuplicate` proves stream
  count stays 1 against an embedded `nats-server` (no Docker, per ADR
  0006's verification stance).
- **Onboarding saga (`internal/saga`).** create tenant → bootstrap key →
  default-policy event → welcome event; every step idempotent, every step
  with an explicit compensation (event-only steps compensate by emitting a
  `saga.compensated` fact — appended events cannot be unappended). State
  persists in the DB (`sagas` table in SQLite/Postgres, memory for tests).
  Tests cover the happy path, mid-saga failure with full compensation
  (tenant gone, created+deleted both in the stream), crash-resume from a
  persisted cursor without minting duplicate keys or events (deterministic
  saga event IDs + `INSERT OR IGNORE`/`ON CONFLICT DO NOTHING`), and
  terminal-state stickiness.
- **CQRS projection.** `tenant_summary` folds the stream into per-tenant
  counters; the regeneration test replays the stream twice and asserts
  byte-identical JSON, then cross-checks counts against the write model.
- `cmd/control-plane` starts the relay when `POLYFORGE_NATS_URL` is set;
  without it events accumulate in the outbox and publish on the first
  start that has a backbone — which is the outbox's whole point.

### What changed — AI cluster (commit 2, ADR 0007)

- **W17 (`internal/ai/embed`, `internal/ai/vector`).** One `Embedder`
  interface; `OpenAI` speaks any OpenAI-compatible `/embeddings` endpoint,
  `Local` is a deterministic hashed n-gram embedder that makes every
  downstream assertion exact and offline. The vector index is exact
  per-tenant brute-force cosine — the recall=1.0 baseline pgvector/HNSW
  will be benchmarked against (`BenchmarkSearch10k`). Tenant isolation is
  structural (one map per tenant), tested by asserting another tenant sees
  zero results.
- **W18 (`internal/ai/gateway`).** Chat proxy over the OpenAI wire
  protocol with SSE streaming both up and down; per-tenant semantic cache
  at 0.95 cosine over the full conversation (fails open on embedder
  errors, `X-PolyForge-Cache: hit/miss` headers, hit/miss counters on
  `/metrics`); tenant-scoped semantic search (`/ai/index`, `/ai/search`).
  Auth reuses the control plane's API keys and scopes. Every AI request
  records a telemetry event, so the classifier sees AI workload classes.
- **W19 (`internal/ai/agent`).** OpenAI tool-calling loop with three
  tools: `polyforge_query` (tenant's projects via the same repository as
  the HTTP API — isolation inherited, not reimplemented), `polyforge_calc`
  (recursive-descent arithmetic parser, no eval), `polyforge_search`
  (injected engine; DuckDuckGo scrape in the binary, fake in tests). Tool
  failures go back to the model as tool output so it can recover; the run
  errors after 8 steps. `agent.run` → `llm.call`/`tool.call` spans give
  the multi-span causal chain, and `child_spans = llm_calls + tool_calls`
  lands in telemetry — the agentic-multistep feature the W26 classifier
  reads.
- **`cmd/ai-gateway`** (`:8081`): second service binary sharing the
  control plane's SQLite database; defaults to Ollama's local endpoint and
  the local embedder, so it runs with zero external dependencies.

### How it was verified

```text
gofmt -l .                                   -> clean
go vet ./...                                 -> pass
staticcheck ./...                            -> clean
go test ./... -count=1 -covermode=atomic     -> pass, total 60.5% local
```

Local coverage of 60.5% is with the PostgreSQL suite skipped (no Docker on
this machine). CI (race detector + PostgreSQL 18) is GREEN on `be57419`
with **65.4% total coverage** — after one CI-only failure that proves why
the CI-first stance matters: the first run of `003_outbox.sql` failed
`TestPostgresRowLevelIsolation` with `permission denied for table outbox`,
because `INSERT ... ON CONFLICT (id)` needs SELECT privilege on the
arbiter column and the app role is deliberately INSERT-only on the outbox.
Fix: the tenant-transaction path (always fresh random IDs) drops the
conflict clause; deterministic-ID idempotent appends stay on the
admin-role `AppendEvent`. The green run is the first execution of the
PostgreSQL outbox, its RLS policy, and the INSERT-only grant.

### Deliberate deviations from the roadmap's W17-W19 letter

- pgvector + HNSW deferred (needs a CI Postgres image with the extension);
  the exact in-memory index is the measured baseline it must beat.
- No real model host in CI: scripted providers + the local embedder keep
  the gate deterministic. The wire protocol is tested against fakes that
  speak real OpenAI JSON/SSE.
- Ollama/local serving (W20) untouched — next cluster.

### Immediate next tasks

1. W20 local model serving: point `cmd/ai-gateway` at Ollama and demo the
   agent end-to-end against a real model.
2. Choose the pgvector CI image and land the Postgres-backed vector index
   against the exact baseline (ADR 0007 consequence).
3. Add the gateway's AI endpoints to `api/openapi.yaml` (currently the
   spec covers only the control plane).

---

## 2026-07-09 (session 5) — W11 OTel migration, W13 Redis limiter, W14 idempotency, W16 5k-RPS gate

Milestone status: closes W11's SDK migration (traces now come from the OTel
SDK, not the hand-rolled traceparent parser), W13's sliding-window limiter
with per-tenant budgets, and W14's idempotency half; passes the W16
performance gate after finding and fixing a real bottleneck. The W14/W15
events cluster (NATS, outbox, saga, CQRS) is deferred whole — one backbone
decision, not four half-features (ADR 0005).

### What changed

**W11 — OTel SDK tracing (`internal/platform`, `cmd/control-plane`).**

- `trace.go` (hand-rolled W3C parser/generator) deleted; the request
  middleware now extracts context with the OTel propagator, opens a real
  SDK server span, renames it to the low-cardinality route pattern after
  routing, and stamps method/route/status attributes. 5xx sets span error
  status and `writeError` records the error on the span.
- Logs carry `trace_id`/`span_id` from the span context — correlation
  survives the migration. Response `traceparent` is injected by the
  propagator; the old propagation tests were ported to parse with the
  propagator and still pass.
- `TestRequestsEmitOTelServerSpans` proves a request joins a remote trace,
  is a server span named `GET /v1/tenants/{tenant_id}/projects`, and
  carries the golden attributes.
- `main.go` builds the TracerProvider; spans export via OTLP/HTTP only when
  `POLYFORGE_OTLP_ENDPOINT` is set. Collector config at
  `deploy/observability/otel-collector/config.yaml` (+ compose service).

**W13 — Redis sliding-window rate limiter (`internal/limit`).**

- One Lua script over a sorted set: prune, count, admit, expire — atomic
  server-side. Proven exact under 200 concurrent goroutines (admits exactly
  the limit) and resident in the script cache (SCRIPT EXISTS), both against
  miniredis, so the logic is CI-verifiable without Docker.
- Platform accepts any `RequestLimiter`; the token bucket is the no-Redis
  fallback. Credentialed traffic on `/v1/tenants/{id}/...` shares one
  budget per tenant (two keys of one tenant proven to spend one budget);
  anonymous traffic stays host-keyed so strangers cannot drain a tenant.
- Redis unavailable ⇒ fail open with a warning (availability over
  strictness; rationale in ADR 0005). Proven by killing miniredis mid-test.

**W14 (half) — Idempotency-Key replay (`internal/idempotency`).**

- Claim (SETNX) → execute → cache response → replay on retry with
  `Idempotency-Replayed: true`; concurrent duplicate ⇒ 409; 5xx never
  cached. Key scoped to credential + method + path + key + body digest, so
  cross-tenant replay is impossible and a reused key with a different body
  executes as a distinct operation. Redis and in-memory stores share one
  contract test; the HTTP gate proves the write executed exactly once.

**W16 — performance gate, measured, with a real fix.**

- `loadgen` gained `-rate` (shared token dispatcher minting from measured
  elapsed time — immune to Windows timer granularity).
- First run: **~830 RPS ceiling, p99 216 ms** on the authenticated projects
  list. Root cause: `SetMaxOpenConns(1)` + default journal in the SQLite
  store — every request serialized on one connection.
- Fix: WAL + per-connection DSN pragmas (busy_timeout, foreign_keys,
  synchronous=NORMAL) + NumCPU connection pool. Also replaced the
  random-ID ordering tiebreak with `rowid` after the pool exposed a
  key-listing order flake (timestamps collide at Windows clock granularity).
- Gate run (5 minutes sustained, authenticated endpoint, SQLite):
  **5,266 RPS, p50 2.87 ms, p99 22.6 ms, max 160 ms, zero non-2xx, zero
  transport errors over 1,579,872 requests** — artifact at
  `artifacts/m4-projects-5krps.json`. Gate: ≥5,000 RPS, p99 < 50 ms.
  (A first 5-minute run at `-rate 5000` delivered 4,917 RPS — 98.3% —
  because the generator sheds tokens during transient scheduler stalls;
  the committed run targets 5,300 so delivered throughput clears the gate
  with margin. Server headroom, not saturation: p50 under 3 ms.)
- `docs/SERVICE_TEMPLATE.md` records the 14-point cross-cutting checklist
  every future service is reviewed against, each row tied to its proving
  test.

**Plumbing.** Redis (AOF) + otel-collector services in compose;
`POLYFORGE_REDIS_URL` switches limiter + idempotency to Redis. OpenAPI
0.6.0: `Idempotency-Key` parameter on the Projects mutating operations.
New deps: OTel SDK + OTLP/HTTP exporter, go-redis v9, miniredis (test).

### How it was verified

```
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1                  -> pass (10 packages, incl. 5x sqlite for the ex-flake)
npx @redocly/cli lint api/openapi.yaml  -> valid, 0 warnings
loadgen -rate 5000 -duration 5m         -> gate PASSED (artifact committed)
```

Not verified locally (Docker-blocked, unchanged): real Redis persistence/
failover, compose bring-up, kind/Helm, collector end-to-end, RLS tests.
`-race` still needs cgo ⇒ CI-only.

### What to be able to explain next

- Why the sliding window must be one Lua script (what breaks with
  ZREMRANGEBYSCORE + ZCARD + ZADD as three client calls).
- Why the idempotency key includes the body digest, and what Stripe does
  differently with the same collision.
- Why `SetMaxOpenConns(1)` was a 6x throughput ceiling and what WAL changes
  about reader/writer interaction.
- Why fail-open is right for rate limits and wrong for billing quotas.

### Addendum (same day): first push and first green CI

Repo published to `github.com/mohaimin-8/polyforge` (private). The first CI
run earned its keep immediately:

1. Lint gate caught 3 staticcheck QF1008 findings in `internal/auth/jwt.go`
   (explicit selectors through the embedded `rsa.PublicKey`) — fixed.
2. The RLS integration tests failed their first-ever execution with
   `role "polyforge_admin" does not exist`: the CI role-creation step used
   `docker exec` without `-i`, so psql received no stdin and exited 0
   having executed nothing. A green checkmark on a step that did nothing —
   fixed with `-i` and a warning comment.

Run 3 is fully green: gofmt, vet, golangci-lint, **race detector across all
packages** (first execution ever — no cgo locally), **both PostgreSQL RLS
tests against real Postgres 18** (multi-tenant isolation now proven, not
just designed), coverage 66.5% (gate ratcheted 55% → 60%), OpenAPI lint.
ADR 0006 (NATS JetStream backbone: subjects, outbox schema, relay dedupe
semantics, embedded-server test strategy) also landed, unblocking W14/W15.

### Immediate next tasks

1. Implement ADR 0006: outbox schema + relay → audit events → onboarding
   saga → CQRS projection (W14 remainder + W15), each landing whole with
   embedded-NATS tests.
2. Docker-capable host: compose up, Redis failover drill, kind/Helm, image
   size gate, SLO outage drill. Consider a CI job for the Docker build +
   Trivy to close the W9 gates without local Docker.

---

## 2026-07-09 (session 4) — W7 JWT layer closed; W9/W10/W12 deployment + SLO artifacts written (Docker-blocked, unverified)

Milestone status: closes the W7 remainder (RS256 JWT issuer, refresh
rotation, JWKS, key rotation without restart — the last open W7 item after
OAuth2/Hydra was deferred whole, see ADR 0004). Completes the four golden
signals at `/metrics` (W11's metrics deliverable; the OTel SDK migration
itself remains open). Writes the W9 Docker, W10 kind/Helm, and W12 SLO
artifacts, none of which can be executed on this machine.

### What changed

**`internal/auth` — stdlib RS256 JWT issuer (W7).**

- JWS compact serialization on `crypto/rsa` — zero new dependencies
  (ADR 0004). Verifier pins `alg` to RS256 before key lookup; tests attack
  it with `alg=none`, a foreign key, and a tampered payload.
- Refresh tokens: opaque, single-use, stored as SHA-256 digests in memory.
  Replay of a consumed token is 401. In-memory means a restart drops
  refresh sessions — accepted and documented in ADR 0004.
- Key rotation keeps the previous key verifiable: `TestKeyRotationWithoutRestart`
  proves pre-rotation tokens verify, new tokens carry a new kid, and the
  JWKS material itself verifies real signatures (reconstructed `rsa.PublicKey`
  from n/e, the way an external verifier would).

**Platform wiring.**

- `POST /v1/auth/token` (API key → token pair; JWT inherits the key's
  scope), `POST /v1/auth/token/refresh`, `GET /.well-known/jwks.json`
  (public), `POST /v1/auth/keys/rotate` (admin).
- `authorizeTenant` accepts `Authorization: Bearer` as a peer of the API-key
  header, with the same 401/403 contract; wrong-tenant tokens are 403.
  Bearer credentials get their own rate-limit buckets.
- The W7 gate restated for JWTs is automated: read-scope bearer token → 200
  on GET, 403 on POST.
- `/metrics` gains `polyforge_http_in_flight_requests` (saturation), so all
  four golden signals are now derivable from the endpoint.
- `control-plane -healthcheck` subcommand probes `/healthz` and exits 0/1,
  because the distroless image has no shell for container health checks.

**Deployment artifacts — written, NOT verified locally (no Docker).**

- `Dockerfile`: multi-stage, CGO off (modernc SQLite is pure Go),
  distroless/static-debian12 nonroot. The <20MB gate is unproven.
- `compose.yaml`: control-plane service added (postgres-healthy dependency,
  self-probe healthcheck). `docker compose up` has never been run here.
- `deploy/k8s/`: namespace, postgres (Recreate strategy, RLS roles via init
  ConfigMap), control-plane (2 replicas, probes, read-only rootfs), NGINX
  ingress `/api`. `deploy/helm/polyforge/`: chart templating image tag,
  replicas, resources, admin-key Secret, rate limits. Schema-reviewed only;
  `helm install` and the rollout-restart gate need a kind cluster.
- `docs/SLO.md`: 99.9% availability / p99 < 200ms objectives against the
  real metric names, error budget (43.2 min/30d), and the 14.4×/6× burn-rate
  alert expressions. Alerts are not loaded into Prometheus yet.
- OpenAPI 0.5.0: four auth paths, `BearerToken` scheme offered on all
  tenant-scoped operations, `TokenPair`/`JWKS` schemas. Redocly-clean.

### How it was verified

```
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1                  -> pass (auth: 7 tests; platform: +3 endpoint suites)
npx @redocly/cli lint api/openapi.yaml  -> valid, 0 warnings
```

Not verified: everything under "Deployment artifacts" above, plus the two
standing PostgreSQL RLS integration tests (still Docker-blocked). CI on
first push is the earliest environment that can prove any of it.

### What to be able to explain next

- Why pinning `alg` before key lookup kills both `alg=none` and RS256→HS256
  confusion, and why that ordering (not the library choice) is the security
  property.
- Why refresh-token rotation must consume the token even on the success
  path, and what replay window exists if it doesn't.
- Why 429 is excluded from the availability SLI in `docs/SLO.md`.

### Immediate next tasks

1. First `git push`: proves CI (race/lint/coverage), Postgres migrations,
   and can add a Docker build + Trivy job to exercise the W9 artifacts.
2. W11 proper: migrate hand-rolled traceparent/metrics to the OTel SDK
   (whole, not half — same rule as Hydra).
3. On any Docker-capable host: `docker compose up`, image-size gate,
   `helm install` on kind, and the SLO fake-outage drill.

---

## 2026-07-09 (session 3) — API-key scopes with RBAC, and the 30k-RPS gate beaten 2.4×

Milestone status: closes the roadmap's W7 deliverable "API keys with scoped
permissions" including its verification gate ("read-only key returns 403 on
POST"), and the W4 throughput gate ("≥ 30,000 RPS for a simple GET handler").
Commits: `a06f329` (scopes + RBAC), plus this session's benchmark commit.

### What changed

**Read/full API-key scopes, enforced per route (`a06f329`).**

- `tenant` package: `ScopeRead`/`ScopeFull` constants, `NormalizeScope`,
  `ValidScope`, and `ScopeAllows` (fails closed on unknown scopes and unknown
  requirements). `APIKey.Scope` is immutable for the key's lifetime and
  preserved by rotation — widening access requires a new credential.
- All three repositories persist scope. SQLite backfills legacy rows to
  `full` during migration (asserted by the legacy-migration test); PostgreSQL
  migration `002_api_key_scope.sql` adds the column with `DEFAULT 'full'` and
  a CHECK constraint. Backfilling to full rather than least-privilege was
  deliberate: least-privilege would silently break every existing
  deployment's credentials.
- `authorizeTenant` takes the route's required scope: GETs require `read`,
  mutations and telemetry ingest require `full`. 401 (bad credential) vs 403
  (valid credential, missing authority) is deliberate, with a structured
  `forbidden` error code.
- `TestReadOnlyScopeIsEnforced` automates the roadmap gate: a read key gets
  403 with code `forbidden` on every mutating route, 200 on every read route;
  a full key mutates; invalid scopes are 400.
- OpenAPI 0.4.0: `scope` on `APIKey`/`CreateAPIKey`, `Forbidden` response,
  403 documented on all 13 tenant-scoped operations. Redocly-clean.

**HTTP throughput baseline (W4 gate).**

- `cmd/loadgen`: Go load generator — fixed worker pool, per-worker latency
  slices (no locks in the hot path), context cancellation, JSON artifact with
  RPS/percentiles/runtime metadata. Doubles as the W3 concurrency reference
  implementation.
- `scripts/bench-http.ps1`: builds server + loadgen, runs 15 s against
  `/healthz` with the limiter off and logs at warn, writes
  `artifacts/m1-http-baseline.json`.
- `POLYFORGE_LOG_LEVEL` env var (default `info`): per-request access logs are
  info-level, so benchmarks set `warn` to measure the handler path rather
  than the logger. A production ops knob the service needed anyway.

### Measured result

**71,893 RPS, p99 1 ms, 0 transport errors, 0 non-2xx over 1,078,405
requests** in 15 s at concurrency 16 on this 8-CPU Windows laptop — the
roadmap gate is ≥ 30,000. Caveat recorded honestly: Windows' ~0.5 ms
interrupt-timer granularity quantizes sub-millisecond samples, so p50 reads
0 and is not meaningful; throughput is computed over the full wall-clock run
and is unaffected. The middleware chain (request ID, trace context, metrics,
recoverer) was active during the run.

### How it was verified

gofmt/vet/golangci-lint clean, `go test ./...` 7/7 packages, Redocly lint
clean, smoke script green end-to-end, benchmark run twice (warmup discarded).
PostgreSQL migration 002 and the integration-test scope assertions compile
locally but execute only in CI (no local Docker) — not claimed as locally
verified.

### Immediate next tasks

1. Push to a remote; watch race + coverage + lint + migration 002 go green.
2. W7 remainder: JWT RS256 + JWKS (deferred — half-landing auth is worse
   than deferring it).
3. W5 evidence artifacts (EXPLAIN ANALYZE) and W9 Dockerfiles — both need
   Docker; consider installing Docker Desktop before next session.

---

## 2026-07-09 (session 2) — Testing gates, auth coverage, and roadmap alignment

Milestone status: closes the roadmap's W8 testing gates (race in CI, coverage
gate, auth-package coverage) and the W4 recoverer gap. The internal
EXECUTION_PLAN.md milestones remain the day-to-day tracker; `docs/THESIS_DETAILS.html`
is the source roadmap they compress.

### Verification of the previous session

Commit `2de9a04` intact, clean tree, `gofmt`/`go vet`/`go build`/`go test ./...`
all green. The end-to-end feature endpoint behavior was re-proven in session 1;
nothing regressed.

### Audit findings (against the roadmap's own verification gates)

The roadmap requires `go test -race` on every test from W3 onward, and W8 sets
"coverage ≥ 80% on control-plane, 100% on auth; CI: lint → test → race → coverage
gate". Reality found:

1. **The race detector had never run.** Not locally (Windows `-race` requires
   CGO and this machine has no C compiler) and not in CI, which ran plain
   `go test`. All the concurrent code — in-memory stores, rate limiter, metrics
   registry — was unproven against races.
2. **`internal/tenant` had 0% coverage.** That package holds API-key generation,
   hashing, authentication, revocation, and rotation — the auth layer where the
   roadmap demands 100%.
3. **API-key IDs leaked secret entropy.** `NewRandomAPIKey` derived the key ID
   from the same random bytes as the secret (`ID = hex(raw[:8])`, secret =
   `hex(raw)`), so every key ID — visible in URLs, listings, and logs — exposed
   64 of the secret's 192 entropy bits. Not practically exploitable (128 bits
   remain), but a defense examiner would ask, and the fix costs one extra
   `rand.Read`.
4. **No panic-recovery middleware** (W4 expects requestID, logger, recoverer,
   timeout, CORS). A panicking handler dropped the connection with no
   structured error and no request ID for correlation.
5. `internal/controller` at 28% coverage; total 45.1%.

### What changed

- `internal/tenant/store_test.go` (new): provisioning, normalization, project
  CRUD + keyset pagination boundaries, API-key lifecycle including cross-tenant
  authentication/revocation boundaries and rotation atomicity, key-shape
  assertions (including "ID must not be derivable from the secret"), and a
  concurrent-access test that gives CI's race detector real contention.
  Package coverage 0% → 94.9%.
- `internal/tenant/store.go`: key IDs now drawn independently of the secret.
- `internal/controller/controller_test.go`: full action-vector table for all
  six labels plus policy invariants (bursty > steady replicas, cacheable >
  uncacheable cache budget, CRUD never routes to a model, agentic triggers
  guarded fairness). 28% → 100%.
- `internal/platform/server.go`: `recoverPanics` middleware inside the
  `requestLog` chain — structured 500 envelope with request ID, stack logged
  server-side, panic details never sent to the client,
  `http.ErrAbortHandler` re-panicked per net/http convention. Two tests.
- `.github/workflows/ci.yml`: tests now run `-race -covermode=atomic`, and a
  coverage gate fails CI under 55% total (ratchet toward 80% as packages gain
  tests; local-without-Docker total is 57.5%, CI adds the PostgreSQL suite).
- golangci-lint v2.6.2 run locally first (never gate CI on an unverified tool):
  it reported 16 `errcheck` findings — unchecked `rows.Close`, `tx.Rollback`,
  `resp.Body.Close`, and one deferred cleanup `Exec`. All were the intentional
  ignore-the-error idiom; each is now an explicit `_ =` so intent is visible.
  With the tree clean, the linter was added to CI as a gate.

### Roadmap alignment notes (THESIS_DETAILS.html vs. repository)

Tool-level deviations that are deliberate, with matching outcomes:

- Roadmap says chi router; repo uses stdlib `http.ServeMux` (Go 1.22+ method
  patterns cover the need). Outcome equivalent; revisit only if middleware
  ergonomics demand it.
- Roadmap says sqlc + golang-migrate; repo uses hand-written SQL behind
  repository interfaces and an idempotent migrator. Outcomes (type-safe access,
  versioned schema, RLS proven by test) hold. An ADR should record this choice.
- Roadmap says argon2id for API keys; repo hashes with SHA-256. This is sound:
  argon2id exists to slow brute force on low-entropy passwords, while PolyForge
  secrets are 192-bit random strings where brute force is infeasible and fast
  hashing is O(1) per request. Worth an ADR — this exact question is
  interview/defense bait.
- Roadmap's W7 JWT/OAuth2/RBAC scopes and W9 Dockerfile/W10 Helm remain open
  and are the natural next milestones on the main track.

### How it was verified

`gofmt` clean, `go vet` pass, `go build` pass, `go test ./... -count=1` — 7/7
packages ok. Coverage gate logic exercised against the real profile in both
directions (passes at 55, fails at 90). **The race detector still cannot run on
this machine** (no C compiler for CGO); the CI `-race` job is the first
execution and is unproven until a green run. Mutex placement in the tenant
store, telemetry store, metrics registry, and rate limiter was manually
reviewed before gating CI on it.

### Immediate next tasks

1. Push; confirm the new race + coverage gates and the PostgreSQL RLS tests go
   green in CI.
2. ADRs: SHA-256-for-high-entropy-keys, and stdlib-SQL-vs-sqlc.
3. Next roadmap slice: W9 Dockerfile (multi-stage distroless) + compose for the
   full local stack — requires Docker, so plan it for a machine/CI that has it.
   Alternatively W7 auth scopes (read-only vs full keys), which is fully
   testable locally.

---

## 2026-07-09 — Repository audit, build repair, and the M2 feature-query API

Milestone status: **M2 in progress.** The feature-query API exit item is now closed.
Trace exporter, analytical store, and batch export remain open.

### Audit findings

The working tree was audited before any code was written. Four defects were found.

1. **The build was broken.** `internal/storage/postgres` did not compile:
   `*Store` did not satisfy `telemetry.Repository` because `Features` was never
   implemented. A previous session added `Features` to the interface, to the
   in-memory store, and to SQLite, but stopped before PostgreSQL. The SQLite
   development path masked this, because `go run ./cmd/control-plane` with no
   `POLYFORGE_POSTGRES_ADMIN_URL` never compiles against the broken package in a
   way the developer would notice. Only the production path was dead.
2. **CI would have failed on formatting.** `internal/telemetry/store.go` had
   misaligned struct fields and const block. The CI `Verify formatting` step runs
   `test -z "$(gofmt -l .)"`, which fails on any unformatted file.
3. **The repository had zero commits.** Every one of the 52 source files was
   untracked. Roughly two milestones of work existed only as uncommitted working-tree
   state, with no history, no diff review, and no recovery point.
4. **Structure defects.** An empty `.git` directory sat one level above the real
   repository, which made tooling report the parent folder as a Git repository while
   every Git command inside it failed. `THESIS_DETAILS.html` — the 10-month thesis and
   career roadmap, and the source document behind `EXECUTION_PLAN.md` — lived outside
   version control entirely.

### What changed

- `internal/telemetry/store.go` reformatted with `gofmt`.
- `internal/storage/postgres/store.go` gained `Features`, restoring the build. It runs
  inside `withTenantTx`, so the row-level-security tenant context is set before the
  query, and it calls `requireTenant` so an unknown tenant yields `ErrTenantNotFound`
  exactly as SQLite does.
- `internal/platform/server.go` gained `GET /v1/tenants/{tenant_id}/telemetry/features`,
  exposing the aggregation that already existed in the repository layer but was
  unreachable over HTTP.
- `api/openapi.yaml` documents the path, its three query parameters, and the
  `TelemetryFeatureSet` / `TelemetryFeatureRow` schemas.
- `internal/telemetry/store_test.go` is new. The package previously had no tests at all,
  despite holding the classifier's feature math.
- Feature tests added to the SQLite, PostgreSQL, and HTTP suites.
- The empty stray `.git` was removed; `THESIS_DETAILS.html` moved to `docs/`.
- The repository received its first commits.

### Design decisions

**An invalid time window is a `400`, not a repair.** `NormalizeFeatureQuery` silently
rewrites an inverted or zero window to the default 15-minute lookback, which is correct
as a repository-layer guarantee: no store can be handed a range it cannot execute. But
if the HTTP layer inherited that behavior, an experiment could request
`since=T, until=T-1h`, receive `200 OK`, and record features for a window it never
asked for. Reproducible evaluation requires that a malformed request fail loudly. The
handler therefore validates bounds before normalization.

**The window is half-open, `[since, until)`.** Adjacent windows tile without
double-counting an event that falls exactly on a boundary. This matters once the
controller consumes one feature window per control interval; a closed upper bound
would count boundary events twice across consecutive intervals.

**Aggregation lives in one place.** `BuildFeatureSet` is the only implementation of the
feature math. Each repository selects events and delegates. Three stores cannot drift
into three different definitions of `p95_latency_ms`, which would silently invalidate
any cross-backend comparison in the evaluation matrix.

**Tenant isolation is proven, not assumed.** The PostgreSQL feature query filters on
`tenant_id`, so a passing test proves only that the `WHERE` clause works.
`TestPostgresFeaturesAreTenantScopedByRowLevelSecurity` therefore also queries
`telemetry_events` with no `tenant_id` predicate at all, inside a tenant transaction
context, and asserts the foreign tenant's rows are invisible. That is a test of RLS.
The pre-existing isolation test only covered `projects`; the analytical table that the
entire research contribution reads from was never checked.

### How it was verified

Every CI gate was run locally:

```text
gofmt -l .                              -> clean
go vet ./...                            -> pass
go test ./... -count=1                  -> pass
npx @redocly/cli lint api/openapi.yaml  -> valid
```

Not verified locally: `TestPostgresFeaturesAreTenantScopedByRowLevelSecurity` and
`TestPostgresRowLevelIsolation`. Docker is not installed on this machine, so both
**skipped** rather than passed. They compile and vet. CI runs PostgreSQL 18 as a service
and will be the first environment to actually execute the new RLS assertion. Treat it as
unproven until a CI run is green.

### What to be able to explain next

- Why row-level security is enforced at the database rather than in the query builder,
  and what class of bug that defends against. See `docs/adr/0003`.
- Why the `/metrics` endpoint deliberately omits tenant IDs as labels while the feature
  API is tenant-scoped by design. The distinction is cardinality and blast radius.
- What `p95_latency_ms` computed over a 15-minute window of at most 10,000 events can
  and cannot support as a claim. `MaxFeatureEvents` is a silent truncation: a tenant
  above that rate yields a percentile over a prefix of the window, not the window.

### Immediate next tasks

1. Push and confirm a green CI run, which is the only proof the PostgreSQL RLS test passes.
2. Decide whether `MaxFeatureEvents` truncation should be surfaced in the response
   (for example a `truncated: true` field) rather than silently changing what a
   percentile means. This affects evaluation validity and should be an ADR.
3. Continue M2: trace exporter integration, then the analytical store decision
   (ClickHouse vs. PostgreSQL), which also wants an ADR.
