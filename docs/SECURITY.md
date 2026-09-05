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
| API1 | Broken Object Level Authorization | ✅ | Every object route is tenant-scoped: the API key is authenticated *against the tenant in the URL* (`internal/platform/server.go` `authorizeTenant`, same in the AI gateway), and PostgreSQL RLS re-enforces the predicate at the database (ADR 0003), so a missed WHERE clause cannot leak rows. Cross-tenant key use returns 401 (tested). **The RLS half is now empirically verified, not only code-reviewed (session 36):** `internal/storage/postgres/store_integration_test.go` was executed against a real PostgreSQL 18 with the CI role split (`polyforge_admin` BYPASSRLS / `polyforge_app` NOBYPASSRLS) — 3/3 pass, including `TestPostgresRowLevelIsolation` and `TestPostgresFeaturesAreTenantScopedByRowLevelSecurity`. These tests **skip silently** without `POLYFORGE_TEST_POSTGRES_{ADMIN,APP}_URL`, so before Docker existed on the dev machine they had never run locally and a "skip" was indistinguishable from a "pass" in a summary line. `scripts/pg-test-up.sh` makes the run a one-liner so this cannot regress into an untested claim again. |
| API2 | Broken Authentication | ✅ | API keys are random (24B), stored hashed, scope-fixed at creation, rotatable (`internal/tenant`). JWTs are RS256 with kid-based rotation without restart (`internal/auth`, ADR 0004). Human login federates to OIDC with mandatory S256 PKCE and pinned RS256 (`internal/auth/oidc.go`, ADR 0010). Admin key comparison is constant-time in **both** services — `subtle.ConstantTimeCompare` in the control plane (`internal/platform/server.go` `isAdmin`) and the AI gateway (`internal/ai/gateway/knobs.go` `authorizeAdmin`, corrected session 35: it previously used a plain `!=`, a timing side channel on a key that also unlocks the control-plane admin surface). Tenant API keys are compared as SHA-256 hashes, so no invertible timing leak exists there. |
| API3 | Broken Object Property Level Authorization | ✅ | All request bodies decode with `DisallowUnknownFields`; responses are explicit structs — no mass assignment, no reflected extra properties. Key secrets appear once at creation; audit events never carry secrets or hashes (`internal/tenant/audit.go`). |
| API4 | Unrestricted Resource Consumption | ✅ | **Control plane**: Redis sliding-window rate limits with an in-process token-bucket fallback that now stays armed as a second tier and takes over on a Redis error instead of failing fully open (`internal/platform/server.go`, corrected session 35), request body caps (`MaxBytesReader`, 1 MiB), pagination caps (`MaxPageSize`). **AI gateway** (added session 35 — it previously had none): a per-tenant token bucket in front of every AI route (`internal/ai/gateway/ratelimit.go`, default RPM 600 / burst 60, secure-by-default), a per-tenant semantic-cache entry cap and document-index cap so one tenant cannot OOM the shared process (`cache.go` `DefaultCacheCapacityPerTenant`, `server.go` `maxDocsPerTenant`). **Both**: per-tenant daily LLM budget with cheapest-backend degradation (`gateway.Router`), K8s ResourceQuota/LimitRange in silo mode (W21). |
| API5 | Broken Function Level Authorization | ✅ | Admin surface (tenant CRUD, isolation promotion, key rotation endpoints) requires the admin key; tenant keys cannot reach it (tested in `isolation_http_test.go`). Scope model separates read from full keys (`ScopeAllows`). |
| API6 | Unrestricted Access to Sensitive Business Flows | ✅ | Tenant provisioning and isolation promotion are admin-only and audited to the outbox; idempotency keys (W13) stop replay-driven resource creation. |
| API7 | Server Side Request Forgery | ✅ | LLM/embedding base URLs are operator config, not user input (`POLYFORGE_TIER_BACKENDS`, parsed at startup — a tenant cannot set a provider URL). The agent's web-search tool fetches a fixed search endpoint with the query URL-encoded — user text never becomes a URL. **Redirect-based egress is now closed (session 35):** every outbound client — both model providers and the search tool — refuses to follow any 3xx (`internal/ai/gateway/egress.go` `egressClient`), so a compromised or malicious upstream cannot 302 the process into fetching an internal address (cloud metadata at 169.254.169.254, a sibling service, localhost). Network-level containment (W24 NetworkPolicies) remains as a second layer. Residual: no positive host allowlist — containment is by fixed config + no-redirect, not an enumerated allowlist. |
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

`scripts/zap-baseline.sh` runs OWASP ZAP against a running stack.
**EXECUTED session 36** (Docker Desktop installed on the dev machine), in
both modes, against the live compose stack (control plane + PostgreSQL +
Redis).

**Read the two modes carefully — the difference is the point.**

| mode | URLs reached | rules | result |
|---|---|---|---|
| `zap-baseline.sh` (spider) | **2, both 404** | 66 PASS | 0 FAIL, 1 WARN |
| `zap-baseline.sh --api` (OpenAPI) | the 20 declared paths | **116 → 118 PASS** | 0 FAIL, 3 WARN → **1 WARN** (the accepted timestamp) |

The plain baseline scan is **near-worthless on this service and must not be
quoted as evidence**: the control plane is a JSON API with no root route
(`GET /` is 404) and no sitemap, so the spider found nothing to crawl and
every passive rule "passed" against an empty surface. That is a scan of
nothing, reported as a clean bill of health. The `--api` mode imports
`api/openapi.yaml` so ZAP requests the real endpoints, which is where the
rule count nearly doubles and the injection/traversal/SSTI/XXE/cloud-metadata
families actually execute.

**Findings from the first real (`--api`) run, all fixed the same session:**

| Severity | Finding | Disposition |
|---|---|---|
| Low | `X-Content-Type-Options` missing (rule 10021, ×3) | **FIXED** — `securityHeaders` middleware sets `nosniff` on both services |
| Low | `Cross-Origin-Resource-Policy` missing (rule 90004, ×3) | **FIXED** — set to `same-origin` |
| Info | Storable/cacheable content (rule 10049, baseline mode) | **FIXED** — `Cache-Control: no-store`; every response is tenant data or a credential exchange |
| Info | Unix timestamp disclosure (rule 10096, ×1) | **Accepted** — a timestamp in a health/metrics payload; no security value to an attacker |

Re-scan after the fix: **0 FAIL, 1 WARN, 118 PASS** — the two header rules
moved from WARN to PASS (116→118) and the only remaining warning is the
accepted timestamp above. Headers are covered by
`internal/platform/security_headers_test.go` (success, error and 404 paths)
so they cannot silently regress.

Known coverage limit of the session-36 runs, stated rather than hidden: the
endpoints answer 401 without a credential, so those runs covered the
**unauthenticated** surface only. That limit is now closed — see below.

### Authenticated scan (WP12, session 38) — EXECUTED

`./scripts/zap-baseline.sh --auth` runs the OpenAPI-driven scan with a real
tenant API key injected on every request (ZAP `replacer`, header
`X-PolyForge-API-Key`), and rewrites `{tenant_id}` to the scanned tenant so
a key scoped to one tenant does not collect 403s that look like coverage.

**The first authenticated run measured the rate limiter, not the API.** Of
~3,800 responses, **2,079 were 429**. The scan came back "0 FAIL" against a
wall of throttling — the same failure mode as the session-36 spider run,
which "passed" against two 404s. `compose.yaml` now exposes
`POLYFORGE_RATE_LIMIT_RPM` / `_BURST` (defaults unchanged at 600/60) so a
scan can be run un-throttled; the re-run reached the handlers for the first
time — **167×200, 132×201, 127×202** — and immediately found a defect.

| Severity | Finding | Disposition |
|---|---|---|
| Medium | **NUL byte in user input reaches PostgreSQL → HTTP 500.** `GET /v1/tenants/{id}/projects?cursor=%00` and a ` ` inside a JSON body both surfaced `invalid byte sequence for encoding "UTF8": 0x00 (SQLSTATE 22021)` as a server fault | **FIXED** — `rejectNullBytes` middleware (URL) + a body check in `readJSON`; both now return 400 `invalid_argument`. Pinned by `TestNullByteInputIsRejectedWith400` |
| Info | Unix timestamp disclosure (rule 10096, ×1) on `/metrics` | **Accepted** — Prometheus `process_start_time_seconds`; no security value to an attacker |

**What the NUL finding was and was not.** It was *not* injection: the query
was parameterised, which is precisely why the driver rejected the value
instead of the database executing it, and nothing leaked — the response was
the generic `internal_error` envelope with no driver detail. It *was*
unvalidated input reaching the storage layer, answered with a server-fault
status: that burns error budget, pages on-call for a client mistake, and
masks real 500s. NUL is never valid in any identifier, cursor or name this
API accepts, so it is refused at the edge.

Result after the fix: **0 FAIL, 1 WARN (the accepted timestamp), 118 PASS**,
with the authenticated surface genuinely exercised.

**Honest scope of the current claim.** "118 rules, 0 FAIL, authenticated and
unauthenticated surface, one Medium finding found and fixed." Still not a
full pen test: ZAP's passive+API rules are not an adversary, the scan runs
one tenant against a single-node dev stack, and no active-attack or
authenticated-fuzzing campaign has been run.

## Session-43 finding: unbounded metric cardinality, remotely triggerable (FIXED)

**Severity: high. Found by accident, by an instrument check** — the L6 `/metrics`
scrape verification (`eval/results/l6_scrape_verification_evidence/`), not by a
review pass. 8 of 180 replay requests were rate-limited, and the 429s appeared
in the histogram under `route="/v1/tenants/l6/workloads/replay"` — the raw path
— beside the 172 served requests under the templated
`route="/v1/tenants/{tenant_id}/workloads/replay"`.

**Mechanism.** `routePattern` read `r.Pattern`, which `ServeMux` populates only
*after* it matches. `Handler()` wraps `rateLimit` above the mux, so every
rejected request reached the metrics recorder with an empty pattern and fell
back to `r.URL.Path`. `metrics.RecordHTTPRequest` keys two unbounded maps by
that label, and one allocates a fresh 12-bucket histogram per key.

**Impact.** Anyone who can reach the server could allocate permanent series in
its own memory — no authentication needed, since the rate limiter's rejection
is what mints them, and a 404 does the same. One series set per tenant id under
load, unbounded under a crafted request stream. The registry never evicts, so
this is straightforward memory exhaustion against the control plane, and a
cardinality bomb for any Prometheus scraping it. `kubectl`'s stray `/api` and
`/apis` probes minted two of these by accident during the verification run,
which is how visible the path is.

**Fix.** `routePattern` now asks the mux for the pattern it *would* have
matched — which recovers the real templated route for rate-limited requests,
the more useful label anyway — and falls back to the single constant
`unmatched` when nothing matches. Behind that, `metrics.boundRoute` caps
distinct route labels at 256 and folds the rest into `overflow`, so a future
middleware cannot reintroduce the leak silently.

**Tests.** `internal/platform/route_label_cardinality_test.go`: ten tenant ids
through a tripped rate limiter mint no tenant-labelled series; 40 unmatched
paths collapse to one label; the registry stays bounded when driven directly;
and a matched route still reports its own pattern. Mutation-checked — restoring
the `r.URL.Path` fallback fails two of the four.

**Consequence to know about.** Rate-limited requests now share their route's
duration histogram, which has no status dimension. Heavy 429 traffic therefore
pulls a route's observed mean *down*, because a rejection is cheap (65 µs here
against 1.39 ms for a served request). That is the standard Prometheus
trade-off and is disclosed in `scripts/soak_observer.sh`.

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

### Follow-up hardening (same session, the residuals that were code-fixable)

- **Telemetry self-report bounds (F5 follow-up).** `/v1/telemetry` still
  lets a full-scope tenant assert its own metrics *within its own partition*
  (isolation is unaffected), but the values are now bounded at ingest —
  `latency_ms ≤ 1 h`, `rps_window ≤ 1M`, `payload_bytes ≤ 1 GiB`,
  `child_spans ≤ 100k` (`internal/platform/server.go`). Previously only
  negatives were rejected, so a tenant could assert `latency_ms=1e12` to skew
  a shared p95/violation figure without limit; the bound caps the magnitude.
  The definitive live numbers come from the server-measured
  `/workloads/replay` path, not this self-report surface.
- **SSRF redirect guard (API7).** Closed as above (`egress.go`); the residual
  is the absence of a positive host allowlist, accepted because provider URLs
  are fixed operator config and redirects are refused.
- Timing-side-channel exploitability of any remaining `==`/`!=` on
  non-secret values is not a concern; the only secret comparisons are now
  constant-time or hash-based.

### Genuinely out of scope or infrastructure-gated (cannot be closed in code here)

- **NetworkPolicy on a non-enforcing CNI.** A NetworkPolicy is inert on a CNI
  that does not enforce it (kind's default kindnet). This is a deployment
  property, not a code defect — it cannot be forced from the application. The
  load-bearing control is therefore application-layer defence in depth, which
  exists: the planner requires a constant-time bearer token
  (`services/planner`, `hmac.compare_digest`), Postgres/Redis are
  credential-protected and pin their inbound peers, and the admin surfaces
  require the admin key. Deploy on an enforcing CNI (Calico) and run the
  verification command in the manifest header to confirm the policy is live.
- **ZAP baseline scan (`scripts/zap-baseline.sh`).** Ready and correct;
  requires Docker to run, which this development machine does not have. Run it
  in the Codespace/cloud sitting alongside the B1 live plane.
- **mTLS (Linkerd), Vault secret chain, cosign signing, SBOM.** cosign
  keyless signing and syft SBOM generation **do run in CI/release on every
  push** (`.github/workflows/{ci,release}.yml`) — verified there, just not on
  this local machine. Linkerd mTLS and the Vault chain are deploy-time mesh /
  secret-store properties confirmed at cluster bring-up, not in a unit test.
