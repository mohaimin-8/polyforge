# ADR 0003: PostgreSQL RLS with Transaction-Local Tenant Context

## Status

Accepted

## Context

Every pooled database connection can serve many tenants over its lifetime. A session-level tenant variable can survive after a request and expose another tenant's rows. Query predicates alone also make one omitted `WHERE tenant_id = ...` clause a data-isolation defect.

## Decision

- Use distinct PostgreSQL admin and application roles.
- Require the admin role to have `BYPASSRLS`; reject an application role that is superuser or has `BYPASSRLS`.
- Enable and force row-level security on every tenant-owned table.
- Start one transaction for every tenant-scoped repository operation.
- Set `app.tenant_id` with `set_config(..., true)` inside that transaction. The `true` argument makes the value transaction-local.
- Apply both `USING` and `WITH CHECK` policies so reads are filtered and cross-tenant writes are rejected.
- Leave the policy default-deny when no tenant context exists.

## Consequences

- Tenant context is cleared by commit or rollback before a pooled connection is reused.
- Application bugs have a database isolation backstop.
- Administrative operations intentionally use a privileged pool and must remain limited to management handlers.
- Production secret management must supply two database credentials.
- RLS is not a substitute for API authentication; both controls are required.

## Verification

`TestPostgresRowLevelIsolation` proves:

1. an alpha transaction cannot read bravo projects;
2. an alpha transaction cannot insert a bravo project;
3. an application connection without tenant context sees no tenant rows.
