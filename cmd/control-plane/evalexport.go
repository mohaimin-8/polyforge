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

// evalExportTimeout bounds the whole export.
//
// It was 5 minutes, chosen when the exporter only ever ran against smoke-test
// volumes. Measured against the soak-volume probe
// (TestEvalExportPostgresScalesToSoakVolume) the server-side path costs
// ~11.6 s per million events, so attempt 4's ~25M-event sitting projects to
// ~289 s -- inside 5 minutes by about four percent. A run slightly hotter than
// that one, or a slower disk, would have blown the deadline and produced no
// eval-export.json at all, which is exactly the empty-handed state four
// sittings have already ended in. There is no reason for a batch export at the
// end of a 24 h run to be on a tight clock, so the default is generous and
// --timeout exists for the case this estimate is still wrong.
const evalExportTimeout = 30 * time.Minute

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
	// Per-bucket tier spend (session 48): the paired bootstrap the Wave 4
	// pre-registration commits to needs a cost per bucket, and the export
	// carried only the run total. Same pricing as CostTierUSD, per bucket;
	// the harness adds the bucket's infra share from its replica sampler.
	CostTierUSD  float64        `json:"cost_tier_usd"`
	TierRequests map[string]int `json:"tier_requests,omitempty"`
}

// evalBucketAcc accumulates one bucket while the events are walked once.
type evalBucketAcc struct {
	crud, ai          []float64
	violations, total int
	steps             map[int64][2]int
	tierCounts        map[string]int
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
	TierRequests map[string]int `json:"tier_requests"`
	// Buckets is populated only when --bucket-seconds is set, so every
	// existing consumer and every committed record is byte-identical without
	// it (omitempty). 3600 for a soak, 60 for a short validation stage.
	Buckets        []evalBucket `json:"buckets,omitempty"`
	NEvents        int          `json:"n_events"`
	NTenants       int          `json:"n_tenants"`
	GeneratedAtUTC string       `json:"generated_at_utc"`
	// ScoredSinceUTC is the instant the harness passed as --since: only
	// events at or after it were scored. Empty when the whole store was.
	ScoredSinceUTC      string `json:"scored_since_utc,omitempty"`
	CostInfraSourceNote string `json:"cost_infra_source_note"`
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
	timeout := fs.Duration("timeout", evalExportTimeout,
		"ceiling on the whole export; a soak-sized store takes minutes to reduce")
	// Session 48: the WL-H2 knob-liveness gate sends its own requests through
	// the gateway as the first scored tenant before the load window opens --
	// eleven of them pinned to the largest tier, about $0.13, over half of a
	// cheap cell's total -- and every metric of the run carried them. The
	// harness passes the load window's start; nothing before it is scored.
	sinceFlag := fs.String("since", "",
		"RFC3339 instant; only telemetry at or after it is scored (the harness passes the load window's start)")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	var since time.Time
	if *sinceFlag != "" {
		parsed, err := time.Parse(time.RFC3339, *sinceFlag)
		if err != nil {
			fmt.Fprintf(os.Stderr, "eval-export: --since must be RFC3339: %v\n", err)
			return 2
		}
		since = parsed.UTC()
	}
	if *format != "json" || *out == "" {
		fmt.Fprintln(os.Stderr, "usage: control-plane eval-export --format=json --out=PATH|- [--infra-cost-usd=X] [--bucket-seconds=N] [--timeout=D]")
		return 2
	}
	if *bucketSeconds < 0 {
		fmt.Fprintln(os.Stderr, "eval-export: --bucket-seconds must not be negative")
		return 2
	}

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()

	var doc evalExportDoc
	if adminURL := os.Getenv("POLYFORGE_POSTGRES_ADMIN_URL"); adminURL != "" {
		aggDoc, err := evalAggregatePostgres(ctx, adminURL, *bucketSeconds, since)
		if err != nil {
			fmt.Fprintf(os.Stderr, "eval-export: %v\n", err)
			return 1
		}
		doc = aggDoc
		doc.CostInfraUSD = *infraCost
		doc.TotalCostUSD = doc.CostTierUSD + doc.CostInfraUSD
	} else {
		tenants, events, err := evalLoadStore(ctx, *maxEvents)
		if err != nil {
			fmt.Fprintf(os.Stderr, "eval-export: %v\n", err)
			return 1
		}
		doc, err = computeEvalExport(tenants, eventsSince(events, since), *infraCost, *bucketSeconds)
		if err != nil {
			fmt.Fprintf(os.Stderr, "eval-export: %v\n", err)
			return 1
		}
	}
	if !since.IsZero() {
		doc.ScoredSinceUTC = since.Format(time.RFC3339)
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

// evalAggregatePostgres scores the run server-side when the store is
// PostgreSQL. This is the only way a real soak can be exported at all: the
// in-memory path below reads every row through RecentByTenant and OOM-killed
// the control-plane pod at ~950k events inside its 256 Mi limit (exit 137).
// A 24 h run produces ~25M events, twenty-five times more, which is almost
// certainly why WP14 attempt 4 recorded no eval-export.json and therefore no
// metrics at all. See internal/storage/postgres/evalagg.go.
// eventsSince drops every event before `since` (a zero `since` keeps all).
// The in-memory twin of the Postgres path's timestamp predicate, so both
// exports score the same window.
func eventsSince(events map[string][]telemetry.Event, since time.Time) map[string][]telemetry.Event {
	if since.IsZero() {
		return events
	}
	out := make(map[string][]telemetry.Event, len(events))
	for tenantID, evs := range events {
		kept := make([]telemetry.Event, 0, len(evs))
		for _, e := range evs {
			if !e.Timestamp.Before(since) {
				kept = append(kept, e)
			}
		}
		out[tenantID] = kept
	}
	return out
}

func evalAggregatePostgres(ctx context.Context, adminURL string, bucketSeconds int, since time.Time) (evalExportDoc, error) {
	var doc evalExportDoc
	store, err := postgresstore.Open(ctx, postgresstore.Config{
		AdminURL: adminURL,
		AppURL:   os.Getenv("POLYFORGE_POSTGRES_APP_URL"),
	})
	if err != nil {
		return doc, err
	}
	defer store.Close()

	agg, err := store.EvalAggregate(ctx, postgresstore.EvalAggSpec{
		CrudTargetBaseMS: evalSLOBaseMS["crud"],
		AITargetBaseMS:   evalSLOBaseMS["ai"],
		ClassScale:       evalSLOClassScale,
		StepSeconds:      evalStepSeconds,
		BucketSeconds:    bucketSeconds,
		Since:            since,
	})
	if err != nil {
		return doc, err
	}

	doc = evalExportDoc{
		GeneratedAtUTC:      time.Now().UTC().Format(time.RFC3339),
		CostInfraSourceNote: "infra component injected via --infra-cost-usd; replica-hours are not visible in-pod",
		MeanJain:            1.0,
		TierRequests:        map[string]int{},
		NEvents:             agg.Overall.NEvents,
		NTenants:            agg.NTenants,
		CrudP95MS:           agg.Overall.CrudP95,
		CrudP99MS:           agg.Overall.CrudP99,
		AIP95MS:             agg.Overall.AIP95,
		AIP99MS:             agg.Overall.AIP99,
	}
	for tier, n := range agg.TierCounts {
		cost, ok := evalTierCostUSD[tier]
		if !ok {
			// Fail closed exactly as the in-memory path does: an unknown tier
			// would otherwise book AI requests for free and skew cost.
			return doc, fmt.Errorf("unrecognised model tier %q: cannot price the AI request", tier)
		}
		doc.CostTierUSD += cost * float64(n)
		doc.TierRequests[tier] = n
	}
	if agg.Overall.NEvents > 0 {
		doc.MeanViolation = float64(agg.Overall.Violations) / float64(agg.Overall.NEvents)
	}
	if agg.Overall.AITotal > 0 {
		doc.CacheHitRate = float64(agg.Overall.CacheHits) / float64(agg.Overall.AITotal)
	}
	if agg.Overall.StepsTotal > 0 {
		doc.ViolationStepShare = float64(agg.Overall.StepsMissed) / float64(agg.Overall.StepsTotal)
	}
	doc.MeanJain = evalJain(agg.Attainments)
	for _, w := range agg.Buckets {
		b := evalBucket{
			BucketStartUTC: time.Unix(w.StartUnix, 0).UTC().Format(time.RFC3339),
			NEvents:        w.NEvents,
			CrudP95MS:      w.CrudP95,
			CrudP99MS:      w.CrudP99,
			AIP95MS:        w.AIP95,
			AIP99MS:        w.AIP99,
		}
		if w.NEvents > 0 {
			b.MeanViolation = float64(w.Violations) / float64(w.NEvents)
		}
		if w.StepsTotal > 0 {
			b.ViolationStepShare = float64(w.StepsMissed) / float64(w.StepsTotal)
		}
		for tier, n := range w.TierCounts {
			cost, ok := evalTierCostUSD[tier]
			if !ok {
				return doc, fmt.Errorf("unrecognised model tier %q in a bucket: cannot price the AI request", tier)
			}
			b.CostTierUSD += cost * float64(n)
			if b.TierRequests == nil {
				b.TierRequests = map[string]int{}
			}
			b.TierRequests[tier] = n
		}
		doc.Buckets = append(doc.Buckets, b)
	}
	return doc, nil
}

// evalJain is Jain's fairness index over per-tenant attainments, shared by both
// scoring paths so they cannot drift apart.
func evalJain(attainments []float64) float64 {
	if len(attainments) == 0 {
		return 1.0
	}
	var sum, sumSq float64
	for _, a := range attainments {
		sum += a
		sumSq += a * a
	}
	if sumSq == 0 {
		return 1.0
	}
	return (sum * sum) / (float64(len(attainments)) * sumSq)
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
	stepViolations := map[int64][2]int{}  // step -> {over-target, total}
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
					if !e.CacheHit {
						if acc.tierCounts == nil {
							acc.tierCounts = map[string]int{}
						}
						acc.tierCounts[strings.TrimSpace(e.ModelTier)]++
					}
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
		for tier, n := range acc.tierCounts {
			// Unknown tiers were rejected while the events were walked.
			b.CostTierUSD += evalTierCostUSD[tier] * float64(n)
			if b.TierRequests == nil {
				b.TierRequests = map[string]int{}
			}
			b.TierRequests[tier] = n
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
