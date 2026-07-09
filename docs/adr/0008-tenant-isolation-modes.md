# ADR 0008: Tenant isolation modes — pool, bridge, silo

Status: accepted · 2026-07-09 · pool implemented, bridge/silo state machine + artifacts (W21)

## Context

Multi-tenancy is the architectural backbone of the research (roadmap W21;
paper §System Architecture). The AWS SaaS Lens names three isolation
models, trading cost against blast radius:

| Mode | Mechanism | Cost | Blast radius |
|---|---|---|---|
| **pool** | shared schema + PostgreSQL RLS (ADR 0003) | cheapest | one bad predicate away from cross-tenant reads — mitigated by RLS at the database, not the query builder |
| **bridge** | schema-per-tenant in the shared database | middle | schema boundary; shared compute and connection pool |
| **silo** | namespace-per-tenant in Kubernetes | most expensive | dedicated pods, quotas, and network policy per tenant |

The M8 operator needs to promote a tenant up this ladder as its workload
grows — which requires the modes to be first-class platform state, not a
deploy-time convention.

## Decisions

1. **`isolation_mode` is a column on the tenant, promotion is an API.**
   `POST /v1/tenants/{id}/isolation` (admin-only) moves a tenant up the
   ladder. The state machine is upward-only — pool → bridge → silo,
   level-skipping allowed. Demotion is rejected with 409: silently
   weakening a tenant's isolation is a security-posture change that must
   be a deliberate migration, never an API call.
2. **Promotion is an outbox event (`tenant.promoted`), so mode changes are
   event-driven.** The transition (tenant, from, to) lands in the same
   transaction as the update (ADR 0006). Downstream automation — the silo
   namespace provisioner, a future bridge schema migrator — consumes the
   event; the control plane does not shell out to kubectl.
3. **Silo is declarative:** `deploy/k8s/tenant-silo-template.yaml` holds
   the per-tenant Namespace + ResourceQuota + LimitRange, instantiated per
   tenant via `envsubst`. Quota enforcement is the cluster's job; the
   control plane records intent.
4. **Pool is the only mode with a storage-level implementation today.**
   RLS (ADR 0003) is implemented and CI-verified. Bridge's
   schema-per-tenant data migration is specified here but deliberately not
   implemented yet: doing it properly needs zero-downtime copy + cutover
   against a real PostgreSQL, and this development environment has no
   Docker (the CI Postgres suite is the first place it can be proven).
   Claiming a bridge migration that has never executed would be theater.

## Consequences

- The classifier/operator (M7/M8) can read `isolation_mode` as a feature
  and emit promotion recommendations; the enforcement path already exists.
- Bridge remains a documented gap until a schema-per-tenant migrator lands
  (tracked for the M8 operator milestone). Until then, promoting a tenant
  to bridge changes routing intent and emits the event, but rows stay in
  the pooled schema.
- Every store (memory, SQLite, PostgreSQL) implements the same audited
  promotion; the Postgres variant takes `FOR UPDATE` on the tenant row so
  concurrent promotions serialize.
