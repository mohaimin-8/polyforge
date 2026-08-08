# PolyForge Security Posture (roadmap W24)

The security story for the paper's §Threat Model: OWASP API Security
Top 10 (2023) walked line-by-line against the implementation, the network
model, and the supply chain. Every claim cites the code or manifest that
enforces it; anything unenforced is listed as a gap, not omitted.

## Threat model in one paragraph

PolyForge is a multi-tenant control plane plus AI gateway. The assets are
tenant rows (Postgres), tenant vectors (in-memory index), credentials, and
LLM spend. The adversaries considered: a malicious or compromised tenant
trying to read another tenant's data or exhaust shared resources; a
compromised pod pivoting laterally; a leaked credential; a poisoned
dependency or image. Out of scope: malicious cluster admin, hardware.

## OWASP API Security Top 10 (2023)

| # | Risk | Status | Enforcement |
|---|---|---|---|
| API1 | Broken Object Level Authorization | ✅ | Every object route is tenant-scoped: the API key is authenticated *against the tenant in the URL* (`internal/platform/server.go` `authorizeTenant`, same in the AI gateway), and PostgreSQL RLS re-enforces the predicate at the database (ADR 0003), so a missed WHERE clause cannot leak rows. Cross-tenant key use returns 401 (tested). |
| API2 | Broken Authentication | ✅ | API keys are random (24B), stored hashed, scope-fixed at creation, rotatable (`internal/tenant`). JWTs are RS256 with kid-based rotation without restart (`internal/auth`, ADR 0004). Human login federates to OIDC with mandatory S256 PKCE and pinned RS256 (`internal/auth/oidc.go`, ADR 0010). Admin key comparison is constant-time in **both** services — `subtle.ConstantTimeCompare` in the control plane (`internal/platform/server.go` `isAdmin`) and the AI gateway (`internal/ai/gateway/knobs.go` `authorizeAdmin`, corrected session 35: it previously used a plain `!=`, a timing side channel on a key that also unlocks the control-plane admin surface). Tenant API keys are compared as SHA-256 hashes, so no invertible timing leak exists there. |
| API3 | Broken Object Property Level Authorization | ✅ | All request bodies decode with `DisallowUnknownFields`; responses are explicit structs — no mass assignment, no reflected extra properties. Key secrets appear once at creation; audit events never carry secrets or hashes (`internal/tenant/audit.go`). |
| API4 | Unrestricted Resource Consumption | ✅ | **Control plane**: Redis sliding-window rate limits with an in-process token-bucket fallback that now stays armed as a second tier and takes over on a Redis error instead of failing fully open (`internal/platform/server.go`, corrected session 35), request body caps (`MaxBytesReader`, 1 MiB), pagination caps (`MaxPageSize`). **AI gateway** (added session 35 — it previously had none): a per-tenant token bucket in front of every AI route (`internal/ai/gateway/ratelimit.go`, default RPM 600 / burst 60, secure-by-default), a per-tenant semantic-cache entry cap and document-index cap so one tenant cannot OOM the shared process (`cache.go` `DefaultCacheCapacityPerTenant`, `server.go` `maxDocsPerTenant`). **Both**: per-tenant daily LLM budget with cheapest-backend degradation (`gateway.Router`), K8s ResourceQuota/LimitRange in silo mode (W21). |
| API5 | Broken Function Level Authorization | ✅ | Admin surface (tenant CRUD, isolation promotion, key rotation endpoints) requires the admin key; tenant keys cannot reach it (tested in `isolation_http_test.go`). Scope model separates read from full keys (`ScopeAllows`). |
| API6 | Unrestricted Access to Sensitive Business Flows | ✅ | Tenant provisioning and isolation promotion are admin-only and audited to the outbox; idempotency keys (W13) stop replay-driven resource creation. |
| API7 | Server Side Request Forgery | ⚠️ | LLM/embedding base URLs are operator config, not user input. The agent's web-search tool fetches a fixed search endpoint with the query URL-encoded — user text never becomes a URL. Gap: no egress allowlist inside the process; network-level containment relies on the W24 NetworkPolicies. |
| API8 | Security Misconfiguration | ✅ | Default-deny NetworkPolicies (`deploy/k8s/networkpolicies.yaml`), mTLS everywhere via Linkerd (W22), distroless runtime image, secrets out of env into Vault chain (W23), gitleaks in CI over full history. Dev-mode manifests carry self-labeled `dev-only` placeholders. |
| API9 | Improper Inventory Management | ✅ | One OpenAPI document (`api/openapi.yaml`) linted in CI and kept in parity with the mux; ADRs document every surface; SBOM per build (CI) and per image (release workflow). |
| API10 | Unsafe Consumption of APIs | ✅ | Every upstream response (LLM providers, Vault, OIDC, search) is size-capped (`io.LimitReader`), schema-decoded, and status-checked; provider failures map to 502 without echoing upstream bodies to clients. |

## Network

`deploy/k8s/networkpolicies.yaml`: default deny ingress+egress, then
allowlists for ingress→control-plane, ingress→ai-gateway,
control-plane→{postgres,redis}, ai-gateway→ollama, all→DNS. Postgres and
Redis additionally pin their *inbound* peers to the control plane, so a
compromised ai-gateway pod cannot open a database connection at all —
the roadmap's compromised-pod simulation, with the verification command
in the manifest header. Requires an enforcing CNI (Calico on kind).

## Supply chain

- **SBOM**: CycloneDX generated by syft on every CI run (artifact) and
  attested to each released image.
- **Signing**: cosign keyless (Sigstore) on every image pushed by
  `release.yml`; the verify command is documented in that workflow.
- **Secrets hygiene**: gitleaks scans full git history on every push.
- **Build**: distroless final image; Go module sums pinned by `go.sum`.

## Internal control-plane surface: the JCAC planner

The OWASP walk above covers the tenant-facing APIs. The planner
(`services/planner`, ADR 0014) is an *internal* service and was initially
scoped out of that table — recorded here because "internal" is a network
claim, not an authorization one.

Its routes are not read-only conveniences. `POST /v1/plan` chooses
replicas, cache budget and model tier for **every tenant at once**;
`GET /v1/state` exports every tenant's demand history; `POST /v1/state`
overwrites the forecast state the controller plans on. An attacker who
reaches the port can therefore read cross-tenant demand telemetry and
steer capacity for the whole cluster — an integrity and confidentiality
break, not merely a nuisance.

Until this was fixed the **only** control was the NetworkPolicy in
`deploy/helm/polyforge-operator/templates/networkpolicy.yaml`, which is
inert on CNIs that do not enforce NetworkPolicy (kind's default kindnet
among them). That is a control that **fails open silently** — the manifest
applies cleanly and enforces nothing.

**Enforcement now:** a shared bearer token guards every `/v1/*` route and
verb, compared in constant time (`hmac.compare_digest`); `/healthz` stays
open for kubelet probes. The Helm chart generates the token once, pins it
across upgrades via `lookup` so an upgrade cannot desynchronize the two
pods, and injects the same Secret into the planner and the operator
(`planner.auth.*`); `planner.auth.existingSecret` hands token management
to the operator of the cluster. The token is configured by
`--auth-token-file` (a mounted Secret) or `POLYFORGE_PLANNER_TOKEN`, and
an empty token file is a hard boot error rather than a silent downgrade to
open. Unset means unauthenticated — the historical posture the frozen eval
harness reproduces against — and the service says so loudly on stderr at
boot. Defence in depth: the NetworkPolicy is unchanged and still on by
default.

## Penetration test status

`scripts/zap-baseline.sh` runs the OWASP ZAP baseline scan against a
running stack and writes `artifacts/zap-baseline.html`. **Not yet
executed**: no Docker on this development machine. The findings table
below is to be filled from the first run, with every Medium+ finding
fixed or triaged.

| Severity | Finding | Disposition |
|---|---|---|
| _pending first run_ | – | – |

## Known gaps (honest list)

1. ZAP scan and the compromised-pod simulation have not been executed —
   both need Docker/K8s this machine lacks; runbooks are in place.
2. Vector index and semantic cache are in-memory and unencrypted at rest
   (acceptable: rebuildable caches, not systems of record).
3. No per-process egress allowlist (API7) — containment is network-level.
4. Trufflehog as a second scanner (roadmap mentions both) is not wired;
   gitleaks covers the gate.
5. Planner transport is plain HTTP inside the mesh: the bearer token
   authenticates the caller but does not encrypt the hop. Linkerd mTLS
   (W22) covers confidentiality where the mesh is deployed; on a bare
   cluster without it the token is observable to an on-path attacker.
   A single shared token also means no per-caller attribution — the
   operator is the only intended client, so this is scoped, not solved.
6. ~~`govulncheck` is not yet a CI gate.~~ **Closed.** It is a gate in
   `ci.yml` ("Vulnerability scan (reachable symbols)"), reachability-aware
   so it fails only on advisories our code actually *calls* rather than
   reddening the build for every entry in the module graph. It was wired in
   after a manual audit found GO-2026-5970 (infinite-loop DoS in
   `golang.org/x/text`, reachable from the OIDC token exchange and the
   Postgres migration path) sitting in the tree with nothing to catch it.

   Last manual sweep 2026-08-06: **0 reachable**, after
   `google.golang.org/grpc` v1.81.1 → v1.82.1 cleared GO-2026-6061 (xDS
   RBAC / HTTP-2 transport), which *was* reachable via
   `analytics.Batcher.Close`. Two unreachable advisories were cleared in
   the same pass as defence in depth — `golang.org/x/net` v0.55.0 → v0.56.0
   (GO-2026-5942) and `github.com/klauspost/compress` v1.18.6 → v1.18.7
   (GO-2026-5841). One known non-item remains and is expected to:
   GO-2026-5932, the unmaintained `golang.org/x/crypto/openpgp`, transitive
   only, never imported, with no fixed version published.

   The gate catches reachable regressions on every push; the periodic manual
   sweep exists to pick up newly *published* advisories against pinned
   versions, which is exactly what surfaced GO-2026-6061 here.

## Session-35 adversarial re-audit (2026-08-08)

A four-perspective code audit re-checked every load-bearing claim above
against the implementation rather than the prose. `govulncheck ./...` was
clean (0 reachable vulnerabilities). Tenant isolation (BOLA + RLS), SQL
parameterization, JWT/OIDC validation, SSRF containment, secret handling,
and error hygiene were **verified in code** and hold as written — the
finding was that the AI gateway was materially less hardened than the
control plane the table describes. Six gaps were closed:

| # | Sev | Gap | Fix |
|---|---|---|---|
| F1 | MED | Gateway admin key compared with `!=` (timing side channel on a key that also unlocks the control plane) | `subtle.ConstantTimeCompare`, mirroring the control plane |
| F2 | HIGH | AI gateway had **no** rate limiting of any kind | per-tenant token bucket on every AI route, secure-by-default |
| F3 | HIGH | Semantic cache + document index grew unbounded per tenant → shared-process OOM | armed the existing eviction cap (`DefaultCacheCapacityPerTenant`) + `maxDocsPerTenant` |
| F4 | MED | Control-plane limiter failed fully open on a Redis error | keeps the in-process bucket as a second tier and degrades to it |
| F5 | MED | Tenant-asserted `model_tier` priced by eval-export; an unknown tier booked AI requests at $0 (research-integrity, not isolation) | whitelist tier at ingest (`validModelTier`) + eval-export fails closed on an unknown tier |
| F6 | LOW | `RateTracker` keys never reclaimed | self-pruning of idle keys + `Forget` for explicit deletion |

Residual, accepted (out of the stated threat model or low-risk):
- `/v1/telemetry` still lets a full-scope tenant assert its own `LatencyMS` /
  `CacheHit` within its own tenant partition. This cannot cross a tenant
  boundary (isolation holds), but it means the campaign metrics computed
  from tenant-asserted telemetry are trusted-input; the server-measured
  `/workloads/replay` path is the one used for the published live numbers.
- SSRF (API7) containment still relies on the default config (operator-set
  provider URLs, a hardcoded search host) plus the network-level
  NetworkPolicies; there is no in-process egress allowlist. Unchanged from
  the table's ⚠️.
- Timing-side-channel exploitability of any remaining `==`/`!=` on
  non-secret values is not a concern; the only secret comparisons are now
  constant-time or hash-based.
