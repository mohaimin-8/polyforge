package postgres

import (
	"context"
	"fmt"
	"strings"
)

// Server-side aggregation for `control-plane eval-export`.
//
// WHY THIS EXISTS. The exporter used to pull every telemetry row into memory —
// `RecentByTenant(ctx, id, 5_000_000)` per tenant — and score them in Go. That
// works for a smoke test and dies on anything real: at 952k events (172 MB of
// rows) it OOM-killed the control-plane pod inside its 256 Mi limit, exit 137.
// A 24 h soak produces ~25M events, twenty-five times more.
//
// This is almost certainly why WP14 attempt 4 recorded no eval-export.json at
// all. That was written up as "the run failed before the export step"; the
// truth is the export could not have succeeded at that scale, so the sitting
// was unwinnable before it started, for a reason nobody had identified.
//
// The queries here compute the same numbers PostgreSQL-side and return tens of
// rows instead of tens of millions. Correctness rests on one detail:
// `percentile_disc` is the nearest-rank order statistic, matching the Go
// implementation's `ceil(q*n)-1` index exactly. `percentile_cont` interpolates
// and disagrees in the fourth decimal, which would silently break comparability
// with every committed record.

// EvalAggSpec carries the scoring constants from the caller, so this package
// executes SQL without owning domain knowledge about SLO classes or pricing.
type EvalAggSpec struct {
	CrudTargetBaseMS float64
	AITargetBaseMS   float64
	ClassScale       map[string]float64
	StepSeconds      int
	BucketSeconds    int // 0 disables bucketing
}

// EvalAggWindow is one scored time window (or the whole run).
type EvalAggWindow struct {
	StartUnix   int64
	NEvents     int
	AITotal     int
	CacheHits   int
	Violations  int
	CrudP95     float64
	CrudP99     float64
	AIP95       float64
	AIP99       float64
	StepsTotal  int
	StepsMissed int
}

// EvalAgg is everything the exporter needs, already reduced.
type EvalAgg struct {
	Overall     EvalAggWindow
	Buckets     []EvalAggWindow
	TierCounts  map[string]int
	Attainments []float64
	NTenants    int
}

// baseCTE builds the shared derivation: which events are AI, and what latency
// target each one is held to. Kept in one place so the scalar, bucket, step and
// tier queries cannot drift apart in how they classify a row.
func (s *Store) baseCTE(spec EvalAggSpec) (string, []any) {
	pairs := make([]string, 0, len(spec.ClassScale))
	args := []any{spec.CrudTargetBaseMS, spec.AITargetBaseMS}
	i := len(args)
	for plan, scale := range spec.ClassScale {
		pairs = append(pairs, fmt.Sprintf("($%d::text, $%d::float8)", i+1, i+2))
		args = append(args, plan, scale)
		i += 2
	}
	return `
WITH scale(plan, s) AS (VALUES ` + strings.Join(pairs, ", ") + `),
base AS (
  SELECT e.tenant_id,
         e.timestamp,
         e.latency_ms,
         e.cache_hit,
         NULLIF(btrim(e.model_tier), '')            AS tier,
         (NULLIF(btrim(e.model_tier), '') IS NOT NULL) AS is_ai,
         CASE WHEN NULLIF(btrim(e.model_tier), '') IS NOT NULL
              THEN $2::float8 * sc.s
              ELSE $1::float8 * sc.s END            AS target
  FROM telemetry_events e
  JOIN tenants t  ON t.id = e.tenant_id
  JOIN scale  sc  ON sc.plan = btrim(t.plan)
)`, args
}

// EvalAggregate scores the telemetry store without materialising it.
func (s *Store) EvalAggregate(ctx context.Context, spec EvalAggSpec) (EvalAgg, error) {
	var out EvalAgg
	out.TierCounts = map[string]int{}

	// An unrecognised plan is a provisioning bug, not a default. The JOIN above
	// would silently DROP such a tenant's events, which is worse than the Go
	// path's behaviour of failing loudly — so check first and fail the same way.
	plans := make([]string, 0, len(spec.ClassScale))
	for plan := range spec.ClassScale {
		plans = append(plans, plan)
	}
	rows, err := s.admin.Query(ctx,
		`SELECT id, plan FROM tenants WHERE btrim(plan) <> ALL($1::text[])`, plans)
	if err != nil {
		return out, fmt.Errorf("check tenant plans: %w", err)
	}
	// One offender is enough to fail, so this reads the first row and stops.
	// It was written as a `for` that always returns, which staticcheck flagged
	// (SA4004) and which hid the missing rows.Err() check below: a query that
	// failed mid-iteration used to look exactly like "no unrecognised plans".
	if rows.Next() {
		var id, plan string
		if err := rows.Scan(&id, &plan); err != nil {
			rows.Close()
			return out, err
		}
		rows.Close()
		return out, fmt.Errorf("tenant %q has unrecognised plan %q: cannot score "+
			"latency without its SLO class", id, plan)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return out, fmt.Errorf("check tenant plans: %w", err)
	}

	base, args := s.baseCTE(spec)

	scalarSQL := base + `
SELECT count(*)                                                   AS n_events,
       count(*) FILTER (WHERE is_ai)                              AS ai_total,
       count(*) FILTER (WHERE is_ai AND cache_hit)                AS cache_hits,
       count(*) FILTER (WHERE latency_ms > target)                AS violations,
       coalesce(percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE NOT is_ai), 0)                      AS crud_p95,
       coalesce(percentile_disc(0.99) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE NOT is_ai), 0)                      AS crud_p99,
       coalesce(percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE is_ai), 0)                          AS ai_p95,
       coalesce(percentile_disc(0.99) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE is_ai), 0)                          AS ai_p99
FROM base`
	if err := s.admin.QueryRow(ctx, scalarSQL, args...).Scan(
		&out.Overall.NEvents, &out.Overall.AITotal, &out.Overall.CacheHits,
		&out.Overall.Violations, &out.Overall.CrudP95, &out.Overall.CrudP99,
		&out.Overall.AIP95, &out.Overall.AIP99); err != nil {
		return out, fmt.Errorf("aggregate scalars: %w", err)
	}

	// Step violations: a step counts as missed when more than 5% of its
	// requests exceeded target. Reduced to two integers here rather than
	// returning up to 8,640 rows for a 24 h run.
	stepSQL := base + fmt.Sprintf(`
, steps AS (
  SELECT floor(extract(epoch FROM timestamp) / %d)::bigint AS step,
         count(*) AS total,
         count(*) FILTER (WHERE latency_ms > target) AS over
  FROM base GROUP BY 1
)
SELECT count(*), count(*) FILTER (WHERE over > 0.05 * total) FROM steps`,
		spec.StepSeconds)
	if err := s.admin.QueryRow(ctx, stepSQL, args...).Scan(
		&out.Overall.StepsTotal, &out.Overall.StepsMissed); err != nil {
		return out, fmt.Errorf("aggregate steps: %w", err)
	}

	// Per-tenant attainment feeds Jain's index. One row per tenant.
	attSQL := base + `
SELECT 1.0 - (count(*) FILTER (WHERE latency_ms > target))::float8 / count(*)
FROM base GROUP BY tenant_id HAVING count(*) > 0`
	attRows, err := s.admin.Query(ctx, attSQL, args...)
	if err != nil {
		return out, fmt.Errorf("aggregate attainment: %w", err)
	}
	defer attRows.Close()
	for attRows.Next() {
		var a float64
		if err := attRows.Scan(&a); err != nil {
			return out, err
		}
		out.Attainments = append(out.Attainments, a)
	}
	attRows.Close()

	// Serving tier histogram: cache hits never touched a backend and belong to
	// no tier's serving cost, matching the in-memory path.
	tierSQL := base + `
SELECT tier, count(*) FROM base
WHERE is_ai AND NOT cache_hit AND tier IS NOT NULL GROUP BY tier`
	tierRows, err := s.admin.Query(ctx, tierSQL, args...)
	if err != nil {
		return out, fmt.Errorf("aggregate tiers: %w", err)
	}
	defer tierRows.Close()
	for tierRows.Next() {
		var tier string
		var n int
		if err := tierRows.Scan(&tier, &n); err != nil {
			return out, err
		}
		out.TierCounts[tier] = n
	}
	tierRows.Close()

	if err := s.admin.QueryRow(ctx, `SELECT count(*) FROM tenants`).
		Scan(&out.NTenants); err != nil {
		return out, fmt.Errorf("count tenants: %w", err)
	}

	if spec.BucketSeconds > 0 {
		if err := s.evalBuckets(ctx, spec, base, args, &out); err != nil {
			return out, err
		}
	}
	return out, nil
}

func (s *Store) evalBuckets(ctx context.Context, spec EvalAggSpec,
	base string, args []any, out *EvalAgg) error {
	// Buckets are absolute epoch windows, so a boundary falls on the same
	// wall-clock instant regardless of when the run started, and an empty
	// window is simply absent rather than reported as a zero.
	sql := base + fmt.Sprintf(`
, b AS (
  SELECT floor(extract(epoch FROM timestamp) / %d)::bigint AS bucket,
         floor(extract(epoch FROM timestamp) / %d)::bigint AS step,
         latency_ms, target, is_ai, cache_hit
  FROM base
),
steps AS (
  SELECT bucket, step, count(*) AS total,
         count(*) FILTER (WHERE latency_ms > target) AS over
  FROM b GROUP BY bucket, step
),
stepagg AS (
  SELECT bucket, count(*) AS steps_total,
         count(*) FILTER (WHERE over > 0.05 * total) AS steps_missed
  FROM steps GROUP BY bucket
)
SELECT b.bucket * %d,
       count(*),
       count(*) FILTER (WHERE b.is_ai),
       count(*) FILTER (WHERE b.is_ai AND b.cache_hit),
       count(*) FILTER (WHERE b.latency_ms > b.target),
       coalesce(percentile_disc(0.95) WITHIN GROUP (ORDER BY b.latency_ms)
                FILTER (WHERE NOT b.is_ai), 0),
       coalesce(percentile_disc(0.99) WITHIN GROUP (ORDER BY b.latency_ms)
                FILTER (WHERE NOT b.is_ai), 0),
       coalesce(percentile_disc(0.95) WITHIN GROUP (ORDER BY b.latency_ms)
                FILTER (WHERE b.is_ai), 0),
       coalesce(percentile_disc(0.99) WITHIN GROUP (ORDER BY b.latency_ms)
                FILTER (WHERE b.is_ai), 0),
       coalesce(max(sa.steps_total), 0),
       coalesce(max(sa.steps_missed), 0)
FROM b LEFT JOIN stepagg sa ON sa.bucket = b.bucket
GROUP BY b.bucket ORDER BY b.bucket`,
		spec.BucketSeconds, spec.StepSeconds, spec.BucketSeconds)

	rows, err := s.admin.Query(ctx, sql, args...)
	if err != nil {
		return fmt.Errorf("aggregate buckets: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var w EvalAggWindow
		if err := rows.Scan(&w.StartUnix, &w.NEvents, &w.AITotal, &w.CacheHits,
			&w.Violations, &w.CrudP95, &w.CrudP99, &w.AIP95, &w.AIP99,
			&w.StepsTotal, &w.StepsMissed); err != nil {
			return err
		}
		out.Buckets = append(out.Buckets, w)
	}
	return rows.Err()
}
