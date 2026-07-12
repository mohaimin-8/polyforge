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

type evalExportDoc struct {
	TotalCostUSD        float64 `json:"total_cost_usd"`
	CostTierUSD         float64 `json:"cost_tier_usd"`
	CostInfraUSD        float64 `json:"cost_infra_usd"`
	MeanViolation       float64 `json:"mean_violation"`
	ViolationStepShare  float64 `json:"violation_step_share"`
	MeanJain            float64 `json:"mean_jain"`
	CacheHitRate        float64 `json:"cache_hit_rate"`
	CrudP95MS           float64 `json:"crud_p95_ms"`
	AIP95MS             float64 `json:"ai_p95_ms"`
	NEvents             int     `json:"n_events"`
	NTenants            int     `json:"n_tenants"`
	GeneratedAtUTC      string  `json:"generated_at_utc"`
	CostInfraSourceNote string  `json:"cost_infra_source_note"`
}

func evalExport(args []string) int {
	fs := flag.NewFlagSet("eval-export", flag.ContinueOnError)
	format := fs.String("format", "json", "output format; only json is defined")
	out := fs.String("out", "", "output file path (required)")
	infraCost := fs.Float64("infra-cost-usd", 0.0,
		"infra cost component injected by the harness (replica-hours are not visible in-pod)")
	maxEvents := fs.Int("max-events", 5_000_000, "per-tenant telemetry read ceiling")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *format != "json" || *out == "" {
		fmt.Fprintln(os.Stderr, "usage: control-plane eval-export --format=json --out=PATH|- [--infra-cost-usd=X]")
		return 2
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	tenants, events, err := evalLoadStore(ctx, *maxEvents)
	if err != nil {
		fmt.Fprintf(os.Stderr, "eval-export: %v\n", err)
		return 1
	}

	doc := computeEvalExport(tenants, events, *infraCost)
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

func computeEvalExport(tenants []tenant.Tenant, events map[string][]telemetry.Event, infraCostUSD float64) evalExportDoc {
	doc := evalExportDoc{
		CostInfraUSD:        infraCostUSD,
		GeneratedAtUTC:      time.Now().UTC().Format(time.RFC3339),
		CostInfraSourceNote: "infra component injected via --infra-cost-usd; replica-hours are not visible in-pod",
		MeanJain:            1.0,
	}

	var crudLatencies, aiLatencies []float64
	var violations, total int
	var cacheHits, aiTotal int
	attainments := make([]float64, 0, len(tenants))
	stepViolations := map[int64][2]int{} // step -> {over-target, total}

	for _, t := range tenants {
		scale, ok := evalSLOClassScale[strings.TrimSpace(t.Plan)]
		if !ok {
			scale = evalSLOClassScale["standard"]
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
					cacheHits++
				}
				doc.CostTierUSD += evalTierCostUSD[strings.TrimSpace(e.ModelTier)]
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
		}
		if tenantTotal > 0 {
			attainments = append(attainments, 1.0-float64(tenantOver)/float64(tenantTotal))
		}
	}

	doc.NEvents = total
	doc.NTenants = len(tenants)
	doc.CrudP95MS = evalP95(crudLatencies)
	doc.AIP95MS = evalP95(aiLatencies)
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
	return doc
}

func evalP95(values []float64) float64 {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]float64(nil), values...)
	sort.Float64s(sorted)
	index := int(math.Ceil(0.95*float64(len(sorted)))) - 1
	if index < 0 {
		index = 0
	}
	return sorted[index]
}
