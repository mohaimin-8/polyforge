package main

// `control-plane eval-export` — integration point 1 of eval/README.md
// ("Backends", cluster): dump the pod's observed run metrics in the harness
// result schema. The eval kind cluster is provisioned per run, so this
// store's telemetry IS the run: the export window is everything recorded.
//
// Semantics are the live analogues of the sim estimators, ranking-comparable
// by design, never claimed identical (eval/README.md latency note):
//   - crud_p95_ms / ai_p95_ms: exact p95 over per-request latencies by
//     family. An event is AI iff it carries a model tier; CRUD otherwise.
//   - mean_violation: share of requests exceeding their family SLO target
//     (traffic-weighted by construction).
//   - violation_step_share: share of 10 s steps whose over-target share
//     exceeds 5% — i.e. steps that missed their SLO at p95, which is the
//     level the targets are stated at.
//   - mean_jain: Jain's index over per-tenant attainment (1 − per-tenant
//     over-target share).
//   - cache_hit_rate: hits / AI requests.
//   - total_cost_usd: tier (per-request) component measured here, plus
//     --infra-cost-usd injected by the harness (replica-hours live in the
//     Kubernetes control plane, not in this pod). Components are exported
//     separately so nothing is silently mislabeled; wiring the infra term
//     is part of the first-live-run integration (eval/README.md).
//
// SLO targets and tier prices mirror research/jcac_sim/model.py and must be
// kept in lockstep with it — the whole point is that live runs and sim runs
// are scored against the same stated objectives.

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	postgresstore "polyforge/internal/storage/postgres"
	sqlitestore "polyforge/internal/storage/sqlite"
	"polyforge/internal/telemetry"
	"polyforge/internal/tenant"
)

const evalStepSeconds = 10 // CONTROL_INTERVAL_S in the sim model

// Mirrors SLO_BASE_MS and SLO_CLASS_FACTOR in research/jcac_sim/model.py.
var (
	evalSLOBaseMS     = map[string]float64{"crud": 150.0, "ai": 2500.0}
	evalSLOClassScale = map[string]float64{"premium": 1.0, "standard": 2.5, "best-effort": 8.0}
	// Mirrors TIER_COST_USD_PER_REQ in research/jcac_sim/model.py.
	evalTierCostUSD = map[string]float64{"none": 0.0, "small": 0.0001, "mid": 0.001, "large": 0.01}
)

// evalBucket is one time window of the run, scored exactly like the run-level
// scalars beside it.
//
// WP14 needed these and they did not exist. SK-H3 is frozen on *hour-bucketed*
// crud_p95 and SK-H1 on how violation moves around a fault, but eval-export
// emitted only whole-run aggregates -- so cluster_backend's
// `export.get("timeseries", [])` was always empty, live_soak.yaml's
// `timeseries_reps: 0` left the DuckDB table empty too, and four consecutive
// 24 h sittings produced exactly one row of numbers between them. Attempt 4's
// buckets had to be reconstructed from Postgres by hand before
// `kind delete cluster` destroyed the table, and the hours 17-24 of that run
// were lost when the extraction lost that race.
type evalBucket struct {
	BucketStartUTC     string  `json:"bucket_start_utc"`
	NEvents            int     `json:"n_events"`
	CrudP95MS          float64 `json:"crud_p95_ms"`
	CrudP99MS          float64 `json:"crud_p99_ms"`
	AIP95MS            float64 `json:"ai_p95_ms"`
	AIP99MS            float64 `json:"ai_p99_ms"`
	MeanViolation      float64 `json:"mean_violation"`
	ViolationStepShare float64 `json:"violation_step_share"`
}

// evalBucketAcc accumulates one bucket while the events are walked once.
type evalBucketAcc struct {
	crud, ai          []float64
	violations, total int
	steps             map[int64][2]int
}

type evalExportDoc struct {
	TotalCostUSD       float64 `json:"total_cost_usd"`
	CostTierUSD        float64 `json:"cost_tier_usd"`
	CostInfraUSD       float64 `json:"cost_infra_usd"`
	MeanViolation      float64 `json:"mean_violation"`
	ViolationStepShare float64 `json:"violation_step_share"`
	MeanJain           float64 `json:"mean_jain"`
	CacheHitRate       float64 `json:"cache_hit_rate"`
	CrudP95MS          float64 `json:"crud_p95_ms"`
	AIP95MS            float64 `json:"ai_p95_ms"`
	// p99 is a live-only order statistic (PREREG_LIVE_CHAOS_P99.md); the sim
	// estimates p95 only, so these stay out of the sim-comparison tables.
	CrudP99MS float64 `json:"crud_p99_ms"`
	AIP99MS   float64 `json:"ai_p99_ms"`
	// TierRequests counts backend-serving AI requests per tier (cache hits
	// excluded) — the tier histogram the three-knob live plane meters $-cost
	// from (PREREG_WAVE4_LIVE_PLANE.md §Metrics).
	TierRequests        map[string]int `json:"tier_requests"`
	// Buckets is populated only when --bucket-seconds is set, so every
	// existing consumer and every committed record is byte-identical without
	// it (omitempty). 3600 for a soak, 60 for a short validation stage.
	Buckets             []evalBucket   `json:"buckets,omitempty"`
	NEvents             int            `json:"n_events"`
	NTenants            int            `json:"n_tenants"`
	GeneratedAtUTC      string         `json:"generated_at_utc"`
	CostInfraSourceNote string         `json:"cost_infra_source_note"`
}

func evalExport(args []string) int {
	fs := flag.NewFlagSet("eval-export", flag.ContinueOnError)
	format := fs.String("format", "json", "output format; only json is defined")
	out := fs.String("out", "", "output file path (required)")
	infraCost := fs.Float64("infra-cost-usd", 0.0,
		"infra cost component injected by the harness (replica-hours are not visible in-pod)")
	maxEvents := fs.Int("max-events", 5_000_000, "per-tenant telemetry read ceiling")
	bucketSeconds := fs.Int("bucket-seconds", 0,
		"emit per-window buckets alongside the scalars (0 = off; 3600 for a soak, 60 for a short stage)")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *format != "json" || *out == "" {
		fmt.Fprintln(os.Stderr, "usage: control-plane eval-export --format=json --out=PATH|- [--infra-cost-usd=X] [--bucket-seconds=N]")
		return 2
	}
	if *bucketSeconds < 0 {
		fmt.Fprintln(os.Stderr, "eval-export: --bucket-seconds must not be negative")
		return 2
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	tenants, events, err := evalLoadStore(ctx, *maxEvents)
	if err != nil {
		fmt.Fprintf(os.Stderr, "eval-export: %v\n", err)
		return 1
	}

	doc, err := computeEvalExport(tenants, events, *infraCost, *bucketSeconds)
	if err != nil {
		fmt.Fprintf(os.Stderr, "eval-export: %v\n", err)
		return 1
	}
	payload, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "eval-export: %v\n", err)
		return 1
	}
	// "-" streams to stdout: the eval pod's root filesystem is read-only and
	// the distroless image has no tar for `kubectl cp`, so the harness
	// captures the exec step's stdout instead. Status goes to stderr so the
	// payload stays parseable.
	if *out == "-" {
		if _, err := os.Stdout.Write(append(payload, '\n')); err != nil {
			fmt.Fprintf(os.Stderr, "eval-export: %v\n", err)
			return 1
		}
	} else if err := os.WriteFile(*out, append(payload, '\n'), 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "eval-export: %v\n", err)
		return 1
	}
	fmt.Fprintf(os.Stderr, "eval-export: %d events across %d tenants -> %s\n", doc.NEvents, doc.NTenants, *out)
	return 0
}

// evalLoadStore opens the same store the serving process uses (same env
// contract as main) and reads every tenant's telemetry.
func evalLoadStore(ctx context.Context, maxEvents int) ([]tenant.Tenant, map[string][]telemetry.Event, error) {
	var tenantRepo tenant.Repository
	var telemetryRepo telemetry.Repository
	var closeStore func()

	if adminURL := os.Getenv("POLYFORGE_POSTGRES_ADMIN_URL"); adminURL != "" {
		store, err := postgresstore.Open(ctx, postgresstore.Config{
			AdminURL: adminURL,
			AppURL:   os.Getenv("POLYFORGE_POSTGRES_APP_URL"),
		})
		if err != nil {
			return nil, nil, err
		}
		tenantRepo, telemetryRepo, closeStore = store, store, store.Close
	} else {
		dbPath := os.Getenv("POLYFORGE_DB_PATH")
		if dbPath == "" {
			dbPath = filepath.Join("data", "polyforge.db")
		}
		store, err := sqlitestore.Open(ctx, dbPath)
		if err != nil {
			return nil, nil, err
		}
		tenantRepo, telemetryRepo, closeStore = store, store, func() { _ = store.Close() }
	}
	defer closeStore()

	tenants, err := tenantRepo.ListTenants(ctx)
	if err != nil {
		return nil, nil, err
	}
	events := make(map[string][]telemetry.Event, len(tenants))
	for _, t := range tenants {
		rows, err := telemetryRepo.RecentByTenant(ctx, t.ID, maxEvents)
		if err != nil {
			return nil, nil, err
		}
		events[t.ID] = rows
	}
	return tenants, events, nil
}

func computeEvalExport(tenants []tenant.Tenant, events map[string][]telemetry.Event, infraCostUSD float64, bucketSeconds int) (evalExportDoc, error) {
	doc := evalExportDoc{
		CostInfraUSD:        infraCostUSD,
		GeneratedAtUTC:      time.Now().UTC().Format(time.RFC3339),
		CostInfraSourceNote: "infra component injected via --infra-cost-usd; replica-hours are not visible in-pod",
		MeanJain:            1.0,
		TierRequests:        map[string]int{},
	}

	var crudLatencies, aiLatencies []float64
	var violations, total int
	var cacheHits, aiTotal int
	attainments := make([]float64, 0, len(tenants))
	stepViolations := map[int64][2]int{} // step -> {over-target, total}
	buckets := map[int64]*evalBucketAcc{} // bucket index -> accumulator

	for _, t := range tenants {
		// An unrecognised plan is a provisioning bug, not a default. Silently
		// substituting "standard" is how every live tenant came to be graded
		// at 2.5x regardless of its mix — premium 2.5x too leniently,
		// best-effort 3.2x too strictly — which voids any live/sim parity
		// reading on a non-uniform mix. Fail the export instead.
		plan := strings.TrimSpace(t.Plan)
		scale, ok := evalSLOClassScale[plan]
		if !ok {
			return evalExportDoc{}, fmt.Errorf(
				"tenant %q has unrecognised plan %q: cannot score latency "+
					"without its SLO class", t.ID, plan)
		}
		tenantOver, tenantTotal := 0, 0
		for _, e := range events[t.ID] {
			isAI := strings.TrimSpace(e.ModelTier) != ""
			target := evalSLOBaseMS["crud"] * scale
			if isAI {
				target = evalSLOBaseMS["ai"] * scale
				aiLatencies = append(aiLatencies, e.LatencyMS)
				aiTotal++
				if e.CacheHit {
					// A cache hit never touched a model backend: it costs
					// nothing and belongs to no tier's serving histogram.
					// (The gateway records the hit's would-be tier so the
					// event stays in the AI latency family.)
					cacheHits++
				} else {
					tier := strings.TrimSpace(e.ModelTier)
					// Fail closed on an unrecognised tier, mirroring the plan
					// lookup above. A bare map read returns $0 for an unknown
					// tier while still counting it in the histogram, so a
					// tenant asserting ModelTier:"ultra" would book AI requests
					// for free and skew the campaign's cost metric. Ingest now
					// rejects unknown tiers (internal/platform validModelTier);
					// this guards older data and any non-ingest path.
					cost, ok := evalTierCostUSD[tier]
					if !ok {
						return evalExportDoc{}, fmt.Errorf(
							"tenant %q emitted unrecognised model tier %q: cannot "+
								"price the AI request", t.ID, tier)
					}
					doc.CostTierUSD += cost
					doc.TierRequests[tier]++
				}
			} else {
				crudLatencies = append(crudLatencies, e.LatencyMS)
			}
			total++
			tenantTotal++
			step := e.Timestamp.Unix() / evalStepSeconds
			counts := stepViolations[step]
			counts[1]++
			if e.LatencyMS > target {
				violations++
				tenantOver++
				counts[0]++
			}
			stepViolations[step] = counts
			if bucketSeconds > 0 {
				key := e.Timestamp.Unix() / int64(bucketSeconds)
				acc := buckets[key]
				if acc == nil {
					acc = &evalBucketAcc{steps: map[int64][2]int{}}
					buckets[key] = acc
				}
				if isAI {
					acc.ai = append(acc.ai, e.LatencyMS)
				} else {
					acc.crud = append(acc.crud, e.LatencyMS)
				}
				acc.total++
				bc := acc.steps[step]
				bc[1]++
				if e.LatencyMS > target {
					acc.violations++
					bc[0]++
				}
				acc.steps[step] = bc
			}
		}
		if tenantTotal > 0 {
			attainments = append(attainments, 1.0-float64(tenantOver)/float64(tenantTotal))
		}
	}

	doc.NEvents = total
	doc.NTenants = len(tenants)
	doc.CrudP95MS = evalP95(crudLatencies)
	doc.AIP95MS = evalP95(aiLatencies)
	doc.CrudP99MS = evalPercentile(crudLatencies, 0.99)
	doc.AIP99MS = evalPercentile(aiLatencies, 0.99)
	if total > 0 {
		doc.MeanViolation = float64(violations) / float64(total)
	}
	if aiTotal > 0 {
		doc.CacheHitRate = float64(cacheHits) / float64(aiTotal)
	}
	if len(stepViolations) > 0 {
		missed := 0
		for _, counts := range stepViolations {
			if float64(counts[0]) > 0.05*float64(counts[1]) {
				missed++
			}
		}
		doc.ViolationStepShare = float64(missed) / float64(len(stepViolations))
	}
	if len(attainments) > 0 {
		var sum, sumSq float64
		for _, a := range attainments {
			sum += a
			sumSq += a * a
		}
		if sumSq > 0 {
			doc.MeanJain = (sum * sum) / (float64(len(attainments)) * sumSq)
		}
	}
	doc.TotalCostUSD = doc.CostTierUSD + doc.CostInfraUSD
	doc.Buckets = evalFinishBuckets(buckets, bucketSeconds)
	return doc, nil
}

// evalFinishBuckets turns the accumulators into sorted, scored rows.
//
// Percentiles go through evalPercentile unchanged, so a bucket's p95 is the
// same nearest-rank order statistic as the run-level scalar beside it. That
// matters more than it looks: the hand-rolled Postgres rescue for attempt 4
// had to use percentile_disc rather than percentile_cont for exactly this
// reason -- interpolation disagrees with the exporter in the fourth decimal,
// which is enough to make a bucket and a scalar look like different
// measurements of different things.
//
// Buckets are keyed by absolute epoch window, so a bucket boundary falls at
// the same wall-clock instant regardless of when the run started, and an
// empty window simply does not appear rather than appearing as a zero.
func evalFinishBuckets(accs map[int64]*evalBucketAcc, bucketSeconds int) []evalBucket {
	if bucketSeconds <= 0 || len(accs) == 0 {
		return nil
	}
	keys := make([]int64, 0, len(accs))
	for k := range accs {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })

	out := make([]evalBucket, 0, len(keys))
	for _, k := range keys {
		acc := accs[k]
		b := evalBucket{
			BucketStartUTC: time.Unix(k*int64(bucketSeconds), 0).UTC().Format(time.RFC3339),
			NEvents:        acc.total,
			CrudP95MS:      evalP95(acc.crud),
			CrudP99MS:      evalPercentile(acc.crud, 0.99),
			AIP95MS:        evalP95(acc.ai),
			AIP99MS:        evalPercentile(acc.ai, 0.99),
		}
		if acc.total > 0 {
			b.MeanViolation = float64(acc.violations) / float64(acc.total)
		}
		if len(acc.steps) > 0 {
			missed := 0
			for _, counts := range acc.steps {
				if float64(counts[0]) > 0.05*float64(counts[1]) {
					missed++
				}
			}
			b.ViolationStepShare = float64(missed) / float64(len(acc.steps))
		}
		out = append(out, b)
	}
	return out
}

func evalP95(values []float64) float64 { return evalPercentile(values, 0.95) }

// evalPercentile is the exact order statistic used for p95; p99 (Wave 3,
// PREREG_LIVE_CHAOS_P99.md) reads the same per-request latencies through the
// identical path. p99 is a LIVE-ONLY number: the sim is a p95 estimator by
// construction (model.P95_FACTOR), so p99 is exported for the cluster and is
// never back-fitted into the sim tables.
func evalPercentile(values []float64, q float64) float64 {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]float64(nil), values...)
	sort.Float64s(sorted)
	index := int(math.Ceil(q*float64(len(sorted)))) - 1
	if index < 0 {
		index = 0
	}
	if index >= len(sorted) {
		index = len(sorted) - 1
	}
	return sorted[index]
}
