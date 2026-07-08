# Learning Guide: Milestone 1 Backend Core

## What You Should Be Able to Explain

1. **Repository boundary:** HTTP handlers depend on interfaces, so SQLite and PostgreSQL implement the same behavior without branching in business logic.
2. **Authentication versus isolation:** API keys establish caller identity. Tenant predicates and PostgreSQL RLS constrain data access. Neither replaces the other.
3. **Connection-pool safety:** `set_config('app.tenant_id', tenant, true)` is transaction-local. A session-level value could leak into the next request using the same pooled connection.
4. **RLS read and write controls:** `USING` controls which existing rows are visible. `WITH CHECK` controls which new row values may be inserted or updated.
5. **Key rotation:** rotation inserts the replacement and revokes the old key in one transaction. A partial rotation must not leave both keys active or neither key usable.
6. **Keyset pagination:** `id > cursor ORDER BY id LIMIT n+1` avoids increasingly expensive offsets and identifies whether another page exists.
7. **Operational correlation:** the response header, error body, and structured log all share a request ID, allowing one failure to be traced across interfaces.
8. **Rate limiting:** the limiter keys tenant routes by credential fingerprint, tenant-management routes by remote address, and anonymous traffic by remote address. Health checks bypass it so orchestration probes do not consume application quota.

## Code Reading Order

1. `internal/tenant/store.go` for domain contracts and the reference in-memory behavior.
2. `internal/platform/server.go` for authentication, validation, and transport behavior.
3. `internal/storage/sqlite/store.go` for the local persistence implementation.
4. `internal/storage/postgres/migrations/001_initial.sql` for schema constraints and RLS policies.
5. `internal/storage/postgres/store.go` for transaction-local tenant context.
6. `internal/storage/postgres/store_integration_test.go` for the isolation proof.

## Hands-On Checks

```powershell
.\scripts\test.ps1
.\scripts\smoke.ps1
.\scripts\bench-crud.ps1
```

After Docker is installed:

```powershell
.\scripts\test-postgres.ps1
```

Inspect a structured unauthorized response and confirm its body request ID matches the `X-Request-ID` header. Then rotate an API key and prove the old secret receives `401` while the replacement succeeds.

Temporarily set `POLYFORGE_RATE_LIMIT_RPM=1` and `POLYFORGE_RATE_LIMIT_BURST=1`, call an authenticated endpoint twice, and confirm the second response returns `429` with `Retry-After`.

Read `artifacts/m1-crud-baseline.json` and explain why the numbers are valid as a local repeatability baseline but not as a production PostgreSQL capacity claim.

## Defense Questions

- Why is `FORCE ROW LEVEL SECURITY` necessary when a table owner normally bypasses policies?
- What happens if tenant context is not set?
- Why are the admin and application database roles required to be different?
- Which failure modes require an atomic transaction during key rotation?
- Why is a passing application-level cross-tenant test insufficient evidence for a database RLS claim?
- Why should `/healthz` bypass request quotas?
- What extra controls would be needed before using the local CRUD baseline as a publishable performance result?
