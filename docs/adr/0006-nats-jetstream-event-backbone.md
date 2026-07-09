# ADR 0006: NATS JetStream as the event backbone

Status: accepted · 2026-07-09 · implementation scheduled (W14 remainder + W15)

## Context

The audit pipeline (W14), outbox relay, onboarding saga, and CQRS
projections (W15) all need a persistent, replayable event stream. ADR 0005
deferred the backbone choice so these would not be built against a guessed
interface.

## Decision

**NATS JetStream**, per the roadmap's own W14 rationale (lighter than Kafka,
single binary, replayable persistent streams), with these binding choices:

1. **Subjects**: `polyforge.audit.<tenant_id>.<action>` for audit events
   (e.g. `polyforge.audit.acme.project.created`); `polyforge.events.>`
   reserved for the JCAC control loop. One stream `POLYFORGE_AUDIT` capturing
   `polyforge.audit.>`, file storage, 30-day retention.
2. **Outbox, not dual-write**: handlers never publish directly. Writes
   insert the domain row and an `outbox` row in the same transaction
   (`id, occurred_at, subject, payload, published_at NULL`); a relay
   goroutine polls unpublished rows in order, publishes with
   `Nats-Msg-Id = outbox.id` (JetStream de-duplication window makes the
   relay crash-safe/at-least-once → effectively once), then marks
   `published_at`. Schema identical across memory/SQLite/Postgres stores.
3. **Local verification without Docker**: `nats-server` embeds as a Go
   library, so the relay, replay, and dedupe behavior are provable in
   ordinary `go test` — same pattern as miniredis in ADR 0005. This is why
   the cluster is no longer blocked on Docker.
4. **Consumer side (W15)**: pull consumers with explicit acks. The CQRS
   `tenant_summary` projection rebuilds from stream replay; byte-equality
   with the write model is the regeneration test.
5. **Saga (W15)**: tenant onboarding as an orchestrated sequence
   (create tenant → bootstrap key → default policy → welcome event), each
   step idempotent, each with an explicit compensating action, state kept
   in the DB not the broker.

## Rejected

- **Kafka**: operational weight indefensible for a single-researcher
  platform; nothing in the JCAC design needs partitioned consumer scaling.
- **Redis Streams**: already deployed, but retention/replay semantics and
  exactly-once tooling are weaker, and coupling the event log to the cache
  tier makes the failure domains overlap.
- **Publish-in-handler (no outbox)**: loses events on crash between commit
  and publish; the W15 chaos gate explicitly tests this failure.

## Consequences

- New deps next session: `nats.go` + embedded `nats-server` (test).
- Implementation order: outbox schema + relay → audit events on every write
  → saga → CQRS projection. Each lands whole with its tests or not at all.
