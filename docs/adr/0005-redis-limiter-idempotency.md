# ADR 0005: Redis sliding window + idempotency; events cluster deferred

Status: accepted · 2026-07-09

## Context

Roadmap W13 requires a race-free sliding-window rate limiter ("the
Cloudflare algorithm") and per-tenant limits; W14 requires Idempotency-Key
handling and a NATS JetStream audit/event backbone; W15 builds outbox, saga,
and CQRS on that backbone. Redis and NATS cannot run on the dev machine
(no Docker), but Redis logic is testable in-process via miniredis.

## Decisions

1. **Sliding window as one Lua script over a sorted set**
   (`internal/limit`). Admission — prune, count, add, expire — happens
   atomically server-side, so there is no check-then-act race between
   replicas. Proven by a 200-goroutine test admitting exactly the limit,
   and a SCRIPT EXISTS assertion. The in-process token bucket remains the
   no-Redis fallback (single replica ⇒ locally correct).
2. **Per-tenant budget for credentialed traffic.** Requests carrying a
   credential on `/v1/tenants/{id}/...` share the window `tenant:{id}`, so
   minting more API keys does not multiply a tenant's budget.
   Unauthenticated requests stay host-keyed and can never spend a tenant's
   budget (otherwise anyone could starve a victim tenant without a key).
3. **Fail open when Redis is unreachable** — for both the limiter and the
   idempotency store. The control plane keeps serving; the gap is logged.
   Rationale: rate limiting protects capacity, and capacity is exactly what
   an unavailable limiter must not destroy. Revisit for billing-grade
   quotas, which must fail closed.
4. **Idempotency keys are scoped to credential + method + path + key +
   body digest** (`internal/idempotency`). Same key + same payload replays
   the cached response; same key + different payload executes as a distinct
   operation instead of returning the wrong cached result (a deliberate,
   safer divergence from Stripe's 409-on-mismatch). 5xx responses are never
   cached — clients must be able to retry failures. In-memory store without
   Redis; Redis store with it.
5. **NATS JetStream, outbox, saga, CQRS (W14/W15 remainder) deferred
   whole.** They form one architecture decision (the event backbone) and
   half-landing it would freeze interfaces before the backbone exists.
6. **SQLite: WAL + connection pool** (was: single connection, default
   journal). The single connection serialized every request and capped the
   authenticated projects endpoint at ~830 RPS with p99 216 ms under load;
   WAL + per-connection pragmas + a NumCPU pool measured 4,999 RPS with
   p99 25.6 ms (`artifacts/m4-projects-5krps.json`), passing the W16 gate.
   `ORDER BY created_at, rowid` replaced the random-ID tiebreak that made
   key listing order flap when timestamps collide.

## Consequences

- New dependencies: `go-redis/v9` (prod), `miniredis` (test only),
  OTel SDK + OTLP exporter (W11, see session log). All W13/W14 logic is
  CI-verifiable without a real Redis.
- Redis persistence/HA (RDB+AOF, replica failover drill) is configured in
  compose but unproven locally — Docker-blocked like W9/W10.
- Anyone auditing against the roadmap should read "NATS/outbox/saga/CQRS"
  as deliberately absent until the backbone ADR lands.
