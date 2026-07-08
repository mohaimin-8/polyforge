# Milestone 1: Backend Core

## Current Scope

This milestone establishes the persistent multi-tenant control plane.

Implemented:

- repository interfaces for tenant and telemetry data
- SQLite-backed local persistence
- automatic SQLite and versioned PostgreSQL schema migration
- complete tenant project create/read/update/delete operations
- keyset pagination for project collections
- tenant-scoped API keys stored as SHA-256 hashes
- API-key metadata listing, revocation, and atomic rotation
- admin-key protection for tenant management
- tenant-key protection for project, telemetry, classifier, and controller APIs
- request correlation identifiers, structured errors, and status-aware request logs
- in-process token-bucket rate limiting with `Retry-After` responses
- forced PostgreSQL row-level-security policies using transaction-local tenant context
- cross-tenant rejection tests
- environment-gated PostgreSQL RLS integration test
- repeatable local CRUD baseline harness and artifact
- OpenAPI 3.1 contract
- GitHub Actions test workflow with PostgreSQL

Still required for full Milestone 1 exit:

- execute the PostgreSQL RLS suite on a Docker-capable machine and archive its output
- observe the first GitHub Actions run after the repository is published

## Security Boundary

Management endpoints use:

```text
X-PolyForge-Admin-Key
```

Tenant endpoints use:

```text
X-PolyForge-API-Key
```

The tenant ID in the route or telemetry body must match the tenant associated with the API key.

## Evidence

Run:

```powershell
.\scripts\test.ps1
```

The tests verify:

- data survives database close and reopen
- API-key hashes remain valid after reopen
- tenant A's key cannot access tenant B's route
- tenant creation returns a one-time bootstrap secret
- project pagination and full lifecycle behavior
- rotated and revoked keys no longer authenticate
- error request IDs match the `X-Request-ID` response header
- rate-limited responses return `429`, `Retry-After`, and the structured error envelope

With Docker available, run:

```powershell
.\scripts\test-postgres.ps1
```

This additionally verifies database-enforced default-deny, cross-tenant read filtering, and cross-tenant write rejection.

## Local CRUD Baseline

Run:

```powershell
.\scripts\bench-crud.ps1
```

The current local SQLite artifact is `artifacts/m1-crud-baseline.json`:

- 4 tenants
- 25 projects per tenant
- 504 total HTTP requests
- 244.07 requests/second
- latency p50 4.58 ms, p95 5.84 ms, p99 6.74 ms

This is a repeatability check and an initial local capacity baseline. It is not a PostgreSQL or production deployment benchmark.
