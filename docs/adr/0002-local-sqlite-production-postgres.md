# ADR 0002: SQLite Locally, PostgreSQL in Production

## Status

Accepted and implemented

## Context

The production architecture requires PostgreSQL and row-level security, but Docker and PostgreSQL are not currently installed on the development machine. Blocking all backend progress on infrastructure setup would delay the domain model, API contract, authentication boundary, and tests.

## Decision

Define repository interfaces for tenant and telemetry storage.

- Use SQLite for the local executable and automated persistence tests.
- Implement PostgreSQL as a separate repository selected through environment configuration.
- Keep tenant identity explicit in every query and use composite project keys `(tenant_id, id)`.
- Store API-key hashes only; return plaintext secrets only at creation time.

## Consequences

- Local development is persistent and immediately runnable.
- Handlers do not depend on a database implementation.
- SQLite tests prove application-level isolation.
- A PostgreSQL integration test directly proves RLS read filtering, write rejection, and default-deny behavior. It runs when the two test database URLs are configured.
- Local verification of the PostgreSQL test requires Docker; CI provisions PostgreSQL independently.
