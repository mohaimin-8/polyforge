# Session Log

`docs/EXECUTION_PLAN.md` requires that every work session produce runnable code,
passing tests, measured data, thesis text, or a decision record, and that the
session close by recording milestone status, what changed, how it was verified,
and what to study next. This file is that record. Newest entry first.

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
